"""Metric computation over per-query outcomes. Pure functions, no database.

Parameters: the ranked claim ids a run returned and the labelled ground truth.
What it does: computes recall, MRR, nDCG, MAP, the under-return rate, a similarity
floor curve and the latency percentiles named in docs/RETRIEVAL.md.
Output: a flat metrics dictionary plus the evidence a threshold decision needs.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from .corpus import KINDS, Corpus, Embeddings

DEFAULT_LIMIT = 10
CUTOFFS = (1, 5, 10)
DEFAULT_FLOORS = (0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55)


def recall_at_k(returned: Sequence[str], relevant: Sequence[str], k: int) -> float:
    """Share of the relevant claims that appear in the first k results.

    Parameters: the ranked result, the relevant claim ids and the cutoff.
    What it does: counts how many distinct relevant claims the cutoff contains.
    Output: a fraction in [0, 1], or 0.0 when there is no relevant claim.
    """
    truth = set(relevant)
    if not truth:
        return 0.0
    return len(set(returned[:k]) & truth) / len(truth)


def reciprocal_rank_at_k(returned: Sequence[str], relevant: Sequence[str], k: int) -> float:
    """Reciprocal rank of the first relevant result inside the cutoff.

    Parameters: the ranked result, the relevant claim ids and the cutoff.
    What it does: finds the position of the first hit at or above the cutoff.
    Output: 1/rank, or 0.0 when no relevant claim is inside the cutoff.
    """
    truth = set(relevant)
    for position, claim_id in enumerate(returned[:k], start=1):
        if claim_id in truth:
            return 1 / position
    return 0.0


def ndcg_at_k(returned: Sequence[str], relevant: Sequence[str], k: int) -> float:
    """Normalized discounted cumulative gain with binary relevance.

    Parameters: the ranked result, the relevant claim ids and the cutoff.
    What it does: discounts each distinct relevant hit by log2(rank + 1) and divides
    by the gain of an ideal ranking of the same length.
    Output: a fraction in [0, 1], or 0.0 when there is no relevant claim.
    """
    truth = set(relevant)
    if not truth:
        return 0.0
    seen: set[str] = set()
    gain = 0.0
    for position, claim_id in enumerate(returned[:k], start=1):
        if claim_id in truth and claim_id not in seen:
            seen.add(claim_id)
            gain += 1 / math.log2(position + 1)
    ideal = sum(1 / math.log2(position + 1) for position in range(1, min(len(truth), k) + 1))
    return gain / ideal


def average_precision_at_k(returned: Sequence[str], relevant: Sequence[str], k: int) -> float:
    """Average precision at k, with the size of the relevant set as denominator.

    Parameters: the ranked result, the relevant claim ids and the cutoff.
    What it does: averages the precision at every distinct relevant position inside
    the cutoff, ranked-order errors included.
    Output: a fraction in [0, 1]; it reaches 1 only when every relevant claim is
    inside the cutoff and none of them is ranked below an irrelevant one.
    """
    truth = set(relevant)
    if not truth:
        return 0.0
    seen: set[str] = set()
    total = 0.0
    for position, claim_id in enumerate(returned[:k], start=1):
        if claim_id in truth and claim_id not in seen:
            seen.add(claim_id)
            total += len(seen) / position
    return total / len(truth)


def percentile(values: Sequence[float], percent: float) -> float:
    """Nearest-rank percentile, so a reported number is always a measured value.

    Parameters: the samples and the percentile in [0, 100].
    What it does: sorts the samples and takes rank ceil(percent / 100 * count).
    Output: the sample at that rank, or 0.0 for an empty input.
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = math.ceil(percent / 100 * len(ordered))
    return ordered[min(len(ordered), max(1, rank)) - 1]


def is_under_returned(returned_count: int, limit: int, available: int) -> bool:
    """Whether a stage returned fewer rows than it asked for while rows existed.

    Parameters: how many rows came back, how many were asked for and how many
    matching claims the scope holds.
    What it does: applies the definition in docs/RETRIEVAL.md.
    Output: True when the stage under-returned silently.
    """
    return returned_count < limit <= available


