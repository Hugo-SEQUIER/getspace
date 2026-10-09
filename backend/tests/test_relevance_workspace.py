"""Product wiring, citation and live-authorization tests with controlled assessments."""

import json
from uuid import UUID, uuid4

import pytest
from test_memora_workspace import (
    Models,
    delegated,
    record,
    setup,
)
from test_memora_workspace import command_database as command_database
from test_memora_workspace import commands as commands
from test_memora_workspace import database as database

from space import relevance
from space.memory import RecallRequest, RememberClaims
from space.workspace import CommandError


@pytest.fixture
def recalled(commands):
    service, owner, space, source, build = setup(commands)
    built = service.build_memory(owner, space, build)
    return service, owner, space, source, built


def assess(monkeypatch, choose, before=lambda: None):
    calls = []

    def invoke(_session, input, prompt_args=None, response_format=None, **kwargs):
        before()
        if response_format is relevance.EvidenceConflicts:
            return {"conflicts": []}
        assert response_format is relevance.EvidenceAssessment
        payload = json.loads(input[input.index('{"query":') :])
        calls.append(payload)
        return {
            "temporal_intent": "historical" if "Previous" in payload["query"] else "unspecified",
            "answer": "fixture answer",
            "answer_quotes": [
                {"candidate": c["candidate"], "quote": c["text"][:100]}
                for c in payload["candidates"]
            ],
            "assessments": [
                {"candidate": candidate["candidate"], "relevance": choose(candidate)}
                for candidate in payload["candidates"]
            ],
        }

    # Wrap each synthetic provider session without changing embedding behavior.
    return calls, invoke


def wire(service, monkeypatch, choose, before=lambda: None):
    calls, invoke = assess(monkeypatch, choose, before)
    original = service.memory_models.session

    def session(authorize):
        value = original(authorize)
        monkeypatch.setattr(type(value), "invoke", invoke)
        return value

    monkeypatch.setattr(service.memory_models, "session", session)
    return calls


def test_negative_rejects_memory_and_lexical_sources_and_preserves_related_separation(
    recalled, monkeypatch
):
    service, owner, space, _, _ = recalled
    calls = wire(service, monkeypatch, lambda _: "irrelevant")
    result = service.context(
        owner,
        space,
        RecallRequest(
            query="Mira car insurance price",
            include_related=True,
        ),
    )
    assert not result["found"] and result["items"] == []
    assert "Do not invent" in result["note"]
    assert len(calls) == 1
    assert any(c["title"] == "Commute source" for c in calls[0]["candidates"])


def test_paraphrase_retains_exact_claim_and_source_revision(recalled, monkeypatch):
    service, owner, space, source, built = recalled
    wire(service, monkeypatch, lambda c: "direct" if "car" in c["text"] else "irrelevant")
    service.memory_models.calls.clear()
    result = service.context(owner, space, RecallRequest(query="automobile", strategy="semantic"))
    claim = result["items"][0]
    assert claim["id"] == built["id"] and claim["relevance"] == "direct"
    assert claim["sources"][0]["resource_id"] == source["id"]
    assert claim["sources"][0]["revision"] == 1
    assert claim["sources"][0]["url"].endswith("&revision=1")
    assert claim["entry_id"] == record(service, owner, space, built)["claims"][0]["id"]
    assert [c[1] for c in service.memory_models.calls if c[0] == "embed"] == [["automobile"]]


def test_revocation_during_assessment_denies_release(recalled, monkeypatch):
    service, owner, space, _, _ = recalled
    reader = delegated(service, owner)
    wire(
        service,
        monkeypatch,
        lambda _: "direct",
        lambda: service.revoke(owner, UUID(reader.client_id)),
    )
    with pytest.raises(CommandError) as error:
        service.context(reader, space, RecallRequest(query="automobile"))
    assert error.value.status in {401, 403}


