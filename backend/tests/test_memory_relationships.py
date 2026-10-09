from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from space import brain
from space import memory_relationships as links
from space.memory import MemoryRelationships
from space.model_provider import TYPESAFE_MODEL, ModelError, TypeSafeError


def claim(text="Atlas retries payment webhooks", **extra):
    return {"id": str(uuid4()), "index": "Atlas", "value": text, **extra}


def candidate(text="Atlas webhook retries use idempotency keys"):
    return claim(text, resource_id=str(uuid4()), revision=1)


def response(payload, scores):
    return {
        "model": TYPESAFE_MODEL,
        "usage": {"input_tokens": 10, "output_tokens": 5},
        "answers": {
            key: {"type": "noul", "noul": scores.get(key, 0.01)} for key in payload["questions"]
        },
    }


def test_confident_pairs_survive_an_uncertain_neighbor_without_generation():
    candidates = [candidate(), candidate("Unrelated gardening advice")]
    seen = []

    def evaluate(payload):
        seen.append(payload)
        return response(payload, {"r0": 0.95, "r1": 0.6})

    result = links.classify(claim(), candidates, SimpleNamespace(evaluate_typesafe=evaluate))
    assert result["status"] == "matched"
    assert result["links"] == [
        {
            "resource_id": candidates[0]["resource_id"],
            "entry_id": candidates[0]["id"],
            "revision": 1,
            "relation": "related",
            "confidence": 0.95,
            "source_text_hash": brain.text_hash(claim()["value"]).hex(),
            "target_text_hash": brain.text_hash(candidates[0]["value"]).hex(),
        }
    ]
    assert len(seen) == 1
    assert candidates[0]["id"] not in str(seen)
    assert candidates[0]["resource_id"] not in str(seen)
    assert "full" not in result


def test_no_candidates_do_not_call_jev():
    assert links.classify(claim(), [], object()) == links.result("no_match")


def test_long_complete_claims_are_supported_and_candidate_count_is_bounded():
    seen = []

    def evaluate(payload):
        seen.append(payload)
        return response(payload, {})

    candidates = [candidate("x" * 1600) for _ in range(20)]
    result = links.classify(
        claim("y" * 2000), candidates, SimpleNamespace(evaluate_typesafe=evaluate)
    )
    assert result == links.result("no_match")
    assert len(seen[0]["state"]["candidates"]) == 5
    assert seen[0]["state"]["memory"]["text"] == "y" * 2000
    assert all(c["text"] == "x" * 1600 for c in seen[0]["state"]["candidates"])


@pytest.mark.parametrize("bad", [True, float("nan"), float("inf"), -0.1, 1.1, "0.99"])
def test_invalid_jev_response_is_unavailable_not_a_link(bad):
    model = SimpleNamespace(evaluate_typesafe=lambda p: response(p, {"r0": bad}))
    assert links.classify(claim(), [candidate()], model) == links.result("unavailable")


def test_failure_is_visible_and_authorization_budget_failures_are_fatal():
    def fail(payload):
        raise TypeSafeError("Unavailable")

    assert links.classify(
        claim(), [candidate()], SimpleNamespace(evaluate_typesafe=fail)
    ) == links.result("unavailable")

    def fatal(payload):
        raise ModelError("Quota exhausted")

    with pytest.raises(ModelError, match="Quota"):
        links.classify(claim(), [candidate()], SimpleNamespace(evaluate_typesafe=fatal))


def test_link_contract_requires_valid_targets_unique_links_and_matched_status():
    link = dict(resource_id=uuid4(), entry_id=uuid4(), revision=1, confidence=0.95)
    for value in (
        {"status": "matched"},
        {"status": "no_match", "links": [link]},
        {"status": "matched", "links": [link, link]},
        {"status": "matched", "links": [link | {"confidence": 0.89}]},
    ):
        with pytest.raises(ValidationError):
            MemoryRelationships.model_validate(value)


def test_relationship_provider_requires_credentials_and_is_disclosed():
    from space.guide import provider_line
    from space.model_provider import MemoryModels
    from space.settings import Settings

    values = dict(
        environment="test",
        memory_provider="openai_compatible",
        memory_provider_url="https://example.invalid",
        memory_text_model="openai/gpt-6-luna",
        memory_embedding_model="fixture",
        memory_relationship_provider="typesafe",
    )
    with pytest.raises(ValidationError):
        Settings(**values)
    models = MemoryModels(Settings(**values, typesafe_api_key="test-only"))
    assert models.info()["relationship_provider"] == "typesafe"
    assert models.info()["typesafe_model"] == TYPESAFE_MODEL
    assert "up to five nearby stored claims" in provider_line(models)


def test_inferred_detail_link_is_directional_and_bound_to_both_texts():
    incoming = claim("What I like in my work: helping clients build software")
    target = candidate("My work")
    model = SimpleNamespace(evaluate_typesafe=lambda p: response(p, {"r0": 0.98, "e0": 0.95}))
    saved = links.classify(incoming, [target], model)
    edge = saved["links"][0]
    assert edge["relation"] == "expands_on"
    assert edge["resource_id"] == target["resource_id"]
    assert edge["confidence"] == 0.95
    assert edge["source_text_hash"] == brain.text_hash(incoming["value"]).hex()
    assert edge["target_text_hash"] == brain.text_hash(target["value"]).hex()
