"""Real restricted-role isolation tests for multi-Space organizations."""

from dataclasses import replace
from uuid import UUID, uuid4

import psycopg
import pytest
from test_database_setup import database
from test_workspace import change, command_database, commands

from space import agents, teams
from space.workspace import CommandError

__all__ = ["database", "command_database", "commands"]


def create_team(service, owner):
    return teams.create(
        service,
        owner,
        teams.CreateTeam(
            command_id=uuid4(),
            name="Design team",
            display_name="Owner",
        ),
    )


def join(service, owner, team, *, role="member", name="Teammate"):
    person = replace(owner, subject=str(uuid4()))
    invitation = teams.invite(
        service, owner, UUID(team["organization_id"]), teams.Invitation(role=role)
    )
    request = teams.AcceptInvitation(
        token=invitation["url"].split("#invite=")[1], display_name=name
    )
    teams.accept(service, person, request)
    return person, request


def test_team_bootstrap_shared_work_and_personal_isolation(commands):
    service, owner, personal, _ = commands
    team = create_team(service, owner)
    sid, org = UUID(team["id"]), UUID(team["organization_id"])
    person, request = join(service, owner, team)
    assert teams.accept(service, person, request)["id"] == team["id"]
    assert service.bootstrap(owner)["id"] == str(personal)
    saved = service.save(owner, sid, change())
    assert service.get_resource(person, sid, UUID(saved["id"]))["body"] == change().body
    assert service.get_resource(person, sid, UUID(saved["id"]))["author"] == "Teammate"
    service.save(person, sid, change())
    assert len(service.list_resources(owner, sid, "document")["items"]) == 2
    assert len(teams.members(service, person, org)["items"]) == 2
    with pytest.raises(CommandError):
        service.list_resources(person, personal, "document")
    with pytest.raises(CommandError):
        teams.accept(service, replace(person, subject=str(uuid4())), request)


def test_private_and_selected_spaces_deny_even_organization_admins(commands):
    service, owner, _, _ = commands
    team = create_team(service, owner)
    org = UUID(team["organization_id"])
    alice, _ = join(service, owner, team)
    admin, _ = join(service, owner, team, role="admin")
    alice_id = next(m["id"] for m in teams.members(service, alice, org)["items"] if m["is_self"])
    private = teams.create_space(
        service, alice, org, teams.CreateSpace(command_id=uuid4(), name="Private notes")
    )
    selected = teams.create_space(
        service,
        owner,
        org,
        teams.CreateSpace(
            command_id=uuid4(),
            name="Selected project",
            visibility="restricted",
            member_ids=[UUID(alice_id)],
        ),
    )
    for sid in (UUID(private["id"]), UUID(selected["id"])):
        service.save(alice, sid, change())
        with pytest.raises(CommandError):
            service.list_resources(admin, sid, "document")
        with service.db.transaction(admin) as connection:
            service._set_scope(connection, org, sid)
            assert connection.execute("SELECT * FROM space.resource_revisions").fetchall() == []
            assert connection.execute("SELECT * FROM space.space_keys").fetchall() == []
    with pytest.raises(CommandError):
        service.info(owner, UUID(private["id"]))
    assert private["id"] not in {s["id"] for s in teams.listing(service, owner)["items"]}
    teams.set_access(service, owner, UUID(selected["id"]), teams.SpaceAccess(visibility="private"))
    with pytest.raises(CommandError):
        service.info(alice, UUID(selected["id"]))


def test_viewer_denied_writes_and_members_cannot_administer(commands):
    service, owner, _, _ = commands
    team = create_team(service, owner)
    sid, org = UUID(team["id"]), UUID(team["organization_id"])
    viewer, _ = join(service, owner, team, role="viewer")
    assert service.info(viewer, sid)["role"] == "viewer"
    for action in (
        lambda: service.save(viewer, sid, change()),
        lambda: teams.invite(service, viewer, org, teams.Invitation()),
        lambda: teams.create_space(
            service, viewer, org, teams.CreateSpace(command_id=uuid4(), name="No")
        ),
        lambda: service.grant(replace(viewer, selected_space_id=sid), uuid4(), True),
    ):
        with pytest.raises(CommandError) as error:
            action()
        assert error.value.status == 403
    with (
        pytest.raises(psycopg.errors.InsufficientPrivilege),
        service.db.transaction(viewer) as connection,
    ):
        service._scope(connection, viewer, sid)
        connection.execute(
            "INSERT INTO space.agent_identities(organization_id,space_id,id,revision,key_id,"
            "encrypted_metadata,identity_hash) "
            "SELECT organization_id,id,gen_random_uuid(),1,metadata_key_id,'x'::bytea,"
            "decode(repeat('ab',32),'hex') FROM space.spaces"
        )


def test_removal_revokes_agent_and_rejoining_does_not_restore_old_grant(commands):
    service, owner, personal, _ = commands
    team = create_team(service, owner)
    sid, org = UUID(team["id"]), UUID(team["organization_id"])
    person, request = join(service, owner, team)
    selected = replace(person, selected_space_id=sid)
    profile = agents.save_profile(
        service, selected, agents.AgentProfile(name="Team agent", harness="codex")
    )
    client = uuid4()
    service.grant(selected, client, True, UUID(profile["id"]))
    grant = service.grants(selected)["items"][0]
    agent = replace(person, client_id=str(client), grant_id=grant["id"], agent_id=profile["id"])
    assert service.info(agent)["id"] == str(sid)
    service.save(agent, sid, change())
    with pytest.raises(CommandError):
        service.save(agent, personal, change())
    member = next(m for m in teams.members(service, person, org)["items"] if m["is_self"])
    teams.change_member(service, owner, org, UUID(member["id"]))
    for who in (person, agent):
        with pytest.raises(CommandError):
            service.info(who, sid)
    with pytest.raises(CommandError):
        teams.accept(service, person, request)
    invitation = teams.invite(service, owner, org, teams.Invitation())
    teams.accept(
        service,
        person,
        teams.AcceptInvitation(token=invitation["url"].split("#invite=")[1], display_name="Back"),
    )
    with pytest.raises(CommandError):
        service.info(agent, sid)


