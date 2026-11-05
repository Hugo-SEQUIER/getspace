"""Original files kept with a document: parts, the commit that attaches them to a new
document, and the part reads behind the website's viewer and download.

A file travels in parts of at most PART_BYTES, about what one request may carry once
base64-encoded. This edition keeps a part's bytes in the folder-backed object store on the
server's disk and the key in its row, so a large file never enters PostgreSQL. A row
written before that move keeps its bytes in `content` and is still read from there;
`space.file_parts` records the object key or those bytes with the sizes and a digest of
what they hold. A part is staged when it arrives and attached when the document that holds
the file commits, within STAGED_MINUTES. The website discards the staged parts of an
upload that failed; the retention sweep removes any left an hour after their upload. The
document's body holds the text the browser extracted from the file, so search, recall and
agents read the file through it. Only a signed-in person uploads; agents see the file's
details and its text.

A picture, a video or a PDF may bring a thumbnail the browser made from it: a WebP or JPEG of
at most THUMBNAIL_BYTES, uploaded as a one-part file under its own ID and attached to the
same document, so the website's file grid shows it without downloading the original.
The file's details may also carry the picture's dimensions or the recording's duration, read
on the device at upload.
"""

import base64
import binascii
import hashlib
import hmac
import math
import re
from contextlib import suppress
from typing import Literal
from uuid import UUID

from pydantic import Field, ValidationError, field_validator

from space import local_store
from space.local_store import ObjectStoreError
from space.memory import Input
from space.providers import ObjectMissing
from space.read_timing import read_stage
from space.workspace import CommandError, SaveResource

PART_BYTES = 1024 * 1024
MAX_PARTS = 250
MAX_FILE_BYTES = PART_BYTES * MAX_PARTS
# Staged parts must be attached well inside the retention sweep's one-hour grace.
STAGED_MINUTES = 50
RETENTION_MINUTES = 60
# A thumbnail is one small part: large enough for a sharp 480-pixel picture, small enough that
# a grid of them costs little.
THUMBNAIL_BYTES = 64 * 1024
THUMBNAIL_SIDE = 1024
_NAME = re.compile(r"[^/\\\x00-\x1f\x7f]+")
# A media type and subtype made of RFC 6838 name characters, without parameters.
_TYPE = re.compile(r"[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+")


class Part(Input):
    """One part as standard base64; the decoded bytes are checked against PART_BYTES."""

    data: str = Field(min_length=4, max_length=4 * math.ceil(PART_BYTES / 3))


class Thumbnail(Input):
    """A small picture of the file, made in the browser and uploaded as part 0 of its own
    file ID before the commit."""

    id: UUID
    type: Literal["image/webp", "image/jpeg"]
    bytes: int = Field(ge=1, le=THUMBNAIL_BYTES, strict=True)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    width: int = Field(ge=1, le=THUMBNAIL_SIDE, strict=True)
    height: int = Field(ge=1, le=THUMBNAIL_SIDE, strict=True)


class Commit(Input):
    command_id: UUID
    file_id: UUID
    name: str = Field(min_length=1, max_length=255)
    type: str = Field(min_length=3, max_length=255)
    bytes: int = Field(ge=1, le=MAX_FILE_BYTES, strict=True)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    title: str = Field(min_length=1, max_length=300)
    body: str = Field(default="", max_length=700_000)
    project: UUID | None = None
    folder: UUID | None = None
    # What the browser could read about a picture or a recording; absent for other files.
    width: int | None = Field(default=None, ge=1, le=100_000, strict=True)
    height: int | None = Field(default=None, ge=1, le=100_000, strict=True)
    duration: float | None = Field(default=None, gt=0, le=10_000_000)
    thumbnail: Thumbnail | None = None

    @field_validator("thumbnail")
    @classmethod
    def own_id(cls, value, info):
        if value is not None and value.id == info.data.get("file_id"):
            raise ValueError("A thumbnail needs its own file ID")
        return value

    @field_validator("name")
    @classmethod
    def plain_name(cls, value):
        if not _NAME.fullmatch(value) or not value.strip():
            raise ValueError("A file name cannot contain slashes or control characters")
        return value

    @field_validator("type")
    @classmethod
    def media_type(cls, value):
        if not _TYPE.fullmatch(value):
            raise ValueError("Use a media type such as application/pdf")
        return value.lower()


