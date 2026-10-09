"""Exercise provider boundaries and routing through the actual relevance entry point."""

import copy
import json
import time

import httpx
import pytest
from test_relevance import item

from space import relevance, typesafe_relevance
from space.model_provider import TYPESAFE_ENDPOINT, TYPESAFE_MODEL, MemoryModels, ModelError
from space.settings import Settings


def configured(**changes):
    return Settings(
        **(
            {
                "environment": "test",
                "memory_provider": "openai_compatible",
                "memory_provider_url": "https://models.invalid/v1",
                "memory_api_key": "text-secret",
                "memory_text_model": "openai/gpt-6-luna",
                "memory_embedding_model": "fixture-embedding",
                "memory_relevance_provider": "typesafe",
                "typesafe_api_key": "typesafe-secret",
            }
            | changes
        )
    )


def jev_response(payload, labels=None, *, answerable=0.95, temporal="unspecified"):
    answers = {}
    for key, question in payload["questions"].items():
        if question["type"] == "noul":
            answers[key] = {"type": "noul", "noul": answerable}
            continue
        label = temporal if key == "temporal" else (labels or {}).get(key, "direct")
        answers[key] = {
            "type": "choice",
            "choice": label,
            "confidence": 0.95,
            "probabilities": {
                option: 0.95 if option == label else 0.05 / (len(question["criteria"]) - 1)
                for option in question["criteria"]
            },
        }
    return {
        "model": TYPESAFE_MODEL,
        "answers": answers,
        "usage": {"input_tokens": 100, "output_tokens": 30},
    }


def fallback_response(payload, *, conflicts=False):
    schema = payload["response_format"]["json_schema"]["name"]
    candidates = json.loads(payload["messages"][1]["content"])["candidates"]
    if schema == "EvidenceConflicts":
        value = {"conflicts": [{"left": 0, "right": 1}] if conflicts else []}
    else:
        value = {
            "temporal_intent": "unspecified",
            "answer": "synthetic supported answer",
            "answer_quotes": [
                {"candidate": c["candidate"], "quote": c["text"][:50]}
                for c in candidates
                if c["text"].strip()
            ],
            "assessments": [
                {"candidate": c["candidate"], "relevance": "direct"} for c in candidates
            ],
        }
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(value)}}]})


def session(monkeypatch, jev=None, *, settings=None, authorize=lambda: None, fallback=None):
    requests = []

    def handler(request):
        payload = json.loads(request.content)
        requests.append((str(request.url), payload))
        if str(request.url) == TYPESAFE_ENDPOINT:
            assert request.headers["Authorization"] == "Bearer typesafe-secret"
            value = jev(request, payload) if jev else jev_response(payload)
            return value if isinstance(value, httpx.Response) else httpx.Response(200, json=value)
        assert request.headers["Authorization"] == "Bearer text-secret"
        assert str(request.url) == "https://models.invalid/v1/chat/completions"
        return fallback(payload) if fallback else fallback_response(payload)

    client = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: client(transport=httpx.MockTransport(handler), **kw)
    )
    return MemoryModels(settings or configured()).session(authorize), requests


def test_short_passage_keeps_exact_evidence_and_citations_with_one_authorized_request(monkeypatch):
    checks, quota = [], []
    model, requests = session(monkeypatch, authorize=lambda: checks.append(True))
    model.before_request = lambda: quota.append(True)
    original = item()
    selected = relevance.select([original], "Where is data kept?", model)
    assert selected == [original | {"relevance": "direct", "evidence_truncated": False}]
    assert len(requests) == 1 and model.calls == 1
    assert len(checks) == 2 and len(quota) == 1
    payload = requests[0][1]
    assert payload["state"] == {
        "query": "Where is data kept?",
        "candidates": [{"text": original["body"], "stale": False, "title": original["title"]}],
    }
    assert "secret" not in json.dumps(payload)
    assert "store" not in payload


def test_confident_abstention_and_empty_candidates(monkeypatch):
    model, requests = session(
        monkeypatch, lambda _, p: jev_response(p, {"c0": "irrelevant"}, answerable=0.05)
    )
    assert relevance.select([], "storage", model) == []
    assert requests == []
    assert relevance.select([item()], "Unknown capacity?", model) == []
    assert len(requests) == 1


@pytest.mark.parametrize("body", ["x" * 501, "x " * 2000, " "])
def test_long_truncated_or_blank_passages_skip_typesafe(monkeypatch, body):
    model, requests = session(monkeypatch)
    relevance.select([item(body=body)], "storage", model)
    assert all(url != TYPESAFE_ENDPOINT for url, _ in requests)


