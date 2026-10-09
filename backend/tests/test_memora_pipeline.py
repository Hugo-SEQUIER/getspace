import json
from copy import deepcopy
from uuid import UUID, uuid4

import pytest

from space import memory_pipeline as pipeline
from space.memory import RecallRequest
from space.model_provider import ModelError

# The brain stores vector(1536) rows, so the fixture embeddings must be that wide.
DIMENSIONS = 1536
CLAIM_KEYS = {"id", "index", "value", "memory_type", "edited", "importance", "created_at"}


def fixture_embedding(text):
    """Deterministic unit vectors: travel words differ from everything else."""
    axis = 0 if any(word in text.casefold() for word in ("car", "automobile", "commute")) else 1
    vector = [0.0] * DIMENSIONS
    vector[axis] = 1.0
    return vector


class Models:
    enabled = True
    chat_fingerprint = "fixture-chat-v1"
    embedding_fingerprint = "fixture-embedding-v1"
    embedding_model = "fixture-embedding"

    def __init__(self):
        self.calls = []
        self.before_call = lambda: None
        self.outputs = [
            {"memory_type": "factual", "index": "Mira commute", "value": "Mira travels by car."},
            {
                "memory_type": "factual",
                "index": "Outdoor forecast",
                "value": "Heavy rain is forecast.",
            },
        ]
        self.policy = {"action": "STOP"}
        self.cues = []

    def info(self):
        return {
            "enabled": True,
            "provider": "fixture",
            "endpoint": None,
            "text_model": "fixture",
            "embedding_model": self.embedding_model,
        }

    def session(self, authorize):
        parent = self

        class Session:
            def invoke(self, input, prompt_args=None, response_format=None, **kwargs):
                parent.before_call()
                authorize()
                parent.calls.append(("model", input, deepcopy(prompt_args)))
                if response_format is None:
                    # A callable policy reads the frontier out of the real prompt.
                    return (
                        deepcopy(parent.policy)
                        if isinstance(parent.policy, dict)
                        else parent.policy(input)
                    )
                name = response_format.__name__
                if name == "MemoryOutputs":
                    value = {"entries": deepcopy(parent.outputs)}
                elif name == "BatchCueIndices":
                    value = {
                        "results": [
                            {"memory_index": c["index"], "cue_indices": parent.cues}
                            for c in parent.outputs
                        ]
                    }
                elif name == "MemoryUpdateDecision":
                    value = {"should_update": False}
                elif name == "EvidenceConflicts":
                    value = {"conflicts": []}
                elif name == "EvidenceAssessment":
                    payload = json.loads(input[input.index('{"query":') :])
                    query_vector = fixture_embedding(payload["query"])
                    value = {
                        "temporal_intent": "historical"
                        if "previous" in payload["query"]
                        else "unspecified",
                        "answer": "fixture answer",
                        "answer_quotes": [
                            {"candidate": c["candidate"], "quote": c["text"][:100]}
                            for c in payload["candidates"]
                        ],
                        "assessments": [
                            {
                                "candidate": c["candidate"],
                                "relevance": (
                                    "historical"
                                    if c["stale"] and "previous" in payload["query"]
                                    else "direct"
                                )
                                if fixture_embedding(c["text"]) == query_vector
                                else "irrelevant",
                            }
                            for c in payload["candidates"]
                        ],
                    }
                else:
                    raise AssertionError(name)
                return response_format.model_validate(value)

            def embed(self, texts):
                parent.before_call()
                authorize()
                parent.calls.append(("embed", list(texts)))
                # Controlled semantic equivalence without lexical overlap.
                return [fixture_embedding(text) for text in texts]

        return Session()


def source():
    return {
        "id": str(uuid4()),
        "revision": 1,
        "title": "Transport and weather",
        "body": "Mira travels by car. Heavy rain is forecast.",
        "updated_at": "2026-09-12T00:00:00+00:00",
        "kind": "document",
        "status": "todo",
        "author": "agent",
        "recall": {"category": "source", "topics": [], "sources": [], "excluded": False},
    }


