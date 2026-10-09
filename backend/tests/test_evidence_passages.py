"""Lossless passage shaping and context boundaries through the relevance API."""

import copy
import json

import pytest
from test_relevance import item
from test_typesafe_relevance import jev_response, session

from space import relevance
from space.model_provider import TYPESAFE_ENDPOINT


def source(body, **changes):
    value = item(body=body, reason="source_match", **changes)
    value.pop("entry_id")
    return value


@pytest.mark.parametrize("separator", [" ", "\n\n", "\r\n\r\n"])
def test_full_coverage_split_uses_jev_and_retains_raw_offsets(monkeypatch, separator):
    body = separator.join(f"Paragraph {i} gives the precise project facts." for i in range(30))
    original = source(body, evidence_context={"projects": ["Alpha"]})
    snapshot = copy.deepcopy(original)
    model, requests = session(monkeypatch)
    diagnostics = {}
    selected = relevance.select([original], "Project facts?", model, diagnostics=diagnostics)
    assert len(requests) == 1 and requests[0][0] == TYPESAFE_ENDPOINT
    texts = [c["text"] for c in requests[0][1]["state"]["candidates"]]
    assert len(texts) > 1 and "".join(texts) == body
    assert all(len(text) <= 500 for text in texts)
    assert [view["body"] for view in selected] == texts
    end = 0
    for view in selected:
        passage = view["passage"]
        assert passage["start"] == end
        assert view["body"] == body[passage["start"] : passage["end"]]
        end = passage["end"]
        for key in ("id", "revision", "sources", "reason", "evidence_context"):
            assert view[key] == original[key]
        assert "entry_id" not in view and view["evidence_truncated"]
    assert end == len(body) and original == snapshot
    assert diagnostics == {
        "route": "jev",
        "fallback_reason": None,
        "candidate_count": 1,
        "assessed_units": len(texts),
        "selected_units": len(texts),
    }


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ("x" * 501, "size"),
        ("Text. " * 100 + "\n```python\nprint('a')\n```", "unsafe_structure"),
        ("Text. " * 100 + "\n~~~\ncode\n~~~", "unsafe_structure"),
        ("| Name | Value |\n| --- | --- |\n" + "| Alpha | Data |\n" * 40, "unsafe_structure"),
        ("    code = 'x'\n" * 40, "unsafe_structure"),
        (" ", "blank"),
    ],
)
def test_unsafe_candidate_forces_original_chat_fallback_for_every_candidate(
    monkeypatch, body, reason
):
    records = [source("A complete fact. " * 60), source(body, id="second")]
    model, requests = session(monkeypatch)
    diagnostics = {}
    relevance.select(records, "facts", model, diagnostics=diagnostics)
    assert all(url != TYPESAFE_ENDPOINT for url, _ in requests)
    candidates = json.loads(requests[0][1]["messages"][1]["content"])["candidates"]
    assert len(candidates) == 2
    assert [c["text"] for c in candidates] == [i["body"] for i in records]
    assert diagnostics["route"] == "chat" and diagnostics["fallback_reason"] == reason


def test_long_authored_claim_is_never_split_or_mutated(monkeypatch):
    original = item(body="A complete sentence about the source. " * 20)
    before = copy.deepcopy(original)
    model, requests = session(monkeypatch)
    selected = relevance.select([original], "Source?", model)
    assert original == before
    assert all(url != TYPESAFE_ENDPOINT for url, _ in requests)
    assert selected[0]["body"] == original["body"]
    assert selected[0]["entry_id"] == original["entry_id"]
    assert "passage" not in selected[0]


def test_no_candidates_dropped_to_fit_fifty_shaped_units(monkeypatch):
    originals = [source(("x" * 490 + ".\n\n") * 51), item()]
    model, requests = session(monkeypatch)
    diagnostics = {}
    relevance.select(originals, "facts", model, diagnostics=diagnostics)
    assert all(url != TYPESAFE_ENDPOINT for url, _ in requests)
    candidates = json.loads(requests[0][1]["messages"][1]["content"])["candidates"]
    assert len(candidates) == 2 and candidates[1]["text"] == originals[1]["body"]
    assert diagnostics["fallback_reason"] == "size"


def test_subset_of_source_returns_only_accepted_passage(monkeypatch):
    def choose(_, payload):
        labels = {
            f"c{i}": "direct" if i == 1 else "irrelevant"
            for i in range(len(payload["state"]["candidates"]))
        }
        return jev_response(payload, labels)

    original = source("A complete sentence describes project storage. " * 30)
    model, requests = session(monkeypatch, choose)
    selected = relevance.select([original], "Storage?", model)
    assert len(selected) == 1
    view = selected[0]
    assert view["body"] == requests[0][1]["state"]["candidates"][1]["text"]
    assert view["body"] == original["body"][view["passage"]["start"] : view["passage"]["end"]]
    assert view["evidence_truncated"] and view["sources"] == original["sources"]


