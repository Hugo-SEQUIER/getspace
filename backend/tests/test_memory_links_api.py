from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from test_auth import signing as signing_fixture
from test_database_setup import database as database_fixture
from test_memory_relationships_workspace import RelatingModels, remember_request
from test_workspace import command_database as command_database_fixture
from test_workspace import commands as commands_fixture

from space.api.mcp import create_mcp
from space.app import create_app
from space.settings import Settings
from space.workspace import Workspace

database = database_fixture
command_database = command_database_fixture
commands = commands_fixture
signing = signing_fixture


def test_link_tool_is_read_only_and_has_no_external_model_side_effect():
    workspace = Workspace(None, None, None, "http://127.0.0.1:8000/mcp/")
    server = create_mcp(Settings(environment="test"), workspace)
    tool = next(t for t in server._tool_manager.list_tools() if t.name == "get_memory_links")
    assert tool.annotations.readOnlyHint is True
    assert tool.annotations.openWorldHint is False
    assert {"resource_id", "space_id"} <= set(tool.parameters["required"])


def test_link_rest_route_reads_backlinks_without_provider_calls(commands, signing):
    service = commands[0]
    verifier, token = signing
    bearer = token()
    owner = verifier.verify(bearer)
    space = UUID(service.bootstrap(owner)["id"])
    service.memory_models = models = RelatingModels()
    target = service.remember_claims(owner, space, remember_request())
    source = service.remember_claims(owner, space, remember_request(text="Mira drives her car."))
    models.calls.clear()
    settings = Settings(environment="test", allowed_hosts=["testserver"])
    auth = {"Authorization": "Bearer " + bearer}
    endpoint = f"/v1/spaces/{space}/memories/{target['id']}/links"
    with TestClient(create_app(settings, workspace=service, verifier=verifier)) as http:
        assert http.get(endpoint).status_code == 401
        result = http.get(endpoint, headers=auth)
        assert result.status_code == 200, result.text
        assert result.json()["backlinks"][0]["resource_id"] == source["id"]
        assert result.json()["links"] == []
        assert http.get(endpoint + "?limit=0", headers=auth).status_code == 400
        missing = f"/v1/spaces/{space}/memories/{uuid4()}/links"
        assert http.get(missing, headers=auth).status_code == 404
    assert models.calls == []


def test_explicit_link_tool_is_local_write_and_exposes_node_schema():
    workspace = Workspace(None, None, None, "http://127.0.0.1:8000/mcp/")
    server = create_mcp(Settings(environment="test"), workspace)
    tool = next(t for t in server._tool_manager.list_tools() if t.name == "set_memory_links")
    assert tool.annotations.readOnlyHint is False
    assert tool.annotations.openWorldHint is False
    assert tool.annotations.idempotentHint is True
    assert {"resource_id", "expected_revision", "links", "command_id"} <= set(
        tool.parameters["$defs"]["SetMemoryLinks"]["required"]
    )


def test_explicit_link_rest_replace_retry_remove_and_route_guard(commands, signing):
    service = commands[0]
    verifier, token = signing
    bearer = token()
    owner = verifier.verify(bearer)
    space = UUID(service.bootstrap(owner)["id"])
    service.memory_models = models = RelatingModels(relate=False)
    target = service.remember_claims(owner, space, remember_request(text="My work"))
    source = service.remember_claims(owner, space, remember_request(text="What I like in my work"))
    models.calls.clear()
    settings = Settings(environment="test", allowed_hosts=["testserver"])
    auth = {"Authorization": "Bearer " + bearer}
    endpoint = f"/v1/spaces/{space}/memories/{source['id']}/links"
    body = {
        "command_id": str(uuid4()),
        "resource_id": source["id"],
        "expected_revision": source["revision"],
        "links": [{"resource_id": target["id"], "relation": "expands_on"}],
    }
    with TestClient(create_app(settings, workspace=service, verifier=verifier)) as http:
        assert http.put(endpoint, json=body).status_code == 401
        assert (
            http.put(endpoint, headers=auth, json=body | {"resource_id": str(uuid4())}).status_code
            == 400
        )
        saved = http.put(endpoint, headers=auth, json=body)
        assert saved.status_code == 200, saved.text
        assert http.put(endpoint, headers=auth, json=body).json() == saved.json()
        assert http.get(endpoint, headers=auth).json()["links"][0]["origin"] == "explicit"
        backward = f"/v1/spaces/{space}/memories/{target['id']}/links"
        edge = http.get(backward, headers=auth).json()["backlinks"][0]
        assert edge["resource_id"] == source["id"]
        assert edge["relation"] == "expands_on"
        removed = http.put(
            endpoint,
            headers=auth,
            json=body
            | {
                "command_id": str(uuid4()),
                "expected_revision": saved.json()["revision"],
                "links": [],
            },
        )
        assert removed.status_code == 200, removed.text
        assert http.get(backward, headers=auth).json()["backlinks"] == []
    assert models.calls == []
