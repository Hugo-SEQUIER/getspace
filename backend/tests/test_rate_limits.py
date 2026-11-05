"""What needs no credentials is counted per client: one client cannot fill the pending queues
or guess the access key without end."""

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_auth import ACCESS_KEY

from space.app import create_app
from space.rate_limits import (
    KEY_FAILURES,
    UNKNOWN,
    PreAuthLimits,
    Rule,
    Window,
    client_address,
)
from space.security.auth import TokenVerifier
from space.settings import Settings


def scope(peer, forwarded=None):
    headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded else []
    return {"client": (peer, 1234) if peer else None, "headers": headers}


def test_forwarded_address_is_trusted_only_from_a_nearby_proxy():
    # Caddy, on the compose network, appends the client it saw.
    assert client_address(scope("172.18.0.5", "198.51.100.7")) == "198.51.100.7"
    assert client_address(scope("127.0.0.1", "10.0.0.9, 198.51.100.7")) == "198.51.100.7"
    assert client_address(scope("::ffff:10.0.0.2", "2001:db8::1")) == "2001:db8::1"
    # A client reaching the API directly cannot choose the address it is counted against.
    assert client_address(scope("203.0.113.4", "198.51.100.7")) == "203.0.113.4"
    assert client_address(scope("172.18.0.5", "not-an-address")) == "172.18.0.5"
    assert client_address(scope("::ffff:203.0.113.4")) == "203.0.113.4"
    assert client_address(scope(None)) == UNKNOWN


def test_window_counts_per_key_and_forgets_after_its_span():
    window = Window(2, 60, capacity=2)
    assert window.retry_after("a", now=0) == 0
    window.record("a", now=0)
    window.record("a", now=10)
    assert window.retry_after("a", now=11) == 50
    assert window.retry_after("b", now=11) == 0
    assert window.retry_after("a", now=60) == 0
    window.record("b", now=61)
    window.record("c", now=62)
    window.record("d", now=63)
    # Beyond its capacity, the least recently counted key is forgotten first.
    assert set(window._events) == {"c", "d"}


def application(rules=None, failures=None):
    app = FastAPI()
    app.add_middleware(PreAuthLimits, rules=rules, failures=failures)

    @app.post("/oauth/register")
    def register():
        return {"client_id": "fixture"}

    @app.get("/v1/workspace")
    def workspace():
        return {"workspace": "fixture"}

    @app.get("/v1/denied")
    def denied():
        from fastapi.responses import JSONResponse

        return JSONResponse({"error": "Unlock this Space with its access key"}, 401)

    return app


def test_a_rule_answers_429_past_its_limit_for_that_client_only():
    rules = [Rule(frozenset({"POST"}), lambda p: p == "/oauth/register", Window(3, 600))]
    app = application(rules=rules)
    proxy = TestClient(app, client=("172.18.0.5", 1234))
    flood = {"X-Forwarded-For": "198.51.100.7"}
    for _ in range(3):
        assert proxy.post("/oauth/register", headers=flood).status_code == 200
    refused = proxy.post("/oauth/register", headers=flood)
    assert refused.status_code == 429
    assert int(refused.headers["retry-after"]) > 0
    # Another client behind the same proxy, and other routes, are not held.
    other = proxy.post("/oauth/register", headers={"X-Forwarded-For": "198.51.100.8"})
    assert other.status_code == 200
    assert proxy.get("/v1/workspace", headers=flood).status_code == 200


def test_wrong_credentials_on_v1_are_limited_per_client():
    app = application(failures=Window(2, 900))
    attacker = TestClient(app, client=("203.0.113.4", 1234))
    wrong = {"Authorization": "Bearer guess"}
    assert attacker.get("/v1/denied", headers=wrong).status_code == 401
    assert attacker.get("/v1/denied", headers=wrong).status_code == 401
    blocked = attacker.get("/v1/workspace", headers=wrong)
    assert blocked.status_code == 429 and int(blocked.headers["retry-after"]) > 0
    # A request without credentials is not answered for someone else's guesses.
    assert attacker.get("/v1/workspace").status_code == 200
    owner = TestClient(app, client=("198.51.100.20", 1234))
    assert owner.get("/v1/workspace", headers=wrong).status_code == 200


def test_the_api_stops_access_key_guesses_from_one_client():
    settings = Settings(environment="test", access_key=ACCESS_KEY, allowed_hosts=["testserver"])
    app = create_app(settings, workspace=SimpleNamespace(db=None), verifier=TokenVerifier(settings))
    attacker = TestClient(app, client=("203.0.113.4", 1234))
    for attempt in range(KEY_FAILURES):
        guess = {"Authorization": f"Bearer {'g' * 40}{attempt}"}
        assert attacker.get("/v1/workspace", headers=guess).status_code == 401
    refused = attacker.get("/v1/workspace", headers={"Authorization": "Bearer " + ACCESS_KEY})
    assert refused.status_code == 429
    other = TestClient(app, client=("198.51.100.20", 1234))
    assert (
        other.get("/v1/workspace", headers={"Authorization": "Bearer another-guess"}).status_code
        == 401
    )


def test_the_api_limits_anonymous_device_codes_and_oauth_registrations():
    settings = Settings(environment="test", access_key=ACCESS_KEY, allowed_hosts=["testserver"])
    app = create_app(settings, workspace=SimpleNamespace(db=None), verifier=TokenVerifier(settings))
    limits = next(m for m in app.user_middleware if m.cls is PreAuthLimits)
    assert not limits.kwargs  # the default rules
    paths = {
        (method, path)
        for rule in PreAuthLimits(None).rules
        for method in rule.methods
        for path in ["/oauth/register", "/oauth/authorize", "/v1/device/code", "/oauth/token"]
        if rule.matches(path)
    }
    assert paths == {
        ("POST", "/oauth/register"),
        ("GET", "/oauth/authorize"),
        ("POST", "/oauth/authorize"),
        ("POST", "/v1/device/code"),
    }
