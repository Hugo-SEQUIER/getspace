"""Parameters: synthetic candidate records and mocked provider requests.

What it does: verifies exact size reasons and separate, content-free chat HTTP timings.
Output: unchanged routing/evidence assertions, never real-model quality claims.
"""

import json

import pytest
from test_decision_relevance import configured, fallback_response, session
from test_evidence_passages import source
from test_relevance import item

from space import decision_relevance, read_timing, relevance
from space.model_provider import ModelError
from space.read_timing import collect_read_timings
from space.settings import DECISIONS_URL


@pytest.mark.parametrize(
    ("record", "detail", "observed", "limit"),
    [
        (item(body="x" * 501), "authored_claim_too_long", 501, 500),
        (source("x" * 501), "source_unit_too_long", 501, 500),
        (source(("x" * 490 + ".\n\n") * 51), "too_many_passages", 51, 50),
    ],
)
def test_size_fallback_reports_the_exact_guard_without_dropping_candidates(
    monkeypatch, record, detail, observed, limit
):
    model, requests = session(monkeypatch)
    diagnostics = {}
    records = [record, item(id="second", body="A different complete fact.")]
    relevance.select(records, "facts", model, diagnostics=diagnostics)
    assert diagnostics["fallback_reason"] == "size"
    assert diagnostics["fallback_detail"] == detail
    assert diagnostics["fallback_observed"] == observed
    assert diagnostics["fallback_limit"] == limit
    assert diagnostics["candidate_count"] == diagnostics["assessed_units"] == 2
    assert all(url != DECISIONS_URL for url, _ in requests)
    submitted = json.loads(requests[0][1]["messages"][1]["content"])["candidates"]
    assert len(submitted) == 2 and submitted[1]["text"] == records[1]["body"]
    assert "fallback_" not in json.dumps(submitted)


def test_serialized_state_size_is_measured_with_metadata_and_json_escaping(monkeypatch):
    record = item(title='"' * 6100)
    model, _ = session(monkeypatch)
    diagnostics = {}
    relevance.select([record], "facts", model, diagnostics=diagnostics)
    payload = decision_relevance.payload_for([relevance.candidate(record, 0)], "facts")
    assert diagnostics["fallback_detail"] == "state_too_large"
    assert diagnostics["fallback_observed"] == len(json.dumps(payload["state"], ensure_ascii=False))
    assert diagnostics["fallback_limit"] == 12000


def test_size_details_do_not_leak_into_later_jev_or_empty_results(monkeypatch):
    model, _ = session(monkeypatch)
    diagnostics = {}
    relevance.select([item(body="x" * 501)], "facts", model, diagnostics=diagnostics)
    assert diagnostics["fallback_detail"] == "authored_claim_too_long"
    relevance.select([item()], "facts", model, diagnostics=diagnostics)
    assert diagnostics["route"] == "decision"
    assert not any(
        key in diagnostics for key in ("fallback_detail", "fallback_limit", "fallback_observed")
    )
    relevance.select([], "facts", model, diagnostics=diagnostics)
    assert diagnostics["route"] == "none" and diagnostics["fallback_reason"] is None


@pytest.mark.parametrize(
    ("changes", "count", "detail"),
    [
        ({}, 51, "too_many_evidence_units"),
        ({"text": "x" * 501}, 1, "passage_too_long"),
        ({"truncated": True}, 1, "truncated_evidence"),
        ({"text": " "}, 1, "blank_evidence"),
    ],
)
def test_direct_jev_guards_remain_fail_closed_with_precise_details(changes, count, detail):
    class NoProvider:
        def decide(self, payload):
            pytest.fail("Invalid evidence must not reach the decision model")

    evidence = [(relevance.candidate(item(), 0) | changes)] * count
    diagnostics = {}
    assert (
        decision_relevance.assess(evidence, "facts", NoProvider(), diagnostics=diagnostics) is None
    )
    assert diagnostics["fallback_reason"] == "size" and diagnostics["fallback_detail"] == detail


@pytest.mark.parametrize("conflict_failure", [False, True])
def test_two_chat_calls_have_separate_http_times_without_auth_or_private_data(
    monkeypatch, conflict_failure
):
    now = [0.0]
    monkeypatch.setattr(read_timing, "perf_counter", lambda: now[0])

    def authorize():
        now[0] += 2

    def fallback(payload):
        conflict = payload["response_format"]["json_schema"]["name"] == "EvidenceConflicts"
        now[0] += 0.007 if conflict else 0.030
        if conflict and conflict_failure:
            import httpx

            return httpx.Response(503, text="PRIVATE_PROVIDER_BODY")
        return fallback_response(payload)

    model, requests = session(
        monkeypatch,
        settings=configured(memory_relevance_provider="model"),
        authorize=authorize,
        fallback=fallback,
    )
    records = [item(), item(id="second", body="A different complete fact.")]
    with collect_read_timings() as timings:
        if conflict_failure:
            with pytest.raises(ModelError):
                relevance.select(records, "PRIVATE_QUERY", model)
        else:
            selected = relevance.select(records, "PRIVATE_QUERY", model)
            assert [r["id"] for r in selected] == [r["id"] for r in records]
    assert len(requests) == 2
    assert timings.snapshot() == {"chat": 37.0, "chat_assessment": 30.0, "chat_conflicts": 7.0}
    assert "PRIVATE" not in timings.header() and "secret" not in timings.header()


def test_unknown_provider_timing_stage_is_rejected_before_disclosure(monkeypatch):
    model, requests = session(monkeypatch)
    with (
        collect_read_timings() as timings,
        pytest.raises(ValueError, match="Unknown provider timing stage"),
    ):
        model.invoke("PRIVATE_QUERY", timing_stage="PRIVATE_QUERY")
    assert requests == [] and timings.snapshot() == {}