def parts_for(size):
    return math.ceil(size / PART_BYTES)


def fingerprint(file_id, part, raw):
    """A digest bound to the file and part, so a retry can be recognized without a key.

    Parameters: the file's ID, the part number and the bytes the part holds.
    What it does: hashes a fixed label, the two identifiers and the bytes.
    Output: the 32 bytes `space.file_parts.fingerprint` stores; a moved part is checked
    against it before the database gives up its copy of the bytes.
    """
    return hashlib.sha256(
        b"space.file.v1\0" + str(file_id).encode() + b"\0" + str(part).encode() + b"\0" + raw
    ).digest()


def upload_part(workspace, identity, space_id, file_id: UUID, part: int, request: Part):
    """Parameters: a signed-in writer, the Space, the file's client-chosen ID, the part
    number and its base64 bytes.
    What it does: writes the part to the object store and records its key as staged, so a
    crash can leave an unreferenced object but never a row pointing at nothing. Sending
    the same bytes again is safe; different bytes for a part already recorded are refused.
    Output: the part number and its size in bytes.
    """
    if not 0 <= part < MAX_PARTS:
        raise CommandError(f"A file has at most {MAX_PARTS} parts", 422)
    try:
        raw = base64.b64decode(request.data, validate=True)
    except (binascii.Error, ValueError):
        raise CommandError("Send each file part as base64", 422) from None
    if not raw:
        raise CommandError("A file part cannot be empty", 422)
    if len(raw) > PART_BYTES:
        raise CommandError("A file part holds at most 1 MiB", 413)
    digest = fingerprint(file_id, part, raw)
    with workspace.db.transaction(identity) as connection:
        scope = workspace._scope(connection, identity, space_id, write=True, human=True)
        # One writer per part: a retry writes the same object key, so two uploads with
        # different bytes must not race on the file while the row decides which one stays.
        connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            (f"space.file_part:{file_id}:{part}",),
        )
        existing = connection.execute(
            "SELECT resource_id,fingerprint FROM space.file_parts WHERE file_id=%s AND part=%s",
            (file_id, part),
        ).fetchone()
        if existing and not hmac.compare_digest(bytes(existing["fingerprint"]), digest):
            raise CommandError("This file part was already uploaded with different content", 409)
        if existing:
            # Either already committed, or a staged retry that already holds these bytes;
            # in both cases the row needs no write.
            return {"file_id": str(file_id), "part": part, "bytes": len(raw)}
        # The bytes go first, so an interrupted upload leaves an object the reconcile
        # command can sweep, never a row whose object is missing. The storage quota
        # applies at the insert, before the part is counted.
        key = local_store.part_key(scope["organization_id"], space_id, file_id, part)
        try:
            workspace.objects.put(key, raw)
        except ObjectStoreError:
            # The folder is unusable on this server; say which setting an operator must fix.
            raise CommandError(
                "File storage is unavailable on the server. Check SPACE_FILE_DIR.", 503
            ) from None
        try:
            # The connection runs in pipeline mode, so an error the insert raises (the
            # storage quota, for one) surfaces on the result read rather than at execute:
            # read the row here, while the failed part's bytes can still be taken away.
            connection.execute(
                "INSERT INTO space.file_parts(organization_id,space_id,file_id,part,object_key,"
                "fingerprint,plain_bytes,content_bytes) VALUES(%s,%s,%s,%s,%s,%s,%s,%s) "
                "RETURNING part",
                (
                    scope["organization_id"],
                    space_id,
                    file_id,
                    part,
                    key,
                    digest,
                    len(raw),
                    len(raw),
                ),
            ).fetchone()
        except BaseException:
            # A part the database refused leaves no bytes behind.
            with suppress(Exception):
                workspace.objects.delete_keys([key])
            raise
    return {"file_id": str(file_id), "part": part, "bytes": len(raw)}


