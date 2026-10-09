"""Driving the product's retrieval SQL once per strategy and reporting the result.

Parameters: the corpus, the embeddings, the fixture and the strategies to measure.
What it does: runs every query through `brain.search` and `brain.expand` under the
strategy's configuration, records the generated plan and the scores, then assembles the
JSON report and the readable table.
Output: the report document, printed or written by the CLI.
"""

from __future__ import annotations

import json
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

from space import brain

from .corpus import (
    CORPUS_PATH,
    EMBEDDINGS_PATH,
    Corpus,
    Embeddings,
    bake_embeddings,
    load_corpus,
)
from .errors import EvaluationError, UnknownStrategy
from .fixture import (
    DEFAULT_DECOYS,
    DEFAULT_STALE,
    DEFAULT_STALE_GEOMETRY,
    DEFAULT_TWINS,
    STALE_GEOMETRIES,
    DisposableDatabase,
    Fixture,
    explain_search,
    seed_fixture,
    set_scope,
)
from .metrics import (
    DEFAULT_LIMIT,
    QueryOutcome,
    aggregate,
    percentile,
    score_evidence,
)
from .strategies import Candidate, CandidateSet, Strategy, registered

TABLE_ROWS = (
    ("recall@1", "recall@1"),
    ("recall@5", "recall@5"),
    ("recall@10", "recall@10"),
    ("mrr@10", "mrr@10"),
    ("ndcg@10", "ndcg@10"),
    ("map@10", "map@10"),
    ("under_return_rate", "under_return_rate"),
    ("under_return_rate_at_limit", "under_return_rate_at_limit"),
    ("negative_false_positive_rate", "negative_false_positive_rate"),
    ("empty_result_rate", "empty_result_rate"),
    ("mean_returned", "mean_returned"),
    ("mean_available", "mean_available"),
    ("mean_candidates", "mean_candidates"),
    ("candidates_read", "candidates_read"),
    ("embed_calls", "embed_calls"),
    ("latency_p50_ms", "latency_p50_ms"),
    ("latency_p95_ms", "latency_p95_ms"),
)


HNSW_SETTINGS = (
    "hnsw.ef_search",
    "hnsw.iterative_scan",
    "hnsw.scan_mem_multiplier",
    "hnsw.max_scan_tuples",
)


def settings_in_force(connection, config: brain.RetrievalConfig) -> dict:
    """Read the HNSW settings the session actually has, inside the query's transaction.

    Parameters: an open scoped connection and the configuration the strategy asked for.
    What it does: reads the settings with `current_setting`, loading the vector library
    first if the backend has never touched it, so a `SET LOCAL` that was discarded by an
    autocommit boundary shows up as the server default instead of silently changing the
    experiment.
    Output: the observed value of every HNSW setting this harness can configure.
    """
    statement = "SELECT " + ", ".join(
        f"current_setting(%s, true) AS {name.replace('.', '_')}" for name in HNSW_SETTINGS
    )
    observed = connection.execute(statement, HNSW_SETTINGS).fetchone()
    if any(value is None for value in observed.values()):
        connection.execute("SELECT '[1]'::vector")
        observed = connection.execute(statement, HNSW_SETTINGS).fetchone()
    return {name: observed[name.replace(".", "_")] for name in HNSW_SETTINGS}


def check_settings(observed: Mapping, config: brain.RetrievalConfig, strategy_name: str) -> dict:
    """Fail loudly when the session is not running the configuration the strategy named.

    Parameters: the observed settings, the strategy configuration and its name.
    What it does: compares each setting the strategy can influence with what the server
    reports. A strategy with no setting asked for must still show the server defaults.
    Output: the observed settings. Raises EvaluationError on any mismatch.
    """
    expected = {
        "hnsw.ef_search": str(config.ef_search or brain.SERVER_EF_SEARCH),
        "hnsw.iterative_scan": config.iterative_scan,
        "hnsw.scan_mem_multiplier": None
        if config.scan_multiplier is None
        else str(config.scan_multiplier),
        "hnsw.max_scan_tuples": None
        if config.max_scan_tuples is None
        else str(config.max_scan_tuples),
    }
    mismatched = {
        name: (wanted, observed.get(name))
        for name, wanted in expected.items()
        if wanted is not None and observed.get(name) != wanted
    }
    if mismatched:
        raise EvaluationError(
            f"Strategy {strategy_name} is not in force in this session: {mismatched}. "
            "A dropped session setting would have made this strategy identical to baseline."
        )
    return dict(observed)


