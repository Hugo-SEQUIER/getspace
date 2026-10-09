"""Unit tests for the ranking primitives. No database and no network."""

import itertools
from datetime import UTC, datetime, timedelta, timezone

import pytest

from space.ranking import (
    apply_prior,
    combine,
    dedupe_by_memory,
    mmr_select,
    recency_weight,
    reciprocal_rank_fusion,
)

NOW = datetime(2026, 9, 12, 12, 0, 0, tzinfo=UTC)


def ranked(pairs):
    """Build the expected ordered mapping from (id, score) pairs."""
    return {key: pytest.approx(score) for key, score in pairs}


def test_reciprocal_rank_fusion_matches_hand_computed_scores():
    # k=60: a=1/61, b=1/62+1/62, c=1/63+1/61, d=1/63
    fused = reciprocal_rank_fusion([["a", "b", "c"], ["c", "b", "d"]])
    assert fused == ranked(
        [
            ("c", 0.032266458495966696),
            ("b", 0.03225806451612903),
            ("a", 0.01639344262295082),
            ("d", 0.015873015873015872),
        ]
    )
    assert list(fused) == ["c", "b", "a", "d"]


def test_reciprocal_rank_fusion_uses_k_and_rank_from_one():
    assert reciprocal_rank_fusion([["only"]]) == {"only": 1 / 61}
    assert reciprocal_rank_fusion([["a", "b"]], k=1) == ranked([("a", 0.5), ("b", 1 / 3)])


def test_reciprocal_rank_fusion_adds_ids_appearing_in_several_lists():
    fused = reciprocal_rank_fusion([["b", "a"], ["a", "b"]])
    assert fused == {"a": pytest.approx(1 / 61 + 1 / 62), "b": pytest.approx(1 / 61 + 1 / 62)}


def test_reciprocal_rank_fusion_handles_empty_and_single_lists():
    assert reciprocal_rank_fusion([]) == {}
    assert reciprocal_rank_fusion([[], []]) == {}
    assert reciprocal_rank_fusion([["a"], []]) == {"a": 1 / 61}


def test_reciprocal_rank_fusion_keeps_the_best_rank_within_one_list():
    assert reciprocal_rank_fusion([["a", "a"]]) == {"a": 1 / 61}


def test_reciprocal_rank_fusion_is_deterministic_under_shuffling():
    lists = [["a", "b", "c"], ["c", "b", "d"], ["d", "a"]]
    orderings = list(itertools.permutations(lists))
    assert len(orderings) == 6
    for order in orderings:
        # c and d tie exactly, so the id breaks the tie whatever the list order.
        assert list(reciprocal_rank_fusion(list(order))) == ["a", "c", "d", "b"]


def test_reciprocal_rank_fusion_tie_break_does_not_depend_on_list_order():
    first = reciprocal_rank_fusion([["b", "a"], ["a", "b"]])
    second = reciprocal_rank_fusion([["a", "b"], ["b", "a"]])
    assert first == second
    assert list(first) == ["a", "b"]


def test_reciprocal_rank_fusion_does_not_mutate_its_input():
    lists = [["a", "b"], ["b", "a"]]
    copy = [list(item) for item in lists]
    reciprocal_rank_fusion(lists)
    assert lists == copy


def test_recency_weight_is_one_at_age_zero_and_a_half_at_one_half_life():
    assert recency_weight(NOW, NOW, half_life_days=10) == 1.0
    assert recency_weight(NOW - timedelta(days=10), NOW, half_life_days=10) == 0.5
    assert recency_weight(NOW - timedelta(days=20), NOW, half_life_days=10) == 0.25
    assert recency_weight(NOW - timedelta(days=2.5), NOW, half_life_days=5) == pytest.approx(
        0.7071067811865476
    )


def test_recency_weight_clamps_a_future_timestamp():
    assert recency_weight(NOW + timedelta(days=3), NOW, half_life_days=10) == 1.0
    assert recency_weight(NOW + timedelta(seconds=1), NOW, half_life_days=0.001) == 1.0


def test_recency_weight_reads_naive_timestamps_as_utc():
    naive = datetime(2026, 9, 12, 12, 0, 0)
    assert recency_weight(naive, NOW, half_life_days=10) == 1.0
    assert recency_weight(NOW - timedelta(days=10), naive, half_life_days=10) == 0.5


def test_recency_weight_reads_iso_strings_in_any_offset():
    assert recency_weight("2026-09-12T12:00:00Z", NOW, half_life_days=10) == 1.0
    assert recency_weight("2026-09-12T12:00:00", NOW, half_life_days=10) == 1.0
    assert recency_weight("2026-09-12T14:00:00+02:00", NOW, half_life_days=10) == 1.0
    assert recency_weight("2026-09-02T12:00:00Z", NOW, half_life_days=10) == 0.5


