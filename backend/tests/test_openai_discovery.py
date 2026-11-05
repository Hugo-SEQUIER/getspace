"""OpenAI's clients see Space's tools before signing in; every other client keeps the 401."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from test_auth import signing as signing_fixture

from space.app import create_app
from space.openai_discovery import MAX_INSPECTED_BODY, SECURITY_SCHEMES
from space.settings import Settings
from space.workspace import Workspace

signing = signing_fixture

SCANNER = "Python/3.14 aiohttp/3.14.4"
CONNECTOR = "openai-mcp/1.0.0"
INITIALIZE = {
    "protocolVersion": "2025-11-25",
    "capabilities": {},
    "clientInfo": {"name": "openai-mcp", "version": "1.0.0"},
}


@pytest.fixture
def http(signing):
    verifier, _ = signing
    settings = Settings(environment="test", allowed_hosts=["testserver"])
    workspace = Workspace(None, None, None, settings.mcp_resource_url)
    with TestClient(create_app(settings, workspace=workspace, verifier=verifier)) as client:
        yield client


def post(http, body, agent, token=None, **headers):
    headers = {"Accept": "application/json, text/event-stream", "User-Agent": agent} | headers
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return http.post("/mcp/", headers=headers, json=body)


def rpc(method, params=None, request_id=1):
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}


@pytest.mark.parametrize("agent", [SCANNER, CONNECTOR])
def test_openai_clients_discover_the_tools_without_a_token(http, agent):
    initialized = post(http, rpc("initialize", INITIALIZE), agent)
    assert initialized.status_code == 200, initialized.text
    assert initialized.json()["result"]["serverInfo"]["name"] == "Space"
    notified = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    assert post(http, notified, agent).status_code == 202
    tools = post(http, rpc("tools/list"), agent).json()["result"]["tools"]
    assert len(tools) > 30
    for tool in tools:
        assert tool["securitySchemes"] == SECURITY_SCHEMES, tool["name"]
        assert tool["_meta"]["securitySchemes"] == SECURITY_SCHEMES, tool["name"]
    # Without a token the list holds only the shared guide.
    prompts = post(http, rpc("prompts/list"), agent).json()["result"]["prompts"]
    assert [prompt["name"] for prompt in prompts] == ["memory_guide"]
    resources = post(http, rpc("resources/list"), agent).json()["result"]["resources"]
    assert [resource["name"] for resource in resources] == ["memory_guide"]


@pytest.mark.parametrize("agent", ["claude-code/2.1.0", "node", "Cursor/1.7", ""])
def test_other_clients_learn_to_sign_in_from_the_first_401(http, agent):
    denied = post(http, rpc("initialize", INITIALIZE), agent)
    assert denied.status_code == 401
    assert "resource_metadata=" in denied.headers["WWW-Authenticate"]
    assert post(http, rpc("tools/list"), agent).status_code == 401


@pytest.mark.parametrize("token", [None, "not-a-token"])
def test_openai_tool_calls_without_an_accepted_token_ask_to_connect(http, token):
    call = rpc("tools/call", {"name": "server_info", "arguments": {}}, request_id="call-7")
    answer = post(http, call, CONNECTOR, token)
    assert answer.status_code == 200
    reply = answer.json()
    assert reply["id"] == "call-7"
    assert reply["result"]["isError"] is True
    (challenge,) = reply["result"]["_meta"]["mcp/www_authenticate"]
    assert challenge.startswith('Bearer error="invalid_token", error_description="')
    metadata = "http://127.0.0.1:8000/.well-known/oauth-protected-resource/mcp"
    assert f'resource_metadata="{metadata}"' in challenge
    assert "structuredContent" not in reply["result"]


def test_openai_clients_still_need_a_token_for_everything_else(http):
    # Discovery with a token Space refuses: the client must renew it.
    assert post(http, rpc("tools/list"), CONNECTOR, "not-a-token").status_code == 401
    # The scanner's probe has no JSON body.
    probe = http.post(
        "/mcp/",
        headers={"User-Agent": SCANNER, "Content-Type": "application/octet-stream"},
        content=b"",
    )
    assert probe.status_code == 401
    for method in ("resources/read", "prompts/get"):
        assert post(http, rpc(method, {"uri": "space://memory/guide"}), SCANNER).status_code == 401
    batch = [rpc("tools/list"), rpc("tools/call", {"name": "server_info"}, request_id=2)]
    assert post(http, batch, SCANNER).status_code == 401
    large = rpc("initialize", INITIALIZE | {"padding": "x" * MAX_INSPECTED_BODY})
    assert post(http, large, SCANNER).status_code == 401
    assert http.get("/mcp/", headers={"User-Agent": SCANNER}).status_code == 401


def test_signed_in_openai_clients_are_unchanged(http, signing):
    verifier, token = signing
    bearer = token(aud=verifier.resource, client_id=str(uuid4()), space_grant_id=str(uuid4()))
    call = rpc("tools/call", {"name": "server_info", "arguments": {}})
    answer = post(http, call, CONNECTOR, bearer)
    assert answer.status_code == 200, answer.text
    assert answer.json()["result"]["structuredContent"]["resource_access"] is True
