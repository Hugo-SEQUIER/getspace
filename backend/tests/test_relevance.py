"""Evidence-selection protocol tests; these do not estimate model quality."""

import json

import pytest

from space import relevance
from space.model_provider import ModelError


class Assessor:
    def __init__(
        self,
        labels,
        temporal_intent="unspecified",
        conflicts=(),
        answer_quotes=None,
        answer="fixture answer",
    ):
        self.labels = labels
        self.temporal_intent = temporal_intent
        self.conflicts = list(conflicts)
        self.answer_quotes = answer_quotes
        self.answer = answer
        self.calls = []

    def invoke(self, input, response_format, **kwargs):
        assert kwargs["temperature"] == 0
        assert kwargs["instructions"] in {relevance.PROMPT, relevance.CONFLICT_PROMPT}
        self.calls.append(json.loads(input[input.index('{"query":') :]))
        if response_format is relevance.EvidenceConflicts:
            return {"conflicts": self.conflicts}
        return {
            "answer": self.answer,
            "assessments": self.labels,
            "answer_quotes": self.answer_quotes
            if self.answer_quotes is not None
            else [
                {"candidate": i, "quote": c["text"][:100]}
                for i, c in enumerate(self.calls[-1]["candidates"])
            ],
            "temporal_intent": self.temporal_intent,
        }


def item(**changes):
    return {
        "id": "memory",
        "revision": 3,
        "entry_id": "claim",
        "title": "Storage",
        "body": "The service stores documents in PostgreSQL.",
        "updated_at": "2026-09-14T00:00:00Z",
        "stale": False,
        "sources": [{"resource_id": "source", "revision": 2}],
        **changes,
    }


def test_assessment_cannot_rewrite_selected_text_or_citations():
    original = item()
    model = Assessor([{"candidate": 0, "relevance": "direct"}])
    result = relevance.select([original], "Where is durable data kept?", model)
    assert result[0]["body"] == original["body"]
    assert result[0]["sources"] == original["sources"]
    assert result[0]["revision"] == 3 and result[0]["entry_id"] == "claim"
    assert len(model.calls) == 1 and "score" not in model.calls[0]["candidates"][0]


def test_all_candidates_can_be_rejected_even_with_perfect_similarity():
    model = Assessor([{"candidate": 0, "relevance": "irrelevant"}])
    assert relevance.select([item(score=1.0)], "What is the CEO's birthday?", model) == []


@pytest.mark.parametrize(
    "labels",
    [
        [],
        [{"candidate": 1, "relevance": "direct"}],
        [{"candidate": 0, "relevance": "direct"}] * 2,
        [{"candidate": 0, "relevance": "maybe"}],
        [{"candidate": 0, "relevance": "direct", "text": "invented"}],
        [{"candidate": True, "relevance": "direct"}],
    ],
)
def test_invalid_assessment_is_an_error_instead_of_abstention_or_unfiltered_results(labels):
    with pytest.raises(ModelError, match="incomplete or invalid"):
        relevance.select([item()], "storage", Assessor(labels))


def test_stale_cannot_be_presented_as_direct_even_when_model_says_so():
    for label in ("direct", "conflicting", "irrelevant"):
        assert (
            relevance.select(
                [item(stale=True)],
                "Current storage?",
                Assessor(
                    [
                        {"candidate": 0, "relevance": label},
                    ]
                ),
            )
            == []
        )
    historical = relevance.select(
        [item(stale=True)],
        "Previous storage?",
        Assessor(
            [
                {"candidate": 0, "relevance": "historical"},
            ],
            temporal_intent="historical",
        ),
    )
    assert historical[0]["stale"] and historical[0]["relevance"] == "historical"


def test_conflicting_evidence_retains_both_records_and_original_citations():
    records = [item(), item(id="other", body="The service uses SQLite.")]
    selected = relevance.select(
        records,
        "Which database?",
        Assessor(
            [
                {"candidate": 1, "relevance": "conflicting"},
                {"candidate": 0, "relevance": "conflicting"},
            ]
        ),
    )
    assert [i["body"] for i in selected] == [i["body"] for i in records]
    assert all(i["relevance"] == "conflicting" for i in selected)


def test_only_assessed_excerpt_can_be_returned_and_budget_is_enforced():
    model = Assessor([{"candidate": 0, "relevance": "direct"}])
    selected = relevance.select([item(body="x " * 5000)], "storage", model)
    assert selected[0]["body"] == model.calls[0]["candidates"][0]["text"]
    assert len(selected[0]["body"]) <= relevance.MAX_EVIDENCE_CHARS
    assert selected[0]["evidence_truncated"]
    with pytest.raises(ModelError, match="candidate budget"):
        relevance.select([item()] * (relevance.MAX_EVIDENCE + 1), "storage", model)
    assert len(model.calls) == 1


def test_empty_candidates_make_no_model_call():
    model = Assessor([])
    assert relevance.select([], "storage", model) == []
    assert model.calls == []


def test_direct_label_without_an_answer_quote_does_not_establish_support():
    model = Assessor([{"candidate": 0, "relevance": "direct"}], answer_quotes=[])
    assert relevance.select([item()], "Which PostgreSQL version?", model) == []


def test_explicit_no_answer_overrides_a_direct_label_and_a_matching_quote():
    model = Assessor([{"candidate": 0, "relevance": "direct"}], answer=None)
    assert relevance.select([item()], "Which PostgreSQL version?", model) == []


def test_historical_query_keeps_supported_stale_evidence_even_if_model_labels_it_direct():
    model = Assessor([{"candidate": 0, "relevance": "direct"}], temporal_intent="historical")
    selected = relevance.select([item(stale=True)], "Previous storage?", model)
    assert selected[0]["relevance"] == "historical" and selected[0]["stale"]


@pytest.mark.parametrize(
    "answer",
    [
        {"candidate": 0, "quote": "PostgreSQL 18"},
        {"candidate": 1, "quote": "PostgreSQL"},
        {"candidate": 0, "quote": " "},
    ],
)
def test_invented_or_foreign_answer_quotes_fail_instead_of_supporting_a_result(answer):
    model = Assessor([{"candidate": 0, "relevance": "direct"}], answer_quotes=[answer])
    with pytest.raises(ModelError, match="incomplete or invalid"):
        relevance.select([item()], "Which PostgreSQL version?", model)


def test_historical_label_does_not_bypass_current_query_abstention():
    model = Assessor([{"candidate": 0, "relevance": "historical"}], temporal_intent="current")
    assert relevance.select([item(stale=True)], "Where is storage now?", model) == []


def test_explicit_conflict_pairs_mark_both_sides_even_when_labels_say_direct():
    model = Assessor(
        [
            {"candidate": 0, "relevance": "direct"},
            {"candidate": 1, "relevance": "direct"},
        ],
        conflicts=[{"left": 0, "right": 1}],
    )
    selected = relevance.select([item(), item(body="Storage uses SQLite.")], "Storage?", model)
    assert len(selected) == 2 and all(i["relevance"] == "conflicting" for i in selected)


@pytest.mark.parametrize("pair", [{"left": 0, "right": 2}, {"left": 0, "right": 0}])
def test_invalid_conflict_references_fail_closed(pair):
    with pytest.raises(ModelError, match="incomplete or invalid"):
        relevance.select(
            [item(), item(body="Storage uses SQLite.")],
            "Storage?",
            Assessor(
                [
                    {"candidate": 0, "relevance": "direct"},
                    {"candidate": 1, "relevance": "direct"},
                ],
                conflicts=[pair],
            ),
        )
