import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from test_auth import signing as signing_fixture
from test_database_setup import database as database_fixture

from space.app import create_app
from space.providers import ScopedKey
from space.security.auth import Identity
from space.security.payloads import SpaceKey
from space.settings import Settings
from space.workspace import CommandError, Database, SaveResource, Workspace

database = database_fixture
signing = signing_fixture


class TestDatabase(Database):
    __test__ = False

    def __init__(self, connect, password):
        # Use the production transaction path, but only the disposable database.
        with connect(runtime=True) as connection:
            self.pool = ConnectionPool(
                connection.info.dsn,
                min_size=0,
                max_size=4,
                open=False,
                configure=self._configure,
                kwargs={"row_factory": dict_row, "password": password},
            )
        self.open()


class TestKeys:
    __test__ = False
    reference = "test-key-reference"

    def __init__(self):
        self.material = {}

    def generate(self, org, space):
        key = ScopedKey(org, space, SpaceKey(str(uuid4()), secrets.token_bytes(32)))
        wrapped = secrets.token_bytes(64)
        self.material[wrapped] = key
        return key, wrapped

    def unwrap(self, org, space, row):
        key = self.material[bytes(row["wrapped_key"])]
        key.get_key(org, space, str(row["id"]))
        return key


class TestObjects:
    __test__ = False

    def __init__(self):
        self.values = {}
        self.after_put = lambda: None

    def put(self, name, ciphertext):
        self.values[name] = ciphertext
        self.after_put()

    def get(self, name):
        return self.values[name]

    def delete_prefix(self, prefix):
        names = [name for name in self.values if name.startswith(prefix)]
        return self.delete_keys(names)

    def delete_keys(self, names):
        names = list(names)
        for name in names:
            self.values.pop(name, None)
        return len(names)


@pytest.fixture
def command_database(database):
    value = TestDatabase(database[0], database[4])
    try:
        yield value
    finally:
        value.close()


@pytest.fixture
def commands(database, command_database):
    connect = database[0]
    service = Workspace(command_database, TestKeys(), TestObjects(), "http://127.0.0.1:8000/mcp/")
    identity = Identity("https://identity.invalid", str(uuid4()), int(time.time()) + 600)
    space = UUID(service.bootstrap(identity)["id"])
    return service, identity, space, connect


def change(**values):
    return SaveResource(
        command_id=uuid4(),
        expected_revision=0,
        kind="document",
        title="Private title canary 915",
        body="Private body canary 731",
        **values,
    )


def test_concurrent_bootstrap_is_one_private_space(command_database):
    service = Workspace(command_database, TestKeys(), TestObjects(), "http://127.0.0.1:8000/mcp/")
    identity = Identity("https://identity.invalid", str(uuid4()), int(time.time()) + 600)
    with ThreadPoolExecutor(max_workers=4) as pool:
        spaces = list(pool.map(lambda _: service.bootstrap(identity), range(4)))
    assert len({space["id"] for space in spaces}) == 1
    assert len({space["tasks_collection_id"] for space in spaces}) == 1


def test_documents_tasks_and_history_share_canonical_resources(commands):
    service, identity, space, _ = commands
    request = change()
    first = service.save(identity, space, request)
    assert first["revision"] == 1
    assert service.save(identity, space, request) == first
    assert service.objects.values == {}  # bodies are stored inline
    update = request.model_copy(
        update={
            "command_id": uuid4(),
            "resource_id": UUID(first["id"]),
            "expected_revision": 1,
            "body": "Updated",
        }
    )
    second = service.save(identity, space, update)
    assert second["revision"] == 2
    assert service.get_resource(identity, space, UUID(first["id"]), 1)["body"] == request.body
    assert service.get_resource(identity, space, UUID(first["id"]))["body"] == "Updated"
    with pytest.raises(CommandError, match="changed"):
        service.save(identity, space, update.model_copy(update={"command_id": uuid4()}))
    with pytest.raises(CommandError, match="already used"):
        service.save(identity, space, request.model_copy(update={"title": "Different"}))
    task = service.save(
        identity,
        space,
        request.model_copy(update={"command_id": uuid4(), "kind": "task", "status": "done"}),
    )
    assert task["kind"] == "task" and task["status"] == "done"
    assert len(service.list_resources(identity, space, "document")["items"]) == 1
    with service.db.transaction(identity) as connection:
        service._scope(connection, identity, space)
        row = connection.execute(
            "SELECT r.kind,c.system_role FROM space.resources r "
            "JOIN space.collections c ON r.collection_id=c.id WHERE r.id=%s",
            (task["id"],),
        ).fetchone()
        assert row == {"kind": "record", "system_role": "tasks"}
        assert connection.execute("SELECT count(*) AS n FROM space.events").fetchone()["n"] == 3


