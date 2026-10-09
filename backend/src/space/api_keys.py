"""Personal API keys, so a person's own client can call the REST API as them.

The owner creates a key for one Space on the website, read-only or read-write, and
sees the secret once. The key acts as the owner within that Space: writes are theirs and
their current role still applies. It never administers access (agents, keys, members,
restores, webhooks) and never reaches MCP. Keys are stored as SHA-256 digests.
"""

import hashlib
import secrets
import time
from datetime import UTC, datetime, timedelta
from uuid import UUID

from psycopg.errors import RaiseException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from space.device_auth import _clean_name
from space.security.auth import API_KEY_PREFIX, ApiKeyScope, Identity
from space.workspace import CommandError

MAX_ACTIVE_KEYS = 25  # per person per Space, enforced in SQL
MAX_LIFETIME_DAYS = 365
# A key without an expiry still yields a bounded identity, re-checked on every request.
SESSION_SECONDS = 24 * 60 * 60


def _digest(value: str) -> bytes:
    return hashlib.sha256(value.encode()).digest()


class CreateApiKey(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    can_write: bool = False
    # None creates a key that lasts until it is revoked.
    expires_in_days: int | None = Field(default=90, ge=1, le=MAX_LIFETIME_DAYS)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value):
        return _clean_name(value)


def _item(row) -> dict:
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "prefix": row["prefix"],
        "can_write": row["can_write"],
        "created_at": row["created_at"].isoformat(),
        "expires_at": row["expires_at"].isoformat() if row["expires_at"] else None,
        "last_used_at": row["last_used_at"].isoformat() if row["last_used_at"] else None,
    }


def create(workspace, identity: Identity, space_id: UUID, body: CreateApiKey) -> dict:
    """Parameters: the signed-in person, the Space and the key's settings. What it does:
    stores the digest of a new key. Output: the key's listing, plus the secret once."""
    identity.check(human=True)
    token = API_KEY_PREFIX + secrets.token_urlsafe(32)
    expires_at = (
        datetime.now(UTC) + timedelta(days=body.expires_in_days) if body.expires_in_days else None
    )
    try:
        with workspace.db.transaction(identity) as connection:
            workspace._scope(connection, identity, space_id, human=True, write=body.can_write)
            key_id = connection.execute(
                "SELECT space.api_key_create(%s,%s,%s,%s,%s,%s) AS id",
                (
                    _digest(token),
                    token[: len(API_KEY_PREFIX) + 8],
                    body.name,
                    body.can_write,
                    expires_at,
                    MAX_ACTIVE_KEYS,
                ),
            ).fetchone()["id"]
            row = next(
                row
                for row in connection.execute("SELECT * FROM space.api_key_list()").fetchall()
                if row["id"] == key_id
            )
    except RaiseException as error:
        if error.diag.message_primary == "space.api_key_limit":
            raise CommandError(
                f"This Space already has {MAX_ACTIVE_KEYS} active API keys of yours. "
                "Revoke one first.",
                409,
            ) from None
        raise
    return {**_item(row), "token": token}


def listing(workspace, identity: Identity, space_id: UUID) -> dict:
    """Parameters: the signed-in person and the Space. Output: their live keys there."""
    identity.check(human=True)
    with workspace.db.transaction(identity) as connection:
        workspace._scope(connection, identity, space_id, human=True)
        rows = connection.execute("SELECT * FROM space.api_key_list()").fetchall()
    return {"items": [_item(row) for row in rows]}


def revoke(workspace, identity: Identity, space_id: UUID, key_id: UUID) -> dict:
    """Parameters: the signed-in person, the Space and one of their keys. What it does:
    deletes the key, which stops working on its next request. Output: confirmation."""
    identity.check(human=True)
    with workspace.db.transaction(identity) as connection:
        workspace._scope(connection, identity, space_id, human=True)
        revoked = connection.execute("SELECT space.api_key_revoke(%s) AS ok", (key_id,)).fetchone()[
            "ok"
        ]
    if not revoked:
        raise CommandError("API key not found", 404)
    return {"revoked": True}


class ApiKeys:
    """Resolves personal API keys for the token verifier."""

    def __init__(self, database, issuer: str):
        self.database, self.issuer = database, issuer

    def identity(self, token: str) -> Identity | None:
        with self.database.system_transaction() as connection:
            row = connection.execute(
                "SELECT * FROM space.api_key_identity(%s)", (_digest(token),)
            ).fetchone()
        if row is None or row["issuer"] != self.issuer:
            return None
        session_end = int(time.time()) + SESSION_SECONDS
        expires_at = row["expires_at"]
        return Identity(
            row["issuer"],
            row["subject"],
            min(int(expires_at.timestamp()), session_end) if expires_at else session_end,
            api_key=ApiKeyScope(str(row["id"]), row["space_id"], row["can_write"]),
        )