def test_maximum_short_passage_and_total_context_limits(monkeypatch):
    model, requests = session(monkeypatch)
    assert relevance.select([item(body="x" * 500)], "storage", model)
    assert requests[0][0] == TYPESAFE_ENDPOINT
    requests.clear()
    relevance.select([item(body="x" * 250)] * 50, "storage", model)
    assert all(url != TYPESAFE_ENDPOINT for url, _ in requests)
    before = len(requests)
    with pytest.raises(ModelError, match="candidate budget"):
        relevance.select([item()] * 51, "storage", model)
    assert len(requests) == before


@pytest.mark.parametrize("case", ["uncertain", "uncertain_rejection", "inconsistent", "conflict"])
def test_fallback_receives_every_original_candidate(monkeypatch, case):
    def jev(_, payload):
        r = jev_response(payload, {"c0": "direct", "c1": "irrelevant"})
        if case == "uncertain":
            r["answers"]["answerable"]["noul"] = 0.5
        elif case == "uncertain_rejection":
            r["answers"]["c1"]["probabilities"] = {
                "direct": 0.4,
                "irrelevant": 0.6,
                "historical": 0,
                "conflicting": 0,
            }
        elif case == "inconsistent":
            r["answers"]["answerable"]["noul"] = 0.05
        else:
            r = jev_response(payload, {"c0": "conflicting", "c1": "conflicting"})
        return r

    model, requests = session(monkeypatch, jev)
    originals = [item(), item(id="second", body="Other complete evidence.")]
    selected = relevance.select(originals, "storage", model)
    assert len(requests) == 3  # Jev, full support assessment, then conflict check.
    fallback = json.loads(requests[1][1]["messages"][1]["content"])
    assert [c["text"] for c in fallback["candidates"]] == [i["body"] for i in originals]
    assert [i["id"] for i in selected] == ["memory", "second"]


def test_conflict_fallback_preserves_both_sides(monkeypatch):
    model, _ = session(
        monkeypatch,
        lambda _, p: jev_response(p, {"c0": "conflicting", "c1": "conflicting"}),
        fallback=lambda p: fallback_response(p, conflicts=True),
    )
    records = [item(), item(id="other", body="The service uses SQLite.")]
    selected = relevance.select(records, "Which database?", model)
    assert [i["body"] for i in selected] == [i["body"] for i in records]
    assert all(i["relevance"] == "conflicting" for i in selected)


def test_stale_evidence_needs_confident_historical_intent(monkeypatch):
    model, requests = session(monkeypatch, lambda _, p: jev_response(p, temporal="historical"))
    result = relevance.select([item(stale=True)], "Previous database?", model)
    assert result[0]["relevance"] == "historical" and len(requests) == 1


def test_stale_current_claim_falls_back_and_cannot_be_returned(monkeypatch):
    model, requests = session(monkeypatch)
    assert relevance.select([item(stale=True)], "Current database?", model) == []
    assert len(requests) == 2


def corrupt(response, case):
    r = copy.deepcopy(response)
    if case == "model":
        r["model"] = "unknown-model"
    elif case == "missing":
        r["answers"].pop("c0")
    elif case == "extra":
        r["answers"]["c99"] = r["answers"]["c0"]
    elif case == "options":
        r["answers"]["c0"]["probabilities"]["invented"] = 0
    elif case == "distribution":
        r["answers"]["c0"]["probabilities"]["direct"] = 0.1
    elif case == "type":
        r["answers"]["c0"]["type"] = "noul"
    elif case == "boolean":
        r["answers"]["answerable"]["noul"] = True
    elif case == "nan":
        r["answers"]["answerable"]["noul"] = float("nan")
    elif case == "usage":
        r["usage"]["input_tokens"] = -1
    elif case == "confidence":
        r["answers"]["c0"]["confidence"] = 2
    elif case == "choice":
        r["answers"]["c0"]["choice"] = "irrelevant"
    return r


@pytest.mark.parametrize(
    "case",
    [
        "model",
        "missing",
        "extra",
        "options",
        "distribution",
        "type",
        "boolean",
        "nan",
        "usage",
        "confidence",
        "choice",
    ],
)
def test_malformed_response_uses_fallback_instead_of_unfiltered_results(monkeypatch, case):
    model, requests = session(
        monkeypatch,
        lambda _, p: httpx.Response(
            200, content=json.dumps(corrupt(jev_response(p), case)).encode()
        ),
    )
    assert relevance.select([item()], "storage", model)
    assert len(requests) == 2 and requests[1][0] != TYPESAFE_ENDPOINT


@pytest.mark.parametrize("status", [302, 401, 422, 429, 529])
def test_provider_error_falls_back_without_retry_or_redirect(monkeypatch, status):
    model, requests = session(
        monkeypatch,
        lambda *_: httpx.Response(
            status, text="PRIVATE-ERROR-CANARY", headers={"Location": "https://elsewhere.invalid"}
        ),
    )
    assert relevance.select([item()], "storage", model)
    assert len(requests) == 2 and sum(url == TYPESAFE_ENDPOINT for url, _ in requests) == 1