def batch(model, original=None, memory_id=None):
    """Build one memory resource as a caller of the pipeline sees it."""
    original = original or source()
    id_ = memory_id or uuid4()
    data = pipeline.build(original, id_, model.session(lambda: None), model.embedding_fingerprint)
    return {
        **original,
        "id": str(id_),
        "_memory": data["value"],
        "recall": {
            "category": "memory",
            "excluded": False,
            "topics": [],
            "sources": [{"resource_id": original["id"], "revision": original["revision"]}],
        },
    }


def allowed(*resources):
    """The vector index selects claim IDs, as Workspace reports them."""
    return {
        resource["id"]: {claim["id"] for claim in resource["_memory"]["claims"]}
        for resource in resources
    }


def embeds(model):
    return [call[1] for call in model.calls if call[0] == "embed"]


def test_build_indexes_every_claim_immediately_and_retrieves_a_synonym(capsys):
    models = Models()
    resource = json.loads(json.dumps(batch(models)))
    claims = resource["_memory"]["claims"]
    assert all(set(claim) == CLAIM_KEYS and claim["edited"] is False for claim in claims)
    assert claims[0]["index"] == "Mira commute" and claims[0]["value"] == "Mira travels by car."
    # The index rows carry the claim identity and text the brain stores as a hash.
    assert [row["id"] for row in resource["_memory"]["claims"]] == [c["id"] for c in claims]
    assert list(resource["_memory"]) == [
        "version",
        "provider_fingerprint",
        "claims",
        "forgotten",
        "entries",
        "build_stats",
    ]
    fresh = Models()
    request = RecallRequest(query="automobile", strategy="semantic")
    result = pipeline.retrieve([resource], request, fresh.session(lambda: None), allowed(resource))
    assert result[0][0]["body"] == "Mira travels by car."
    assert result[0][0]["verification"] == "memory"
    assert result[0][1] == "memory_semantic"
    # An empty or narrower selection must not reach a claim outside the index hits.
    assert pipeline.retrieve([resource], request, fresh.session(lambda: None), {}) == []
    rain = [claim for claim in claims if claim["index"] == "Outdoor forecast"]
    only_rain = {resource["id"]: {rain[0]["id"]}}
    assert "Mira travels by car." not in [
        view["body"]
        for view, _ in pipeline.retrieve(
            [resource], request, fresh.session(lambda: None), only_rain
        )
    ]
    assert capsys.readouterr().out == ""


def test_prompted_retrieval_uses_actual_policy_and_provider_errors_fail_closed():
    models = Models()
    resource = batch(models)
    models.calls.clear()
    result = pipeline.retrieve(
        [resource],
        RecallRequest(query="automobile", strategy="prompt"),
        models.session(lambda: None),
        allowed(resource),
    )
    assert result and any(call[0] == "model" for call in models.calls)
    # A chat-model change does not invalidate stored vectors or cues: the same memory is
    # still retrievable, because the caller's candidate set is what bounds the result.
    assert pipeline.retrieve(
        [resource],
        RecallRequest(query="automobile"),
        models.session(lambda: None),
        allowed(resource),
    )


def test_change_edits_or_forgets_one_claim_and_refreshes_its_index_row():
    models = Models()
    resource = batch(models)
    record = deepcopy(resource["_memory"])
    car, rain = (claim["id"] for claim in record["claims"])
    assert record["forgotten"] == []
    pipeline.change(record, car, "edit", "Mira commutes by electric automobile.")
    assert record["claims"][0]["edited"] is True
    assert record["claims"][0]["value"] == "Mira commutes by electric automobile."
    rows = pipeline.index_rows(record)
    assert [row["id"] for row in rows] == [car, rain]
    assert rows[0]["text"] == "Mira commutes by electric automobile."
    assert [row["entry_index"] for row in rows] == [0, 1]
    pipeline.change(record, rain, "forget")
    assert [claim["id"] for claim in record["claims"]] == [car]
    assert record["forgotten"] == [rain]
    assert [row["id"] for row in pipeline.index_rows(record)] == [car]
    assert [
        view["entry_id"] for view in pipeline.virtual_memories(resource | {"_memory": record})
    ] == [car]
    with pytest.raises(KeyError):
        pipeline.change(record, rain, "edit", "Rewritten")
    # An edited claim must not retrieve through the cues generated before the edit.
    edited = resource | {"_memory": record}
    models.calls.clear()
    assert (
        pipeline.retrieve(
            [json.loads(json.dumps(edited))],
            RecallRequest(query="automobile", strategy="semantic"),
            models.session(lambda: None),
            allowed(edited),
        )[0][0]["body"]
        == "Mira commutes by electric automobile."
    )


