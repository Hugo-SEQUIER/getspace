"""Device-code connections for agents that cannot receive a browser redirect.

The agent asks for a short code, the signed-in person types it on the website and
approves the access, and the agent polls until it collects a Space-issued bearer
token. The token stands for an ordinary connection grant row, so listing, attribution
and revocation work the same for every connection. The owner can also create a
connection directly on the website (issue), which runs the same steps in one call.
"""

import hashlib
import secrets
import unicodedata
from dataclasses import replace
from datetime import timedelta
from typing import Literal
from uuid import UUID, uuid4

from fastapi.responses import JSONResponse
from psycopg.errors import RaiseException, UniqueViolation
from pydantic import BaseModel, ConfigDict, Field, field_validator

from space.agents import HARNESSES
from space.security.auth import SPACE_TOKEN_PREFIX, Identity
from space.workspace import CommandError

CODE_LIFETIME = timedelta(minutes=10)
TOKEN_LIFETIME = timedelta(days=180)
# Seconds between polls that the agent is told to respect; faster polls are refused.
POLL_INTERVAL = 5
MAX_CODE_ATTEMPTS = 10  # wrong codes per person per fifteen minutes, enforced in SQL
CODE_LENGTH = 6


def _digest(value: str) -> bytes:
    return hashlib.sha256(value.encode()).digest()


def _clean_name(value: str) -> str:
    value = " ".join(unicodedata.normalize("NFKC", value).split())
    if not value or len(value) > 80 or any(unicodedata.category(c).startswith("C") for c in value):
        raise ValueError("Use a name of 1 to 80 visible characters")
    return value


class DeviceStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_name: str = Field(default="Agent", min_length=1, max_length=80)
    harness: str = Field(default="other", min_length=1, max_length=40)

    @field_validator("client_name")
    @classmethod
    def clean_client_name(cls, value):
        return _clean_name(value)

    @field_validator("harness")
    @classmethod
    def known_harness(cls, value):
        # Agents name their harness freely; the person confirms it during consent.
        value = value.strip().lower()
        return value if value in HARNESSES else "other"


class DeviceClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_code: str = Field(min_length=CODE_LENGTH, max_length=32)

    @field_validator("user_code")
    @classmethod
    def digits_only(cls, value):
        digits = "".join(c for c in value if c.isdigit())
        if len(digits) != CODE_LENGTH or len(value) > 3 * CODE_LENGTH:
            raise ValueError("Enter the six-digit code shown by your agent")
        return digits


class DeviceConsent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    authorization_id: UUID
    action: Literal["approve", "deny"]
    can_write: bool = False
    agent_id: UUID | None = None
    space_id: UUID | None = None


class DevicePoll(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_code: str = Field(min_length=20, max_length=128)


class DirectConnection(BaseModel):
    """A connection the owner creates on the website and pastes into the agent's config."""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="Agent", min_length=1, max_length=80)
    harness: str = Field(default="other", min_length=1, max_length=40)
    can_write: bool = False
    agent_id: UUID | None = None
    space_id: UUID | None = None

    @field_validator("name")
    @classmethod
    def clean_connection_name(cls, value):
        return _clean_name(value)

    @field_validator("harness")
    @classmethod
    def known_connection_harness(cls, value):
        value = value.strip().lower()
        return value if value in HARNESSES else "other"


def begin(workspace, body: DeviceStart) -> dict:
    """Parameters: the agent's self-description. What it does: registers a pending
    request. Output: the secret for polling, the code to show the person, where to
    enter it, and the polling contract."""
    device_code = secrets.token_urlsafe(32)
    for _ in range(5):
        user_code = f"{secrets.randbelow(10**CODE_LENGTH):0{CODE_LENGTH}d}"
        try:
            with workspace.db.system_transaction() as connection:
                connection.execute(
                    "SELECT space.device_begin(%s,%s,%s,%s,%s)",
                    (
                        _digest(device_code),
                        user_code,
                        body.client_name,
                        body.harness,
                        CODE_LIFETIME,
                    ),
                )
        except UniqueViolation:
            continue  # the six-digit value is in use; draw another
        except RaiseException as error:
            if error.diag.message_primary == "space.device_busy":
                raise CommandError(
                    "Too many pending connection codes. Try again in a few minutes.", 503
                ) from None
            raise
        return {
            "device_code": device_code,
            "user_code": user_code,
            "verification_url": f"{workspace.web_url}/connect",
            "verification_url_complete": f"{workspace.web_url}/connect?code={user_code}",
            "expires_in": int(CODE_LIFETIME.total_seconds()),
            "interval": POLL_INTERVAL,
        }
    raise CommandError("Could not allocate a connection code. Try again.", 503)