def test_confidence_fallback_discards_split_representation_not_originals(monkeypatch):
    originals = [source("A complete sentence about storage. " * 25), item()]
    model, requests = session(monkeypatch, lambda _, p: jev_response(p, answerable=0.5))
    diagnostics = {}
    selected = relevance.select(originals, "Storage?", model, diagnostics=diagnostics)
    candidates = json.loads(requests[1][1]["messages"][1]["content"])["candidates"]
    assert [c["text"] for c in candidates] == [i["body"] for i in originals]
    assert all("passage" not in view for view in selected)
    assert diagnostics["route"] == "chat" and diagnostics["fallback_reason"] == "confidence"


def test_project_context_disambiguates_same_body_without_creating_support(monkeypatch):
    def choose(_, payload):
        records = payload["state"]["candidates"]
        assert all("evidence_context" in record and "title" in record for record in records)
        assert (
            "cannot supply facts" in payload["questions"]["c0"]["instructions"]["source_boundary"]
        )
        labels = {
            f"c{i}": "direct" if c["evidence_context"]["projects"] == ["Alpha"] else "irrelevant"
            for i, c in enumerate(records)
        }
        return jev_response(payload, labels)

    body = "The database is PostgreSQL."
    originals = [
        item(body=body, evidence_context={"projects": [name]}) for name in ("Beta", "Alpha")
    ]
    model, requests = session(monkeypatch, choose)
    selected = relevance.select(originals, "Alpha database?", model)
    assert len(requests) == 1
    assert len(selected) == 1 and selected[0]["evidence_context"]["projects"] == ["Alpha"]
    assert selected[0]["body"] == body


@pytest.mark.parametrize("field", ["title", "evidence_context"])
def test_full_serialized_context_budget_triggers_fallback(monkeypatch, field):
    # Raw text fits; escaped metadata and JSON framing do not.
    changes = {field: '"' * 6100 if field == "title" else {"projects": ['"' * 6100]}}
    model, requests = session(monkeypatch)
    diagnostics = {}
    relevance.select([item(**changes)], "database?", model, diagnostics=diagnostics)
    assert all(url != TYPESAFE_ENDPOINT for url, _ in requests)
    assert diagnostics["fallback_reason"] == "size"
    candidate = json.loads(requests[0][1]["messages"][1]["content"])["candidates"][0]
    assert candidate[field] == changes[field]


def test_chat_and_conflicts_keep_context_but_cannot_quote_it(monkeypatch):
    records = [item(body=f"Fact {i}.", evidence_context={"projects": ["Alpha"]}) for i in range(2)]
    model, requests = session(monkeypatch, lambda _, p: jev_response(p, answerable=0.5))
    relevance.select(records, "Alpha facts?", model)
    for _, payload in requests[1:]:
        candidates = json.loads(payload["messages"][1]["content"])["candidates"]
        assert all(
            c["title"] == "Storage" and c["evidence_context"] == {"projects": ["Alpha"]}
            for c in candidates
        )
    from test_relevance import Assessor

    assessor = Assessor(
        [{"candidate": 0, "relevance": "direct"}],
        answer_quotes=[{"candidate": 0, "quote": "Alpha"}],
    )
    with pytest.raises(relevance.ModelError, match="incomplete or invalid"):
        relevance.select(records[:1], "Which project?", assessor)


def test_diagnostics_distinguish_confident_abstention_empty_and_provider_fallback(monkeypatch):
    model, requests = session(
        monkeypatch, lambda _, p: jev_response(p, {"c0": "irrelevant"}, answerable=0.05)
    )
    diagnostics = {}
    assert relevance.select([], "database?", model, diagnostics=diagnostics) == []
    assert diagnostics["route"] == "none" and diagnostics["fallback_reason"] is None
    assert relevance.select([item()], "database?", model, diagnostics=diagnostics) == []
    assert len(requests) == 1
    assert diagnostics["route"] == "jev" and diagnostics["fallback_reason"] is None
    assert diagnostics["selected_units"] == 0

    def malformed(payload):
        return {}

    model.evaluate_typesafe = malformed
    relevance.select([item()], "database?", model, diagnostics=diagnostics)
    assert diagnostics["route"] == "chat" and diagnostics["fallback_reason"] == "provider"


@pytest.mark.parametrize(
    ("labels", "answerable", "stale", "reason"),
    [
        ({"c0": "conflicting"}, 0.95, False, "conflict"),
        ({"c0": "direct"}, 0.05, False, "inconsistent"),
        ({"c0": "direct"}, 0.95, True, "temporal"),
    ],
)
def test_diagnostics_do_not_call_confident_conflicts_or_temporal_rejection_uncertain(
    monkeypatch, labels, answerable, stale, reason
):
    model, _ = session(monkeypatch, lambda _, p: jev_response(p, labels, answerable=answerable))
    diagnostics = {}
    relevance.select([item(stale=stale)], "Current database?", model, diagnostics=diagnostics)
    assert diagnostics["route"] == "chat" and diagnostics["fallback_reason"] == reason
