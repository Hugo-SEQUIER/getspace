"""Personal API keys: a person's own client calls the REST API as them, in one Space."""

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_auth import signing as signing_fixture
from test_database_setup import database as database_fixture
from test_memory_jobs import queue as queue_fixture
from test_workspace import change
from test_workspace import command_database as command_database_fixture
from test_workspace import commands as commands_fixture

from space import api_keys
from space.app import create_app
from space.security.auth import Unauthenticated
from space.settings import Settings

database = database_fixture
command_database = command_database_fixture
commands = commands_fixture
signing = signing_fixture
queue = queue_fixture


def test_key_names_are_cleaned_and_lifetimes_bounded():
    assert api_keys.CreateApiKey(name="  My   client ").name == "My client"
    assert api_keys.CreateApiKey(name="x").expires_in_days == 90
    assert api_keys.CreateApiKey(name="x", expires_in_days=None).expires_in_days is None
    for invalid in (
        {"name": "bad\x00"},
        {"name": ""},
        {"name": "x", "expires_in_days": 0},
        {"name": "x", "expires_in_days": 366},
        {"name": "x", "scope": "all"},
    ):
        with pytest.raises(ValidationError):
            api_keys.CreateApiKey(**invalid)


def test_api_keys_are_rejected_on_mcp_and_without_a_store(signing):
    verifier, _ = signing
    with pytest.raises(Unauthenticated):
        verifier.verify("spk_" + "a" * 43)
    verifier.api_keys = SimpleNamespace(identity=lambda _: pytest.fail("MCP must not look up"))
    with pytest.raises(Unauthenticated):
        verifier.verify("spk_" + "a" * 43, mcp=True)


def test_openapi_lists_exactly_the_routes_an_api_key_may_call(signing):
    verifier, _ = signing
    settings = Settings(environment="test", allowed_hosts=["testserver"])
    app = create_app(settings, workspace=SimpleNamespace(db=None), verifier=verifier)
    schema = TestClient(app).get("/openapi.json").json()
    paths = schema["paths"]
    assert "/v1/spaces/{space_id}/resources" in paths
    assert "/v1/spaces/{space_id}/export" in paths
    assert "/v1/workspace" in paths and set(paths["/v1/workspace"]) == {"get"}
    for hidden in (
        "/v1/agents",
        "/v1/workspaces",
        "/v1/device/token",
        "/v1/oauth/consent",
        "/v1/spaces/{space_id}/api-keys",
        "/v1/spaces/{space_id}/webhooks",
        "/v1/spaces/{space_id}/restores",
        "/v1/spaces/{space_id}/items/{item_id}/visibility",
        "/v1/spaces/{space_id}/memory/imports",
        "/ops/metrics",
    ):
        assert hidden not in paths, hidden
    assert schema["components"]["securitySchemes"]["ApiKey"]["scheme"] == "bearer"
    for path, operations in paths.items():
        for operation in operations.values():
            if path.startswith("/v1/"):
                assert operation["security"] == [{"ApiKey": []}], path
                assert operation["tags"] and operation["summary"], path
            names = {parameter["name"] for parameter in operation.get("parameters", [])}
            assert "workspace_id" not in names, path