@dataclass(frozen=True)
class QueryOutcome:
    """Everything one query measured, before aggregation.

    Parameters: the query, its label, what the harness returned, what the candidate
    stage returned, what existed in scope and how long the database work took.
    What it does: keeps the raw evidence, so metric code stays a pure function.
    Output: an immutable outcome record.
    """

    query: str
    kind: str
    relevant: tuple[str, ...]
    returned: tuple[str, ...]
    candidates: int
    search_returned: int
    search_limit: int
    available: int
    latency_ms: float
    scores: tuple[float, ...] = ()


def aggregate(
    outcomes: Sequence[QueryOutcome],
    *,
    limit: int = DEFAULT_LIMIT,
    embed_calls: int = 0,
    candidates_read: int | None = None,
) -> dict:
    """Turn per-query outcomes into the metrics docs/RETRIEVAL.md requires.

    Parameters: the outcomes, the caller limit, the provider calls this run made
    and an optional candidate total (defaults to the sum over the outcomes).
    What it does: averages recall, MRR, nDCG and MAP over labelled queries, counts
    negative-control violations, and measures under-return at the search stage and
    at the caller limit.
    Output: a flat metrics dictionary, with per-kind and diagnostic fields added.
    """
    positive = [outcome for outcome in outcomes if outcome.kind != "negative"]
    negative = [outcome for outcome in outcomes if outcome.kind == "negative"]
    latencies = [outcome.latency_ms for outcome in outcomes]
    metrics: dict[str, Any] = {
        "queries": len(outcomes),
        "positive_queries": len(positive),
        "negative_queries": len(negative),
        "embed_calls": embed_calls,
        "candidates_read": sum(outcome.candidates for outcome in outcomes)
        if candidates_read is None
        else candidates_read,
        "under_return_rate": _mean(
            is_under_returned(outcome.search_returned, outcome.search_limit, outcome.available)
            for outcome in outcomes
        ),
        "under_return_rate_at_limit": _mean(
            is_under_returned(len(outcome.returned), limit, outcome.available)
            for outcome in outcomes
        ),
        "negative_false_positive_rate": _mean(
            1.0 if outcome.returned else 0.0 for outcome in negative
        ),
        "empty_result_rate": _mean(1.0 if not outcome.returned else 0.0 for outcome in positive),
        "mean_returned": _mean(len(outcome.returned) for outcome in outcomes),
        "mean_available": _mean(outcome.available for outcome in outcomes),
        "mean_candidates": _mean(outcome.candidates for outcome in outcomes),
        "latency_p50_ms": percentile(latencies, 50),
        "latency_p95_ms": percentile(latencies, 95),
    }
    for k in CUTOFFS:
        metrics[f"recall@{k}"] = _mean(
            recall_at_k(outcome.returned, outcome.relevant, k) for outcome in positive
        )
    metrics["mrr@10"] = _mean(
        reciprocal_rank_at_k(outcome.returned, outcome.relevant, CUTOFFS[-1])
        for outcome in positive
    )
    metrics["ndcg@10"] = _mean(
        ndcg_at_k(outcome.returned, outcome.relevant, CUTOFFS[-1]) for outcome in positive
    )
    metrics["map@10"] = _mean(
        average_precision_at_k(outcome.returned, outcome.relevant, CUTOFFS[-1])
        for outcome in positive
    )
    metrics["per_kind"] = {
        kind: _rounded(
            {
                "queries": len(subset),
                "recall@10": _mean(recall_at_k(o.returned, o.relevant, 10) for o in subset),
                "mrr@10": _mean(reciprocal_rank_at_k(o.returned, o.relevant, 10) for o in subset),
                "negative_false_positive_rate": _mean(1.0 if o.returned else 0.0 for o in subset)
                if kind == "negative"
                else 0.0,
            }
        )
        for kind in KINDS
        if (subset := [outcome for outcome in outcomes if outcome.kind == kind])
    }
    metrics["missed_queries"] = [
        {
            "query": outcome.query,
            "kind": outcome.kind,
            "relevant": list(outcome.relevant),
            "returned": list(outcome.returned[:10]),
        }
        for outcome in positive
        if recall_at_k(outcome.returned, outcome.relevant, 10) < 1.0
    ][:25]
    return _rounded(metrics)