def test_expired_revoked_invites_and_owner_protection(commands):
    service, owner, _, connect = commands
    team = create_team(service, owner)
    org = UUID(team["organization_id"])
    for expired in (False, True):
        invite = teams.invite(service, owner, org, teams.Invitation())
        if expired:
            with connect(admin=True) as connection:
                connection.execute(
                    "UPDATE space.team_invitations SET expires_at=now()-interval '1 day' WHERE "
                    "id=%s",
                    (invite["id"],),
                )
        else:
            teams.revoke_invitation(service, owner, org, UUID(invite["id"]))
        with pytest.raises(CommandError):
            teams.accept(
                service,
                replace(owner, subject=str(uuid4())),
                teams.AcceptInvitation(token=invite["url"].split("#invite=")[1], display_name="No"),
            )
    member = next(m for m in teams.members(service, owner, org)["items"] if m["is_self"])
    with pytest.raises(CommandError):
        teams.change_member(service, owner, org, UUID(member["id"]))
    stranger = replace(owner, subject=str(uuid4()))
    service.bootstrap(stranger)
    with pytest.raises(CommandError):
        teams.change_member(service, stranger, org, UUID(member["id"]), "admin")
    person, _ = join(service, owner, team)
    other = next(m for m in teams.members(service, person, org)["items"] if m["is_self"])
    with pytest.raises(CommandError):
        teams.change_member(service, stranger, org, UUID(other["id"]), "admin")


def test_membership_is_rechecked_at_commit(commands, monkeypatch):
    service, owner, _, _ = commands
    team = create_team(service, owner)
    org, sid = UUID(team["organization_id"]), UUID(team["id"])
    person, _ = join(service, owner, team)
    member = next(m for m in teams.members(service, person, org)["items"] if m["is_self"])
    pack, removed = service._pack, []

    def remove_then_pack(*args):
        if not removed:
            removed.append(teams.change_member(service, owner, org, UUID(member["id"])))
        return pack(*args)

    monkeypatch.setattr(service, "_pack", remove_then_pack)
    with pytest.raises(CommandError):
        service.save(person, sid, change())
    assert service.list_resources(owner, sid, "document")["items"] == []


def test_reauthorizing_same_client_selects_one_space_and_invalidates_old_token(commands):
    service, owner, personal, _ = commands
    client = uuid4()
    service.grant(owner, client, True)
    first = service.grants(owner)["items"][0]
    old = replace(owner, client_id=str(client), grant_id=first["id"])
    team = create_team(service, owner)
    selected = replace(owner, selected_space_id=UUID(team["id"]))
    service.grant(selected, client, False)
    current = service.grants(selected)["items"][0]
    assert service.info(replace(old, grant_id=current["id"]))["id"] == team["id"]
    assert service.grants(owner)["items"] == []
    with pytest.raises(CommandError):
        service.info(old, personal)


def test_personal_account_purge_refuses_team_history_before_deletion(commands):
    from space.purge import purge_space

    service, owner, personal, connect = commands
    personal_info = service.info(owner)
    team = create_team(service, owner)
    with connect(admin=True) as connection, pytest.raises(ValueError, match="team history"):
        purge_space(connection, UUID(personal_info["organization_id"]), personal, account=True)
    assert service.info(owner)["id"] == str(personal)
    assert service.info(owner, UUID(team["id"]))["name"] == "Design team"


def test_rest_team_flow_and_explicit_agent_scope(commands):
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from fastapi.testclient import TestClient

    from space.api.rest import create_routes
    from space.settings import Settings

    service, owner, personal, _ = commands
    peer = replace(owner, subject=str(uuid4()))
    identities = {"owner": owner, "peer": peer}
    app = FastAPI()

    @app.exception_handler(CommandError)
    def command_error(request, error):
        return JSONResponse({"error": error.message}, status_code=error.status)

    app.include_router(
        create_routes(
            service,
            SimpleNamespace(verify=lambda token: identities[token]),
            Settings(environment="test"),
        )
    )
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer owner"
        created = client.post(
            "/v1/organizations",
            json={
                "command_id": str(uuid4()),
                "name": "REST team",
                "display_name": "Owner",
            },
        )
        assert created.status_code == 200, created.text
        team = created.json()
        org, sid = team["organization_id"], team["id"]
        profile = client.post(
            f"/v1/agent-identities?workspace_id={sid}",
            json={
                "name": "Shared agent",
                "harness": "codex",
            },
        )
        assert profile.status_code == 200, profile.text
        assert client.get("/v1/agent-identities").json()["items"] == []
        assert len(client.get(f"/v1/agent-identities?workspace_id={sid}").json()["items"]) == 1
        invite = client.post(f"/v1/organizations/{org}/invitations", json={"role": "viewer"})
        assert invite.status_code == 200, invite.text
        client.headers["Authorization"] = "Bearer peer"
        accepted = client.post(
            "/v1/invitations/accept",
            json={
                "token": invite.json()["url"].split("#invite=")[1],
                "display_name": "Peer",
            },
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["role"] == "viewer"
        assert client.get(f"/v1/spaces/{personal}/resources?kind=document").status_code == 404
        assert (
            client.post(
                f"/v1/spaces/{sid}/resources", json=change().model_dump(mode="json")
            ).status_code
            == 403
        )
        assert len(client.get("/v1/workspaces").json()["items"]) == 2
