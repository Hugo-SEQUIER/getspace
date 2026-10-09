"""Owner-only, immutable archive chunks for review before publication."""

import hashlib
import hmac
from contextlib import contextmanager
from io import BytesIO
from threading import BoundedSemaphore
from uuid import UUID

from pydantic import Field

from space.admission import AdmissionError
from space.archive_schema import MAX_ARCHIVE_BYTES, decode_archive, validate_archive
from space.memory import Input
from space.workspace import CommandError

_processing = BoundedSemaphore(1)


class Start(Input):
    id: UUID
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    bytes: int = Field(ge=1, le=MAX_ARCHIVE_BYTES, strict=True)
    parts: int = Field(ge=1, le=1024, strict=True)


class Chunk(Input):
    # Chunks are strings, split on JavaScript code points to preserve non-ASCII text.
    text: str = Field(min_length=1, max_length=180000)


def session(workspace, identity, space_id, restore_id):
    with workspace.db.transaction(identity) as connection:
        scope = workspace._scope(connection, identity, space_id, human=True)
        row = connection.execute(
            "SELECT * FROM space.restore_sessions WHERE id=%s AND expires_at>now()",
            (restore_id,),
        ).fetchone()
        if not row:
            raise CommandError("Restore expired or was not found. Upload the archive again.", 404)
        key_row = workspace._key_row(connection, scope, row["key_id"])
    key = workspace.keys.unwrap(scope["organization_id"], space_id, key_row)
    manifest = workspace._unpack(
        key, scope, restore_id, 1, "restore_manifest", row["encrypted_manifest"]
    )
    return scope, key, row, manifest


def start(workspace, identity, space_id, request: Start):
    with workspace.db.transaction(identity) as connection:
        workspace._scope(connection, identity, space_id, human=True)
    scope, key = workspace._key(identity, space_id, write=True)
    encrypted = workspace._pack(
        key, scope, request.id, 1, "restore_manifest", request.model_dump(mode="json")
    )
    with workspace.db.transaction(identity) as connection:
        workspace._scope(connection, identity, space_id, human=True)
        connection.execute("SELECT id FROM space.spaces FOR UPDATE")
        existing = connection.execute(
            "SELECT id FROM space.restore_sessions WHERE id=%s", (request.id,)
        ).fetchone()
        if not existing:
            count = connection.execute(
                "SELECT count(*) AS n FROM space.restore_sessions "
                "WHERE expires_at>now() AND state!='committed'"
            ).fetchone()["n"]
            if count >= 2:
                raise CommandError(
                    "Finish or cancel a pending restore before uploading another archive.", 409
                )
            connection.execute(
                "INSERT INTO space.restore_sessions(organization_id,space_id,id,key_id,"
                "encrypted_manifest) VALUES(%s,%s,%s,%s,%s)",
                (scope["organization_id"], space_id, request.id, key.key.key_id, encrypted),
            )
    _, _, row, manifest = session(workspace, identity, space_id, request.id)
    if manifest != request.model_dump(mode="json"):
        raise CommandError("This restore ID belongs to another archive.", 409)
    return {"id": request.id, "state": row["state"], "expires_at": row["expires_at"]}


