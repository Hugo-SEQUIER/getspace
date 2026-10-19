"""Parameters: a signed-in person and a small picture they chose on the website.
What it does: keeps one profile picture per person in their personal Space.
Output: the picture as a data URL, or None when they have not chosen one.
"""

import base64
import binascii
import re

from pydantic import Field

from space.memory import Input
from space.providers import key_operation

# The website sends a 256-pixel square; this leaves room for a larger photo, not a document.
MAX_IMAGE_BYTES = 200_000
_DATA_URL = re.compile(r"^data:(image/(?:png|jpeg|webp));base64,([A-Za-z0-9+/]+={0,2})$")
_SIGNATURES = {
    "image/png": lambda data: data.startswith(b"\x89PNG\r\n\x1a\n"),
    "image/jpeg": lambda data: data.startswith(b"\xff\xd8\xff"),
    "image/webp": lambda data: data[:4] == b"RIFF" and data[8:12] == b"WEBP",
}


class SaveImage(Input):
    image: str = Field(min_length=1, max_length=4 * ((MAX_IMAGE_BYTES + 2) // 3) + 32)


def _parse(image):
    """Accepts a PNG, JPEG or WebP data URL whose bytes match its declared type."""
    from space.workspace import CommandError

    match = _DATA_URL.match(image)
    if not match:
        raise CommandError("Choose a PNG, JPEG or WebP picture.", 400)
    mime, data = match.groups()
    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        raise CommandError("Choose a PNG, JPEG or WebP picture.", 400) from None
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        raise CommandError("Choose a picture of at most 200 KB.", 400)
    if not _SIGNATURES[mime](raw):
        raise CommandError("The picture's content does not match its type.", 400)
    return mime, data


def _personal_scope(workspace, connection, identity, *, write=False):
    """The person's own personal Space: their picture follows them across every Space."""
    from space.workspace import CommandError

    row = connection.execute(
        "SELECT default_space_id FROM space.organizations "
        "WHERE kind='personal' AND owner_principal_id=space.current_principal_id()"
    ).fetchone()
    if row is None:
        raise CommandError("Open your Space once before choosing a picture.", 404)
    return workspace._scope(connection, identity, row["default_space_id"], write=write, human=True)


@key_operation
def get(workspace, identity):
    with workspace.db.transaction(identity) as connection:
        scope = _personal_scope(workspace, connection, identity)
        row = connection.execute(
            "SELECT key_id,revision,encrypted_image FROM space.profile_images "
            "WHERE principal_id=space.current_principal_id()"
        ).fetchone()
        key_row = row and workspace._key_row(connection, scope, row["key_id"])
    if row is None:
        return {"image": None}
    key = workspace._unwrap_key(scope["organization_id"], scope["space_id"], key_row)
    value = workspace._unpack(
        key, scope, scope["principal_id"], row["revision"], "profile_image", row["encrypted_image"]
    )
    return {"image": f"data:{value['mime']};base64,{value['data']}"}


@key_operation
def save(workspace, identity, change: SaveImage):
    mime, data = _parse(change.image)
    with workspace.db.transaction(identity) as connection:
        scope = _personal_scope(workspace, connection, identity, write=True)
        key_row = workspace._key_row(connection, scope)
        current = connection.execute(
            "SELECT revision FROM space.profile_images "
            "WHERE principal_id=space.current_principal_id()"
        ).fetchone()
    key = workspace._unwrap_key(scope["organization_id"], scope["space_id"], key_row)
    # Each picture takes a new revision, like every other stored payload.
    revision = (current["revision"] if current else 0) + 1
    encrypted = workspace._pack(
        key, scope, scope["principal_id"], revision, "profile_image", {"mime": mime, "data": data}
    )
    with workspace.db.transaction(identity) as connection:
        scope = _personal_scope(workspace, connection, identity, write=True)
        connection.execute(
            "INSERT INTO space.profile_images(organization_id,space_id,principal_id,key_id,"
            "revision,encrypted_image) VALUES(%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (organization_id,space_id,principal_id) DO UPDATE SET "
            "key_id=excluded.key_id,revision=excluded.revision,"
            "encrypted_image=excluded.encrypted_image,updated_at=now()",
            (
                scope["organization_id"],
                scope["space_id"],
                scope["principal_id"],
                key.key.key_id,
                revision,
                encrypted,
            ),
        )
    return {"image": f"data:{mime};base64,{data}"}


def delete(workspace, identity):
    with workspace.db.transaction(identity) as connection:
        _personal_scope(workspace, connection, identity, write=True)
        connection.execute(
            "DELETE FROM space.profile_images WHERE principal_id=space.current_principal_id()"
        )
    return {"image": None}