def test_resource_pages_follow_the_requested_size(commands):
    service, identity, space, _ = commands
    ids = sorted(
        service.save(identity, space, change().model_copy(update={"title": f"Doc {n}"}))["id"]
        for n in range(5)
    )
    first = service.list_resources(identity, space, "document", limit=2)
    assert [item["id"] for item in first["items"]] == ids[:2]
    assert str(first["next_cursor"]) == ids[1]
    second = service.list_resources(identity, space, "document", UUID(ids[1]), limit=2)
    assert [item["id"] for item in second["items"]] == ids[2:4]
    last = service.list_resources(identity, space, "document", UUID(ids[3]), limit=2)
    assert [item["id"] for item in last["items"]] == ids[4:] and last["next_cursor"] is None
    whole = service.list_resources(identity, space, "document", limit=5)
    assert len(whole["items"]) == 5 and whole["next_cursor"] is None


def test_concurrent_updates_have_one_winner(commands):
    service, identity, space, _ = commands
    first = service.save(identity, space, change())

    def attempt(_):
        request = change().model_copy(
            update={"resource_id": UUID(first["id"]), "expected_revision": 1}
        )
        try:
            return service.save(identity, space, request)["revision"]
        except CommandError as error:
            return error.status

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, range(2))) == [2, 409]


def test_storage_holds_readable_resource_content(commands):
    service, identity, space, _ = commands
    request = change()
    service.save(identity, space, request)
    service.save(
        identity, space, request.model_copy(update={"command_id": uuid4(), "kind": "task"})
    )
    with service.db.transaction(identity) as connection:
        service._scope(connection, identity, space)
        rows = connection.execute(
            "SELECT encrypted_metadata,encrypted_record FROM space.resource_revisions"
        ).fetchall()
        rows += connection.execute("SELECT encrypted_metadata FROM space.spaces").fetchall()
        rows += connection.execute("SELECT encrypted_schema FROM space.collections").fetchall()
    stored = b"".join(bytes(v) for row in rows for v in row.values() if v is not None)
    assert request.title.encode() in stored and request.body.encode() in stored
    assert b'"name":"Private Space"' in stored
    assert not service.objects.values


def test_cross_owner_reads_and_writes_are_denied(commands):
    service, identity, space, _ = commands
    saved = service.save(identity, space, change())
    other = replace(identity, subject=str(uuid4()))
    service.bootstrap(other)
    for call in (
        lambda: service.get_resource(other, space, UUID(saved["id"])),
        lambda: service.list_resources(other, space, "document"),
        lambda: service.save(other, space, change()),
    ):
        with pytest.raises(CommandError) as error:
            call()
        assert error.value.status == 404


def test_pooled_transactions_clear_scope_before_another_identity(commands):
    service, identity, space, _ = commands
    with service.db.transaction(identity) as connection:
        service._scope(connection, identity, space)
        first_pid = connection.execute("SELECT pg_backend_pid() AS pid").fetchone()["pid"]
    other = replace(identity, subject=str(uuid4()))
    with service.db.transaction(other) as connection:
        row = connection.execute(
            "SELECT pg_backend_pid() AS pid, "
            "current_setting('space.organization_id', true) AS organization, "
            "current_setting('space.space_id', true) AS space"
        ).fetchone()
        assert row["pid"] == first_pid
        assert not row["organization"] and not row["space"]
        assert (
            connection.execute("SELECT count(*) AS n FROM space.organizations").fetchone()["n"] == 0
        )


def test_queued_query_failure_rolls_back_and_keeps_connection_usable(commands):
    service, identity, space, _ = commands
    saved = service.save(identity, space, change())
    with (
        pytest.raises(psycopg.errors.DivisionByZero),
        service.db.transaction(identity) as connection,
    ):
        service._scope(connection, identity, space)
        connection.execute(
            "UPDATE space.resources SET current_revision=2 WHERE id=%s", (saved["id"],)
        )
        # No fetch: a queued failure must still surface before the transaction commits.
        connection.execute("SELECT 1 / 0")
    assert service.get_resource(identity, space, UUID(saved["id"]))["revision"] == 1


def delegated(service, identity, *, write=False):
    from space import agents

    client_id = uuid4()
    profile = agents.save_profile(
        service, identity, agents.AgentProfile(name=f"Test {client_id}", harness="codex")
    )
    service.grant(identity, client_id, write, UUID(profile["id"]))
    grant = next(
        row for row in service.grants(identity)["items"] if row["client_id"] == str(client_id)
    )
    return replace(identity, client_id=str(client_id), grant_id=grant["id"], agent_id=profile["id"])


def test_grants_enforce_read_write_revocation_and_reauthorization(commands):
    service, identity, space, _ = commands
    saved = service.save(identity, space, change())
    reader = delegated(service, identity)
    assert service.get_resource(reader, space, UUID(saved["id"]))["id"] == saved["id"]
    with pytest.raises(CommandError) as error:
        service.save(reader, space, change())
    assert error.value.status == 403
    service.revoke(identity, UUID(reader.client_id))
    with pytest.raises(CommandError):
        service.info(reader)
    service.grant(identity, UUID(reader.client_id), True)
    with pytest.raises(CommandError):
        service.info(reader)
    writer = delegated(service, identity, write=True)
    assert service.save(writer, space, change())["author"] == "agent"
    with pytest.raises(Exception, match="Agents cannot perform this operation"):
        service.grants(writer)


