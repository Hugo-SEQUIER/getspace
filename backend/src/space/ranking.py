"""Ranking primitives: fusion, priors and diversity. Recall uses the fusion today.

Pure functions only: no database, no network, no model call, and no dependency on
the Workspace. Every result is deterministic, because ties break on the id or the
index and never on dict or set iteration order.
"""

import math
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

# Smallest positive float, so an arbitrarily old memory still weighs above zero.
MIN_WEIGHT = 5e-324


def reciprocal_rank_fusion(rankings: Sequence[Sequence[str]], *, k: int = 60) -> dict[str, float]:
    """Fuse several ranked id lists into one score per id.

    Parameters: the ranked id lists, and the RRF constant k; rank starts at 1.
    What it does: sums 1 / (k + rank) over the lists; an id repeated inside one
        list contributes only its best rank from that list.
    Output: fused scores by id, best score first, ties ordered by id.
    """
    fused: dict[str, float] = {}
    for ranking in rankings:
        seen: set[str] = set()
        for rank, item in enumerate(ranking, start=1):
            if item in seen:
                continue
            seen.add(item)
            fused[item] = fused.get(item, 0.0) + 1.0 / (k + rank)
    return _ranked(fused)


def recency_weight(updated_at: datetime | str, now: datetime, *, half_life_days: float) -> float:
    """Weight a memory by how recently it was updated.

    Parameters: the update time as a datetime or ISO 8601 string, the reference
        time, and the half-life in days.
    What it does: returns 0.5 ** (age / half_life_days), reading a naive
        timestamp as UTC and treating a future timestamp as age zero.
    Output: a multiplier in (0, 1], being 1.0 at age zero and 0.5 at one half-life.
    """
    if half_life_days <= 0:
        raise ValueError("half_life_days must be positive")
    age_days = (_as_utc(now) - _as_utc(updated_at)).total_seconds() / 86400.0
    if age_days <= 0:
        return 1.0
    return max(0.5 ** (age_days / half_life_days), MIN_WEIGHT)


def apply_prior(
    scores: Mapping[str, float],
    weights: Mapping[str, float],
    *,
    weight: float,
    floor: float,
) -> dict[str, float]:
    """Blend a per-id prior, such as recency, into primary scores.

    Parameters: the primary scores, the prior weights, the blend weight in 0..1,
        and the lowest multiplier that may apply.
    What it does: multiplies each score by (1 - weight) + weight * w, where a
        missing id takes w = 1.0, then raises that multiplier to at least floor.
    Output: the blended scores by id, best score first, ties ordered by id.
    """
    if not 0.0 <= weight <= 1.0:
        raise ValueError("weight must be between 0 and 1")
    if not 0.0 < floor <= 1.0:
        raise ValueError("floor must be between 0 and 1")
    blended = {
        key: score * max((1.0 - weight) + weight * weights.get(key, 1.0), floor)
        for key, score in scores.items()
    }
    return _ranked(blended)


def mmr_select(
    vectors: Sequence[Sequence[float]],
    scores: Sequence[float],
    limit: int,
    *,
    diversity: float,
) -> list[int]:
    """Select up to `limit` candidates by maximal marginal relevance.

    Parameters: the candidate vectors, their relevance scores, the selection
        limit, and diversity in 0..1.
    What it does: repeatedly picks the remaining index with the best
        (1 - diversity) * score - diversity * best cosine similarity to the
        already picked vectors, breaking ties on score and then on index.
    Output: the picked indices in selection order, best first.
    """
    if not 0.0 <= diversity <= 1.0:
        raise ValueError("diversity must be between 0 and 1")
    if len(vectors) != len(scores):
        raise ValueError("vectors and scores must have the same length")
    count = min(limit, len(vectors))
    if count <= 0:
        return []
    remaining = set(range(len(vectors)))
    picked: list[int] = []
    closest = [0.0] * len(vectors)
    while len(picked) < count:
        index = max(
            remaining,
            key=lambda item: (_relevance(item, scores, closest, diversity), scores[item], -item),
        )
        picked.append(index)
        remaining.discard(index)
        for other in remaining:
            closest[other] = max(closest[other], _cosine(vectors[index], vectors[other]))
    return picked


def dedupe_by_memory(
    items: Sequence[Mapping], limit: int, *, key: str = "memory_id"
) -> list[Mapping]:
    """Keep the best-ranked item per memory.

    Parameters: items already in rank order, the output limit, and the field
        holding the memory id.
    What it does: keeps the first item seen for each memory id plus every item
        that carries no memory id, in input order, stopping at the limit.
    Output: the kept items, best-ranked first.
    """
    if limit <= 0:
        return []
    kept: list[Mapping] = []
    seen: set = set()
    for item in items:
        memory = item.get(key)
        if memory is None or memory not in seen:
            kept.append(item)
            if memory is not None:
                seen.add(memory)
        if len(kept) >= limit:
            break
    return kept


def combine(
    scores: Mapping[str, float], extra: Mapping[str, float], *, weight: float
) -> dict[str, float]:
    """Add a bounded secondary signal, such as a normalised lexical score.

    Parameters: the primary scores, the secondary signal, and its weight.
    What it does: adds weight * extra to each primary id, reading a missing id as
        0.0, so the primary scores stay the candidate set.
    Output: the combined scores by id, best score first, ties ordered by id.
    """
    combined = {key: score + weight * extra.get(key, 0.0) for key, score in scores.items()}
    return _ranked(combined)


def _ranked(scores: Mapping[str, float]) -> dict[str, float]:
    """Order scored ids by descending score, then by id.

    Parameters: scores by id.
    What it does: sorts on (-score, id) so equal scores never depend on input order.
    Output: a new dict with that order.
    """
    return {key: scores[key] for key in sorted(scores, key=lambda key: (-scores[key], key))}


def _relevance(
    index: int, scores: Sequence[float], closest: Sequence[float], diversity: float
) -> float:
    """Score one candidate for the next MMR pick.

    Parameters: the candidate index, all scores, the best similarity to a picked
        vector, and the diversity weight.
    What it does: weighs relevance against redundancy with the MMR formula.
    Output: the objective value, higher being better.
    """
    return (1.0 - diversity) * scores[index] - diversity * closest[index]


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    """Cosine similarity of two vectors.

    Parameters: the two vectors.
    What it does: divides the dot product by both lengths, clamped to -1..1.
    Output: the similarity, or 0.0 when either vector has zero length.
    """
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=False))
    return min(max(dot / (left_norm * right_norm), -1.0), 1.0)


def _as_utc(value: datetime | str) -> datetime:
    """Read a datetime or ISO 8601 string as an aware UTC datetime.

    Parameters: the datetime or ISO 8601 string; a naive value is read as UTC.
    What it does: parses the string, then attaches UTC to a naive result and
        converts an aware result to UTC.
    Output: the equivalent aware UTC datetime.
    """
    moment = datetime.fromisoformat(value) if isinstance(value, str) else value
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)