def test_recency_weight_stays_positive_for_a_very_old_timestamp():
    ancient = NOW - timedelta(days=365000)
    assert 0.0 < recency_weight(ancient, NOW, half_life_days=30) < 1e-300


def test_naive_and_aware_timestamps_can_be_mixed():
    naive_now = datetime(2026, 9, 12, 12, 0, 0)
    assert recency_weight(naive_now, NOW, half_life_days=10) == 1.0
    offset_now = datetime(2026, 9, 12, 14, 0, 0, tzinfo=timezone(timedelta(hours=2)))
    assert recency_weight(NOW - timedelta(days=10), offset_now, half_life_days=10) == 0.5


def test_recency_weight_rejects_a_non_positive_half_life():
    with pytest.raises(ValueError):
        recency_weight(NOW, NOW, half_life_days=0)
    with pytest.raises(ValueError):
        recency_weight(NOW, NOW, half_life_days=-1.0)


def test_recency_weight_rejects_an_unparsable_string():
    with pytest.raises(ValueError):
        recency_weight("yesterday", NOW, half_life_days=1)


def test_apply_prior_with_zero_weight_returns_the_scores_unchanged():
    scores = {"a": 3.0, "b": 1.5}
    assert apply_prior(scores, {"a": 0.0, "b": 0.0}, weight=0.0, floor=0.9) == scores


def test_apply_prior_blends_the_prior_and_orders_by_score():
    blended = apply_prior(
        {"a": 10.0, "b": 10.0, "c": 10.0}, {"a": 0.5, "b": 0.0}, weight=0.5, floor=0.05
    )
    assert blended == ranked([("a", 7.5), ("b", 5.0), ("c", 10.0)])
    assert blended == ranked([("c", 10.0), ("a", 7.5), ("b", 5.0)])


def test_apply_prior_floors_the_multiplier():
    assert apply_prior({"a": 10.0}, {"a": 0.0}, weight=1.0, floor=0.05) == {"a": 0.5}
    assert apply_prior({"a": 10.0}, {"a": 0.9}, weight=1.0, floor=0.05) == {"a": pytest.approx(9.0)}


def test_apply_prior_treats_a_missing_weight_as_one():
    assert apply_prior({"a": 4.0, "b": 2.0}, {"b": 0.0}, weight=0.5, floor=0.5) == ranked(
        [("a", 4.0), ("b", 1.0)]
    )
    assert apply_prior({"a": 4.0}, {}, weight=1.0, floor=0.01) == {"a": 4.0}


def test_apply_prior_boundaries_of_weight_and_floor():
    assert apply_prior({"a": 2.0}, {"a": 0.0}, weight=1.0, floor=1.0) == {"a": 2.0}
    assert apply_prior({"a": 2.0}, {}, weight=1.0, floor=1e-9) == {"a": 2.0}
    for weight in (-0.1, 1.1):
        with pytest.raises(ValueError):
            apply_prior({"a": 1.0}, {}, weight=weight, floor=0.5)
    for floor in (0.0, -0.5, 1.5):
        with pytest.raises(ValueError):
            apply_prior({"a": 1.0}, {}, weight=0.5, floor=floor)


def test_apply_prior_does_not_mutate_its_inputs_and_is_order_independent():
    scores = {"a": 1.0, "b": 2.0}
    weights = {"a": 0.1, "b": 0.2}
    result = apply_prior(scores, weights, weight=0.5, floor=0.25)
    assert scores == {"a": 1.0, "b": 2.0}
    assert weights == {"a": 0.1, "b": 0.2}
    reversed_result = apply_prior(
        {"b": 2.0, "a": 1.0}, {"b": 0.2, "a": 0.1}, weight=0.5, floor=0.25
    )
    assert list(result) == list(reversed_result) == ["b", "a"]


def test_mmr_select_with_zero_diversity_follows_score_order():
    vectors = [[1.0, 0.0], [0.0, 1.0], [0.5, 0.5]]
    assert mmr_select(vectors, [0.1, 0.9, 0.5], 2, diversity=0.0) == [1, 2]
    assert mmr_select(vectors, [0.1, 0.9, 0.5], 3, diversity=0.0) == [1, 2, 0]


def test_mmr_select_breaks_score_ties_by_index():
    vectors = [[1.0, 0.0], [0.0, 1.0]]
    assert mmr_select(vectors, [0.5, 0.5], 2, diversity=0.0) == [0, 1]


def test_mmr_select_handles_identical_vectors_by_falling_back_to_score():
    vectors = [[1.0, 0.0], [2.0, 0.0], [3.0, 0.0]]
    assert mmr_select(vectors, [0.3, 0.9, 0.6], 3, diversity=1.0) == [1, 2, 0]


def test_mmr_select_prefers_an_orthogonal_vector_over_a_similar_one():
    vectors = [[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]]
    # After index 0: index 1 is identical (similarity 1.0), index 2 is orthogonal (0.0).
    assert mmr_select(vectors, [0.9, 0.8, 0.5], 3, diversity=1.0) == [0, 2, 1]
    assert mmr_select(vectors, [0.9, 0.8, 0.5], 3, diversity=0.5) == [0, 2, 1]