def test_timeout_falls_back_and_both_failures_stay_content_free(monkeypatch):
    def timeout(request, _):
        raise httpx.ReadTimeout("PRIVATE-ERROR-CANARY", request=request)

    model, requests = session(
        monkeypatch, timeout, fallback=lambda _: httpx.Response(500, text="PRIVATE-ERROR-CANARY")
    )
    with pytest.raises(ModelError) as error:
        relevance.select([item()], "storage", model)
    assert len(requests) == 2 and "PRIVATE" not in str(error.value)


@pytest.mark.parametrize("boundary", ["revoked", "quota", "requests", "deadline"])
def test_local_denials_do_not_disclose_or_trigger_fallback(monkeypatch, boundary):
    def deny():
        raise PermissionError("denied")

    model, requests = session(
        monkeypatch, authorize=deny if boundary == "revoked" else lambda: None
    )
    if boundary == "quota":
        model.before_request = deny
    elif boundary == "requests":
        model.calls = 80
    elif boundary == "deadline":
        model.deadline = time.monotonic() - 1
    with pytest.raises((PermissionError, ModelError)):
        relevance.select([item()], "storage", model)
    assert requests == []


def test_revocation_after_typesafe_response_does_not_fallback_or_release(monkeypatch):
    revoked = False

    def authorize():
        if revoked:
            raise PermissionError("revoked")

    def jev(_, p):
        nonlocal revoked
        revoked = True
        return jev_response(p)

    model, requests = session(monkeypatch, jev, authorize=authorize)
    with pytest.raises(PermissionError, match="revoked"):
        relevance.select([item()], "storage", model)
    assert len(requests) == 1


def test_typesafe_credential_and_mode_do_not_change_memory_fingerprint():
    original = MemoryModels(configured(memory_relevance_provider="model", typesafe_api_key=None))
    enabled = MemoryModels(configured())
    assert original.chat_fingerprint == enabled.chat_fingerprint
    assert original.embedding_fingerprint == enabled.embedding_fingerprint
    assert enabled.info()["typesafe_model"] == TYPESAFE_MODEL
    assert "secret" not in json.dumps(enabled.info())
    assert original.info()["typesafe_endpoint"] is None
    with pytest.raises(ValueError, match="TypeSafe assessment requires"):
        configured(typesafe_api_key=None)


def test_model_mode_uses_only_existing_assessor(monkeypatch):
    model, requests = session(monkeypatch, settings=configured(memory_relevance_provider="model"))
    assert relevance.select([item()], "storage", model)
    assert len(requests) == 1 and requests[0][0] != TYPESAFE_ENDPOINT


def test_duplicate_response_keys_trigger_fallback(monkeypatch):
    def duplicate(_, payload):
        text = json.dumps(jev_response(payload))
        return httpx.Response(200, content=('{"model":"duplicate",' + text[1:]).encode())

    model, requests = session(monkeypatch, duplicate)
    assert relevance.select([item()], "storage", model)
    assert len(requests) == 2


def test_fallback_shares_request_budget_and_disclosure_quota(monkeypatch):
    model, requests = session(monkeypatch, lambda _, p: jev_response(p, answerable=0.5))
    charges = []
    model.before_request = lambda: charges.append(True)
    assert relevance.select([item()], "storage", model)
    assert len(requests) == len(charges) == model.calls == 2
    requests.clear()
    charges.clear()
    model.calls = 79
    with pytest.raises(ModelError, match="request budget"):
        relevance.select([item()], "storage", model)
    assert len(requests) == len(charges) == 1


def test_response_byte_budget_aborts_without_fallback(monkeypatch):
    model, requests = session(monkeypatch, lambda *_: httpx.Response(200, content=b"x" * 2_000_001))
    with pytest.raises(ModelError, match="request budget"):
        relevance.select([item()], "storage", model)
    assert len(requests) == 1


def test_rounding_tolerance_accepts_realistic_distributions(monkeypatch):
    def rounded(_, payload):
        response = jev_response(payload)
        for answer in response["answers"].values():
            if answer["type"] == "choice":
                answer["probabilities"] = {
                    k: round(v, 2) for k, v in answer["probabilities"].items()
                }
        return response

    model, requests = session(monkeypatch, rounded)
    assert relevance.select([item()], "storage", model)
    assert len(requests) == 1


def test_production_payload_matches_measured_structured_rubric():
    from pathlib import Path

    rubric = Path(__file__).parents[1] / "evaluations/typesafe/context-v2.json"
    assert typesafe_relevance.RUBRIC == json.loads(rubric.read_text())
