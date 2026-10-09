"""Unit tests for the retrieval-evaluation metrics. No database and no network.

The metric functions are pure, so every expectation here is computed by hand.
"""

import json
import math

import pytest

from space.evaluation import (
    Candidate,
    CandidateSet,
    Claim,
    Corpus,
    CorpusError,
    Embeddings,
    EvaluationError,
    Query,
    QueryOutcome,
    Strategy,
    UnknownStrategy,
    aggregate,
    average_precision_at_k,
    bake_embeddings,
    claim_uuid,
    is_under_returned,
    load_corpus,
    ndcg_at_k,
    percentile,
    recall_at_k,
    reciprocal_rank_at_k,
    registered,
    shipped_order,
    text_key,
)


def outcome(kind="keyword", relevant=("c001",), returned=("c001",), **kwargs):
    defaults = {
        "query": "query",
        "kind": kind,
        "relevant": relevant,
        "returned": returned,
        "candidates": 10,
        "search_returned": 4,
        "search_limit": 4,
        "available": 100,
        "latency_ms": 1.0,
    }
    return QueryOutcome(**{**defaults, **kwargs})


def test_recall_counts_relevant_items_inside_the_cutoff():
    returned, relevant = ["a", "b", "c"], ["b", "d"]
    assert recall_at_k(returned, relevant, 1) == 0.0
    assert recall_at_k(returned, relevant, 2) == 0.5
    assert recall_at_k(returned, relevant, 3) == 0.5
    assert recall_at_k([], [], 10) == 0.0


def test_recall_ignores_a_duplicated_result():
    assert recall_at_k(["a", "a", "b"], ["a", "b"], 3) == 1.0


def test_reciprocal_rank_uses_the_first_relevant_position():
    assert reciprocal_rank_at_k(["x", "a", "b"], ["a", "b"], 10) == 0.5
    assert reciprocal_rank_at_k(["x", "a"], ["a"], 1) == 0.0
    assert reciprocal_rank_at_k(["x", "y"], ["a"], 10) == 0.0


def test_ndcg_matches_a_hand_computed_case():
    # Relevant hits at ranks 1 and 3: 1/log2(2) + 1/log2(4) = 1.5.
    # Ideal ranking of two hits: 1/log2(2) + 1/log2(3) = 1.630929...
    expected = 1.5 / (1 + 1 / math.log2(3))
    assert ndcg_at_k(["a", "x", "b"], ["a", "b"], 10) == pytest.approx(expected)
    assert ndcg_at_k(["x", "y"], ["a"], 10) == 0.0
    assert ndcg_at_k(["a"], [], 10) == 0.0


def test_ndcg_is_one_for_a_perfect_ranking():
    assert ndcg_at_k(["a", "b", "c"], ["a", "b"], 10) == pytest.approx(1.0)


def test_average_precision_matches_a_hand_computed_case():
    # Relevant at ranks 1 and 3: (1/1 + 2/3) / 2.
    assert average_precision_at_k(["a", "x", "b"], ["a", "b"], 10) == pytest.approx(5 / 6)
    assert average_precision_at_k(["x", "a"], ["a"], 10) == pytest.approx(0.5)
    assert average_precision_at_k(["a"], [], 10) == 0.0


def test_percentile_uses_the_nearest_rank():
    values = [1.0, 2.0, 3.0, 4.0]
    assert percentile(values, 50) == 2.0
    assert percentile(values, 95) == 4.0
    assert percentile(values, 100) == 4.0
    assert percentile([], 50) == 0.0
    assert percentile([7.0], 95) == 7.0


def test_under_return_needs_a_short_result_and_available_rows():
    assert is_under_returned(39, 40, 100) is True
    assert is_under_returned(40, 40, 100) is False
    assert is_under_returned(0, 40, 12) is False
    assert is_under_returned(5, 10, 10) is True


