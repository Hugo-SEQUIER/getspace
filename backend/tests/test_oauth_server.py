"""The self-hosted OAuth server, end to end against a disposable local PostgreSQL.

A client registers, the owner approves it through the website's consent routes, and the
client exchanges its code with PKCE, calls MCP, refreshes, and loses access when the owner
revokes the connection. Also: discovery, refused requests, client secrets and private mode.
The rules for redirect URIs and client names run without a database.
"""

import base64
import hashlib
import secrets
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from test_selfhosted import ACCESS_KEY, document, mcp
from test_selfhosted import server as server_fixture

from space.app import create_app
from space.oauth_server import client_name, redirect_allowed

server = server_fixture
OWNER = {"Authorization": "Bearer " + ACCESS_KEY}
CALLBACK = "https://claude.ai/api/mcp/auth_callback"
RESOURCE = "http://testserver/mcp"


def pkce():
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def register(http, **metadata):
    body = {
        "client_name": "Claude",
        "redirect_uris": [CALLBACK],
        "token_endpoint_auth_method": "none",
    } | metadata
    response = http.post("/oauth/register", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def authorize(http, client, challenge, **params):
    query = {
        "response_type": "code",
        "client_id": client["client_id"],
        "redirect_uri": CALLBACK,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": "state-123",
        "resource": RESOURCE,
    } | params
    return http.get("/oauth/authorize", params=query, follow_redirects=False)


def consent_page(response) -> str:
    """The request ID on the website's consent page the client was sent to."""
    assert response.status_code == 302, response.text
    target = urlsplit(response.headers["location"])
    assert f"{target.scheme}://{target.netloc}{target.path}" == (
        "http://testserver/connect/authorize"
    )
    return parse_qs(target.query)["authorization_id"][0]


def answer(http, authorization_id, action="approve", **choices):
    return http.post(
        "/v1/oauth/consent",
        headers=OWNER,
        json={"authorization_id": authorization_id, "action": action} | choices,
    )


def returned(redirect_url, callback=CALLBACK) -> dict:
    target = urlsplit(redirect_url)
    assert f"{target.scheme}://{target.netloc}{target.path}" == callback
    return {key: values[0] for key, values in parse_qs(target.query).items()}


def exchange(http, client, code, verifier, **extra):
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": CALLBACK,
        "client_id": client["client_id"],
        "code_verifier": verifier,
        "resource": RESOURCE,
    } | extra
    return http.post("/oauth/token", data=form)


def refresh(http, client, token, **extra):
    form = {"grant_type": "refresh_token", "refresh_token": token} | {
        "client_id": client["client_id"]
    }
    return http.post("/oauth/token", data=form | extra)


def connect(http, client, *, can_write=True, **choices):
    """Runs the whole sign-in for a public client. Output: the token response."""
    verifier, challenge = pkce()
    request = consent_page(authorize(http, client, challenge))
    decided = answer(http, request, can_write=can_write, **choices)
    assert decided.status_code == 200, decided.text
    code = returned(decided.json()["redirect_url"])["code"]
    tokens = exchange(http, client, code, verifier)
    assert tokens.status_code == 200, tokens.text
    return tokens.json()


def bearer(token, agent_id=None):
    headers = {"Authorization": "Bearer " + token}
    return headers | ({"X-Space-Agent-ID": agent_id} if agent_id else {})


def test_discovery_points_mcp_clients_at_the_authorization_server(server):
    with TestClient(create_app(server.app_settings())) as http:
        challenge = mcp(http, "workspace_info", {})
        assert challenge.status_code == 401
        metadata_url = "http://testserver/.well-known/oauth-protected-resource/mcp"
        assert f'resource_metadata="{metadata_url}"' in challenge.headers["www-authenticate"]

        for path in ("/mcp", "/mcp/", ""):
            resource = http.get("/.well-known/oauth-protected-resource" + path)
            assert resource.status_code == 200, (path, resource.text)
            assert resource.json()["resource"] == RESOURCE
            assert resource.json()["authorization_servers"] == ["http://testserver/"]

        server_metadata = http.get("/.well-known/oauth-authorization-server").json()
        assert server_metadata["issuer"] == "http://testserver/"
        assert server_metadata["authorization_endpoint"] == "http://testserver/oauth/authorize"
        assert server_metadata["token_endpoint"] == "http://testserver/oauth/token"  # noqa: S105
        assert server_metadata["registration_endpoint"] == "http://testserver/oauth/register"
        assert server_metadata["code_challenge_methods_supported"] == ["S256"]
        assert "none" in server_metadata["token_endpoint_auth_methods_supported"]