def test_edited_claim_cannot_be_recalled_through_its_old_generated_cue():
    models = Models()
    models.outputs = models.outputs[:1]
    models.cues = ["Outdoor forecast"]
    resource = batch(models)
    claim = resource["_memory"]["claims"][0]
    assert pipeline.index_rows(resource["_memory"])[0]["cues"] == ["Outdoor forecast"]
    request = RecallRequest(query="forecast", strategy="semantic")

    def retrieve():
        return pipeline.retrieve(
            [resource],
            request,
            models.session(lambda: None),
            allowed(resource),
        )

    assert retrieve()[0][0]["entry_id"] == claim["id"]
    pipeline.change(resource["_memory"], claim["id"], "edit", "Mira commutes by automobile.")
    assert pipeline.index_rows(resource["_memory"])[0]["cues"] == []
    assert retrieve() == []


def test_retain_edits_keeps_human_text_and_forgetting_across_a_rebuild():
    models = Models()
    resource = batch(models)
    previous = deepcopy(resource["_memory"])
    car, rain = (claim["id"] for claim in previous["claims"])
    pipeline.change(previous, car, "edit", "Mira takes the train.")
    pipeline.change(previous, rain, "forget")
    rebuilt = batch(models, memory_id=UUID(resource["id"]))["_memory"]
    assert [claim["id"] for claim in rebuilt["claims"]] == [car, rain]
    pipeline.retain_edits(rebuilt, previous)
    assert [claim["id"] for claim in rebuilt["claims"]] == [car]
    assert rebuilt["claims"][0]["value"] == "Mira takes the train."
    assert rebuilt["claims"][0]["edited"] is True
    assert rebuilt["claims"][0]["generated_value"] == "Mira travels by car."
    assert rebuilt["forgotten"] == [rain]
    assert [row["id"] for row in pipeline.index_rows(rebuilt)] == [car]


def test_no_same_title_merge_between_sources_and_no_unsupported_size():
    models = Models()
    one, two = batch(models), batch(models)
    result = pipeline.retrieve(
        [one, two],
        RecallRequest(query="automobile", strategy="semantic"),
        models.session(lambda: None),
        allowed(one, two),
    )
    assert {r[0]["id"] for r in result} == {one["id"], two["id"]}
    oversized = source() | {"body": "x" * 12001}
    before = len(models.calls)
    with pytest.raises(ModelError, match="12,000"):
        pipeline.build(
            oversized, uuid4(), models.session(lambda: None), models.embedding_fingerprint
        )
    assert len(models.calls) == before


def test_prompted_cue_expansion_reaches_claims_across_source_batches():
    models = Models()
    models.cues = ["Travel planning context"]
    models.outputs = models.outputs[:1]
    one = batch(models)
    models.outputs = [
        {"memory_type": "factual", "index": "Road conditions", "value": "Heavy rain is forecast."}
    ]
    two = batch(models)
    request = RecallRequest(query="travel planning context", strategy="prompt")
    result = pipeline.retrieve(
        [one, two],
        request,
        models.session(lambda: None),
        allowed(one, two),
    )
    # One shared generated cue reaches the approved claim of both source batches.
    assert {r[0]["id"] for r in result} == {one["id"], two["id"]}
    assert {r[0]["body"] for r in result} == {"Mira travels by car.", "Heavy rain is forecast."}
    # The cue graph cannot reach a claim the index did not select.
    assert pipeline.retrieve([one, two], request, models.session(lambda: None), {}) == []
    assert (
        pipeline.retrieve(
            [one, two],
            request,
            models.session(lambda: None),
            {one["id"]: {one["_memory"]["claims"][0]["id"]}},
        )[0][0]["body"]
        == "Mira travels by car."
    )


def test_unchanged_claims_keep_stable_identities_and_row_order():
    models = Models()
    rebuild = batch(models)
    again = batch(models, rebuild, memory_id=UUID(rebuild["id"]))
    assert [c["id"] for c in rebuild["_memory"]["claims"]] == [
        c["id"] for c in again["_memory"]["claims"]
    ]
    assert rebuild["_memory"]["provider_fingerprint"] == Models.embedding_fingerprint
