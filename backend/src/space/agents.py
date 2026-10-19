"""Reusable attribution profiles; connection grants remain the authority."""

import hmac
import unicodedata
from uuid import UUID, uuid4

from pydantic import Field, field_validator

from space import content_changes
from space.memory import Input

HARNESSES = (
    "codex",
    "claude-code",
    "hermes",
    "claude",
    "chatgpt",
    "cursor",
    "gemini",
    "pi",
    "openclaw",
    "prime-agent",
    "opencode",
    "cline",
    "devin",
    "factory",
    "grok",
    "perplexity",
    "deepseek",
    "zai",
    "meta",
    "minimax",
    "kimi",
    "other",
)
MAX_AGENTS = 200


class AgentProfile(Input):
    name: str = Field(min_length=1, max_length=80)
    harness: str = Field(min_length=1, max_length=40)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value):
        value = " ".join(unicodedata.normalize("NFKC", value).split())
        if (
            not value
            or len(value) > 80
            or any(unicodedata.category(c).startswith("C") for c in value)
        ):
            raise ValueError("Use a name of 1 to 80 visible characters")
        return value

    @field_validator("harness")
    @classmethod
    def known_harness(cls, value):
        if value not in HARNESSES:
            raise ValueError("Choose a supported harness or other")
        return value


class EditAgent(AgentProfile):
    expected_revision: int = Field(ge=1, strict=True)


def _key(workspace, identity, *, human=False, write=False):
    with workspace.db.transaction(identity) as connection:
        scope = workspace._scope(
            connection, identity, human=human, write=write, allow_unidentified=True
        )
        key_row = workspace._key_row(connection, scope)
    return scope, workspace.keys.unwrap(scope["organization_id"], scope["space_id"], key_row)


def _view(workspace, identity, scope, row):
    with workspace.db.transaction(identity) as connection:
        workspace._scope(connection, identity, allow_unidentified=True)
        key_row = workspace._key_row(connection, scope, row["key_id"])
    key = workspace.keys.unwrap(scope["organization_id"], scope["space_id"], key_row)
    value = workspace._unpack(
        key, scope, row["id"], row["revision"], "agent", row["encrypted_metadata"]
    )
    return {"id": str(row["id"]), "revision": row["revision"], **value}


def list_profiles(workspace, identity):
    with workspace.db.transaction(identity) as connection:
        scope = workspace._scope(connection, identity, allow_unidentified=True)
        rows = connection.execute(
            "SELECT * FROM space.agent_identities WHERE deleted_at IS NULL "
            "ORDER BY created_at,id LIMIT %s",
            (MAX_AGENTS,),
        ).fetchall()
        key_rows = {
            key_id: workspace._key_row(connection, scope, key_id)
            for key_id in {row["key_id"] for row in rows}
        }
    keys = {
        key_id: workspace.keys.unwrap(scope["organization_id"], scope["space_id"], row)
        for key_id, row in key_rows.items()
    }
    items = [
        {
            "id": str(row["id"]),
            "revision": row["revision"],
            **workspace._unpack(
                keys[row["key_id"]],
                scope,
                row["id"],
                row["revision"],
                "agent",
                row["encrypted_metadata"],
            ),
        }
        for row in rows
    ]
    with workspace.db.transaction(identity) as connection:
        workspace._scope(connection, identity, allow_unidentified=True)
    return {"items": items}


def save_profile(workspace, identity, profile, agent_id=None, *, human=True):
    from space.workspace import CommandError

    scope, key = _key(workspace, identity, human=human, write=human)
    if scope["role"] == "viewer":
        raise CommandError("Ask a Space editor to create an agent identity first.", 403)
    fingerprint = hmac.digest(
        key.key.material,
        b"space.agent.v1\0" + profile.harness.encode() + b"\0" + profile.name.casefold().encode(),
        "sha256",
    )
    candidate = agent_id or uuid4()
    revision = profile.expected_revision + 1 if agent_id else 1
    encrypted = workspace._pack(
        key, scope, candidate, revision, "agent", {"name": profile.name, "harness": profile.harness}
    )
    with workspace.db.transaction(identity) as connection:
        workspace._scope(connection, identity, human=human, write=human, allow_unidentified=True)
        connection.execute("SELECT id FROM space.spaces FOR UPDATE")
        duplicate = connection.execute(
            "SELECT * FROM space.agent_identities WHERE identity_hash=%s AND deleted_at IS NULL",
            (fingerprint,),
        ).fetchone()
        if duplicate and (agent_id is None or duplicate["id"] != agent_id):
            if agent_id:
                raise CommandError(
                    "An agent with this name and harness already exists. Use that identity.", 409
                )
            row = duplicate
        elif agent_id:
            row = connection.execute(
                "UPDATE space.agent_identities SET revision=%s,key_id=%s,encrypted_metadata=%s,"
                "identity_hash=%s,updated_at=now() WHERE id=%s AND revision=%s "
                "AND deleted_at IS NULL RETURNING *",
                (
                    revision,
                    key.key.key_id,
                    encrypted,
                    fingerprint,
                    agent_id,
                    profile.expected_revision,
                ),
            ).fetchone()
            if row is None:
                raise CommandError("Agent changed or was not found. Refresh and try again.", 409)
        else:
            count = connection.execute(
                "SELECT count(*) AS n FROM space.agent_identities WHERE deleted_at IS NULL"
            ).fetchone()["n"]
            if count >= MAX_AGENTS:
                raise CommandError("This Space has reached its agent identity limit", 409)
            row = connection.execute(
                "INSERT INTO space.agent_identities (organization_id,space_id,id,revision,"
                "key_id,encrypted_metadata,identity_hash) "
                "VALUES (%s,%s,%s,1,%s,%s,%s) RETURNING *",
                (
                    scope["organization_id"],
                    scope["space_id"],
                    candidate,
                    key.key.key_id,
                    encrypted,
                    fingerprint,
                ),
            ).fetchone()
        if not duplicate or agent_id:
            content_changes.changed(connection)
    value = _view(workspace, identity, scope, row)
    with workspace.db.transaction(identity) as connection:
        workspace._scope(connection, identity, allow_unidentified=True)
    return value