def commit(workspace, identity, space_id, request: Commit):
    """Parameters: a signed-in writer, the Space and the file's details with the document's
    title, extracted text and placement.
    What it does: creates a document that holds the file, attaching every staged part in the
    same transaction as the document's first revision, the thumbnail's part included when
    the browser made one. A retry with the same command ID and request returns that document.
    Output: the saved document, including its `file` details.
    """
    with workspace.db.transaction(identity) as connection:
        workspace._scope(connection, identity, space_id, write=True, human=True)
    try:
        document = SaveResource(
            command_id=request.command_id,
            expected_revision=0,
            kind="document",
            title=request.title,
            body=request.body,
            project=request.project,
            folder=request.folder,
        )
    except ValidationError:
        # Within the character limits, multi-byte text can still pass the 1 MiB resource cap.
        raise CommandError(
            "The document's text exceeds 1 MiB. Save the file with less extracted text.", 413
        ) from None
    file = {
        "id": str(request.file_id),
        "name": request.name,
        "type": request.type,
        "bytes": request.bytes,
        "sha256": request.sha256,
        "parts": parts_for(request.bytes),
    }
    for detail in ("width", "height", "duration"):
        if getattr(request, detail) is not None:
            file[detail] = getattr(request, detail)
    if request.thumbnail:
        file["thumbnail"] = {**request.thumbnail.model_dump(mode="json"), "parts": 1}
    return workspace.save_file(identity, space_id, document, file)


def attach(connection, resource_id, file):
    """Attach a new document's staged parts inside its commit transaction.

    Parameters: the transaction, the new document's ID and its file details, or those of
    the file's thumbnail.
    What it does: claims every staged part of the file uploaded within STAGED_MINUTES and
    checks they are exactly the parts the declared size needs.
    Output: none; a missing, extra or expired part raises and rolls the commit back.
    """
    rows = connection.execute(
        "UPDATE space.file_parts SET resource_id=%s WHERE file_id=%s AND resource_id IS NULL "
        "AND created_at>now()-make_interval(mins=>%s) RETURNING part,plain_bytes",
        (resource_id, file["id"], STAGED_MINUTES),
    ).fetchall()
    found = {row["part"]: row["plain_bytes"] for row in rows}
    count = parts_for(file["bytes"])
    expected = {part: PART_BYTES for part in range(count - 1)}
    expected[count - 1] = file["bytes"] - PART_BYTES * (count - 1)
    if found != expected:
        raise CommandError("The file upload is incomplete or expired. Upload the file again.", 409)


def detach(connection, resource_id):
    """Delete a removed document's parts in its deletion transaction.

    Parameters: the transaction and the document's ID.
    What it does: removes the stored parts, which frees their storage through the usage
        trigger. Call with space.quota_deletion on, before the document's own rows go.
    Output: the object prefixes to free, one per file, once the deletion has committed.
        Deleting the bytes before the rows commit could leave a row pointing at nothing.
    """
    rows = connection.execute(
        "DELETE FROM space.file_parts WHERE resource_id=%s "
        "RETURNING organization_id,space_id,file_id",
        (resource_id,),
    ).fetchall()
    return list(
        dict.fromkeys(
            local_store.file_prefix(row["organization_id"], row["space_id"], row["file_id"])
            for row in rows
        )
    )