DEFAULT_FLOORS = (0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55)


def summarize(values: Sequence[float]) -> dict:
    """Percentile summary of one similarity distribution.

    Parameters: the samples.
    What it does: reports the points a floor decision needs, by nearest rank.
    Output: a dictionary with count, min, max and five percentiles.
    """
    if not values:
        return {"count": 0}
    return {
        "count": len(values),
        "min": round(min(values), 4),
        "p05": round(percentile(values, 5), 4),
        "p25": round(percentile(values, 25), 4),
        "p50": round(percentile(values, 50), 4),
        "p75": round(percentile(values, 75), 4),
        "p95": round(percentile(values, 95), 4),
        "max": round(max(values), 4),
    }


def cosine(left: Sequence[float], right: Sequence[float]) -> float:
    """Cosine similarity of two embeddings, computed locally from the baked cache."""
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    return dot / (left_norm * right_norm) if left_norm and right_norm else 0.0


def score_evidence(
    corpus: Corpus, embeddings: Embeddings, outcomes: Sequence[QueryOutcome]
) -> dict:
    """The similarity evidence a relevance floor has to be chosen from.

    Parameters: the corpus, the baked embeddings and the outcomes of one run.
    What it does: measures the cosine similarity of every relevant claim to its own
    query (the true-answer side) and the best returned similarity on every negative
    control (the false-answer side), then recomputes recall and negative false
    positives for a range of floors.
    Output: both distributions, their overlap, and one curve row per floor.
    """
    claims = {claim.id: claim for claim in corpus.claims}
    relevant_scores: list[float] = []
    for query in corpus.queries:
        if not query.relevant:
            continue
        vector = embeddings.vector(query.query)
        for claim_id in query.relevant:
            claim = claims.get(claim_id)
            if claim is not None:
                relevant_scores.append(cosine(vector, embeddings.vector(claim.text)))
    negative_best = [
        max(outcome.scores) for outcome in outcomes if outcome.kind == "negative" and outcome.scores
    ]
    return {
        "relevant_claim_similarity": summarize(relevant_scores),
        "negative_control_best_returned_similarity": summarize(negative_best),
        "negative_controls_with_a_result": len(negative_best),
        "floor_curve": floor_curve(outcomes),
    }


def floor_curve(
    outcomes: Sequence[QueryOutcome],
    *,
    floors: Sequence[float] = DEFAULT_FLOORS,
    limit: int = DEFAULT_LIMIT,
) -> list[dict]:
    """Recall and negative false positives for a range of similarity floors.

    Parameters: the outcomes of one run (they carry each returned item's score), the
    floors to try and the caller limit.
    What it does: drops every returned item below each floor and recomputes recall@10,
    the negative-control false positive rate and the empty-result rate. This is the
    cheap evidence curve; a real floor strategy is measured through the product path.
    Output: one row per floor.
    """
    rows = []
    for floor in floors:
        kept = [
            replace(
                outcome,
                returned=tuple(
                    claim_id
                    for claim_id, score in zip(outcome.returned, outcome.scores, strict=True)
                    if score >= floor
                ),
            )
            for outcome in outcomes
        ]
        kept_positive = [outcome for outcome in kept if outcome.kind != "negative"]
        kept_negative = [outcome for outcome in kept if outcome.kind == "negative"]
        rows.append(
            {
                "floor": round(floor, 4),
                "recall@10": round(
                    _mean(recall_at_k(o.returned, o.relevant, 10) for o in kept_positive), 4
                ),
                "negative_false_positive_rate": round(
                    _mean(1.0 if o.returned else 0.0 for o in kept_negative), 4
                ),
                "empty_result_rate": round(
                    _mean(1.0 if not o.returned else 0.0 for o in kept_positive), 4
                ),
                "mean_returned": round(_mean(len(o.returned) for o in kept), 4),
                "limit": limit,
            }
        )
    return rows


def _mean(values: Iterable[float]) -> float:
    items = list(values)
    return sum(items) / len(items) if items else 0.0


def _rounded(metrics: Mapping[str, Any]) -> dict:
    return {
        key: round(value, 6) if isinstance(value, float) else value
        for key, value in metrics.items()
    }
