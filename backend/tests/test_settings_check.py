"""The settings check: plain words, no secrets, and the application's own model code."""

import json
from contextlib import contextmanager

import httpx
import pytest

from space import check

KEY = "sk-check-canary-0123456789"
ANTHROPIC_KEY = "sk-ant-check-canary-0123456789"
ACCESS_KEY = "settings-check-access-key-" + "x" * 20


@pytest.fixture
def configure(monkeypatch, tmp_path):
    monkeypatch.setattr(check, "CONFIG_FILE", tmp_path / "missing.env")
    for name in list(__import__("os").environ):
        if name.startswith("SPACE_"):
            monkeypatch.delenv(name)

    def apply(**changes):
        values = {
            "SPACE_ENVIRONMENT": "test",
            "SPACE_WEB_URL": "https://space.example.com",
            "SPACE_MCP_RESOURCE_URL": "https://space.example.com/mcp/",
            "SPACE_ALLOWED_HOSTS": '["space.example.com","localhost","api"]',
            "SPACE_ACCESS_MODE": "key",
            "SPACE_ACCESS_KEY": ACCESS_KEY,
            "SPACE_DATABASE_URL": "postgresql://space_app:pw@db.invalid:5432/space",
            "SPACE_MEMORY_PROVIDER": "openai_compatible",
            "SPACE_MEMORY_PROVIDER_URL": "https://openrouter.ai/api/v1",
            "SPACE_MEMORY_API_KEY": KEY,
            "SPACE_MEMORY_TEXT_MODEL": "openai/gpt-6-luna",
            "SPACE_MEMORY_EMBEDDING_MODEL": "text-embedding-3-small",
            "SPACE_MEMORY_DECISION_MODEL": "typesafe/jev-1.13",
            "SPACE_MEMORY_RELEVANCE_PROVIDER": "decision",
        } | changes
        for name, value in values.items():
            if value is None:
                monkeypatch.delenv(name, raising=False)
            else:
                monkeypatch.setenv(name, value)

    return apply


@pytest.fixture
def database(monkeypatch):
    """A database that answers as the restricted login, with a 1,536-wide memory index."""
    state = {"user": "space_app", "width": 1536, "reachable": True}

    class Connection:
        def execute(self, sql):
            return type("Rows", (), {"fetchone": lambda _: (state["user"],)})()

    @contextmanager
    def connect(*args, **kwargs):
        if not state["reachable"]:
            raise check.psycopg.OperationalError("PRIVATE-DSN-CANARY")
        yield Connection()

    monkeypatch.setattr(check.psycopg, "connect", connect)
    monkeypatch.setattr(check.brain, "load_dimensions", lambda connection: state["width"])
    return state


@pytest.fixture
def provider(monkeypatch):
    """A fake provider, OpenRouter, Anthropic and public address; behaviour per endpoint."""
    state = {
        "chat": 200,
        "embedding_size": 1536,
        "decision": 200,
        "anthropic": 200,
        "health": 200,
        "calls": [],
    }

    def handler(request):
        url = str(request.url)
        state["calls"].append(url)
        if url.endswith("/health/live"):
            return httpx.Response(state["health"], json={"status": "ok"})
        if request.url.host == "api.anthropic.com":
            assert request.headers["x-api-key"] == ANTHROPIC_KEY
            if state["anthropic"] != 200:
                return httpx.Response(state["anthropic"], text="PRIVATE-REPLY-CANARY")
            content = [{"type": "text", "text": json.dumps({"ok": True})}]
            return httpx.Response(200, json={"content": content, "stop_reason": "end_turn"})
        assert request.headers["Authorization"] == "Bearer " + KEY
        body = json.loads(request.content)
        if url.endswith("/chat/completions"):
            if state["chat"] != 200:
                return httpx.Response(state["chat"], text="PRIVATE-REPLY-CANARY")
            content = json.dumps({"ok": True})
            return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})
        if url.endswith("/embeddings"):
            vector = [0.1] * state["embedding_size"]
            return httpx.Response(200, json={"data": [{"index": 0, "embedding": vector}]})
        assert url == "https://openrouter.ai/api/alpha/decisions"
        assert body["model"] == "typesafe/jev-1.13"
        if state["decision"] != 200:
            return httpx.Response(state["decision"], text="PRIVATE-REPLY-CANARY")
        return httpx.Response(
            200,
            json={
                "model": "typesafe/jev-1.13-20261001",
                "answers": {"blue": {"type": "noul", "noul": 0.97}},
                "usage": {"input_tokens": 20, "output_tokens": 1, "cost": 0.000001},
            },
        )

    real = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw)
    )
    return state