def test_cors_admits_browser_clients_without_credentials(signing):
    verifier, _ = signing
    settings = Settings(environment="test", allowed_hosts=["testserver"])
    http = TestClient(create_app(settings, workspace=SimpleNamespace(db=None), verifier=verifier))
    preflight = http.options(
        "/v1/workspace",
        headers={
            "Origin": "https://client.example",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "*"
    assert "access-control-allow-credentials" not in preflight.headers
    closed = Settings(environment="test", allowed_hosts=["testserver"], api_cors_origins=[])
    http = TestClient(create_app(closed, workspace=SimpleNamespace(db=None), verifier=verifier))
    response = http.get("/health/live", headers={"Origin": "https://client.example"})
    assert "access-control-allow-origin" not in response.headers


def test_api_key_acts_as_the_person_in_one_space_only(commands, signing, database, monkeypatch):
    service = commands[0]
    verifier, token = signing
    browser_token = token()
    settings = Settings(environment="test", allowed_hosts=["testserver"])
    with TestClient(create_app(settings, workspace=service, verifier=verifier)) as http:
        auth = {"Authorization": "Bearer " + browser_token}
        space = http.post("/v1/workspace", headers=auth).json()["id"]
        keys = f"/v1/spaces/{space}/api-keys"

        created = http.post(keys, headers=auth, json={"name": "My client"})
        assert created.status_code == 200, created.text
        reader = created.json()
        assert reader["token"].startswith("spk_") and reader["prefix"] == reader["token"][:12]
        assert reader["can_write"] is False and reader["expires_at"] and not reader["last_used_at"]
        listed = http.get(keys, headers=auth).json()["items"]
        assert [item["id"] for item in listed] == [reader["id"]]
        assert "token" not in listed[0]

        read_only = {"Authorization": "Bearer " + reader["token"]}
        assert http.get("/v1/workspace", headers=read_only).json()["id"] == space
        assert http.get(keys, headers=auth).json()["items"][0]["last_used_at"]
        documents = http.get(
            f"/v1/spaces/{space}/resources", params={"kind": "document"}, headers=read_only
        )
        assert documents.status_code == 200, documents.text
        assert (
            http.post(f"/v1/spaces/{space}/export", headers=read_only, json={}).status_code == 200
        )
        assert http.get(f"/v1/spaces/{space}/overview", headers=read_only).status_code == 200
        body = change().model_dump(mode="json")
        refused = http.post(f"/v1/spaces/{space}/resources", headers=read_only, json=body)
        assert (refused.status_code, refused.json()["error"]) == (403, "This API key is read-only.")

        # Another Space looks absent, whether named in the path or as the selector.
        other = str(uuid4())
        foreign = http.get(
            f"/v1/spaces/{other}/resources", params={"kind": "document"}, headers=read_only
        )
        assert foreign.status_code == 404
        selected = http.get("/v1/workspace", params={"workspace_id": other}, headers=read_only)
        assert selected.status_code == 404

        # Keys never administer access, including other keys.
        for method, path, payload in (
            ("get", keys, None),
            ("post", keys, {"name": "Escalation"}),
            ("delete", f"{keys}/{reader['id']}", None),
            ("get", "/v1/agents", None),
            ("get", "/v1/agent-identities", None),
            ("get", "/v1/workspaces", None),
            ("post", "/v1/workspace", None),
            ("post", "/v1/device/claim", {"user_code": "123456"}),
            ("get", f"/v1/spaces/{space}/webhooks", None),
            ("get", f"/v1/spaces/{space}/restores", None),
        ):
            response = http.request(method, path, headers=read_only, json=payload)
            assert response.status_code == 403, (path, response.text)
        mcp = http.post(
            "/mcp/",
            headers={**read_only, "Accept": "application/json, text/event-stream"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        )
        assert mcp.status_code == 401

        writer = http.post(
            keys, headers=auth, json={"name": "Writer", "can_write": True, "expires_in_days": None}
        ).json()
        assert writer["can_write"] is True and writer["expires_at"] is None
        read_write = {"Authorization": "Bearer " + writer["token"]}
        saved = http.post(f"/v1/spaces/{space}/resources", headers=read_write, json=body)
        assert saved.status_code == 200, saved.text
        fetched = http.get(f"/v1/spaces/{space}/resources/{saved.json()['id']}", headers=auth)
        assert fetched.json()["title"] == "Private title canary 915"

        # Revocation applies on the next request and only once.
        assert http.delete(f"{keys}/{reader['id']}", headers=auth).json() == {"revoked": True}
        assert http.get("/v1/workspace", headers=read_only).status_code == 401
        assert http.delete(f"{keys}/{reader['id']}", headers=auth).status_code == 404

        # An expired key stops working and leaves the listing.
        with database[0]() as maintenance:
            maintenance.execute(
                "UPDATE space.api_keys SET expires_at=now()-interval '1 second' WHERE id=%s",
                (writer["id"],),
            )
        assert http.get("/v1/workspace", headers=read_write).status_code == 401
        assert http.get(keys, headers=auth).json()["items"] == []

        # Each person has a bounded number of live keys per Space.
        monkeypatch.setattr(api_keys, "MAX_ACTIVE_KEYS", 1)
        assert http.post(keys, headers=auth, json={"name": "One"}).status_code == 200
        assert http.post(keys, headers=auth, json={"name": "Two"}).status_code == 409

        # Someone else's session sees none of these keys and cannot revoke them.
        stranger = {"Authorization": "Bearer " + token(sub=str(uuid4()))}
        http.post("/v1/workspace", headers=stranger)
        assert http.get(keys, headers=stranger).status_code == 404
        mine = http.get(keys, headers=auth).json()["items"][0]["id"]
        assert http.delete(f"{keys}/{mine}", headers=stranger).status_code == 404
        assert http.get(keys, headers=auth).json()["items"][0]["id"] == mine


def test_team_keys_follow_membership_and_role(commands):
    from test_teams import create_team, join

    from space import teams
    from space.workspace import CommandError

    service, owner, _, _ = commands
    team = create_team(service, owner)
    sid, org = UUID(team["id"]), UUID(team["organization_id"])
    viewer, _ = join(service, owner, team, role="viewer")
    with pytest.raises(CommandError, match="view-only"):
        api_keys.create(service, viewer, sid, api_keys.CreateApiKey(name="x", can_write=True))
    made = api_keys.create(service, viewer, sid, api_keys.CreateApiKey(name="Reader"))
    store = api_keys.ApiKeys(service.db, owner.issuer)
    key = store.identity(made["token"])
    assert service.info(key)["role"] == "viewer"
    with pytest.raises(CommandError, match="view-only"):
        service.save(key, sid, change())

    # A role change or removal deletes the person's keys, so rejoining revives nothing.
    member_id = next(
        m["id"] for m in teams.members(service, owner, org)["items"] if m["role"] == "viewer"
    )
    teams.change_member(service, owner, org, UUID(member_id), "member")
    assert store.identity(made["token"]) is None
    assert api_keys.listing(service, viewer, sid) == {"items": []}

    # Changing who can open a Space likewise deletes every key in it.
    project = UUID(
        teams.create_space(
            service, owner, org, teams.CreateSpace(command_id=uuid4(), name="Project")
        )["id"]
    )
    mine = api_keys.create(service, owner, project, api_keys.CreateApiKey(name="Owner"))
    assert store.identity(mine["token"]) is not None
    teams.set_access(service, owner, project, teams.SpaceAccess(visibility="shared"))
    assert store.identity(mine["token"]) is None