def test_aggregate_separates_labelled_from_negative_queries():
    outcomes = [
        outcome(relevant=("c001",), returned=("c001",), latency_ms=1.0),
        outcome(relevant=("c002",), returned=("c003",), latency_ms=3.0),
        outcome(kind="negative", relevant=(), returned=(), latency_ms=2.0),
    ]
    metrics = aggregate(outcomes, limit=10, embed_calls=0)
    assert metrics["queries"] == 3
    assert metrics["positive_queries"] == 2
    assert metrics["negative_queries"] == 1
    assert metrics["recall@1"] == 0.5
    assert metrics["mrr@10"] == 0.5
    assert metrics["negative_false_positive_rate"] == 0.0
    assert metrics["empty_result_rate"] == 0.0
    assert metrics["latency_p50_ms"] == 2.0
    assert metrics["latency_p95_ms"] == 3.0
    assert metrics["candidates_read"] == 30
    assert metrics["embed_calls"] == 0


def test_aggregate_counts_a_labelled_query_that_returned_nothing():
    metrics = aggregate([outcome(relevant=("c001",), returned=())], limit=10)
    assert metrics["empty_result_rate"] == 1.0
    assert metrics["recall@10"] == 0.0


def test_aggregate_flags_a_negative_control_that_returned_items():
    metrics = aggregate([outcome(kind="negative", relevant=(), returned=("c001",))], limit=10)
    assert metrics["negative_false_positive_rate"] == 1.0
    assert metrics["recall@10"] == 0.0


def test_aggregate_measures_under_return_at_the_search_stage_and_at_the_limit():
    short = outcome(search_returned=12, search_limit=40, available=150, returned=("c001",))
    metrics = aggregate([short], limit=10)
    assert metrics["under_return_rate"] == 1.0
    assert metrics["under_return_rate_at_limit"] == 1.0
    full = aggregate([outcome(search_returned=40, search_limit=40, available=150)], limit=10)
    assert full["under_return_rate"] == 0.0
    assert full["under_return_rate_at_limit"] == 1.0
    filled = aggregate([outcome(returned=tuple(f"c{index:03d}" for index in range(10)))], limit=10)
    assert filled["under_return_rate_at_limit"] == 0.0


def test_aggregate_reports_missed_queries_for_diagnosis():
    metrics = aggregate([outcome(query="where is the queue?", relevant=("c001", "c002"))], limit=10)
    assert metrics["missed_queries"] == [
        {
            "query": "where is the queue?",
            "kind": "keyword",
            "relevant": ["c001", "c002"],
            "returned": ["c001"],
        }
    ]


def test_aggregate_splits_metrics_by_query_kind():
    outcomes = [
        outcome(kind="paraphrase", relevant=("c001",), returned=("c001",)),
        outcome(kind="multi-hop", relevant=("c001", "c002"), returned=("c001",)),
    ]
    metrics = aggregate(outcomes, limit=10)
    assert metrics["per_kind"]["paraphrase"]["queries"] == 1
    assert metrics["per_kind"]["paraphrase"]["recall@10"] == 1.0
    assert metrics["per_kind"]["multi-hop"]["recall@10"] == 0.5


def test_shipped_order_keeps_search_order_then_expansion_without_duplicates():
    candidates = CandidateSet(
        query="q",
        vector=(0.0,),
        hits=(
            Candidate(claim_id="a", memory_id="m", entry_index=0, score=0.9),
            Candidate(claim_id="b", memory_id="m", entry_index=1, score=0.8),
        ),
        expanded=(
            Candidate(claim_id="b", memory_id="m", entry_index=1, score=None),
            Candidate(claim_id="c", memory_id="m", entry_index=2, score=None),
        ),
    )
    assert shipped_order(candidates) == ["a", "b", "c"]