def run(**options):
    lines = []
    code = check.run(write=lines.append, **options)
    return code, "\n".join(lines)


def test_a_working_installation_passes_every_check(configure, database, provider):
    configure()
    code, text = run()
    assert code == 0, text
    for subject in (
        "Settings",
        "Access",
        "Address",
        "App sign-in",
        "Database",
        "Public address",
        "Text model",
        "Embedding model",
        "Decision model",
    ):
        assert f"PASS  {subject}:" in text, (subject, text)
    assert "served as typesafe/jev-1.13-20261001" in text
    assert KEY not in text and ACCESS_KEY not in text


def test_problems_are_named_in_plain_words_without_secrets(configure, database, provider):
    configure(SPACE_ALLOWED_HOSTS='["localhost"]')
    provider.update(chat=404, embedding_size=768, decision=401)
    code, text = run()
    assert code == 1
    assert "FAIL  Address: SPACE_ALLOWED_HOSTS does not include space.example.com" in text
    assert "FAIL  Text model: openai/gpt-6-luna: the model or the endpoint was not found" in text
    assert "SPACE_MEMORY_EMBEDDING_DIMENSIONS=768" in text
    assert "FAIL  Decision model: typesafe/jev-1.13: the API key was refused (HTTP 401)" in text
    assert "3 problem(s)" not in text and "4 problem(s) to fix." in text
    assert "CANARY" not in text and KEY not in text


def test_the_anthropic_route_is_checked_apart_from_the_provider(configure, database, provider):
    configure(SPACE_MEMORY_ANTHROPIC_API_KEY=ANTHROPIC_KEY)
    provider.update(chat=404)
    code, text = run()
    assert code == 1
    assert "FAIL  Text model: openai/gpt-6-luna: the model or the endpoint was not found" in text
    assert "PASS  Anthropic model: claude-haiku-5-5 answered" in text
    provider.update(chat=200, anthropic=401)
    code, text = run()
    assert code == 1
    assert "PASS  Text model: openai/gpt-6-luna answered" in text
    assert "FAIL  Anthropic model: claude-haiku-5-5: the API key was refused (HTTP 401)" in text
    assert "CANARY" not in text and ANTHROPIC_KEY not in text and KEY not in text


def test_invalid_settings_are_reported_by_their_rule(configure, database, provider):
    configure(SPACE_MEMORY_DECISION_URL="http://decisions.example.com/v1")
    code, text = run()
    assert code == 1
    assert "FAIL  Settings: Decision features require a memory provider, a decision model" in text
    assert provider["calls"] == []


def test_private_mode_on_a_public_address_fails(configure, database, provider):
    configure(SPACE_ACCESS_MODE="private", SPACE_ACCESS_KEY=None)
    code, text = run(offline=True)
    assert code == 1
    assert "FAIL  Access: private mode on the public address space.example.com" in text
    assert provider["calls"] == []


def test_an_unreachable_database_and_no_provider(configure, database, provider):
    database["reachable"] = False
    configure(SPACE_MEMORY_PROVIDER="disabled", SPACE_MEMORY_RELEVANCE_PROVIDER="model")
    code, text = run()
    assert code == 1
    assert "FAIL  Database: cannot connect; check SPACE_DATABASE_URL" in text
    assert "PASS  Memory provider: off" in text
    assert "CANARY" not in text