def run_strategy(
    database: DisposableDatabase,
    corpus: Corpus,
    embeddings: Embeddings,
    fixture: Fixture,
    strategy: Strategy,
    *,
    limit: int = DEFAULT_LIMIT,
) -> dict:
    """Drive every corpus query through the product's retrieval SQL once.

    Parameters: the database, the corpus, the embeddings, the fixture, the strategy
    and the caller limit.
    What it does: for each query opens a transaction, sets the same session scope the
    application sets, calls `brain.search` and `brain.expand` with the strategy's
    configuration, records the returned rows, the candidate count and the latency,
    and rolls the transaction back so no session setting can leak.
    Output: the strategy report with plan, fixture conditions and metrics.
    """
    search_limit = strategy.candidates_for(limit)
    outcomes: list[QueryOutcome] = []
    plan: dict = {}
    plan_indexes: Counter = Counter()
    settings_observed: dict = {}
    with database.connect() as connection:
        for index, query in enumerate(corpus.queries):
            connection.rollback()
            set_scope(connection, fixture.identity)
            vector = list(embeddings.vector(query.query))
            brain.configure(connection, strategy.config)
            # Record which plan the database chose for this query, so a run that uses
            # the exact path and a run that uses the index are never confused.
            explained = explain_search(
                connection, vector, fixture.model, search_limit, config=strategy.config
            )
            plan_indexes[explained["index"] or "seq-scan"] += 1
            if index == 0:
                plan = explained
            # Apply the same configuration brain.search applies, then read it back on
            # this connection in this transaction: if the session setting does not hold
            # here, the strategy is really the baseline and the numbers would lie.
            observed = check_settings(
                settings_in_force(connection, strategy.config), strategy.config, strategy.name
            )
            if index == 0:
                settings_observed = observed
            started = time.perf_counter()
            hits = brain.search(
                connection,
                vector,
                fixture.model,
                search_limit,
                config=strategy.config,
            )
            expanded = []
            if strategy.expand:
                cues: set[bytes] = set()
                for hit in hits:
                    cues.update(
                        brain.cues_for(connection, hit["memory_id"], [str(hit["claim_id"])])
                    )
                expanded = brain.expand(
                    connection,
                    sorted(cues),
                    strategy.config.max_cue_candidates,
                    config=strategy.config,
                    vector=vector if strategy.config.expansion_order == "vector" else None,
                )
            latency_ms = (time.perf_counter() - started) * 1000
            available = connection.execute(
                "SELECT count(*) AS total FROM space.memory_claims WHERE embedding_model=%s",
                (fixture.model,),
            ).fetchone()["total"]
            connection.rollback()
            candidates = CandidateSet(
                query=query.query,
                vector=tuple(vector),
                hits=tuple(
                    Candidate(
                        claim_id=str(row["claim_id"]),
                        memory_id=str(row["memory_id"]),
                        entry_index=row["entry_index"],
                        score=float(row["score"]),
                    )
                    for row in hits
                ),
                expanded=tuple(
                    Candidate(
                        claim_id=str(row["claim_id"]),
                        memory_id=str(row["memory_id"]),
                        entry_index=row["entry_index"],
                        score=None,
                        cue_hashes=tuple(row["cue_hashes"]),
                    )
                    for row in expanded
                ),
            )
            scores_by_claim = {candidate.claim_id: candidate.score for candidate in candidates.hits}
            ranked = [
                (fixture.claim_ids[claim_id], scores_by_claim.get(claim_id))
                for claim_id in strategy.order(candidates)
                if claim_id in fixture.claim_ids
            ][:limit]
            outcomes.append(
                QueryOutcome(
                    query=query.query,
                    kind=query.kind,
                    relevant=query.relevant,
                    returned=tuple(claim_id for claim_id, _ in ranked),
                    # A cue-expanded row carries no similarity score of its own; it is
                    # recorded as 0.0 so a similarity floor always drops it.
                    scores=tuple(float(score or 0.0) for _, score in ranked),
                    candidates=len(hits) + len(expanded),
                    search_returned=len(hits),
                    search_limit=search_limit,
                    available=int(available),
                    latency_ms=latency_ms,
                )
            )
    if outcomes and not any(outcome.returned for outcome in outcomes):
        raise EvaluationError(
            f"Strategy {strategy.name} returned no ranked item for any of "
            f"{len(outcomes)} queries; a measurement like that cannot mean anything. "
            f"Fixture counts: {dict(fixture.verified)}"
        )
    metrics = aggregate(outcomes, limit=limit, embed_calls=embeddings.calls)
    return {
        "strategy": strategy.info(),
        "plan": plan,
        "plan_counts": dict(plan_indexes),
        "settings_in_force": settings_observed,
        "score_evidence": score_evidence(corpus, embeddings, outcomes),
        "metrics": metrics,
    }


