"""The retrieval-quality CLI named in docs/RETRIEVAL.md.

Parameters: a labelled corpus, a strategy name, the caller limit and an optional
JSON destination.
What it does: starts a disposable pgvector database, seeds the corpus and measures
one or more strategies through the product's own retrieval SQL.
Output: a readable table on stdout, and a JSON report when --json is given.
"""

import argparse
import sys
from pathlib import Path

from space.evaluation import (
    CORPUS_PATH,
    DEFAULT_DECOYS,
    DEFAULT_FLOORS,
    DEFAULT_LIMIT,
    DEFAULT_STALE,
    DEFAULT_STALE_GEOMETRY,
    DEFAULT_TWINS,
    EMBEDDINGS_PATH,
    PRODUCT_CLAIMS,
    PRODUCT_FLOORS,
    PRODUCT_QUERIES,
    STALE_GEOMETRIES,
    EvaluationError,
    bake_embeddings,
    evaluate,
    floor_strategies,
    format_table,
    load_corpus,
    registered,
    run_product_evaluation,
    write_report,
)


def parse_args(argv=None):
    """Parse the command line.

    Parameters: the argument list, defaulting to sys.argv.
    What it does: defines the documented flags plus fixture-size controls.
    Output: the parsed namespace.
    """
    parser = argparse.ArgumentParser(
        description="Measure retrieval quality of the memory brain on a labelled corpus."
    )
    parser.add_argument("--corpus", default=str(CORPUS_PATH), help="labelled corpus JSON")
    parser.add_argument(
        "--strategy",
        action="append",
        default=None,
        help="strategy name from the registry; repeat for a comparison",
    )
    parser.add_argument(
        "--all-strategies", action="store_true", help="measure every registered strategy"
    )
    parser.add_argument("--json", default=None, help="write the full report to this path")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="caller item limit")
    parser.add_argument("--embeddings", default=str(EMBEDDINGS_PATH), help="embedding cache")
    parser.add_argument("--rebake", action="store_true", help="ignore the embedding cache")
    parser.add_argument(
        "--decoys",
        type=int,
        default=DEFAULT_DECOYS,
        help="rows to seed in a second tenant, so the search filter must reject them",
    )
    parser.add_argument(
        "--stale",
        type=int,
        default=DEFAULT_STALE,
        help="rows to seed in the evaluated Space under an older embedding model",
    )
    parser.add_argument(
        "--geometry",
        choices=STALE_GEOMETRIES,
        default=DEFAULT_STALE_GEOMETRY,
        help="stale-row geometry (default: unrelated; duplicates is a synthetic control)",
    )
    parser.add_argument(
        "--twins",
        type=int,
        default=DEFAULT_TWINS,
        help="near-copy stale rows for similar/duplicates geometry (default: one per claim)",
    )
    parser.add_argument(
        "--sweep-threshold",
        action="store_true",
        help="measure every registered similarity floor, to choose a relevance floor",
    )
    parser.add_argument(
        "--floors",
        default=None,
        help="comma-separated floors for --sweep-threshold, e.g. 0.30,0.35,0.40",
    )
    parser.add_argument(
        "--product-path",
        action="store_true",
        help="measure the product layer (Workspace.context) and sweep query_score_threshold",
    )
    parser.add_argument(
        "--product-claims", type=int, default=PRODUCT_CLAIMS, help="claims in the product fixture"
    )
    parser.add_argument(
        "--product-queries",
        type=int,
        default=PRODUCT_QUERIES,
        help="queries in the product fixture",
    )
    parser.add_argument("--list-strategies", action="store_true", help="print strategies and exit")
    args = parser.parse_args(argv)
    if args.twins is not None and args.twins > 0 and args.geometry == "unrelated":
        parser.error("positive --twins requires --geometry similar or --geometry duplicates")
    return args


def print_product_report(
    corpus_path, cache_path, *, floors, limit, claims, queries, destination, rebake=False
):
    """Measure the product path and print one row per expansion mode and threshold.

    Parameters: the corpus, the embedding cache, the thresholds to sweep, the caller
    limit, the fixture size and an optional JSON destination.
    What it does: runs `Workspace.context` end to end and reports the metrics with the
    layer named, so an index-layer number is never confused with a user-visible one.
    Output: the table on stdout, and a JSON report when a destination is given.
    """
    corpus = load_corpus(corpus_path)
    embeddings = bake_embeddings(corpus.texts(), path=cache_path, rebake=rebake)
    report = run_product_evaluation(
        corpus, embeddings, floors=floors, limit=limit, claims=claims, queries=queries
    )
    print(
        f"layer: {report['layer']} - {report['fixture']['path']} "
        f"({report['fixture']['claims_indexed']} claims, "
        f"{report['corpus']['queries_used']} queries)"
    )
    print(
        "relevant-claim similarity: "
        + ", ".join(f"{key}={value}" for key, value in report["relevant_claim_similarity"].items())
    )
    header = (
        "expansion   threshold  recall@10  mrr@10  ndcg@10  neg_fp  empty  mean_returned  p50_ms"
    )
    print(header)
    for combination in report["combinations"]:
        for entry in combination["thresholds"]:
            metrics = entry["metrics"]
            print(
                f"{combination['expansion']:10s}  {entry['threshold']:9.2f}  "
                f"{metrics['recall@10']:.4f}  {metrics['mrr@10']:.4f}  "
                f"{metrics['ndcg@10']:.4f}  {metrics['negative_false_positive_rate']:.2f}  "
                f"{metrics['empty_result_rate']:.2f}  {metrics['mean_returned']:.2f}  "
                f"{metrics['latency_p50_ms']:.1f}"
            )
    if destination:
        print("wrote " + str(write_report(report, Path(destination))))


def main(argv=None) -> int:
    """Run the harness and print its table.

    Parameters: the argument list.
    What it does: resolves the strategy selection, runs the evaluation and reports
    failures as a single content-free line.
    Output: a process exit code.
    """
    args = parse_args(argv)
    try:
        available = registered()
        if args.list_strategies:
            for name, strategy in sorted(available.items()):
                print(f"{name:22s} {strategy.description}")
            return 0
        if args.product_path:
            print_product_report(
                args.corpus,
                args.embeddings,
                floors=[float(value) for value in args.floors.split(",")]
                if args.floors
                else list(PRODUCT_FLOORS),
                limit=args.limit,
                claims=args.product_claims,
                queries=args.product_queries,
                destination=args.json,
                rebake=args.rebake,
            )
            return 0
        names = ["baseline"] if args.all_strategies else list(args.strategy or ["baseline"])
        if args.all_strategies:
            names = sorted(available)
        if args.sweep_threshold:
            floors = (
                [float(value) for value in args.floors.split(",")]
                if args.floors
                else list(DEFAULT_FLOORS)
            )
            names += [
                strategy.name for strategy in floor_strategies(floors) if strategy.name not in names
            ]
        document = evaluate(
            corpus_path=args.corpus,
            strategies=names,
            limit=args.limit,
            cache_path=args.embeddings,
            rebake=args.rebake,
            decoys=args.decoys,
            stale=args.stale,
            twins=args.twins,
            geometry=args.geometry,
        )
    except EvaluationError as error:
        print("FAILED " + str(error))
        return 1
    print(format_table(document))
    if args.json:
        print("wrote " + str(write_report(document, Path(args.json))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