def bind(workspace, identity, agent_id, *, client_id=None):
    from space.workspace import CommandError

    with workspace.db.transaction(identity) as connection:
        workspace._scope(
            connection,
            identity,
            human=client_id is not None,
            allow_unidentified=True,
            grant_for_update=True,
        )
        row = connection.execute(
            "SELECT id FROM space.agent_identities WHERE id=%s AND deleted_at IS NULL FOR SHARE",
            (agent_id,),
        ).fetchone()
        if row is None:
            raise CommandError("Agent identity not found in this Space", 404)
        target = client_id or identity.client_id
        if not target:
            raise CommandError("An agent connection is required", 400)
        current = connection.execute(
            "SELECT agent_id FROM space.oauth_grants "
            "WHERE client_id=%s AND revoked_at IS NULL FOR UPDATE",
            (target,),
        ).fetchone()
        if current is None:
            raise CommandError("Active connection not found", 404)
        if client_id is None and current["agent_id"] not in (None, agent_id):
            raise CommandError(
                "This connection belongs to another agent. Change it on the website.", 409
            )
        connection.execute(
            "UPDATE space.oauth_grants SET agent_id=%s WHERE client_id=%s", (agent_id, target)
        )
    return current_profile(workspace, identity, agent_id=agent_id)


def current_profile(workspace, identity, *, agent_id=None):
    from space.workspace import CommandError

    with workspace.db.transaction(identity) as connection:
        scope = workspace._scope(connection, identity, allow_unidentified=True)
        selected = agent_id or scope.get("agent_id")
        row = (
            connection.execute(
                "SELECT * FROM space.agent_identities WHERE id=%s", (selected,)
            ).fetchone()
            if selected
            else None
        )
    profile = _view(workspace, identity, scope, row) if row else None
    with workspace.db.transaction(identity) as connection:
        workspace._scope(connection, identity, allow_unidentified=True)
    if agent_id and profile is None:
        raise CommandError("Agent identity not found in this Space", 404)
    return profile


def identify(workspace, identity, *, agent_id: UUID | None = None, profile=None):
    from space.workspace import CommandError

    if not identity.client_id:
        raise CommandError("Identify through your MCP connection", 400)
    if agent_id is None and profile is None:
        selected = current_profile(workspace, identity)
        return {
            "agent": selected,
            "required_header": "X-Space-Agent-ID",
            "instructions": (
                "Send this agent ID on every content request as X-Space-Agent-ID or agent_id. "
                "If no identity is assigned, call identify_agent with an existing agent_id "
                "or your name and harness."
            ),
        }
    if (agent_id is None) == (profile is None):
        raise CommandError("Supply an agent_id or a name and harness", 400)
    if profile:
        # A bound agent may inspect/reuse its profile, but cannot create unrelated
        # identities merely by retrying identification with a different name.
        selected = current_profile(workspace, identity)
        if selected:
            if (
                selected["name"].casefold() != profile.name.casefold()
                or selected["harness"] != profile.harness
            ):
                raise CommandError(
                    "This connection already has an identity. Reuse its agent ID.", 409
                )
            agent_id = UUID(selected["id"])
        else:
            agent_id = UUID(save_profile(workspace, identity, profile, human=False)["id"])
    selected = bind(workspace, identity, agent_id)
    return {
        "agent": selected,
        "required_header": "X-Space-Agent-ID",
        "instructions": (
            "Send agent.id as X-Space-Agent-ID on every request, or as agent_id on every "
            "content tool call. Reuse this identity for other connections."
        ),
    }


def delete_profile(workspace, identity, agent_id, expected_revision):
    """Keep historical references, but retire an identity once access is revoked."""
    from space.workspace import CommandError

    with workspace.db.transaction(identity) as connection:
        workspace._scope(connection, identity, human=True, write=True)
        connection.execute("SELECT id FROM space.spaces FOR UPDATE")
        row = connection.execute(
            "SELECT revision,deleted_at FROM space.agent_identities WHERE id=%s FOR UPDATE",
            (agent_id,),
        ).fetchone()
        if row is None:
            raise CommandError("Agent identity not found in this Space", 404)
        if row["deleted_at"] is not None:
            return {"deleted": True}
        if row["revision"] != expected_revision:
            raise CommandError("Agent changed. Refresh and try again.", 409)
        if connection.execute(
            "SELECT id FROM space.oauth_grants WHERE agent_id=%s AND revoked_at IS NULL LIMIT 1",
            (agent_id,),
        ).fetchone():
            raise CommandError("Revoke all connections before deleting this agent.", 409)
        connection.execute(
            "UPDATE space.agent_identities SET deleted_at=now(),updated_at=now() WHERE id=%s",
            (agent_id,),
        )
        content_changes.changed(connection)
    return {"deleted": True}