def test_a_rerank_hook_replaces_the_shipped_order():
    hook = lambda candidates: ["b", "a"]  # noqa: E731 - a strategy hook, not a definition
    strategy = Strategy(name="hook", description="test", rerank=hook)
    candidates = CandidateSet(
        query="q",
        vector=(0.0,),
        hits=(Candidate(claim_id="a", memory_id="m", entry_index=0, score=1.0),),
        expanded=(Candidate(claim_id="b", memory_id="m", entry_index=1, score=None),),
    )
    assert strategy.order(candidates) == ["b", "a"]


def test_strategy_candidate_limit_follows_the_product_multiplier():
    assert Strategy(name="baseline", description="x").candidates_for(10) == 40
    assert Strategy(name="fixed", description="x", search_limit=7).candidates_for(10) == 7


def test_the_registry_ships_the_baseline_and_rejects_unknown_names():
    names = registered()
    assert "baseline" in names
    assert names["baseline"].config.ef_search is None
    assert names["baseline"].config.iterative_scan == "off"
    with pytest.raises(UnknownStrategy):
        registered("not-a-strategy")


def test_a_json_overlay_adds_a_strategy_without_touching_metric_code(tmp_path, monkeypatch):
    import space.evaluation.strategies as strategies

    overlay = tmp_path / "strategies.json"
    overlay.write_text(
        json.dumps(
            {
                "strategies": {
                    "tuned-from-json": {
                        "description": "from json",
                        "config": {"ef_search": 120, "iterative_scan": "strict_order"},
                        "expand": False,
                    }
                }
            }
        )
    )
    monkeypatch.setattr(strategies, "STRATEGIES_PATH", overlay)
    try:
        added = strategies.load_strategy_overlay(overlay)
        assert added == 1
        tuned = registered("tuned-from-json")["tuned-from-json"]
        assert tuned.config.ef_search == 120
        assert tuned.config.iterative_scan == "strict_order"
        assert tuned.expand is False
        assert strategies.load_strategy_overlay(overlay) == 0
    finally:
        strategies.STRATEGIES.pop("tuned-from-json", None)


def test_a_json_overlay_can_name_a_rerank_hook(tmp_path):
    import space.evaluation.strategies as strategies

    overlay = tmp_path / "strategies.json"
    overlay.write_text(
        json.dumps(
            {
                "strategies": {
                    "hooked-from-json": {
                        "description": "from json",
                        "rerank": "space.evaluation:shipped_order",
                    }
                }
            }
        )
    )
    try:
        assert strategies.load_strategy_overlay(overlay) == 1
        strategy = registered("hooked-from-json")["hooked-from-json"]
        assert strategy.rerank is strategies.shipped_order
        assert strategy.order(CandidateSet(query="q", vector=(0.0,), hits=(), expanded=())) == []
    finally:
        strategies.STRATEGIES.pop("hooked-from-json", None)
    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps({"strategies": {"x": {"rerank": "space.nope:missing"}}}))
    with pytest.raises(EvaluationError, match="Cannot resolve rerank hook"):
        strategies.load_strategy_overlay(broken)


def test_claim_uuids_are_stable_and_distinct():
    assert claim_uuid("c001") == claim_uuid("c001")
    assert claim_uuid("c001") != claim_uuid("c002")


def test_load_corpus_validates_word_counts_and_ground_truth(tmp_path):
    good = {
        "claims": [
            {
                "id": "c001",
                "text": "one two three four five",
                "topics": ["t"],
                "source": "s",
                "memory_type": "factual",
            }
        ],
        "queries": [{"query": "q", "relevant": ["c001"], "kind": "keyword"}],
    }
    path = tmp_path / "corpus.json"
    path.write_text(json.dumps(good))
    corpus = load_corpus(path)
    assert corpus.claim_ids() == {"c001"}
    assert corpus.kind_counts()["keyword"] == 1
    assert corpus.texts() == ["one two three four five", "q"]

    good["claims"][0]["text"] = "too short"
    path.write_text(json.dumps(good))
    with pytest.raises(CorpusError, match="words"):
        load_corpus(path)

    good["claims"][0]["text"] = "one two three four five"
    good["queries"][0]["relevant"] = ["c999"]
    path.write_text(json.dumps(good))
    with pytest.raises(CorpusError, match="unknown claims"):
        load_corpus(path)

    good["queries"][0] = {"query": "q", "relevant": ["c001"], "kind": "negative"}
    path.write_text(json.dumps(good))
    with pytest.raises(CorpusError, match="Negative query"):
        load_corpus(path)

    with pytest.raises(CorpusError, match="not found"):
        load_corpus(tmp_path / "missing.json")


