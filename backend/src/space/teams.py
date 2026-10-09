"""Human-managed organizations and independently authorized Spaces."""

import hashlib
import secrets
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from space.providers import LOCAL_PROVIDER
from space.workspace import CommandError


class Named(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    name: str = Field(min_length=1, max_length=100)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value):
        if not value.strip():
            raise ValueError("Enter a name")
        return value.strip()


class CreateTeam(Named):
    command_id: UUID
    display_name: str = Field(min_length=1, max_length=100)


class SpaceAccess(BaseModel):
    model_config = ConfigDict(extra="forbid")
    visibility: Literal["private", "restricted", "shared"] = "private"
    # Organization-local member IDs, never external account IDs.
    member_ids: list[UUID] = Field(default_factory=list, max_length=200)


class CreateSpace(Named, SpaceAccess):
    command_id: UUID


class Invitation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["admin", "member", "viewer"] = "member"


class AcceptInvitation(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    token: str = Field(min_length=43, max_length=43, pattern=r"^[A-Za-z0-9_-]+$")
    display_name: str = Field(min_length=1, max_length=100)


class MemberRole(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["admin", "member", "viewer"]


def _organization(connection, organization_id, *, admin=False, lock=False):
    if lock:
        connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,16))", (str(organization_id),)
        )
    row = connection.execute(
        "SELECT *,space.organization_role(id) AS role FROM space.organizations "
        "WHERE id=%s AND kind='team'",
        (organization_id,),
    ).fetchone()
    if not row:
        raise CommandError("Organization not found", 404)
    if admin and row["role"] not in ("owner", "admin"):
        raise CommandError("Only organization owners and admins can manage members.", 403)
    return row


def listing(workspace, identity):
    identity.check(human=True)
    with workspace.db.transaction(identity) as connection:
        rows = connection.execute("SELECT * FROM space.accessible_spaces()").fetchall()
    items = []
    for row in rows:
        try:
            items.append(workspace.info(identity, row["space_id"]))
        except CommandError as error:
            if error.status != 404:
                raise
    with workspace.db.transaction(identity) as connection:
        visible = {
            str(row["space_id"])
            for row in connection.execute("SELECT * FROM space.accessible_spaces()").fetchall()
        }
    return {"items": [item for item in items if item["id"] in visible]}


def _prepared(workspace, org_id, space_id, name):
    scope = {"organization_id": org_id, "space_id": space_id}
    key, wrapped = workspace.keys.generate(org_id, space_id)
    collection = uuid4()
    metadata = workspace._pack(key, scope, space_id, 1, "metadata", {"name": name})
    schema = workspace._pack(
        key,
        scope,
        collection,
        1,
        "schema",
        {
            "name": "Tasks",
            "version": 1,
            "fields": {"title": "text", "body": "text", "status": ["todo", "in_progress", "done"]},
        },
    )
    return scope, key, wrapped, collection, metadata, schema


def _insert(workspace, connection, prepared, visibility):
    scope, key, wrapped, collection, metadata, schema = prepared
    org, sid = scope["organization_id"], scope["space_id"]
    workspace._set_scope(connection, org, sid)
    connection.execute(
        "INSERT INTO space.spaces(organization_id,id,metadata_key_id,encrypted_metadata,"
        "visibility) VALUES(%s,%s,%s,%s,%s)",
        (org, sid, key.key.key_id, metadata, visibility),
    )
    connection.execute(
        "INSERT INTO space.space_keys(organization_id,space_id,id,wrapping_provider,"
        "wrapping_key_reference,wrapped_key) VALUES(%s,%s,%s,%s,%s,%s)",
        (org, sid, key.key.key_id, LOCAL_PROVIDER, workspace.keys.reference, wrapped),
    )
    connection.execute(
        "INSERT INTO space.collections(organization_id,space_id,id,system_role,key_id,"
        "encrypted_schema) VALUES(%s,%s,%s,'tasks',%s,%s)",
        (org, sid, collection, key.key.key_id, schema),
    )


def create(workspace, identity, request: CreateTeam):
    identity.check(human=True)
    workspace.bootstrap(replace(identity, selected_space_id=None))
    org_id, sid = request.command_id, request.command_id
    with workspace.db.transaction(identity) as connection:
        existing = connection.execute(
            "SELECT default_space_id FROM space.organizations WHERE id=%s "
            "AND owner_principal_id=space.current_principal_id() AND kind='team'",
            (org_id,),
        ).fetchone()
        count = connection.execute(
            "SELECT count(*) AS n FROM space.organizations "
            "WHERE owner_principal_id=space.current_principal_id() AND kind='team'"
        ).fetchone()["n"]
    if existing:
        return workspace.info(identity, existing["default_space_id"])
    if count >= 30:
        raise CommandError("You can own up to 30 organizations.", 409)
    prepared = _prepared(workspace, org_id, sid, request.name)
    with workspace.db.transaction(identity) as connection:
        connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,16))", (str(org_id),))
        exists = connection.execute(
            "SELECT id FROM space.organizations WHERE id=%s", (org_id,)
        ).fetchone()
        if not exists:
            connection.execute(
                "INSERT INTO space.organizations(id,owner_principal_id,default_space_id,kind) "
                "VALUES(%s,space.current_principal_id(),%s,'team')",
                (org_id, sid),
            )
            _insert(workspace, connection, prepared, "shared")
            connection.execute(
                "INSERT INTO space.organization_members(organization_id,principal_id,role) "
                "VALUES(%s,space.current_principal_id(),'admin')",
                (org_id,),
            )
    _profile(workspace, identity, org_id, request.display_name)
    return workspace.info(identity, sid)


