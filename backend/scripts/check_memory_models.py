"""Opt-in provider smoke test using synthetic content and no live Space writes.

Costs real provider usage. Covers both extraction paths, because they behave
differently: the chat builder (remember with memory_type="chat", or build_memory
over a saved handoff) extracts one claim per distinct fact, while build_memory
over a document keeps a coherent segment as one memory by upstream design.
"""

import json
import time
from datetime import UTC, datetime
from uuid import uuid4

from space.memory import RecallRequest
from space.memory_pipeline import build, retrieve
from space.model_provider import MemoryModels
from space.settings import CONFIG_FILE, Settings

FACTS = (
    "Mira travels to work by car. The Juniper team chose a staged rollout. "
    "The next action is testing the rollback procedure. The launch date is unresolved."
)
BUILDERS = (("chat", "handoff", 2), ("document", "source", 1))


def probe_embeddings(models):
    """Fail fast when the embedding endpoint is unreachable.

    Chat and embeddings can live on different services, because a provider can serve
    chat completions without an embedding route. Running this first keeps a broken
    embedding endpoint from surfacing as a confusing extraction error.
    """
    started = time.monotonic()
    vectors = models.session(lambda: None).embed(["Synthetic embedding probe."])
    if not vectors or not vectors[0]:
        raise RuntimeError("The embedding endpoint returned no vector")
    return {
        "dimensions": len(vectors[0]),
        "latency_ms": round((time.monotonic() - started) * 1000),
    }


def check(models, label, category, minimum_claims):
    batch_id = uuid4()
    source = {
        "id": str(uuid4()),
        "revision": 1,
        "kind": "document",
        "status": "todo",
        "title": "Synthetic continuity check",
        "author": "agent",
        "updated_at": datetime.now(UTC).isoformat(),
        "body": FACTS,
        "recall": {"category": category, "topics": [], "sources": [], "excluded": False},
    }
    built = build(source, batch_id, models.session(lambda: None), models.embedding_fingerprint)
    record = built["value"]
    if len(record["claims"]) < minimum_claims:
        raise RuntimeError(f"The {label} builder extracted too few claims")
    resource = {
        **source,
        "id": str(batch_id),
        "_memory": json.loads(json.dumps(record)),
        "recall": {
            "category": "memory",
            "topics": [],
            "excluded": False,
            "sources": [{"resource_id": source["id"], "revision": 1}],
        },
    }
    allowed = {str(batch_id): {claim["id"] for claim in record["claims"]}}
    recall = {}
    for strategy in ("semantic", "prompt"):
        hits = retrieve(
            [resource],
            RecallRequest(query="How does Mira commute?", strategy=strategy),
            models.session(lambda: None),
            allowed,
        )
        recall[strategy] = any("car" in item[0]["body"].lower() for item in hits)
    return {
        "claims": len(record["claims"]),
        "cue_entries": len(record["entries"]),
        "index_rows": len(built["index"]),
        "recall": recall,
    }


def main():
    models = MemoryModels(Settings(_env_file=CONFIG_FILE))
    embeddings = probe_embeddings(models)
    results = {}
    for label, category, minimum in BUILDERS:
        results[label] = check(models, label, category, minimum)
    # Only the builder results carry a recall outcome; the embedding probe has none.
    failed = [label for label, value in results.items() if not all(value["recall"].values())]
    if failed:
        raise RuntimeError("Memory model smoke test did not recover the synthetic evidence")
    print(
        json.dumps(
            {
                "provider": models.info()["provider"],
                "chat_endpoint": models.info()["endpoint"],
                "embedding_endpoint": models.info()["embedding_endpoint"],
                "text_model": models.info()["text_model"],
                "relevance_model": models.info()["relevance_model"],
                "embedding_model": models.info()["embedding_model"],
                "checks": {"embeddings": embeddings, **results},
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
