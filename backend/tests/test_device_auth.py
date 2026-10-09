"""Device-code connections: a six-digit code replaces the browser redirect."""

from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_agents import commands as commands_fixture
from test_agents import connection
from test_auth import signing as signing_fixture
from test_database_setup import database as database_fixture
from test_memory_jobs import queue as queue_fixture
from test_workspace import command_database as command_database_fixture

from space import device_auth
from space.app import create_app
from space.settings import Settings

database = database_fixture
command_database = command_database_fixture
commands = commands_fixture
signing = signing_fixture
queue = queue_fixture


def test_start_request_normalizes_name_and_harness():
    body = device_auth.DeviceStart(client_name="  Cursor   Agent ", harness=" CURSOR ")
    assert (body.client_name, body.harness) == ("Cursor Agent", "cursor")
    assert device_auth.DeviceStart(harness="my-own-runner").harness == "other"
    assert device_auth.DeviceStart().client_name == "Agent"
    with pytest.raises(ValidationError):
        device_auth.DeviceStart(client_name="Agent\x00")


@pytest.mark.parametrize("typed", ["483920", "483 920", "483-920", " 48 39 20 "])
def test_typed_codes_accept_spacing(typed):
    assert device_auth.DeviceClaim(user_code=typed).user_code == "483920"


@pytest.mark.parametrize("typed", ["48392", "4839201", "abcdef", "483920483920483920483920"])
def test_typed_codes_must_be_six_digits(typed):
    with pytest.raises(ValidationError):
        device_auth.DeviceClaim(user_code=typed)


def test_space_tokens_are_rejected_without_a_lookup(signing):
    verifier, _ = signing
    from space.security.auth import Unauthenticated

    with pytest.raises(Unauthenticated):
        verifier.verify("spc_" + "a" * 43, mcp=True)
    with pytest.raises(Unauthenticated):
        verifier.verify("spc_" + "a" * 43)


def test_code_flow_issues_a_revocable_agent_token(commands, signing, monkeypatch):
    service = commands[0]
    verifier, token = signing
    browser_token = token()
    owner = verifier.verify(browser_token)
    settings = Settings(environment="test", allowed_hosts=["testserver"])
    with TestClient(create_app(settings, workspace=service, verifier=verifier)) as http:
        auth = {"Authorization": "Bearer " + browser_token}
        space = http.post("/v1/workspace", headers=auth).json()["id"]

        started = http.post(
            "/v1/device/code", json={"client_name": "Cursor Agent", "harness": "cursor"}
        )
        assert started.status_code == 200, started.text
        start = started.json()
        assert len(start["user_code"]) == 6 and start["user_code"].isdigit()
        assert start["verification_url"].endswith("/connect")
        assert start["verification_url_complete"].endswith("/connect?code=" + start["user_code"])
        poll = {"device_code": start["device_code"]}

        waiting = http.post("/v1/device/token", json=poll)
        assert (waiting.status_code, waiting.json()["error"]) == (400, "authorization_pending")
        hurried = http.post("/v1/device/token", json=poll)
        assert hurried.json()["error"] == "slow_down"
        # The remaining polls in this test do not wait out the real interval.
        monkeypatch.setattr(device_auth, "POLL_INTERVAL", 0)

        wrong = http.post("/v1/device/claim", headers=auth, json={"user_code": "000000"})
        if wrong.status_code != 404:  # the random code was 000000 itself
            assert wrong.status_code == 200
        claimed = http.post(
            "/v1/device/claim", headers=auth, json={"user_code": start["user_code"]}
        )
        assert claimed.status_code == 200, claimed.text
        details = claimed.json()
        assert details["client"]["name"] == "Cursor Agent" and details["harness"] == "cursor"
        assert details["redirect_uri"] is None
        # Reloading the consent page claims the same code again without penalty.
        assert (
            http.post(
                "/v1/device/claim", headers=auth, json={"user_code": start["user_code"]}
            ).json()["authorization_id"]
            == details["authorization_id"]
        )

        identity = http.post(
            "/v1/agent-identities", headers=auth, json={"name": "Cursor Agent", "harness": "cursor"}
        ).json()
        approved = http.post(
            "/v1/device/consent",
            headers=auth,
            json={
                "authorization_id": details["authorization_id"],
                "action": "approve",
                "can_write": True,
                "agent_id": identity["id"],
                "space_id": space,
            },
        )
        assert approved.status_code == 200, approved.text
        # A decision is final: the same request cannot be answered twice.
        assert (
            http.post(
                "/v1/device/consent",
                headers=auth,
                json={"authorization_id": details["authorization_id"], "action": "deny"},
            ).status_code
            == 409
        )

        issued = http.post("/v1/device/token", json=poll)
        assert issued.status_code == 200, issued.text
        credentials = issued.json()
        assert credentials["access_token"].startswith("spc_")
        assert credentials["agent_id"] == identity["id"]
        assert credentials["mcp_url"] == service.resource_url
        assert credentials["headers"]["X-Space-Agent-ID"] == identity["id"]
        # The secret redeems once.
        assert http.post("/v1/device/token", json=poll).json()["error"] == "expired_token"

        grant = next(g for g in service.grants(owner)["items"] if g["agent_id"] == identity["id"])
        assert grant["can_write"] is True and grant["revoked_at"] is None

        agent_headers = {**credentials["headers"], "Accept": "application/json, text/event-stream"}

        def rpc(name, arguments):
            return http.post(
                "/mcp/",
                headers=agent_headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": arguments},
                },
            )

        found = rpc("identify_agent", {})
        assert found.status_code == 200, found.text
        assert found.json()["result"]["structuredContent"]["agent"]["id"] == identity["id"]
        listed = rpc("list_resources", {"space_id": space, "kind": "document"})
        assert listed.json()["result"]["structuredContent"]["items"] == []
        # Agent tokens never administer access through the browser routes.
        assert http.get("/v1/agent-identities", headers=agent_headers).status_code == 401
        refused = http.post("/v1/device/claim", headers=agent_headers, json={"user_code": "123456"})
        assert refused.status_code == 401

        service.revoke(owner, UUID(grant["client_id"]))
        assert rpc("identify_agent", {}).status_code == 401