class StubModels:
    """A MemoryModels stand-in that embeds without a provider."""

    def __init__(self, model="stub-model", enabled=True):
        self.embedding_model = model
        self.enabled = enabled
        self.calls = 0
        self.seen = []

    def info(self):
        return {"provider": "stub", "embedding_model": self.embedding_model}

    def session(self, authorize):
        return self

    def embed(self, texts):
        self.calls += 1
        self.seen.extend(texts)
        return [
            [1.0 if index == position % 1536 else 0.0 for index in range(1536)]
            for position, _ in enumerate(texts)
        ]


def test_bake_embeddings_caches_by_text_and_never_reembeds(tmp_path):
    cache = tmp_path / "embeddings.json"
    models = StubModels()
    first = bake_embeddings(["alpha", "beta"], path=cache, models=models)
    assert first.embedded == 2
    assert models.calls == 1
    assert set(first.vectors) == {text_key("alpha"), text_key("beta")}
    second_models = StubModels()
    second = bake_embeddings(["alpha", "beta", "gamma"], path=cache, models=second_models)
    assert second.embedded == 1
    assert second_models.seen == ["gamma"]
    assert len(second.vectors) == 3


def test_bake_embeddings_discards_a_cache_from_another_model(tmp_path):
    cache = tmp_path / "embeddings.json"
    bake_embeddings(["alpha"], path=cache, models=StubModels(model="first-model"))
    other = StubModels(model="second-model")
    embeddings = bake_embeddings(["alpha"], path=cache, models=other)
    assert other.seen == ["alpha"]
    assert embeddings.model == "second-model"


def test_bake_embeddings_refuses_to_run_without_a_provider(tmp_path):
    with pytest.raises(EvaluationError, match="not configured"):
        bake_embeddings(["alpha"], path=tmp_path / "e.json", models=StubModels(enabled=False))


def test_embeddings_raise_on_a_text_that_was_never_baked():
    embeddings = Embeddings(
        provider="stub", model="m", dimension=1536, vectors={text_key("a"): (0.0,)}
    )
    assert embeddings.vector("a") == (0.0,)
    with pytest.raises(EvaluationError, match="No baked embedding"):
        embeddings.vector("b")


def test_the_shipped_corpus_satisfies_the_contract_shape():
    corpus = load_corpus()
    assert 120 <= len(corpus.claims) <= 200
    assert len(corpus.queries) >= 60
    counts = corpus.kind_counts()
    assert counts["paraphrase"] >= 15
    assert counts["keyword"] >= 10
    assert counts["multi-hop"] >= 8
    assert counts["partial"] >= 8
    assert counts["negative"] >= 8
    for claim in corpus.claims:
        assert 5 <= len(claim.text.split()) <= 60
    for query in corpus.queries:
        if query.kind == "negative":
            assert query.relevant == ()


def test_documented_corpus_claims_are_typed_records():
    corpus = Corpus(
        claims=(
            Claim(
                id="c001",
                text="a b c d e",
                topics=("t",),
                source="s",
                memory_type="factual",
            ),
        ),
        queries=(Query(query="q", relevant=("c001",), kind="keyword"),),
        path=None,
    )
    assert corpus.claim_ids() == {"c001"}