def upload(workspace, identity, space_id, restore_id, part, request: Chunk):
    scope, key, _, manifest = session(workspace, identity, space_id, restore_id)
    if not 0 <= part < manifest["parts"]:
        raise CommandError("Invalid archive part", 422)
    raw = request.text.encode()
    if len(raw) > 720000:
        raise CommandError("Archive part exceeds its size limit", 413)
    digest = hmac.digest(key.key.material, raw, "sha256")
    encrypted = workspace._pack(
        key, scope, restore_id, part + 1, "restore_chunk", {"text": request.text}
    )
    with workspace.db.transaction(identity) as connection:
        workspace._scope(connection, identity, space_id, human=True)
        row = connection.execute(
            "SELECT state FROM space.restore_sessions WHERE id=%s AND expires_at>now() FOR UPDATE",
            (restore_id,),
        ).fetchone()
        if not row or row["state"] != "staged":
            raise CommandError("This restore no longer accepts uploads", 409)
        existing = connection.execute(
            "SELECT fingerprint FROM space.restore_chunks WHERE restore_id=%s AND part=%s",
            (restore_id, part),
        ).fetchone()
        if existing and not hmac.compare_digest(bytes(existing["fingerprint"]), digest):
            raise CommandError("This archive part already contains different content", 409)
        if not existing:
            used = connection.execute(
                "SELECT coalesce(sum(plain_bytes),0) AS n FROM space.restore_chunks "
                "WHERE restore_id=%s",
                (restore_id,),
            ).fetchone()["n"]
            if used + len(raw) > manifest["bytes"]:
                raise CommandError("Archive parts exceed the declared size", 413)
            connection.execute(
                "INSERT INTO space.restore_chunks(organization_id,space_id,restore_id,part,"
                "encrypted_payload,fingerprint,plain_bytes) VALUES(%s,%s,%s,%s,%s,%s,%s)",
                (scope["organization_id"], space_id, restore_id, part, encrypted, digest, len(raw)),
            )
    return {"part": part, "saved": True}


@contextmanager
def load(workspace, identity, space_id, restore_id):
    """Keep one archive in flight per API process through review or publication."""
    if not _processing.acquire(blocking=False):
        raise AdmissionError("Archive restore", "full")
    try:
        yield _load(workspace, identity, space_id, restore_id)
    finally:
        _processing.release()


def _load(workspace, identity, space_id, restore_id):
    scope, key, row, manifest = session(workspace, identity, space_id, restore_id)
    with workspace.db.transaction(identity) as connection:
        workspace._scope(connection, identity, space_id, human=True)
        chunks = connection.execute(
            "SELECT part,encrypted_payload FROM space.restore_chunks "
            "WHERE restore_id=%s ORDER BY part",
            (restore_id,),
        ).fetchall()
    if len(chunks) != manifest["parts"]:
        raise CommandError("Upload every archive part before review", 409)
    raw = BytesIO()
    for chunk in chunks:
        text = workspace._unpack(
            key, scope, restore_id, chunk["part"] + 1, "restore_chunk", chunk["encrypted_payload"]
        )["text"]
        raw.write(text.encode())
        if raw.tell() > manifest["bytes"] or raw.tell() > MAX_ARCHIVE_BYTES:
            raise CommandError("Archive size does not match its manifest", 422)
    # Drop the stored chunks before constructing the decoded object graph.
    del chunks
    raw = raw.getvalue()
    if len(raw) != manifest["bytes"] or hashlib.sha256(raw).hexdigest() != manifest["sha256"]:
        raise CommandError("Archive checksum does not match. Upload the original file again.", 422)
    try:
        archive = decode_archive(raw)
        groups = validate_archive(archive)
    except (ValueError, KeyError, TypeError, AttributeError, OverflowError, RecursionError):
        raise CommandError(
            "Archive is invalid, incomplete, or exceeds supported limits.", 422
        ) from None
    workspace._recheck(identity, space_id)
    return scope, key, row, archive, groups


def cancel(workspace, identity, space_id, restore_id):
    with workspace.db.transaction(identity) as connection:
        workspace._scope(connection, identity, space_id, human=True)
        row = connection.execute(
            "SELECT state,lease_until>now() AS active FROM space.restore_sessions "
            "WHERE id=%s FOR UPDATE",
            (restore_id,),
        ).fetchone()
        if row and row["state"] == "publishing" and row["active"]:
            raise CommandError("Restore is publishing. Wait for its result before cancelling.", 409)
        connection.execute("DELETE FROM space.restore_sessions WHERE id=%s", (restore_id,))
    return {"cancelled": True}


def listing(workspace, identity, space_id):
    with workspace.db.transaction(identity) as connection:
        workspace._scope(connection, identity, space_id, human=True)
        rows = connection.execute(
            "SELECT id,state,created_at,expires_at FROM space.restore_sessions "
            "WHERE expires_at>now() AND state!='committed' ORDER BY created_at"
        ).fetchall()
    return {"items": rows}
