"""Reproducible synthetic lexical benchmark; loads no providers and no user data.

Vector ranking runs inside PostgreSQL through the pgvector index, so this measures
only the in-process part: metadata ranking and BM25 over a bounded set of bodies.
Database and model latency are excluded.
"""

import json
import platform
import statistics
import time
import tracemalloc
from uuid import UUID

from space.memory import ENGINE, MAX_LEXICAL_BODIES, UPSTREAM_COMMIT, rank_lexical


def rows(count):
    return [
        {
            "id": str(UUID(int=i + 1)),
            "title": f"Project {i} handoff needle{i}",
            "topics": [f"project {i}"],
            "body": ("progress decision followup " * 1100) + f"needle{i}",
        }
        for i in range(count)
    ]


def rank_sources(sources, query):
    """Use the product's metadata-first, bounded-body lexical path."""
    summary = [(item, item["title"] + " " + " ".join(item["topics"])) for item in sources]
    head = rank_lexical(summary, query)
    bodies = [(item, item["body"]) for item, _, _ in head[:MAX_LEXICAL_BODIES]]
    return bodies, rank_lexical(bodies, query)


def benchmark(count):
    if count < 1:
        raise ValueError("Benchmark needs at least one resource")
    sources = rows(count)
    # The shared term fills the candidate budget; the needle makes the expected
    # source win both metadata selection and body ranking, even past the body cap.
    query = f"project needle{count - 1}"
    expected_id = sources[-1]["id"]
    times = []
    for _ in range(5):
        started = time.perf_counter()
        bodies, ranked = rank_sources(sources, query)
        times.append((time.perf_counter() - started) * 1000)
        if not ranked or ranked[0][0]["id"] != expected_id:
            raise RuntimeError("Benchmark relevance check failed")
    tracemalloc.start()
    try:
        rank_sources(sources, query)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return {
        "resources": count,
        "indexed_bodies": len(bodies),
        "body_bytes": sum(len(item["body"].encode()) for item in sources),
        "indexed_body_bytes": sum(len(body.encode()) for _, body in bodies),
        "cold_request_ms": round(times[0], 2),
        "median_request_ms": round(statistics.median(times), 2),
        "python_traced_peak_bytes": peak,
        "expected_source_rank": next(
            rank for rank, (item, _, _) in enumerate(ranked, 1) if item["id"] == expected_id
        ),
    }


if __name__ == "__main__":
    print(
        json.dumps(
            {
                "engine": ENGINE,
                "upstream_commit": UPSTREAM_COMMIT,
                "python": platform.python_version(),
                "machine": platform.machine(),
                "scope": (
                    "In-process metadata ranking and BM25 over the selected bounded bodies; "
                    "latency and traced Python peak cover both ranking stages, excluding "
                    "source construction, database, native allocations and models"
                ),
                "runs": [benchmark(n) for n in (1, 32, 128)],
            },
            indent=2,
        )
    )