def percentile_summary(outcomes: Sequence[QueryOutcome]) -> dict:
    """Latency percentiles of one run, in milliseconds."""
    latencies = [outcome.latency_ms for outcome in outcomes]
    return {"p50": percentile(latencies, 50), "p95": percentile(latencies, 95)}


def build_report(
    *,
    corpus: Corpus,
    embeddings: Embeddings,
    fixture: Fixture,
    reports: Sequence[dict],
    limit: int,
) -> dict:
    """Assemble the JSON report that records a whole evaluation.

    Parameters: the corpus, the embeddings, the fixture, one report per strategy and
    the caller limit.
    What it does: adds the conditions a reader needs to interpret the metrics, so a
    number is never separated from the fixture it came from.
    Output: a JSON-serializable report document.
    """
    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "layer": "index",
        "layer_note": (
            "brain.search and brain.expand only. This layer has no relevance floor, so "
            "negative_false_positive_rate here is not what a user sees; measure the "
            "product layer (Workspace.context) before quoting a recall number."
        ),
        "corpus": {
            "path": str(corpus.path),
            "claims": len(corpus.claims),
            "queries": len(corpus.queries),
            "kinds": corpus.kind_counts(),
        },
        "embeddings": embeddings.info(),
        "fixture": fixture.info(),
        "limit": limit,
        "metric_definitions": {
            "recall@k": "mean over labelled queries of |top-k that are relevant| / |relevant|",
            "mrr@10": "mean of 1/rank of the first relevant claim inside the top 10",
            "ndcg@10": "binary-gain nDCG at 10, ideal ranking over min(|relevant|, 10) positions",
            "map@10": "average precision at 10 with |relevant| as denominator",
            "under_return_rate": (
                "fraction of queries where the search step returned fewer rows than its "
                "limit while at least that many matching claims exist in scope"
            ),
            "under_return_rate_at_limit": (
                "fraction of queries where the caller-visible item count is below the "
                "limit while further matching claims exist"
            ),
            "negative_false_positive_rate": (
                "fraction of negative-control queries that returned any item"
            ),
            "latency_p50_ms": "nearest-rank 50th percentile of one query's database work",
            "latency_p95_ms": "nearest-rank 95th percentile of one query's database work",
            "candidates_read": "claim rows returned by search and expansion, summed over queries",
            "embed_calls": "provider embedding calls this run paid for; 0 when the cache is warm",
        },
        "reports": list(reports),
    }


def format_table(document: Mapping) -> str:
    """Render the report as a readable table of strategies by metric.

    Parameters: the report document from `build_report`.
    What it does: prints one column per strategy and one row per metric, with the
    fixture conditions and the chosen plan above the numbers.
    Output: the table as text.
    """
    reports = document["reports"]
    names = [report["strategy"]["name"] for report in reports]
    width = max(len(label) for label, _ in TABLE_ROWS) + 2
    lines = []
    fixture = document["fixture"]
    lines.append(
        "fixture: "
        f"{fixture['corpus_claims']} corpus claims in one Space, "
        f"{fixture['stale_claims_in_space']} stale rows in the same Space, "
        f"{fixture['decoy_claims_other_tenant']} rows in a second tenant, "
        f"model {fixture['embedding_model']}"
    )
    lines.append(
        "corpus: "
        f"{document['corpus']['claims']} claims, {document['corpus']['queries']} queries "
        f"({', '.join(f'{kind} {count}' for kind, count in document['corpus']['kinds'].items())})"
    )
    for name, report in zip(names, reports, strict=True):
        plan = report["plan"]
        lines.append(
            f"plan {name}: index={plan.get('index')} "
            f"queries={report.get('plan_counts', {})} "
            f"nodes={'>'.join(node.split('(')[0].strip() for node in plan.get('nodes', []))[:80]}"
        )
    header = "metric".ljust(width) + "".join(name.rjust(12) for name in names)
    lines.append(header)
    lines.append("-" * len(header))
    for label, key in TABLE_ROWS:
        cells = []
        for report in reports:
            value = report["metrics"].get(key, 0)
            cells.append(f"{value:12.4f}" if isinstance(value, float) else f"{value:12d}")
        lines.append(label.ljust(width) + "".join(cells))
    evidence = reports[0].get("score_evidence") if reports else None
    if evidence:
        lines.append(
            "relevant-claim similarity: "
            + ", ".join(f"{k}={v}" for k, v in evidence["relevant_claim_similarity"].items())
        )
        lines.append(
            "negative-control best returned similarity: "
            + ", ".join(
                f"{k}={v}" for k, v in evidence["negative_control_best_returned_similarity"].items()
            )
        )
        lines.append("floor curve, " + reports[0]["strategy"]["name"] + ":")
        lines.append("   floor  recall@10  neg_fp  empty  mean_returned")
        for row in evidence["floor_curve"]:
            lines.append(
                f"   {row['floor']:.2f}   {row['recall@10']:.4f}   "
                f"{row['negative_false_positive_rate']:.2f}   {row['empty_result_rate']:.2f}   "
                f"{row['mean_returned']:.2f}"
            )
    missed = [
        (report["strategy"]["name"], report["metrics"]["missed_queries"]) for report in reports
    ]
    for name, rows in missed:
        if rows:
            lines.append(f"missed queries, {name}: {len(rows)}")
            for row in rows[:5]:
                lines.append(f"   [{row['kind']}] {row['query'][:88]}")
    return "\n".join(lines)


