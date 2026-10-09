"""The strategy registry and the candidate shapes a strategy sees.

Parameters: a name, a `brain.RetrievalConfig`, and optional expansion, search limit,
similarity floor and rerank hook.
What it does: describes one retrieval experiment without containing metric code, so a
new variant is one registry entry or one entry in `strategies.json`.
Output: the registered strategies, resolved and ready to run.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from importlib import import_module
from pathlib import Path

from space import brain
from space.settings import BACKEND_DIR

from .errors import EvaluationError, UnknownStrategy
from .metrics import DEFAULT_FLOORS

STRATEGIES_PATH = BACKEND_DIR / "evaluations" / "retrieval" / "strategies.json"


@dataclass(frozen=True)
class Candidate:
    """One claim row the database returned as a retrieval candidate.

    Parameters: the claim identity, its memory, its position, its cosine score and
    the cue hashes the row carries.
    What it does: carries a database row without holding a live cursor.
    Output: an immutable candidate record.
    """

    claim_id: str
    memory_id: str
    entry_index: int
    score: float | None
    cue_hashes: tuple[bytes, ...] = ()


@dataclass(frozen=True)
class CandidateSet:
    """The raw candidate output of one retrieval, before it is ranked.

    Parameters: the query text, its embedding, the search rows and the expansion rows.
    What it does: gives a rerank hook everything the product's SQL produced, so an
    alternative strategy can score the same candidates instead of querying again.
    Output: an immutable candidate set.
    """

    query: str
    vector: tuple[float, ...]
    hits: tuple[Candidate, ...]
    expanded: tuple[Candidate, ...]


def shipped_order(candidates: CandidateSet) -> list[str]:
    """The order the product ships today: search results, then cue expansion.

    Parameters: the candidate set of one query.
    What it does: keeps `brain.search` order and appends expansion rows in the order
    `brain.expand` returned them, dropping duplicates.
    Output: ranked claim ids, best first.
    """
    ranked: list[str] = []
    seen: set[str] = set()
    for candidate in list(candidates.hits) + list(candidates.expanded):
        if candidate.claim_id not in seen:
            seen.add(candidate.claim_id)
            ranked.append(candidate.claim_id)
    return ranked


@dataclass(frozen=True)
class Strategy:
    """One retrieval configuration the harness can measure.

    Parameters: a name, what the variant changes, the brain retrieval config, an
    optional fixed search limit, whether cue expansion runs and an optional rerank
    hook over the candidate set.
    What it does: describes a variant without containing any metric code, so a new
    experiment is one more registry entry.
    Output: an immutable strategy record.
    """

    name: str
    description: str
    config: brain.RetrievalConfig = brain.RetrievalConfig(selection="approximate")
    search_limit: int | None = None
    expand: bool = True
    rerank: Callable[[CandidateSet], list[str]] | None = None
    min_score: float | None = None

    def candidates_for(self, limit: int) -> int:
        """How many rows the search step asks for, exactly as the product does."""
        if self.search_limit is not None:
            return self.search_limit
        return limit * self.config.candidate_multiplier

    def order(self, candidates: CandidateSet) -> list[str]:
        """Rank one candidate set. The baseline keeps the shipped database order.

        Parameters: the candidates of one query.
        What it does: drops every search row below `min_score` when a similarity floor
        is configured, then applies the rerank hook or the shipped order.
        Output: ranked claim ids, best first.
        """
        if self.min_score is not None:
            candidates = replace(
                candidates,
                hits=tuple(
                    hit
                    for hit in candidates.hits
                    if hit.score is not None and hit.score >= self.min_score
                ),
                expanded=(),
            )
        return (self.rerank or shipped_order)(candidates)

    def info(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "expand": self.expand,
            "search_limit": self.search_limit,
            "min_score": self.min_score,
            "rerank": getattr(self.rerank, "__name__", None),
            "brain_config": {
                field_name: getattr(self.config, field_name)
                for field_name in (
                    "selection",
                    "ef_search",
                    "iterative_scan",
                    "scan_multiplier",
                    "max_scan_tuples",
                    "expansion_order",
                    "candidate_multiplier",
                    "max_candidates",
                    "max_cue_candidates",
                )
            },
        }


# The registry. A new variant is one line here, or one entry in
# evaluations/retrieval/strategies.json; metric code never changes.
STRATEGIES: dict[str, Strategy] = {}


def register(strategy: Strategy) -> Strategy:
    """Add a strategy to the registry.

    Parameters: a Strategy.
    What it does: stores it under its name, replacing a previous entry of that name.
    Output: the registered strategy, so a definition can be used inline.
    """
    STRATEGIES[strategy.name] = strategy
    return strategy


def registered(name: str | None = None) -> dict[str, Strategy]:
    """The registered strategies, or one of them by name.

    Parameters: an optional strategy name.
    What it does: loads the JSON overlay once, then looks the name up.
    Output: the whole registry or the named strategy. Raises UnknownStrategy.
    """
    load_strategy_overlay()
    if name is None:
        return dict(STRATEGIES)
    if name not in STRATEGIES:
        raise UnknownStrategy(
            f"Unknown strategy {name!r}; registered: {', '.join(sorted(STRATEGIES))}"
        )
    return {name: STRATEGIES[name]}


def load_strategy_overlay(path: str | Path = STRATEGIES_PATH) -> int:
    """Read optional extra strategies from JSON so no metric code changes.

    Parameters: the overlay path. A missing file is not an error.
    What it does: builds a Strategy per entry from `config` (brain.RetrievalConfig
    fields), optional `expand`, `search_limit` and `rerank` ("module:function",
    resolved lazily so a ranking module can land later without breaking a run).
    Output: the number of strategies registered from the file.
    """
    path = Path(path)
    if not path.exists():
        return 0
    document = json.loads(path.read_text())
    entries = document.get("strategies", document)
    count = 0
    for name, entry in entries.items():
        if name in STRATEGIES and not entry.get("replace", False):
            continue
        rerank = entry.get("rerank")
        register(
            Strategy(
                name=name,
                description=str(entry.get("description", "defined in strategies.json")),
                config=brain.RetrievalConfig(
                    **{"selection": "approximate", **entry.get("config", {})}
                ),
                search_limit=entry.get("search_limit"),
                expand=bool(entry.get("expand", True)),
                rerank=resolve_hook(rerank) if rerank else None,
            )
        )
        count += 1
    return count


def resolve_hook(reference: str) -> Callable[[CandidateSet], list[str]]:
    """Resolve a "module:function" reference to a callable.

    Parameters: the dotted reference.
    What it does: imports the module and returns the attribute.
    Output: the callable. Raises EvaluationError when it cannot be resolved.
    """
    module_name, _, attribute = reference.partition(":")
    try:
        module = import_module(module_name)
        hook = getattr(module, attribute)
    except (ImportError, AttributeError) as error:
        raise EvaluationError(f"Cannot resolve rerank hook {reference!r}: {error}") from None
    if not callable(hook):
        raise EvaluationError(f"Rerank hook {reference!r} is not callable")
    return hook


register(
    Strategy(
        name="baseline",
        description="Historical ANN baseline: server defaults, search(limit * 4), then expansion.",
    )
)
register(
    Strategy(
        name="search_only",
        description="Shipped search with no cue expansion, to isolate the vector index.",
        expand=False,
    )
)
register(
    Strategy(
        name="iterative_relaxed",
        description="Iterative scan in relaxed order, which fills the limit with less filtering.",
        config=brain.RetrievalConfig(selection="approximate", iterative_scan="relaxed_order"),
    )
)
register(
    Strategy(
        name="iterative_strict",
        description="Iterative scan in strict order, which never returns a row out of order.",
        config=brain.RetrievalConfig(selection="approximate", iterative_scan="strict_order"),
    )
)
register(
    Strategy(
        name="ef_search_200",
        description="A larger HNSW candidate list, which raises recall and read cost.",
        config=brain.RetrievalConfig(selection="approximate", ef_search=200),
    )
)


def fusion_order(candidates: CandidateSet) -> list[str]:
    """Rank one candidate set by reciprocal rank fusion of search and expansion.

    Parameters: the candidate set of one query.
    What it does: fuses the vector search ranking with the cue-expansion ranking using
    the project's own `space.ranking.reciprocal_rank_fusion`, which is the ranking
    primitive the recall path is meant to adopt.
    Output: fused claim ids, best first.
    """
    from space import ranking

    rankings = [
        [candidate.claim_id for candidate in candidates.hits],
        [candidate.claim_id for candidate in candidates.expanded],
    ]
    fused = ranking.reciprocal_rank_fusion([ranking for ranking in rankings if ranking])
    return list(fused)


def floor_strategies(floors: Sequence[float] = DEFAULT_FLOORS) -> list[Strategy]:
    """Build one strategy per similarity floor.

    Parameters: the floors to build.
    What it does: pairs each floor with the shipped search and no cue expansion, because
    an expanded row carries no score the floor could compare.
    Output: the strategies, registered under `floor_<value>`.
    """
    built = []
    for floor in floors:
        built.append(
            register(
                Strategy(
                    name=f"floor_{floor:.2f}",
                    description=(
                        "Shipped search with no expansion, dropping any claim below a "
                        f"similarity floor of {floor:.2f}."
                    ),
                    expand=False,
                    min_score=floor,
                )
            )
        )
    return built


floor_strategies()


register(
    Strategy(
        name="fusion_rrf",
        description="Reciprocal rank fusion of the vector search and the cue expansion.",
        rerank=fusion_order,
    )
)
register(
    Strategy(
        name="expansion_by_vector",
        description="Cue expansion ordered by closeness to the query instead of index order.",
        config=brain.RetrievalConfig(selection="approximate", expansion_order="vector"),
    )
)


register(
    Strategy(
        name="exact",
        description="Exact authorized Space/model ranking, no expansion.",
        config=brain.DEFAULT_RETRIEVAL,
        expand=False,
    )
)