def test_denied_unknown_and_guessed_codes(commands, signing, monkeypatch):
    service = commands[0]
    verifier, token = signing
    browser_token = token()
    owner = verifier.verify(browser_token)
    settings = Settings(environment="test", allowed_hosts=["testserver"])
    monkeypatch.setattr(device_auth, "POLL_INTERVAL", 0)
    with TestClient(create_app(settings, workspace=service, verifier=verifier)) as http:
        auth = {"Authorization": "Bearer " + browser_token}
        http.post("/v1/workspace", headers=auth)
        start = http.post("/v1/device/code", json={}).json()
        poll = {"device_code": start["device_code"]}
        details = http.post(
            "/v1/device/claim", headers=auth, json={"user_code": start["user_code"]}
        ).json()
        assert details["client"]["name"] == "Agent" and details["harness"] == "other"
        denied = http.post(
            "/v1/device/consent",
            headers=auth,
            json={"authorization_id": details["authorization_id"], "action": "deny"},
        )
        assert denied.json() == {"status": "denied"}
        assert http.post("/v1/device/token", json=poll).json()["error"] == "access_denied"
        assert http.post("/v1/device/token", json=poll).json()["error"] == "expired_token"
        assert (
            http.post("/v1/device/token", json={"device_code": "x" * 43}).json()["error"]
            == "expired_token"
        )
        assert len(service.grants(owner)["items"]) == 0

        # Another person's code cannot be taken over once claimed.
        other_token = token(sub=str(uuid4()))
        other_auth = {"Authorization": "Bearer " + other_token}
        http.post("/v1/workspace", headers=other_auth)
        second = http.post("/v1/device/code", json={}).json()
        assert (
            http.post(
                "/v1/device/claim", headers=auth, json={"user_code": second["user_code"]}
            ).status_code
            == 200
        )
        assert (
            http.post(
                "/v1/device/claim", headers=other_auth, json={"user_code": second["user_code"]}
            ).status_code
            == 404
        )

        # Guessing is budgeted per person.
        statuses = [
            http.post(
                "/v1/device/claim", headers=other_auth, json={"user_code": f"{n:06d}"}
            ).status_code
            for n in range(1, 12)
        ]
        assert 429 in statuses and statuses[-1] == 429
        assert statuses.count(429) >= 2

        # Existing delegated connections are unaffected by device requests.
        actor = connection(service, owner)
        assert actor.grant_id