def test_source_revision_change_is_supplied_to_assessor(recalled, monkeypatch):
    service, owner, space, source, _ = recalled
    from space.workspace import SaveResource

    service.save(
        owner,
        space,
        SaveResource(
            command_id=uuid4(),
            resource_id=UUID(source["id"]),
            expected_revision=1,
            kind="document",
            title="Commute source",
            body="Details removed.",
        ),
    )
    calls = wire(service, monkeypatch, lambda c: "historical" if c["stale"] else "irrelevant")
    result = service.context(owner, space, RecallRequest(query="Previous automobile commute?"))
    assert any(c["stale"] for c in calls[0]["candidates"])
    assert result["items"] and all(i["stale"] for i in result["items"])
    assert all(i["relevance"] == "historical" for i in result["items"])


def test_support_can_come_from_candidate_beyond_the_old_32_resource_load_cap(commands, monkeypatch):
    service, owner, space, _ = commands
    service.memory_models = Models()
    saved = []
    for index in range(40):
        memory = service.remember_claims(
            owner,
            space,
            RememberClaims(
                command_id=uuid4(),
                title=f"Route {index}",
                claims=[{"index": "route", "value": f"Mira travels by car on route {index}."}],
            ),
        )
        saved.append((memory["memory"]["claims"][0]["id"], memory))
    # Every fixture vector ties, so the largest claim ID is candidate 40.
    _, last = max(saved, key=lambda entry: entry[0])
    target = last["memory"]["claims"][0]["value"]
    calls = wire(service, monkeypatch, lambda c: "direct" if c["text"] == target else "irrelevant")
    result = service.context(owner, space, RecallRequest(query="automobile", limit=10))
    assert len(calls[0]["candidates"]) == 40
    assert [i["id"] for i in result["items"]] == [last["id"]]


def typesafe_wire(service, monkeypatch, before=lambda: None):
    from test_typesafe_relevance import configured, jev_response, session

    from space.model_provider import MemoryModels

    def answer(_request, payload):
        before()
        labels = {
            f"c{i}": "direct" if "car" in c["text"] else "irrelevant"
            for i, c in enumerate(payload["state"]["candidates"])
        }
        return jev_response(
            payload, labels, answerable=0.95 if "direct" in labels.values() else 0.05
        )

    _, requests = session(monkeypatch, answer)
    provider = MemoryModels(configured())
    original = service.memory_models.session

    def create(authorize):
        value = provider.session(authorize)
        # Preserve deterministic query embeddings and fixture memory fingerprints;
        # actual provider transport, quotas and database authorization handle Jev.
        value.embed = original(authorize).embed
        return value

    monkeypatch.setattr(service.memory_models, "session", create)
    return requests


def test_typesafe_recall_preserves_claim_and_real_source_citations(recalled, monkeypatch):
    service, owner, space, source, built = recalled
    requests = typesafe_wire(service, monkeypatch)
    result = service.context(owner, space, RecallRequest(query="automobile", strategy="semantic"))
    assert len(requests) == 1 and requests[0][0].startswith("https://api.typesafe.ai/")
    claim = result["items"][0]
    assert claim["id"] == built["id"] and claim["excerpt"] == "Mira travels by car."
    assert claim["sources"][0]["resource_id"] == source["id"]
    assert claim["sources"][0]["revision"] == 1
    assert claim["sources"][0]["url"].endswith("&revision=1")
    assert claim["entry_id"] == record(service, owner, space, built)["claims"][0]["id"]


def test_typesafe_revocation_during_network_call_denies_release_without_fallback(
    recalled, monkeypatch
):
    service, owner, space, _, _ = recalled
    reader = delegated(service, owner)
    requests = typesafe_wire(
        service, monkeypatch, lambda: service.revoke(owner, UUID(reader.client_id))
    )
    with pytest.raises(CommandError) as error:
        service.context(reader, space, RecallRequest(query="automobile"))
    assert error.value.status in {401, 403}
    assert len(requests) == 1


def test_typesafe_is_not_called_for_lexical_or_resume(recalled, monkeypatch):
    service, owner, space, _, _ = recalled
    requests = typesafe_wire(service, monkeypatch)
    service.context(owner, space, RecallRequest(query="car", strategy="lexical"))
    service.context(owner, space, RecallRequest(mode="resume"))
    assert requests == []