def claim(workspace, identity: Identity, body: DeviceClaim) -> dict:
    """Parameters: the signed-in person and the code they typed. What it does: binds
    the pending request to them. Output: consent details in the OAuth details shape."""
    identity.check(human=True)
    try:
        with workspace.db.transaction(identity) as connection:
            row = connection.execute(
                "SELECT * FROM space.device_claim(%s)", (body.user_code,)
            ).fetchone()
    except RaiseException as error:
        if error.diag.message_primary == "space.device_attempts":
            raise CommandError(
                "Too many incorrect codes. Wait fifteen minutes, then ask your agent "
                "for a new code.",
                429,
            ) from None
        if error.diag.message_primary == "space.invalid_scope":
            raise CommandError("Open your Space once before connecting an agent.", 409) from None
        raise
    if row is None:
        raise CommandError(
            "That code is not valid or has expired. Ask your agent for a new one.", 404
        )
    return {
        "authorization_id": str(row["authorization_id"]),
        "client": {"id": "device-code", "name": row["agent_name"]},
        "harness": row["agent_harness"],
        "redirect_uri": None,
        "scope": "",
    }


def decide(workspace, identity: Identity, body: DeviceConsent) -> dict:
    """Parameters: the signed-in person's decision. What it does: on approval creates
    the grant and marks the request approved in one transaction; a denial only marks
    it. Output: the recorded status."""
    identity.check(human=True)
    approve = body.action == "approve"
    client_id = uuid4()
    actor = replace(identity, selected_space_id=body.space_id)
    with workspace.db.transaction(actor) as connection:
        if approve:
            grant_id = workspace._grant(connection, actor, client_id, body.can_write, body.agent_id)
        else:
            workspace._scope(connection, actor, human=True)
            grant_id = None
        decided = connection.execute(
            "SELECT space.device_decide(%s,%s,%s,%s,%s,%s) AS ok",
            (
                body.authorization_id,
                approve,
                client_id if approve else None,
                UUID(grant_id) if grant_id else None,
                body.agent_id if approve else None,
                body.can_write if approve else None,
            ),
        ).fetchone()["ok"]
        if not decided:
            # Rolls back the grant with it.
            raise CommandError(
                "This connection request expired or was already answered. "
                "Ask your agent for a new code.",
                409,
            )
    return {"status": "approved" if approve else "denied"}


def poll(workspace, body: DevicePoll):
    """Parameters: the agent's secret. What it does: reports the decision and, once,
    issues the token. Output: RFC 8628-style statuses while waiting, credentials on
    approval."""
    token = SPACE_TOKEN_PREFIX + secrets.token_urlsafe(32)
    with workspace.db.system_transaction() as connection:
        row = connection.execute(
            "SELECT * FROM space.device_poll(%s,%s,%s,%s)",
            (
                _digest(body.device_code),
                _digest(token),
                TOKEN_LIFETIME,
                timedelta(seconds=max(POLL_INTERVAL - 1, 0)),
            ),
        ).fetchone()
    outcome = row["outcome"]
    if outcome == "approved":
        agent_id = str(row["agent"]) if row["agent"] else None
        headers = {"Authorization": "Bearer " + token}
        if agent_id:
            headers["X-Space-Agent-ID"] = agent_id
        return {
            "access_token": token,
            "token_type": "Bearer",
            "expires_in": int(TOKEN_LIFETIME.total_seconds()),
            "agent_id": agent_id,
            "mcp_url": workspace.resource_url,
            "headers": headers,
        }
    errors = {
        "pending": (
            "authorization_pending",
            "The code has not been approved yet. Poll again after the interval.",
        ),
        "slow_down": ("slow_down", "Polling too fast. Wait the full interval between polls."),
        "denied": ("access_denied", "The person declined this connection."),
        "expired": (
            "expired_token",
            "The code expired or was already used. Ask for a new code.",
        ),
    }
    code, description = errors[outcome]
    return JSONResponse(
        {"error": code, "error_description": description, "interval": POLL_INTERVAL}, 400
    )


def issue(workspace, identity: Identity, body: DirectConnection) -> dict:
    """Parameters: the owner and the connection to create. What it does: runs the
    device-code steps in one call (request, claim, approval, collection), so the new
    connection is listed, attributed and revoked exactly like one an agent requested.
    Output: the token and MCP settings, shown once, in the device poll's shape."""
    identity.check(human=True)
    started = begin(workspace, DeviceStart(client_name=body.name, harness=body.harness))
    claimed = claim(workspace, identity, DeviceClaim(user_code=started["user_code"]))
    decide(
        workspace,
        identity,
        DeviceConsent(
            authorization_id=UUID(claimed["authorization_id"]),
            action="approve",
            can_write=body.can_write,
            agent_id=body.agent_id,
            space_id=body.space_id,
        ),
    )
    result = poll(workspace, DevicePoll(device_code=started["device_code"]))
    if isinstance(result, JSONResponse):
        raise CommandError("The connection could not be created. Try again.", 503)
    return result


class SpaceTokens:
    """Resolves Space-issued bearer tokens for the token verifier."""

    def __init__(self, database, issuer: str):
        self.database, self.issuer = database, issuer

    def identity(self, token: str) -> Identity | None:
        with self.database.system_transaction() as connection:
            row = connection.execute(
                "SELECT * FROM space.device_token_identity(%s)", (_digest(token),)
            ).fetchone()
        if row is None or row["issuer"] != self.issuer:
            return None
        return Identity(
            row["issuer"],
            row["subject"],
            int(row["valid_until"].timestamp()),
            str(row["client"]),
            str(row["grant_row"]),
        )