def evaluate(
    *,
    corpus_path: str | Path = CORPUS_PATH,
    strategies: Sequence[str] = ("baseline",),
    limit: int = DEFAULT_LIMIT,
    cache_path: str | Path = EMBEDDINGS_PATH,
    rebake: bool = False,
    decoys: int = DEFAULT_DECOYS,
    stale: int = DEFAULT_STALE,
    twins: int | None = DEFAULT_TWINS,
    geometry: str = DEFAULT_STALE_GEOMETRY,
    database: DisposableDatabase | None = None,
) -> dict:
    """Run the whole harness once and return the report document.

    Parameters: the corpus path, the strategy names, the caller limit, the embedding
    cache path, a rebake flag, the fixture sizes and an optional already-started
    database (a caller can pass one it owns).
    What it does: loads and validates the corpus, bakes or reuses the embedding cache,
    starts a disposable pgvector database, seeds the fixture, then measures each
    strategy against the same seeded rows.
    Output: the JSON report document. The caller prints or writes it.
    """
    selected = registered()
    missing = [name for name in strategies if name not in selected]
    if missing:
        raise UnknownStrategy(
            f"Unknown strategy {', '.join(missing)}; registered: {', '.join(sorted(selected))}"
        )
    if geometry not in STALE_GEOMETRIES:
        raise EvaluationError(f"Unknown stale geometry {geometry!r}")
    if twins is not None and twins > 0 and geometry == "unrelated":
        raise EvaluationError("Positive twins require similar or duplicates stale geometry")
    corpus = load_corpus(corpus_path)
    embeddings = bake_embeddings(corpus.texts(), path=cache_path, rebake=rebake)
    if database is None:
        with DisposableDatabase.start() as owned:
            return _run(
                owned,
                corpus,
                embeddings,
                [selected[name] for name in strategies],
                limit=limit,
                decoys=decoys,
                stale=stale,
                twins=twins,
                geometry=geometry,
            )
    return _run(
        database,
        corpus,
        embeddings,
        [selected[name] for name in strategies],
        limit=limit,
        decoys=decoys,
        stale=stale,
        twins=twins,
        geometry=geometry,
    )


def _run(
    database: DisposableDatabase,
    corpus: Corpus,
    embeddings: Embeddings,
    strategies: Sequence[Strategy],
    *,
    limit: int,
    decoys: int,
    stale: int,
    twins: int | None,
    geometry: str,
) -> dict:
    fixture = seed_fixture(
        database,
        corpus,
        embeddings,
        model_name=embeddings.model,
        decoys=decoys,
        stale=stale,
        twins=twins,
        geometry=geometry,
    )
    reports = [
        run_strategy(database, corpus, embeddings, fixture, strategy, limit=limit)
        for strategy in strategies
    ]
    return build_report(
        corpus=corpus, embeddings=embeddings, fixture=fixture, reports=reports, limit=limit
    )


def write_report(document: Mapping, path: str | Path) -> Path:
    """Write the JSON report next to the corpus.

    Parameters: the report document and the destination path.
    What it does: creates the parent directory and writes indented JSON.
    Output: the path written.
    """
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(document, indent=2))
    return destination