def discard(workspace, identity, space_id, file_id: UUID):
    """Parameters: a signed-in writer, the Space and a file whose upload stopped.
    What it does: removes the file's staged parts and their objects, so a failed upload
    stops counting towards storage and takes no disk space with it. A part a document
    already committed is never touched.
    Output: {"discarded": the number of parts removed}.
    """
    with workspace.db.transaction(identity) as connection:
        workspace._scope(connection, identity, space_id, write=True, human=True)
        connection.execute("SELECT set_config('space.quota_deletion','on',true)")
        keys = [
            row["object_key"]
            for row in connection.execute(
                "DELETE FROM space.file_parts WHERE file_id=%s AND resource_id IS NULL "
                "RETURNING object_key",
                (file_id,),
            ).fetchall()
        ]
    workspace.objects.delete_keys([key for key in keys if key])
    return {"discarded": len(keys)}


def read_part(
    workspace, identity, space_id, resource_id: UUID, part: int, revision=None, thumbnail=False
):
    """Parameters: a reader of the Space, the document, the part number, optionally an
    earlier revision, and whether to read the file's thumbnail instead of the file.
    What it does: finds the file the revision holds, reads that one part, and rechecks access
    before returning it.
    Output: {"data": base64 of the part's bytes}, with the thumbnail's media "type" when it
    is the thumbnail.
    """
    with read_stage("db_read"), workspace.db.transaction(identity) as connection:
        scope = workspace._scope(connection, identity, space_id)
        row = connection.execute(
            "SELECT v.revision,v.key_id,v.encrypted_metadata,r.kind,k.wrapping_provider,"
            "k.wrapping_key_reference,k.wrapped_key FROM space.resources r "
            "JOIN space.resource_revisions v ON (r.organization_id,r.space_id,r.id)="
            "(v.organization_id,v.space_id,v.resource_id) "
            "JOIN space.space_keys k ON (k.organization_id,k.space_id,k.id)="
            "(v.organization_id,v.space_id,v.key_id) "
            "WHERE r.id=%s AND v.revision=coalesce(%s,r.current_revision)",
            (resource_id, revision),
        ).fetchone()
        if row is None or row["kind"] != "document":
            raise CommandError("Resource not found", 404)
        key_row = {
            "id": row["key_id"],
            "wrapping_provider": row["wrapping_provider"],
            "wrapping_key_reference": row["wrapping_key_reference"],
            "wrapped_key": row["wrapped_key"],
        }
        # Which file a revision holds is part of its metadata, so read this part of every
        # file the document has and keep the one its metadata names below. A row written
        # before the move to disk keeps its bytes inline; both are read the same way.
        stored = {
            str(found["file_id"]): (
                found["object_key"],
                None if found["content"] is None else bytes(found["content"]),
            )
            for found in connection.execute(
                "SELECT file_id,content,object_key FROM space.file_parts "
                "WHERE resource_id=%s AND part=%s",
                (resource_id, part),
            ).fetchall()
        }
    with read_stage("space_key"):
        key = workspace._unwrap_key(scope["organization_id"], scope["space_id"], key_row)
    with read_stage("decode"):
        metadata = workspace._unpack(
            key, scope, resource_id, row["revision"], "metadata", row["encrypted_metadata"]
        )
    file = metadata.get("file")
    if not file:
        raise CommandError("This document has no original file", 404)
    if thumbnail:
        file = file.get("thumbnail")
        if not file:
            raise CommandError("This file has no thumbnail", 404)
    if not 0 <= part < file["parts"] or file["id"] not in stored:
        raise CommandError("File part not found", 404)
    with read_stage("db_recheck"):
        workspace._recheck(identity, space_id)
    object_key, content = stored[file["id"]]
    if object_key:
        with read_stage("object_get"):
            try:
                content = workspace.objects.get(object_key)
            except ObjectMissing:
                raise CommandError("File part not found", 404) from None
    elif content is None:
        # The check constraint keeps this from happening; refuse rather than encode nothing.
        raise CommandError("File part not found", 404)
    result = {"data": base64.b64encode(content).decode("ascii")}
    if thumbnail:
        result["type"] = file["type"]
    return result
