import json
from contextlib import contextmanager, nullcontext

import httpx
import pytest
from pydantic import SecretStr

from space import model_provider, read_timing
from space.model_provider import TYPESAFE_ENDPOINT, ModelError, ModelSession, TypeSafeError
from space.read_timing import collect_read_timings, record_timing
from space.settings import Settings


@pytest.fixture
def clock(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(read_timing, "perf_counter", lambda: now[0])
    return now


@pytest.fixture
def settings():
    return Settings(
        environment="test",
        memory_provider="openai_compatible",
        memory_provider_url="https://models.invalid/v1",
        memory_text_model="fixture-chat",
        memory_embedding_model="fixture-embedding",
        memory_api_key="CREDENTIAL_CANARY",
        typesafe_api_key="CREDENTIAL_CANARY",
    )


@pytest.mark.parametrize(
    "stage",
    [
        "db_transaction",
        "recall_total",
        "recall_sources",
        "recall_vector",
        "recall_lexical",
        "recall_context",
        "recall_assessment",
        "recall_auth",
        "embedding",
        "jev",
        "chat",
    ],
)
def test_new_stages_accumulate_only_numeric_durations_in_current_request(stage):
    with collect_read_timings() as timings:
        record_timing(stage, 0.01)
        record_timing(stage, 0.015)
        with pytest.raises(ValueError, match="Unknown timing stage"):
            record_timing(stage + ';desc="PRIVATE_QUERY"', 1)
    record_timing(stage, 1)
    assert timings.header() == f"{stage};dur=25.0"
    with collect_read_timings() as following:
        assert following.header() == ""


@pytest.mark.parametrize("provider", ["openai_compatible", "ollama"])
@pytest.mark.parametrize("operation", ["embedding", "jev", "chat"])
@pytest.mark.parametrize("prepared", [False, True])
def test_provider_roundtrip_excludes_auth_quota_client_setup_and_json_decode(
    clock, settings, monkeypatch, provider, operation, prepared
):
    settings = settings.model_copy(update={"memory_provider": provider})
    content = '{"action":"STOP"}'
    encoded = json.dumps(
        {
            "data": [{"index": 0, "embedding": [1, 0]}],
            "embeddings": [[1, 0]],
            "choices": [{"message": {"content": content}}],
            "message": {"content": content},
            "assessed": True,
        }
    ).encode()
    events, requests = [], []

    class Body(httpx.SyncByteStream):
        def __iter__(self):
            clock[0] += 0.004
            yield encoded[:10]
            clock[0] += 0.006
            yield encoded[10:]

        def close(self):
            clock[0] += 0.003

    def handler(request):
        requests.append(request)
        events.append("http")
        clock[0] += 0.012
        return httpx.Response(200, stream=Body())

    def authorize():
        events.append("auth")
        clock[0] += 2

    def prepare():
        events.append("prepare" if prepared else "quota")
        clock[0] += 3

    original_decode = model_provider.unique_object

    def decode(pairs):
        clock[0] += 4
        return original_decode(pairs)

    monkeypatch.setattr(model_provider, "unique_object", decode)
    with httpx.Client(transport=httpx.MockTransport(handler), trust_env=False) as client:

        @contextmanager
        def client_context():
            clock[0] += 1
            yield client
            clock[0] += 1

        session = ModelSession(settings, authorize, client_context=client_context)
        session.before_request = prepare
        if prepared:
            session.prepare_request = prepare
        with collect_read_timings() as timings:
            if operation == "embedding":
                assert session.embed(["PRIVATE_QUERY"]) == [[1, 0]]
                assert session.embed(["PRIVATE_QUERY"]) == [[1, 0]]
            elif operation == "jev":
                assert session.evaluate_typesafe({"input": "PRIVATE_QUERY"})["assessed"]
            else:
                assert session.invoke("PRIVATE_QUERY") == {"action": "STOP"}
        assert not client.is_closed
    assert len(requests) == 1
    assert events == (["prepare"] if prepared else ["auth", "quota"]) + ["http", "auth"]
    assert timings.header() == f"{operation};dur=25.0"


@pytest.mark.parametrize(
    "url,payload,stage,failure",
    [
        (
            "https://models.invalid/embeddings",
            {"input": ["PRIVATE_QUERY"]},
            "embedding",
            ModelError,
        ),
        (TYPESAFE_ENDPOINT, {"input": "PRIVATE_QUERY"}, "jev", TypeSafeError),
        ("https://models.invalid/chat", {"messages": ["PRIVATE_QUERY"]}, "chat", ModelError),
    ],
)
@pytest.mark.parametrize("problem", ["status", "transport", "body", "invalid_json"])
def test_failed_roundtrip_is_timed_without_retries_or_private_error_details(
    clock, settings, url, payload, stage, failure, problem
):
    requests, checks = [], []

    class BrokenBody(httpx.SyncByteStream):
        def __iter__(self):
            clock[0] += 0.02
            raise httpx.ReadError("PRIVATE_PROVIDER_BODY")
            yield b""

    def handler(request):
        requests.append(request)
        clock[0] += 0.01
        if problem == "transport":
            raise httpx.ConnectError("PRIVATE_PROVIDER_BODY")
        if problem == "body":
            return httpx.Response(200, stream=BrokenBody())
        return httpx.Response(503 if problem == "status" else 200, text="PRIVATE_PROVIDER_BODY")

    with httpx.Client(transport=httpx.MockTransport(handler), trust_env=False) as client:
        session = ModelSession(
            settings, lambda: checks.append("auth"), client_context=lambda: nullcontext(client)
        )
        with collect_read_timings() as timings, pytest.raises(failure) as error:
            session._request(url, payload, SecretStr("CREDENTIAL_CANARY"), failure=failure)
    assert len(requests) == 1
    assert checks == ["auth"]
    assert timings.header() == f"{stage};dur={30 if problem == 'body' else 10}.0"
    assert "PRIVATE" not in str(error.value)
    assert "CREDENTIAL" not in str(error.value)


@pytest.mark.parametrize("denied_by", ["authorize", "quota", "prepare", "calls", "deadline"])
def test_preflight_rejection_never_records_provider_time(clock, settings, denied_by):
    def denied():
        clock[0] += 1
        raise PermissionError("PRIVATE_REJECTION")

    def handler(request):
        pytest.fail("Preflight rejection must not disclose content")

    with httpx.Client(transport=httpx.MockTransport(handler), trust_env=False) as client:
        session = ModelSession(settings, lambda: None, client_context=lambda: nullcontext(client))
        if denied_by == "authorize":
            session.authorize = denied
        elif denied_by == "quota":
            session.before_request = denied
        elif denied_by == "prepare":
            session.prepare_request = denied
        elif denied_by == "calls":
            session.calls = 80
        else:
            session.deadline = 0
        with collect_read_timings() as timings, pytest.raises((PermissionError, ModelError)):
            session.embed(["PRIVATE_QUERY"])
    assert timings.header() == ""


def test_postflight_revocation_still_blocks_result_after_timed_disclosure(clock, settings):
    checks = []

    def authorize():
        checks.append("auth")
        clock[0] += 1
        if len(checks) == 2:
            raise PermissionError("revoked")

    def handler(request):
        clock[0] += 0.01
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1, 0]}]})

    with httpx.Client(transport=httpx.MockTransport(handler), trust_env=False) as client:
        session = ModelSession(settings, authorize, client_context=lambda: nullcontext(client))
        with collect_read_timings() as timings, pytest.raises(PermissionError, match="revoked"):
            session.embed(["PRIVATE_QUERY"])
    assert checks == ["auth", "auth"]
    assert session.embeddings == {}
    assert timings.header() == "embedding;dur=10.0"