def _profile(workspace, identity, org_id, name):
    with workspace.db.transaction(identity) as connection:
        org = _organization(connection, org_id)
        member = connection.execute(
            "SELECT id FROM space.organization_members WHERE organization_id=%s "
            "AND principal_id=space.current_principal_id() AND active",
            (org_id,),
        ).fetchone()
    scope, key = workspace._key(identity, org["default_space_id"])
    encrypted = workspace._pack(
        key, scope, member["id"], 1, "metadata", {"name": name.strip() or "Member"}
    )
    with workspace.db.transaction(identity) as connection:
        workspace._scope(connection, identity, org["default_space_id"])
        connection.execute(
            "UPDATE space.organization_members SET encrypted_profile=%s,key_id=%s "
            "WHERE id=%s AND principal_id=space.current_principal_id() AND active",
            (encrypted, key.key.key_id, member["id"]),
        )


def members(workspace, identity, org_id):
    identity.check(human=True)
    with workspace.db.transaction(identity) as connection:
        org = _organization(connection, org_id)
        scope = workspace._scope(connection, identity, org["default_space_id"])
        rows = connection.execute(
            "SELECT *,principal_id=space.current_principal_id() AS is_self "
            "FROM space.organization_members WHERE organization_id=%s AND active ORDER BY "
            "created_at,id",
            (org_id,),
        ).fetchall()
        key_rows = {
            row["key_id"]: workspace._key_row(connection, scope, row["key_id"])
            for row in rows
            if row["key_id"]
        }
    keys = {
        id_: workspace.keys.unwrap(org_id, scope["space_id"], row) for id_, row in key_rows.items()
    }
    items = [
        {
            "id": str(row["id"]),
            "is_self": row["is_self"],
            "role": "owner" if row["principal_id"] == org["owner_principal_id"] else row["role"],
            "name": workspace._unpack(
                keys[row["key_id"]], scope, row["id"], 1, "metadata", row["encrypted_profile"]
            )["name"]
            if row["encrypted_profile"]
            else "Member",
        }
        for row in rows
    ]
    workspace._recheck(identity, scope["space_id"])
    return {"items": items, "role": org["role"], "default_space_id": str(org["default_space_id"])}


def invite(workspace, identity, org_id, request: Invitation):
    identity.check(human=True)
    token = secrets.token_urlsafe(32)
    invite_id = uuid4()
    expires = datetime.now(UTC) + timedelta(days=7)
    with workspace.db.transaction(identity) as connection:
        org = _organization(connection, org_id, admin=True, lock=True)
        if request.role == "admin" and org["role"] != "owner":
            raise CommandError("Only the owner can invite admins.", 403)
        count = connection.execute(
            "SELECT count(*) AS n FROM space.team_invitations WHERE organization_id=%s "
            "AND revoked_at IS NULL AND accepted_at IS NULL AND expires_at>now()",
            (org_id,),
        ).fetchone()["n"]
        if count >= 100:
            raise CommandError("Revoke an unused invitation before creating another.", 409)
        connection.execute(
            "INSERT INTO space.team_invitations(organization_id,id,token_hash,role,"
            "created_by,expires_at) "
            "VALUES(%s,%s,%s,%s,space.current_principal_id(),%s)",
            (org_id, invite_id, hashlib.sha256(token.encode()).digest(), request.role, expires),
        )
    return {
        "id": str(invite_id),
        "url": f"{workspace.web_url}/app/settings/team#invite={token}",
        "expires_at": expires.isoformat(),
    }


def invitations(workspace, identity, org_id):
    identity.check(human=True)
    with workspace.db.transaction(identity) as connection:
        _organization(connection, org_id, admin=True)
        rows = connection.execute(
            "SELECT id,role,expires_at,created_at FROM space.team_invitations "
            "WHERE organization_id=%s AND revoked_at IS NULL AND accepted_at IS NULL "
            "AND expires_at>now() ORDER BY created_at DESC",
            (org_id,),
        ).fetchall()
    return {"items": [{**row, "id": str(row["id"])} for row in rows]}