def test_revocation_while_preparing_a_write_prevents_commit(commands, monkeypatch):
    service, identity, space, _ = commands
    writer = delegated(service, identity, write=True)
    pack, revoked = service._pack, []

    def revoke_then_pack(*args):
        if not revoked:
            revoked.append(service.revoke(identity, UUID(writer.client_id)))
        return pack(*args)

    monkeypatch.setattr(service, "_pack", revoke_then_pack)
    with pytest.raises(CommandError):
        service.save(writer, space, change())
    assert service.list_resources(identity, space, "document")["items"] == []


@pytest.mark.parametrize("operation", ["info", "list", "get"])
def test_revocation_during_key_unwrap_prevents_read_disclosure(commands, monkeypatch, operation):
    service, identity, space, _ = commands
    saved = service.save(identity, space, change())
    reader = delegated(service, identity)
    original_unwrap = service.keys.unwrap

    def revoke_while_unwrapping(*args):
        key = original_unwrap(*args)
        service.revoke(identity, UUID(reader.client_id))
        return key

    monkeypatch.setattr(service.keys, "unwrap", revoke_while_unwrapping)
    with pytest.raises(CommandError) as error:
        if operation == "info":
            service.info(reader)
        elif operation == "list":
            service.list_resources(reader, space, "document")
        else:
            service.get_resource(reader, space, UUID(saved["id"]))
    assert error.value.status == 403


def test_revision_history_cannot_be_rewritten_by_runtime(commands):
    service, identity, space, _ = commands
    service.save(identity, space, change())
    with (
        pytest.raises(psycopg.errors.InsufficientPrivilege),
        service.db.transaction(identity) as connection,
    ):
        service._scope(connection, identity, space)
        connection.execute(
            "UPDATE space.resource_revisions SET encrypted_metadata='changed'::bytea"
        )


def test_rest_and_mcp_share_resources_and_reject_wrong_tokens(commands, signing):
    service = commands[0]
    verifier, token = signing
    browser_token = token()
    owner = verifier.verify(browser_token)
    settings = Settings(environment="test", allowed_hosts=["testserver"])
    with TestClient(create_app(settings, workspace=service, verifier=verifier)) as http:
        auth = {"Authorization": "Bearer " + browser_token}
        response = http.post("/v1/workspace", headers=auth)
        assert response.status_code == 200, response.text
        space = response.json()["id"]
        resource = http.post(
            f"/v1/spaces/{space}/resources", json=change().model_dump(mode="json"), headers=auth
        ).json()
        assert resource["revision"] == 1
        agent = delegated(service, owner, write=True)
        agent_token = token(
            aud=verifier.resource, client_id=agent.client_id, space_grant_id=agent.grant_id
        )
        headers = {
            "Authorization": "Bearer " + agent_token,
            "X-Space-Agent-ID": agent.agent_id,
            "Accept": "application/json, text/event-stream",
        }

        def rpc(name, arguments):
            result = http.post(
                "/mcp/",
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": arguments},
                },
            )
            assert result.status_code == 200, result.text
            return result.json()["result"]

        read = rpc("get_resource", {"space_id": space, "resource_id": resource["id"]})
        assert "structuredContent" in read, read
        assert read["structuredContent"]["body"] == resource["body"]
        updated = change().model_copy(
            update={
                "resource_id": UUID(resource["id"]),
                "expected_revision": 1,
                "body": "Written over MCP",
            }
        )
        saved = rpc("save_resource", {"space_id": space, "change": updated.model_dump(mode="json")})
        assert saved["structuredContent"]["revision"] == 2
        viewed = http.get(f"/v1/spaces/{space}/resources/{resource['id']}", headers=auth)
        assert viewed.json()["body"] == "Written over MCP"
        assert viewed.headers["Cache-Control"] == "no-store"
        assert http.get("/v1/workspace", headers=headers).status_code == 401
        denied = http.post("/mcp/", json={}, headers=auth)
        assert denied.status_code == 401
        # A refused request points clients that sign in at the protected-resource metadata.
        metadata = "http://127.0.0.1:8000/.well-known/oauth-protected-resource/mcp"
        assert f'resource_metadata="{metadata}"' in denied.headers.get("WWW-Authenticate", "")
        described = http.get("/.well-known/oauth-protected-resource/mcp")
        assert described.json()["resource"] == "http://127.0.0.1:8000/mcp"
        invalid = rpc(
            "save_resource", {"space_id": space, "change": {"body": "NEVER-ECHO-PRIVATE-INPUT"}}
        )
        assert invalid["isError"] is True
        assert "NEVER-ECHO-PRIVATE-INPUT" not in str(invalid)
        service.revoke(owner, UUID(agent.client_id))
        assert (
            rpc("get_resource", {"space_id": space, "resource_id": resource["id"]})["isError"]
            is True
        )
        assert http.get(f"/v1/spaces/{space}/resources/{resource['id']}").status_code == 401
