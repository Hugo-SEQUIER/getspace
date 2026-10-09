import pytest
from fastapi.testclient import TestClient

from space.api.mcp import create_mcp
from space.app import create_app
from space.settings import Settings
from space.workspace import Workspace


@pytest.fixture
def client():
    with TestClient(create_app(Settings(environment="test", allowed_hosts=["testserver"]))) as http:
        yield http


def rpc(client, method, params=None):
    response = client.post(
        "/mcp/",
        headers={"Accept": "application/json, text/event-stream"},
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_liveness_is_not_storage_readiness(client):
    assert client.get("/health/live").json()["status"] == "ok"
    assert client.get("/health/ready").status_code == 503


def test_unexpected_database_failure_logs_only_a_safe_category(caplog):
    from psycopg import OperationalError

    app = create_app(Settings(environment="test", allowed_hosts=["testserver"]))

    @app.get("/failure-fixture")
    def failure():
        raise OperationalError("SECRET SQL, hostname and password")

    with TestClient(app) as http:
        response = http.get("/failure-fixture")
    assert response.status_code == 503
    assert "api_total;dur=" in response.headers["server-timing"]
    assert response.headers["cache-control"] == "no-store"
    assert "space_api_failure" in caplog.text
    assert "database operation failed" in caplog.text
    assert "SECRET" not in caplog.text + response.text


def test_official_mcp_initialize_and_tool_roundtrip(client):
    initialized = rpc(
        client,
        "initialize",
        {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "foundation-test", "version": "1"},
        },
    )
    assert initialized["result"]["serverInfo"]["name"] == "Space"
    tools = rpc(client, "tools/list")["result"]["tools"]
    assert [tool["name"] for tool in tools] == ["server_info"]
    assert tools[0]["annotations"]["readOnlyHint"] is True
    result = rpc(client, "tools/call", {"name": "server_info", "arguments": {}})
    assert result["result"]["structuredContent"]["resource_access"] is False


def test_no_unprotected_resource_interface(client):
    for path in ["/v1/spaces", "/v1/resources", "/v1/me/personal-workspace"]:
        assert client.get(path).status_code == 404
    assert (
        rpc(client, "tools/call", {"name": "resource_create", "arguments": {}})["result"]["isError"]
        is True
    )


def test_unknown_host_is_denied_except_for_the_liveness_probe(client):
    """The liveness probe is exempt from host validation; every other route is not.

    HealthCheckedHostMiddleware exempts GET /health/live because an ALB probe sends the
    task's changing private IP as Host. This test pins that exemption as narrow: the
    readiness route and a cross-origin MCP request are still refused.
    """
    assert client.get("/health/live", headers={"Host": "attacker.invalid"}).status_code == 200
    assert client.get("/health/ready", headers={"Host": "attacker.invalid"}).status_code == 400
    response = client.post(
        "/mcp/",
        headers={
            "Accept": "application/json, text/event-stream",
            "Origin": "https://attacker.invalid",
        },
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )
    assert response.status_code == 403


def test_production_publishes_the_api_reference_without_private_routes():
    with TestClient(
        create_app(
            Settings(environment="test", allowed_hosts=["testserver"]).model_copy(
                update={"environment": "production"}
            )
        )
    ) as client:
        assert client.get("/docs").status_code == 404
        paths = client.get("/openapi.json").json()["paths"]
        assert "/ops/metrics" not in paths and "/health/live" in paths


def test_memory_tool_annotations_describe_forgetting_and_external_model_access():
    workspace = Workspace(None, None, None, "http://127.0.0.1:8000/mcp/")
    server = create_mcp(Settings(environment="test"), workspace)
    annotations = {tool.name: tool.annotations for tool in server._tool_manager.list_tools()}
    assert annotations["change_memory"].destructiveHint is True
    assert annotations["change_memory"].idempotentHint is True
    for name in ("remember", "build_memory", "change_memory", "get_context"):
        assert annotations[name].openWorldHint is True
    for name in ("save_resource", "save_handoff", "list_memories"):
        assert annotations[name].openWorldHint is False