def test_a_client_signs_in_with_consent_and_loses_access_when_revoked(server):
    with TestClient(create_app(server.app_settings())) as http:
        client = register(http)
        assert client["client_name"] == "Claude" and "client_secret" not in client
        verifier, challenge = pkce()
        request = consent_page(authorize(http, client, challenge))

        assert http.get(f"/v1/oauth/details?authorization_id={request}").status_code == 401
        shown = http.get(f"/v1/oauth/details?authorization_id={request}", headers=OWNER)
        assert shown.status_code == 200, shown.text
        assert shown.json()["client"]["name"] == "Claude"
        assert shown.json()["redirect_uri"] == CALLBACK

        agent = http.post(
            "/v1/agent-identities", headers=OWNER, json={"name": "Claude", "harness": "claude"}
        ).json()
        decided = answer(http, request, can_write=True, agent_id=agent["id"])
        assert decided.status_code == 200, decided.text
        response = returned(decided.json()["redirect_url"])
        assert response["state"] == "state-123"
        # A request is answered once.
        assert answer(http, request).status_code == 409

        wrong = exchange(http, client, response["code"], pkce()[0])
        assert wrong.status_code == 400 and wrong.json()["error"] == "invalid_grant"
        tokens = exchange(http, client, response["code"], verifier)
        assert tokens.status_code == 200, tokens.text
        tokens = tokens.json()
        assert tokens["access_token"].startswith("spo_")
        assert tokens["refresh_token"].startswith("spr_")
        assert tokens["token_type"] == "Bearer" and tokens["expires_in"] == 3600  # noqa: S105

        # The identity chosen during consent is the connection's own.
        found = mcp(http, "identify_agent", {}, bearer(tokens["access_token"]))
        assert found.status_code == 200, found.text
        assert found.json()["result"]["structuredContent"]["agent"]["id"] == agent["id"]
        space = http.get("/v1/workspace", headers=OWNER).json()["id"]
        saved = mcp(
            http,
            "save_resource",
            {"space_id": space, "change": document("Signed in", "Written over OAuth")},
            bearer(tokens["access_token"], agent["id"]),
        ).json()["result"]
        assert saved.get("isError") is not True, saved
        resource = saved["structuredContent"]["id"]
        read = http.get(f"/v1/spaces/{space}/resources/{resource}", headers=OWNER).json()
        assert read["body"] == "Written over OAuth" and read["author"] == "agent"
        # MCP only: the token opens no REST route.
        assert http.get("/v1/workspace", headers=bearer(tokens["access_token"])).status_code == 401

        rotated = refresh(http, client, tokens["refresh_token"])
        assert rotated.status_code == 200, rotated.text
        rotated = rotated.json()
        assert rotated["refresh_token"] != tokens["refresh_token"]
        assert mcp(http, "workspace_info", {}, bearer(tokens["access_token"])).status_code == 401
        reused = refresh(http, client, tokens["refresh_token"])
        assert reused.status_code == 400 and reused.json()["error"] == "invalid_grant"
        live = mcp(http, "workspace_info", {}, bearer(rotated["access_token"], agent["id"]))
        assert live.status_code == 200, live.text

        connections = http.get("/v1/agents", headers=OWNER).json()["items"]
        assert any(item["client_id"] == client["client_id"] for item in connections)
        assert http.delete(f"/v1/agents/{client['client_id']}", headers=OWNER).status_code == 200
        revoked = mcp(http, "workspace_info", {}, bearer(rotated["access_token"], agent["id"]))
        assert revoked.status_code == 401
        assert refresh(http, client, rotated["refresh_token"]).status_code == 400

    # Only digests are stored.
    with server.admin() as connection:
        digest = hashlib.sha256(rotated["access_token"].encode()).digest()
        assert (
            connection.execute(
                "SELECT count(*) FROM space.oauth_tokens WHERE access_hash = %s", (digest,)
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM space.oauth_tokens t WHERE strpos(t::text, %s) > 0",
                (rotated["access_token"],),
            ).fetchone()[0]
            == 0
        )


def test_a_code_presented_twice_revokes_what_it_produced(server):
    with TestClient(create_app(server.app_settings())) as http:
        client = register(http)
        verifier, challenge = pkce()
        request = consent_page(authorize(http, client, challenge))
        code = returned(answer(http, request).json()["redirect_url"])["code"]
        tokens = exchange(http, client, code, verifier).json()
        agent = mcp(http, "identify_agent", {}, bearer(tokens["access_token"]))
        assert agent.status_code == 200, agent.text

        again = exchange(http, client, code, verifier)
        assert again.status_code == 400 and again.json()["error"] == "invalid_grant"
        assert mcp(http, "identify_agent", {}, bearer(tokens["access_token"])).status_code == 401
        assert refresh(http, client, tokens["refresh_token"]).status_code == 400