def revoke_invitation(workspace, identity, org_id, invite_id):
    identity.check(human=True)
    with workspace.db.transaction(identity) as connection:
        _organization(connection, org_id, admin=True, lock=True)
        connection.execute(
            "UPDATE space.team_invitations SET revoked_at=now() WHERE organization_id=%s AND id=%s",
            (org_id, invite_id),
        )
    return {"revoked": True}


def accept(workspace, identity, request: AcceptInvitation):
    identity.check(human=True)
    workspace.bootstrap(replace(identity, selected_space_id=None))
    with workspace.db.transaction(identity) as connection:
        org_id = connection.execute(
            "SELECT space.accept_team_invitation(%s) AS id",
            (hashlib.sha256(request.token.encode()).digest(),),
        ).fetchone()["id"]
        if not org_id:
            raise CommandError("This invitation is expired, revoked, or already used.", 404)
        org = _organization(connection, org_id)
    _profile(workspace, identity, org_id, request.display_name)
    return workspace.info(identity, org["default_space_id"])


def change_member(workspace, identity, org_id, member_id, role=None):
    identity.check(human=True)
    with workspace.db.transaction(identity) as connection:
        changed = connection.execute(
            "SELECT space.change_team_member(%s,%s,%s) AS changed",
            (org_id, member_id, role),
        ).fetchone()["changed"]
        if not changed:
            raise CommandError(
                "You cannot change this member or remove the organization owner.", 403
            )
    return {"changed": True}


def _selected_members(connection, org_id, request):
    if request.visibility != "restricted" and request.member_ids:
        raise CommandError("Select people only for a restricted Space.")
    rows = connection.execute(
        "SELECT id,principal_id FROM space.organization_members "
        "WHERE organization_id=%s AND active AND id=ANY(%s::uuid[])",
        (org_id, request.member_ids),
    ).fetchall()
    if len(rows) != len(set(request.member_ids)):
        raise CommandError("A selected person is no longer an organization member.", 409)
    return rows


def create_space(workspace, identity, org_id, request: CreateSpace):
    identity.check(human=True)
    with workspace.db.transaction(identity) as connection:
        org = _organization(connection, org_id)
        if org["role"] == "viewer":
            raise CommandError("Viewers cannot create Spaces.", 403)
    prepared = _prepared(workspace, org_id, request.command_id, request.name)
    with workspace.db.transaction(identity) as connection:
        _organization(connection, org_id, lock=True)
        selected = _selected_members(connection, org_id, request)
        workspace._set_scope(connection, org_id, request.command_id)
        existing = connection.execute(
            "SELECT id FROM space.spaces WHERE id=%s", (request.command_id,)
        ).fetchone()
        if not existing:
            _insert(workspace, connection, prepared, request.visibility)
            for member in selected:
                connection.execute(
                    "INSERT INTO space.space_members(organization_id,space_id,principal_id,role) "
                    "VALUES(%s,%s,%s,'member')",
                    (org_id, request.command_id, member["principal_id"]),
                )
    return workspace.info(identity, request.command_id)


def access(workspace, identity, space_id):
    identity.check(human=True)
    with workspace.db.transaction(identity) as connection:
        scope = workspace._scope(connection, identity, space_id)
        rows = connection.execute(
            "SELECT m.id FROM space.space_members sm JOIN space.organization_members m "
            "ON m.organization_id=sm.organization_id AND m.principal_id=sm.principal_id "
            "WHERE sm.space_id=%s AND m.active",
            (space_id,),
        ).fetchall()
    return {"member_ids": [str(row["id"]) for row in rows], "can_manage": scope["role"] == "owner"}


def set_access(workspace, identity, space_id, request: SpaceAccess):
    identity.check(human=True)
    # Resolve IDs before taking the exclusive lock; reauthorize after taking it.
    with workspace.db.transaction(identity) as connection:
        scope = workspace._scope(connection, identity, space_id)
    with workspace.db.transaction(identity) as connection:
        org = _organization(connection, scope["organization_id"], lock=True)
        scope = workspace._scope(connection, identity, space_id)
        if scope["role"] != "owner":
            raise CommandError("Only this Space's creator can change its access.", 403)
        if org["default_space_id"] == space_id:
            raise CommandError("The organization home is always shared with all members.", 409)
        selected = _selected_members(connection, scope["organization_id"], request)
        connection.execute("DELETE FROM space.space_members WHERE space_id=%s", (space_id,))
        for member in selected:
            connection.execute(
                "INSERT INTO space.space_members(organization_id,space_id,principal_id,role) "
                "VALUES(%s,%s,%s,'member')",
                (scope["organization_id"], space_id, member["principal_id"]),
            )
        connection.execute(
            "UPDATE space.spaces SET visibility=%s WHERE id=%s", (request.visibility, space_id)
        )
        connection.execute(
            "SELECT space.revoke_space_connections(%s,%s)", (scope["organization_id"], space_id)
        )
    return workspace.info(identity, space_id)