def test_mmr_select_balances_relevance_and_similarity():
    vectors = [[1.0, 0.0], [2.0, 0.0], [0.0, 1.0]]
    # Index 1: 0.5 * 0.9 - 0.5 * 1.0 = -0.05. Index 2: 0.5 * 0.5 - 0.5 * 0.0 = 0.25.
    assert mmr_select(vectors, [1.0, 0.9, 0.5], 2, diversity=0.5) == [0, 2]


def test_mmr_select_handles_limits_and_empty_input():
    vectors = [[1.0, 0.0], [0.0, 1.0]]
    assert mmr_select(vectors, [0.2, 0.8], 5, diversity=0.5) == [1, 0]
    assert mmr_select(vectors, [0.2, 0.8], 2, diversity=0.5) == [1, 0]
    assert mmr_select(vectors, [0.2, 0.8], 0, diversity=0.5) == []
    assert mmr_select(vectors, [0.2, 0.8], -3, diversity=0.5) == []
    assert mmr_select([], [], 3, diversity=0.5) == []


def test_mmr_select_treats_a_zero_vector_as_unsimilar():
    vectors = [[0.0, 0.0], [1.0, 0.0]]
    assert mmr_select(vectors, [0.4, 0.6], 2, diversity=1.0) == [1, 0]


def test_mmr_select_rejects_bad_arguments():
    vectors = [[1.0, 0.0], [0.0, 1.0]]
    for diversity in (-0.01, 1.01):
        with pytest.raises(ValueError):
            mmr_select(vectors, [0.1, 0.2], 1, diversity=diversity)
    with pytest.raises(ValueError):
        mmr_select(vectors, [0.1], 1, diversity=0.0)


EDGES = [
    {"memory_id": "m1", "id": "a"},
    {"memory_id": "m1", "id": "b"},
    {"memory_id": "m2", "id": "c"},
    {"memory_id": "m2", "id": "d"},
    {"memory_id": "m2", "id": "e"},
    {"id": "f"},
]


def test_dedupe_by_memory_keeps_the_first_item_per_memory():
    assert dedupe_by_memory(EDGES, 10) == [EDGES[0], EDGES[2], EDGES[5]]


def test_dedupe_by_memory_stops_at_the_limit():
    assert dedupe_by_memory(EDGES, 2) == [EDGES[0], EDGES[2]]
    assert dedupe_by_memory(EDGES, 1) == [EDGES[0]]
    assert dedupe_by_memory(EDGES, 0) == []
    assert dedupe_by_memory(EDGES, -1) == []


def test_dedupe_by_memory_keeps_items_missing_the_key():
    items = [
        {"memory_id": "m1", "id": "a"},
        {"id": "b"},
        {"id": "c"},
        {"memory_id": None, "id": "d"},
    ]
    assert dedupe_by_memory(items, 4) == items
    assert dedupe_by_memory(items, 3) == items[:3]


def test_dedupe_by_memory_accepts_another_key():
    items = [{"claim_id": "c1", "memory_id": "m1"}, {"claim_id": "c1", "memory_id": "m2"}]
    assert dedupe_by_memory(items, 10, key="claim_id") == [items[0]]
    assert dedupe_by_memory(items, 10) == items


def test_dedupe_by_memory_does_not_mutate_its_input():
    items = [dict(item) for item in EDGES]
    dedupe_by_memory(EDGES, 2)
    assert EDGES == items


def test_combine_adds_a_bounded_secondary_signal():
    assert combine({"a": 1.0, "b": 0.5}, {"a": 0.4, "b": 1.0}, weight=0.5) == ranked(
        [("a", 1.2), ("b", 1.0)]
    )
    assert combine({"a": 1.0}, {"a": 1.0}, weight=0.0) == {"a": 1.0}


def test_combine_ignores_ids_outside_the_primary_scores_and_missing_extras():
    assert combine({"a": 2.0}, {"b": 1.0}, weight=1.0) == {"a": 2.0}
    assert combine({"a": 2.0, "b": 1.0}, {}, weight=1.0) == ranked([("a", 2.0), ("b", 1.0)])


def test_combine_is_deterministic_under_shuffling():
    results = {
        tuple(combine(dict(pair), {"a": 0.5, "b": 0.5, "c": 0.5}, weight=1.0))
        for pair in itertools.permutations([("a", 1.0), ("b", 1.0), ("c", 0.5)])
    }
    assert results == {("a", "b", "c")}


def test_combine_does_not_mutate_its_inputs():
    scores = {"a": 1.0}
    extra = {"a": 0.5}
    combine(scores, extra, weight=1.0)
    assert scores == {"a": 1.0}
    assert extra == {"a": 0.5}
