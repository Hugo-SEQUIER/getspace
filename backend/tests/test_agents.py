"""Reusable attribution over independently authorized connections, on real PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_auth import signing as signing_fixture
from test_database_setup import database as database_fixture
from test_memora_workspace import Models
from test_memory_jobs import queue as queue_fixture
from test_memory_jobs import request as memory_request
from test_workspace import change
from test_workspace import command_database as command_database_fixture
from test_workspace import commands as commands_fixture

from space import agents, jobs
from space.app import create_app
from space.memory import RememberClaims
from space.security.auth import Unauthenticated
from space.settings import Settings
from space.workspace import CommandError

database = database_fixture
command_database = command_database_fixture
commands = commands_fixture
signing = signing_fixture
queue = queue_fixture


@pytest.mark.parametrize("harness", agents.HARNESSES)
def test_supported_harness_profile(harness):
    assert agents.AgentProfile(name="My agent", harness=harness).harness == harness


def test_harness_catalog_matches_frontend():
    import re

    source = Path(__file__).resolve().parents[2] / "apps/web/lib/space/agents.ts"
    ids = re.findall(r'\{ id: "([a-z-]+)", name:', source.read_text())
    assert set(ids) == set(agents.HARNESSES)


def test_unknown_harness_is_rejected():
    with pytest.raises(ValidationError, match="Choose a supported harness"):
        agents.AgentProfile(name="My agent", harness="not-a-harness")


def connection(service, owner, *, write=True, agent_id=None, client_id=None):
    client_id = client_id or uuid4()
    service.grant(owner, client_id, write, UUID(agent_id) if agent_id else None)
    grant = next(g for g in service.grants(owner)["items"] if g["client_id"] == str(client_id))
    return replace(owner, client_id=str(client_id), grant_id=grant["id"], agent_id=agent_id)


def profile(service, owner, name="Coding partner", harness="codex"):
    return agents.save_profile(service, owner, agents.AgentProfile(name=name, harness=harness))


def test_unidentified_connections_can_discover_but_cannot_access_content(commands):
    service, owner, space, _ = commands
    actor = connection(service, owner)
    saved = service.save(owner, space, change())
    assert service.info(actor)["agent_id"] is None
    assert agents.identify(service, actor)["agent"] is None
    assert agents.list_profiles(service, actor) == {"items": []}
    for call in (
        lambda: service.list_resources(actor, space, "document"),
        lambda: service.get_resource(actor, space, UUID(saved["id"])),
        lambda: service.save(actor, space, change()),
        lambda: service.list_memories(actor, space),
    ):
        with pytest.raises(CommandError, match="identification required") as error:
            call()
        assert error.value.status == 409


def test_two_connections_identify_as_one_profile_and_distinct_agents_stay_separate(commands):
    service, owner, space, _ = commands
    first, second = connection(service, owner), connection(service, owner)
    one = agents.identify(
        service, first, profile=agents.AgentProfile(name="  Coding   partner ", harness="codex")
    )["agent"]
    two = agents.identify(
        service, second, profile=agents.AgentProfile(name="CODING PARTNER", harness="codex")
    )["agent"]
    assert one == two and one["name"] == "Coding partner"
    assert len(agents.list_profiles(service, owner)["items"]) == 1
    third = profile(service, owner, name="Review partner")
    fourth = profile(service, owner, harness="hermes")
    assert len({one["id"], third["id"], fourth["id"]}) == 3
    for actor in (first, second):
        assert agents.identify(service, actor)["agent"] == one
        bound = replace(actor, agent_id=one["id"])
        assert service.save(bound, space, change())["author_agent"] == one
        with pytest.raises(CommandError, match="another agent"):
            agents.identify(service, bound, agent_id=UUID(third["id"]))


def test_concurrent_profile_creation_reuses_the_same_id(commands):
    service, owner, _, _ = commands
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: profile(service, owner), range(4)))
    assert len({value["id"] for value in results}) == 1
    assert len(agents.list_profiles(service, owner)["items"]) == 1


def test_read_only_identification_does_not_grant_write_or_profile_administration(commands):
    service, owner, space, _ = commands
    actor = connection(service, owner, write=False)
    selected = agents.identify(
        service, actor, profile=agents.AgentProfile(name="Researcher", harness="hermes")
    )["agent"]
    actor = replace(actor, agent_id=selected["id"])
    assert service.list_resources(actor, space, "document")["items"] == []
    with pytest.raises(CommandError) as error:
        service.save(actor, space, change())
    assert error.value.status == 403
    with pytest.raises(Unauthenticated):
        profile(service, actor, name="Administrator")
    with pytest.raises(Unauthenticated):
        agents.bind(service, actor, UUID(selected["id"]), client_id=UUID(actor.client_id))


def test_profile_names_are_stored_readably_and_cross_space_ids_cannot_be_bound(commands, queue):
    service, owner, space, connect = commands
    selected = profile(service, owner, name="PRIVATE-AGENT-CANARY")
    other = replace(owner, subject=str(uuid4()))
    other_space = UUID(service.bootstrap(other)["id"])
    alien = profile(service, other)
    actor = connection(service, owner)
    with pytest.raises(CommandError, match="not found"):
        agents.identify(service, actor, agent_id=UUID(alien["id"]))
    with pytest.raises(CommandError, match="not found"):
        service.grant(owner, uuid4(), True, UUID(alien["id"]))
    with service.db.transaction(other) as runtime:
        service._scope(runtime, other, other_space)
        assert (
            runtime.execute(
                "SELECT id FROM space.agent_identities WHERE id=%s", (selected["id"],)
            ).fetchone()
            is None
        )
    with connect() as maintenance:
        row = maintenance.execute(
            "SELECT encrypted_metadata,identity_hash FROM space.agent_identities WHERE id=%s",
            (selected["id"],),
        ).fetchone()
        metadata, identity_hash = row
        # Self-hosted storage is readable JSON; the dedup hash still reveals no name.
        assert b"PRIVATE-AGENT-CANARY" in bytes(metadata)
        assert b"PRIVATE-AGENT-CANARY" not in bytes(identity_hash)
        assert maintenance.execute(
            "SELECT relrowsecurity,relforcerowsecurity FROM pg_class "
            "WHERE oid='space.agent_identities'::regclass"
        ).fetchone() == (True, True)
    with queue.transaction():
        with pytest.raises(psycopg.errors.InsufficientPrivilege), queue.transaction():
            queue.execute("SELECT * FROM space.agent_identities")
        with pytest.raises(psycopg.errors.InsufficientPrivilege), queue.transaction():
            queue.execute("UPDATE space.memory_jobs SET agent_id=gen_random_uuid()")


def test_rename_preserves_history_and_revocation_only_removes_one_connection(commands):
    service, owner, space, _ = commands
    selected = profile(service, owner)
    first = connection(service, owner, agent_id=selected["id"])
    second = connection(service, owner, agent_id=selected["id"])
    saved = service.save(first, space, change())
    edited = agents.save_profile(
        service,
        owner,
        agents.EditAgent(name="Build partner", harness="claude-code", expected_revision=1),
        UUID(selected["id"]),
    )
    assert edited["id"] == selected["id"] and edited["revision"] == 2
    with pytest.raises(CommandError, match="changed"):
        agents.save_profile(
            service,
            owner,
            agents.EditAgent(name="Stale edit", harness="codex", expected_revision=1),
            UUID(selected["id"]),
        )
    service.revoke(owner, UUID(first.client_id))
    with pytest.raises(CommandError):
        service.get_resource(first, space, UUID(saved["id"]))
    assert service.get_resource(second, space, UUID(saved["id"]))["author_agent"] == selected
    assert service.list_resources(owner, space, "document")["items"][0]["author_agent"] == selected
    fresh = connection(service, owner, agent_id=selected["id"], client_id=UUID(first.client_id))
    assert fresh.grant_id != first.grant_id
    with pytest.raises(CommandError):
        service.info(first)
    assert agents.identify(service, fresh)["agent"] == edited
    assert service.save(fresh, space, change())["author_agent"] == edited
    assert len(agents.list_profiles(service, owner)["items"]) == 1


def test_reassignment_rejects_old_ids_and_idempotency_cannot_change_authorship(commands, queue):
    service, owner, space, _ = commands
    service.memory_models = Models()
    first, second = profile(service, owner), profile(service, owner, name="Reviewer")
    actor = connection(service, owner, agent_id=first["id"])
    saved_request, queued_request = change(), memory_request()
    service.save(actor, space, saved_request)
    service.extract_memory(actor, space, queued_request)
    claimed = jobs.claim(queue)
    assert str(claimed["agent_id"]) == first["id"]
    agents.bind(service, owner, UUID(second["id"]), client_id=UUID(actor.client_id))
    with pytest.raises(CommandError, match="does not match"):
        service.list_resources(actor, space, "document")
    reassigned = replace(actor, agent_id=second["id"])
    with pytest.raises(CommandError, match="already used"):
        service.save(reassigned, space, saved_request)
    with pytest.raises(CommandError, match="already used"):
        service.extract_memory(reassigned, space, queued_request)
    assert jobs.process(service, queue, claimed) is None
    assert service.memory_job(owner, space, queued_request.command_id)["status"] == "failed"
    assert service.memory_models.calls == []


def test_migration_leaves_existing_connections_unassigned(commands):
    service, owner, _, connect = commands
    actor = connection(service, owner)
    migration = (
        Path(__file__).parents[1] / "migrations/versions/0011_agent_identities.sql"
    ).read_text()
    with connect() as maintenance, maintenance.transaction(force_rollback=True):
        maintenance.execute(
            "ALTER TABLE IF EXISTS space.activity_events DROP COLUMN IF EXISTS agent_id"
        )
        maintenance.execute("ALTER TABLE space.memory_jobs DROP COLUMN agent_id")
        maintenance.execute("ALTER TABLE space.oauth_grants DROP COLUMN agent_id")
        maintenance.execute("DROP TABLE space.agent_identities")
        maintenance.execute(migration)
        assert maintenance.execute(
            "SELECT agent_id FROM space.oauth_grants WHERE id=%s", (actor.grant_id,)
        ).fetchone() == (None,)
        assert maintenance.execute("SELECT count(*) FROM space.agent_identities").fetchone() == (0,)


def test_memory_jobs_and_prepared_claims_keep_the_submitting_agent(commands, queue):
    service, owner, space, _ = commands
    service.memory_models = Models()
    selected = profile(service, owner)
    actor = connection(service, owner, agent_id=selected["id"])
    request = memory_request()
    service.extract_memory(actor, space, request)
    extracted = jobs.process(service, queue, jobs.claim(queue))
    assert extracted["author_agent"] == selected
    prepared = RememberClaims(
        command_id=uuid4(),
        title="Decision",
        claims=[
            {
                "index": "Review owner",
                "value": "Nila owns the review.",
                "importance": 3,
            }
        ],
    )
    saved = service.remember_claims(actor, space, prepared)
    assert saved["author_agent"] == selected
    assert all(
        item["author_agent"] == selected for item in service.list_memories(owner, space)["items"]
    )
    renamed = profile(service, owner, name="Reviewer")
    with pytest.raises(CommandError, match="already exists"):
        agents.save_profile(
            service,
            owner,
            agents.EditAgent(
                name=renamed["name"],
                harness=renamed["harness"],
                expected_revision=1,
            ),
            UUID(selected["id"]),
        )


@pytest.mark.parametrize(
    "name,harness", [("   ", "codex"), ("Agent\x00", "codex"), ("Agent", "invalid")]
)
def test_profile_validation(name, harness):
    with pytest.raises(ValidationError):
        agents.AgentProfile(name=name, harness=harness)


def test_mcp_transports_agent_id_in_header_or_argument_and_owner_routes_manage_profiles(
    commands, signing
):
    service = commands[0]
    verifier, token = signing
    browser_token = token()
    owner = verifier.verify(browser_token)
    settings = Settings(environment="test", allowed_hosts=["testserver"])
    with TestClient(create_app(settings, workspace=service, verifier=verifier)) as http:
        auth = {"Authorization": "Bearer " + browser_token}
        space = http.post("/v1/workspace", headers=auth).json()["id"]
        response = http.post(
            "/v1/agent-identities", headers=auth, json={"name": "Builder", "harness": "codex"}
        )
        assert response.status_code == 200, response.text
        selected = response.json()
        actor = connection(service, owner)
        assert (
            http.post(
                f"/v1/agents/{actor.client_id}/identity",
                headers=auth,
                json={"agent_id": selected["id"]},
            ).status_code
            == 200
        )
        auth_agent = {
            "Authorization": "Bearer "
            + token(
                aud=verifier.resource, client_id=actor.client_id, space_grant_id=actor.grant_id
            ),
            "Accept": "application/json, text/event-stream",
        }

        def rpc(name, arguments, header=None):
            result = http.post(
                "/mcp/",
                headers={**auth_agent, **({"X-Space-Agent-ID": header} if header else {})},
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": arguments},
                },
            )
            assert result.status_code == 200, result.text
            return result.json()["result"]

        assert rpc("identify_agent", {})["structuredContent"]["agent"] == selected
        params = {"space_id": space, "kind": "document"}
        assert rpc("list_resources", params)["isError"]
        assert rpc("list_resources", params, str(uuid4()))["isError"]
        assert rpc("list_resources", params, "malformed")["isError"]
        assert rpc("list_resources", params, selected["id"])["structuredContent"]["items"] == []
        assert (
            rpc("list_resources", {**params, "agent_id": selected["id"]})["structuredContent"][
                "items"
            ]
            == []
        )
        assert rpc("list_resources", {**params, "agent_id": str(uuid4())}, selected["id"])[
            "isError"
        ]
        saved = rpc(
            "save_resource",
            {
                "space_id": space,
                "change": change().model_dump(mode="json"),
                "agent_id": selected["id"],
            },
        )
        assert saved["structuredContent"]["author_agent"] == selected
        service.revoke(owner, UUID(actor.client_id))
        assert rpc("identify_agent", {})["isError"]
        assert rpc("list_resources", params, selected["id"])["isError"]
        deleted = http.delete(
            f"/v1/agent-identities/{selected['id']}?expected_revision=1", headers=auth
        )
        assert deleted.status_code == 200, deleted.text
        assert http.get("/v1/agent-identities", headers=auth).json() == {"items": []}


def test_delete_agent_preserves_work_and_requires_revocation(commands):
    service, owner, space, _ = commands
    selected = profile(service, owner)
    agent_id = UUID(selected["id"])
    actor = connection(service, owner, agent_id=selected["id"])
    saved = service.save(actor, space, change())
    with pytest.raises(Unauthenticated):
        agents.delete_profile(service, actor, agent_id, 1)
    with pytest.raises(CommandError, match="Revoke all"):
        agents.delete_profile(service, owner, agent_id, 1)
    service.revoke(owner, UUID(actor.client_id))
    with pytest.raises(CommandError, match="changed"):
        agents.delete_profile(service, owner, agent_id, 2)
    assert agents.delete_profile(service, owner, agent_id, 1) == {"deleted": True}
    assert agents.delete_profile(service, owner, agent_id, 1) == {"deleted": True}
    assert agents.list_profiles(service, owner)["items"] == []
    assert service.grants(owner)["items"] == []
    from space.workspace_archive import ExportPage, export_page

    exported = export_page(service, owner, space, ExportPage())
    assert all(entry["entity"] != "agent" for entry in exported["entries"])
    assert service.get_resource(owner, space, UUID(saved["id"]))["author_agent"] == selected
    with pytest.raises(CommandError):
        service.info(actor)
    with pytest.raises(CommandError, match="not found"):
        service.grant(owner, uuid4(), True, agent_id)
    fresh_connection = connection(service, owner)
    with pytest.raises(CommandError, match="not found"):
        agents.bind(service, owner, agent_id, client_id=UUID(fresh_connection.client_id))
    with pytest.raises(CommandError, match="changed"):
        agents.save_profile(
            service,
            owner,
            agents.EditAgent(name="Renamed", harness="codex", expected_revision=1),
            agent_id,
        )
    assert profile(service, owner)["id"] != selected["id"]


def test_delete_agent_cannot_cross_spaces(commands):
    service, owner, _, _ = commands
    selected = profile(service, owner)
    other = replace(owner, subject=str(uuid4()))
    service.bootstrap(other)
    with pytest.raises(CommandError) as error:
        agents.delete_profile(service, other, UUID(selected["id"]), 1)
    assert error.value.status == 404
    assert agents.list_profiles(service, owner)["items"] == [selected]
