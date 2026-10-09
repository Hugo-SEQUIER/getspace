import json
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from space import memory_classification as classifier
from space import memory_pipeline
from space.memory import ChangeMemory, MemoryAssignment
from space.model_provider import TYPESAFE_MODEL, ModelError, TypeSafeError


def catalog():
    section = {
        "id": str(uuid4()),
        "kind": "section",
        "name": "Work",
        "description": "Work projects",
    }
    return [
        {"id": str(uuid4()), "kind": "category", "name": "Dev", "description": "Code and tests"},
        {"id": str(uuid4()), "kind": "category", "name": "Accounting", "description": "Invoices"},
        section,
        {
            "id": str(uuid4()),
            "kind": "project",
            "name": "Atlas",
            "description": "Billing app",
            "section_id": section["id"],
        },
    ]


def response(payload, scores=None):
    return {
        "model": TYPESAFE_MODEL,
        "usage": {"input_tokens": 10, "output_tokens": 10},
        "answers": {
            key: {"type": "noul", "noul": (scores or {}).get(key, 0.01)}
            for key in payload["questions"]
        },
    }


def test_multilabel_thresholds_and_derived_parent_section():
    items = catalog()
    result = classifier.select(
        {
            "global": {"noul": 0.99},
            "i0": {"noul": 0.9},
            "i1": {"noul": 0.86},
            "i2": {"noul": 0.1},
            "i3": {"noul": 0.92},
        },
        items,
    )
    assert result["scope"] == "project"
    assert result["category_ids"] == [items[0]["id"], items[1]["id"]]
    assert result["section_ids"] == [items[2]["id"]]
    assert result["project_ids"] == [items[3]["id"]]


def test_unknown_is_not_global_and_independent_categories_are_allowed():
    items = catalog()
    payload = classifier.payload_for({"index": "Fact", "value": "Unclear"}, items)
    answers = response(payload)["answers"]
    assert classifier.select(answers, items) == classifier.empty()
    answers["i0"]["noul"] = 0.86
    result = classifier.select(answers, items)
    assert result["scope"] == "unassigned" and result["category_ids"] == [items[0]["id"]]
    answers["global"]["noul"] = 0.95
    assert classifier.select(answers, items)["scope"] == "global"


def test_provider_sees_context_but_no_private_identifiers():
    items = catalog()
    payload = classifier.payload_for(
        {"index": "Atlas", "value": "Invoice tests", "id": str(uuid4())}, items
    )
    encoded = json.dumps(payload)
    assert "Billing app" in encoded and "Invoice tests" in encoded
    assert not any(item["id"] in encoded for item in items)


@pytest.mark.parametrize("bad", [True, float("nan"), float("inf"), -0.1, 1.1, "0.99"])
def test_malformed_probabilities_abstain(bad):
    def evaluate(payload):
        return response(payload, {"global": bad})

    result = classifier.classify(
        {"index": "x", "value": "y"}, [], SimpleNamespace(evaluate_typesafe=evaluate)
    )
    assert result["reason"] == "unavailable" and result["scope"] == "unassigned"


def test_size_catalog_and_provider_failures_leave_memory_unassigned():
    def fail(payload):
        raise TypeSafeError("Unavailable")

    model = SimpleNamespace(evaluate_typesafe=fail)
    claim = {"index": "x", "value": "y"}
    assert classifier.classify(claim, None, model)["reason"] == "catalog_limit"
    assert (
        classifier.classify(claim, [{**catalog()[0], "description": "x" * 12000}], model)["reason"]
        == "input_limit"
    )
    assert classifier.classify(claim, [], model)["reason"] == "unavailable"

    def fatal(payload):
        raise ModelError("Quota exhausted")

    with pytest.raises(ModelError, match="Quota"):
        classifier.classify(claim, [], SimpleNamespace(evaluate_typesafe=fatal))


def test_rebuild_keeps_manual_assignments_and_forgetting_still_wins():
    manual = {**classifier.empty(), "method": "manual"}
    claim = {
        "id": str(uuid4()),
        "index": "Policy",
        "value": "Original",
        "created_at": "today",
        "classification": manual,
    }
    old = {"claims": [claim]}
    rebuilt = {
        "claims": [{**deepcopy(claim), "value": "New", "classification": classifier.empty()}]
    }
    memory_pipeline.retain_edits(rebuilt, old)
    assert rebuilt["claims"][0]["classification"] == manual
    old["forgotten"] = [claim["id"]]
    memory_pipeline.retain_edits(rebuilt, old)
    assert rebuilt["claims"] == []


def test_assignment_invariants_and_explicit_classify_action():
    for value in (
        {"scope": "global", "project_ids": [uuid4()]},
        {"scope": "project"},
        {"category_ids": ["invented"]},
    ):
        with pytest.raises(ValidationError):
            MemoryAssignment.model_validate(value)
    with pytest.raises(ValidationError):
        ChangeMemory(
            command_id=uuid4(),
            resource_id=uuid4(),
            expected_revision=1,
            entry_id=uuid4(),
            action="forget",
            classification={},
        )


def test_classification_processor_is_disclosed_without_changing_embedding_identity():
    from space.guide import provider_line
    from space.model_provider import MemoryModels
    from space.settings import Settings

    config = {
        "environment": "test",
        "memory_provider": "openai_compatible",
        "memory_provider_url": "https://primary.invalid",
        "memory_text_model": "text",
        "memory_embedding_model": "embedding",
        "memory_classification_provider": "typesafe",
    }
    with pytest.raises(ValidationError):
        Settings(**config)
    models = MemoryModels(Settings(**config, typesafe_api_key="test-only"))
    disabled = MemoryModels(
        models.settings.model_copy(update={"memory_classification_provider": "disabled"})
    )
    assert models.chat_fingerprint == disabled.chat_fingerprint
    assert models.embedding_fingerprint == disabled.embedding_fingerprint
    assert models.info()["classification_provider"] == "typesafe"
    assert models.info()["typesafe_model"] == TYPESAFE_MODEL
    assert "Memory writes also send each claim" in provider_line(models)


def test_sections_are_never_asked_and_come_only_from_matched_projects():
    items = catalog()
    seen = []

    def evaluate(payload):
        seen.append(payload)
        scores = {
            f"i{n}": 0.99
            for n, item in enumerate(payload["state"]["catalog"])
            if item["kind"] == "category"
        }
        return response(payload, scores)

    model = SimpleNamespace(evaluate_typesafe=evaluate)
    claim = {"index": "Work habit", "value": "Work on code reviews every morning"}
    result = classifier.classify(claim, items, model)
    assert all(item["kind"] != "section" for item in seen[0]["state"]["catalog"])
    assert result["section_ids"] == [] and result["project_ids"] == []
    assert result["category_ids"] == [items[0]["id"], items[1]["id"]]