def test_declined_and_malformed_requests_never_reach_the_owner(server):
    with TestClient(create_app(server.app_settings())) as http:
        client = register(http)
        verifier, challenge = pkce()

        declined = answer(http, consent_page(authorize(http, client, challenge)), "deny")
        assert declined.status_code == 200, declined.text
        refusal = returned(declined.json()["redirect_url"])
        assert refusal["error"] == "access_denied" and refusal["state"] == "state-123"

        # Errors go back to a registered callback, never to the consent page.
        elsewhere = authorize(http, client, challenge, resource="https://other.example/mcp")
        assert elsewhere.status_code == 302
        assert returned(elsewhere.headers["location"])["error"] == "invalid_request"
        unpaired = authorize(http, client, "short")
        assert returned(unpaired.headers["location"])["error"] == "invalid_request"
        stranger = authorize(http, client, challenge, redirect_uri="https://evil.example/cb")
        assert stranger.status_code == 400
        unknown = authorize(http, {"client_id": "00000000-0000-4000-8000-000000000000"}, challenge)
        assert unknown.status_code == 400

        request = consent_page(authorize(http, client, challenge))
        code = returned(answer(http, request).json()["redirect_url"])["code"]
        moved = exchange(http, client, code, verifier, redirect_uri="https://claude.ai/other")
        assert moved.status_code == 400
        assert (
            http.get(f"/v1/oauth/details?authorization_id={request}", headers=OWNER).status_code
            == 404
        )


@pytest.mark.parametrize(
    ("uri", "accepted"),
    [
        ("javascript:alert(1)", False),
        ("http://evil.example/callback", False),
        ("https://user:pass@claude.ai/cb", False),
        ("https://claude.ai/cb#fragment", False),
        ("cursor://anysphere.cursor-mcp/oauth/callback", True),
        ("http://127.0.0.1:5555/callback", True),
    ],
)
def test_registration_admits_only_safe_callbacks(server, uri, accepted):
    with TestClient(create_app(server.app_settings())) as http:
        response = http.post(
            "/oauth/register", json={"client_name": "Probe", "redirect_uris": [uri]}
        )
        assert response.status_code == (201 if accepted else 400), response.text


def test_confidential_clients_prove_their_secret(server):
    with TestClient(create_app(server.app_settings())) as http:
        for method in ("client_secret_post", "client_secret_basic"):
            client = register(http, token_endpoint_auth_method=method)
            secret = client["client_secret"]
            verifier, challenge = pkce()
            request = consent_page(authorize(http, client, challenge))
            code = returned(answer(http, request).json()["redirect_url"])["code"]

            missing = exchange(http, client, code, verifier)
            assert missing.status_code == 401
            if method == "client_secret_post":
                wrong = exchange(http, client, code, verifier, client_secret="x" * 64)
                assert wrong.status_code == 401
                good = exchange(http, client, code, verifier, client_secret=secret)
            else:
                credentials = base64.b64encode(f"{client['client_id']}:{secret}".encode())
                good = http.post(
                    "/oauth/token",
                    data={
                        "grant_type": "authorization_code",
                        "code": code,
                        "redirect_uri": CALLBACK,
                        "client_id": client["client_id"],
                        "code_verifier": verifier,
                    },
                    headers={"Authorization": "Basic " + credentials.decode()},
                )
            assert good.status_code == 200, (method, good.text)
        with server.admin() as connection:
            assert (
                connection.execute(
                    "SELECT count(*) FROM space.oauth_clients WHERE metadata ? 'client_secret'"
                ).fetchone()[0]
                == 0
            )


def test_loopback_callbacks_may_change_port(server):
    with TestClient(create_app(server.app_settings())) as http:
        client = register(http, redirect_uris=["http://127.0.0.1:5555/callback"])
        verifier, challenge = pkce()
        moved = authorize(http, client, challenge, redirect_uri="http://127.0.0.1:6123/callback")
        request = consent_page(moved)
        decided = answer(http, request).json()["redirect_url"]
        assert returned(decided, "http://127.0.0.1:6123/callback")["code"]


def test_private_mode_serves_no_authorization_server(server):
    settings = server.app_settings(access_mode="private", access_key=None)
    with TestClient(create_app(settings)) as http:
        assert http.get("/.well-known/oauth-authorization-server").status_code == 404
        assert http.get("/.well-known/oauth-protected-resource/mcp").status_code == 404
        assert http.post("/oauth/register", json={}).status_code == 404


@pytest.mark.parametrize(
    ("uri", "allowed"),
    [
        ("https://chatgpt.com/connector_platform_oauth_redirect", True),
        ("http://localhost:33418/", True),
        ("http://[::1]:8080/callback", True),
        ("com.example.app:/oauth2redirect", True),
        ("https:///missing-host", False),
        ("JavaScript:alert(1)", False),
        ("data:text/html,hi", False),
        ("vbscript:msgbox", False),
        ("http://192.168.1.4/callback", False),
        ("https://claude.ai/cb with space", False),
    ],
)
def test_redirect_rules(uri, allowed):
    assert redirect_allowed(uri) is allowed


def test_client_names_are_shown_as_plain_text():
    assert client_name("  Claude‮  Desktop\n") == "Claude Desktop"
    assert client_name("x" * 200) == "x" * 80
    assert client_name("\u0000​") is None
    assert client_name(None) is None
