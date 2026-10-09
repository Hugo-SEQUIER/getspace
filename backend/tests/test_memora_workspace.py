"""Autonomous memory: capture, indexing, change, recall, encryption and grants."""

import json
from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from test_auth import signing as signing_fixture
from test_database_setup import database as database_fixture
from test_memora_pipeline import Models
from test_workspace import (
    change,
    delegated,
)
from test_workspace import (
    command_database as command_database_fixture,
)
from test_workspace import (
    commands as commands_fixture,
)

from space import brain, memory_pipeline
from space.app import create_app
from space.memory import (
    MAX_RELATED,
    ChangeMemory,
    RecallMetadata,
    RecallRequest,
    RememberClaims,
    RememberMemory,
)
from space.memory_pipeline import BuildMemory
from space.model_provider import MemoryModels
from space.settings import Settings
from space.workspace import CommandError, Workspace

database = database_fixture
command_database = command_database_fixture
commands = commands_fixture
signing = signing_fixture

CAR = "Mira travels by car."
RAIN = "Heavy rain is forecast."
CUE = "Travel planning context"
CLAIM_KEYS = {
    "id",
    "index",
    "value",
    "memory_type",
    "edited",
    "importance",
    "created_at",
    "creator",
    "relationships",
}


def setup(commands, body=None):
    service, owner, space, _ = commands
    service.memory_models = Models()
    ref = service.save(
        owner,
        space,
        change().model_copy(update={"title": "Commute source", "body": body or f"{CAR} {RAIN}"}),
    )
    request = BuildMemory(command_id=uuid4(), source={"resource_id": ref["id"], "revision": 1})
    return service, owner, space, ref, request


def fresh(service, **kwargs):
    return Workspace(service.db, service.keys, service.objects, service.resource_url, **kwargs)


def record(service, identity, space, saved):
    """The full decrypted memory record, as Workspace alone can read it."""
    return service.get_resource(
        identity, space, UUID(saved["id"]), saved["revision"], _internal=True
    )["_memory"]


def index_rows(service, identity, space, memory_id):
    with service.db.transaction(identity) as connection:
        service._scope(connection, identity, space)
        return connection.execute(
            "SELECT claim_id, revision, entry_index, memory_type, importance, text_hash, "
            "cue_hashes, embedding_model, source_resource_id, source_revision "
            "FROM space.memory_claims WHERE memory_id=%s ORDER BY entry_index",
            (memory_id,),
        ).fetchall()


def pending(service, identity, space, memory_id, claims):
    with service.db.transaction(identity) as connection:
        service._scope(connection, identity, space)
        return brain.pending(
            connection,
            memory_id=memory_id,
            claims=claims,
            model_name=service.memory_models.embedding_model,
        )


def ciphertexts(service, identity, space):
    """Every stored payload this Space holds."""
    with service.db.transaction(identity) as connection:
        service._scope(connection, identity, space)
        rows = connection.execute(
            "SELECT encrypted_metadata, encrypted_record FROM space.resource_revisions"
        ).fetchall()
        rows += connection.execute("SELECT encrypted_metadata FROM space.spaces").fetchall()
        rows += connection.execute("SELECT encrypted_schema FROM space.collections").fetchall()
    return list(service.objects.values.values()) + [
        bytes(value) for row in rows for value in row.values() if value is not None
    ]


def embeds(service):
    return [call[1] for call in service.memory_models.calls if call[0] == "embed"]


def claim_change(resource, entry_id, action, *, value=None):
    return ChangeMemory(
        command_id=uuid4(),
        resource_id=UUID(resource["id"]),
        expected_revision=resource["revision"],
        entry_id=UUID(entry_id),
        action=action,
        value=value,
    )


def test_build_indexes_every_claim_immediately_for_a_fresh_agent(commands):
    service, owner, space, ref, request = setup(commands)
    writer = delegated(service, owner, write=True)
    saved = service.build_memory(writer, space, request)
    assert saved["recall"]["category"] == "memory"
    assert saved["author"] == "agent"
    claims = saved["memory"]["claims"]
    assert [claim["index"] for claim in claims] == ["Mira commute", "Outdoor forecast"]
    assert all(set(claim) == CLAIM_KEYS and claim["edited"] is False for claim in claims)
    assert all(claim["relationships"]["status"] == "disabled" for claim in claims)
    assert "state" not in json.dumps(saved)
    rows = index_rows(service, owner, space, saved["id"])
    assert [str(row["claim_id"]) for row in rows] == [claim["id"] for claim in claims]
    assert {row["embedding_model"] for row in rows} == {"fixture-embedding"}
    assert {row["revision"] for row in rows} == {1}
    assert {row["entry_index"] for row in rows} == {0, 1}
    assert str(rows[0]["source_resource_id"]) == ref["id"] and rows[0]["source_revision"] == 1
    # A fresh Workspace and an independent reader recall it without a review step.
    other = fresh(service, memory_models=Models())
    reader = delegated(other, owner)
    result = other.context(reader, space, RecallRequest(query="automobile", strategy="semantic"))
    item = result["items"][0]
    assert item["id"] == saved["id"] and item["revision"] == 1
    assert item["verification"] == "memory" and item["edited"] is False
    assert item["entry_id"] == claims[0]["id"] and item["excerpt"] == CAR
    assert item["memory_type"] == "factual"
    assert "review_state" not in item
    assert item["sources"][0]["resource_id"] == ref["id"]
    assert item["url"].endswith("#memory-" + claims[0]["id"])
    assert result["engine"] == brain.ENGINE


def test_recall_attaches_a_similarity_and_an_unrelated_query_is_not_found(commands):
    service, owner, space, ref, request = setup(commands)
    # Build a memory holding only the weather claim, so a travel query has nothing
    # genuinely relevant to find. The fixture maps travel words to one axis and
    # everything else to another, so the cosine similarity between them is exactly 0.
    service.memory_models.outputs = [Models().outputs[1]]
    service.build_memory(owner, space, request)

    unrelated = fresh(service, memory_models=Models()).context(
        owner, space, RecallRequest(query="automobile", strategy="semantic")
    )
    # Nearest-neighbour search always has a nearest, so the index layer alone would
    # return this claim. The product must not report that it found memory.
    assert unrelated["items"] == []
    assert unrelated["found"] is False
    assert "Do not invent" in unrelated["note"]

    # A genuinely related query returns the claim, and each item carries the
    # similarity that produced it, so a caller can see why it matched.
    related = fresh(service, memory_models=Models()).context(
        owner, space, RecallRequest(query="heavy rain", strategy="semantic")
    )
    assert related["found"] is True and related["items"]
    # Recall also returns lexical source evidence, which carries no similarity score, so
    # assert on the memory items: those are the ones the vector search produced.
    memory_items = [item for item in related["items"] if item.get("entry_id")]
    assert memory_items
    assert all(isinstance(item["score"], float) for item in memory_items)
    assert all(item["score"] > 0 for item in memory_items)


def test_build_is_idempotent_readable_and_rejects_forged_memory(commands):
    service, owner, space, _, request = setup(commands)
    writer = delegated(service, owner, write=True)
    saved = service.build_memory(writer, space, request)
    calls = len(service.memory_models.calls)
    assert service.build_memory(writer, space, request) == saved
    assert len(service.memory_models.calls) == calls
    with pytest.raises(CommandError, match="already used"):
        service.build_memory(writer, space, request.model_copy(update={"expected_revision": 2}))
    assert "vectors" not in json.dumps(saved) and "text_hash" not in json.dumps(saved)
    stored = ciphertexts(service, owner, space)
    assert any(CAR.encode() in value for value in stored)
    # Index fields live in the claim index, never inside the memory revision.
    for value in stored:
        for internal in ("text_hash", "vectors"):
            assert internal.encode() not in value
    # Ordinary writes cannot forge generated memory or bypass the index.
    with pytest.raises(CommandError, match="change_memory"):
        service.save(
            writer,
            space,
            change().model_copy(
                update={
                    "resource_id": UUID(saved["id"]),
                    "expected_revision": 1,
                    "body": "Forged memory",
                }
            ),
        )
    with pytest.raises(CommandError, match="build_memory"):
        service.save(
            writer, space, change().model_copy(update={"recall": RecallMetadata(category="memory")})
        )
    # A read-only agent pays for nothing and learns nothing.
    reader = delegated(service, owner)
    service.memory_models.calls.clear()
    with pytest.raises(CommandError) as error:
        service.build_memory(reader, space, request.model_copy(update={"command_id": uuid4()}))
    assert error.value.status == 403
    assert service.memory_models.calls == []


def test_remember_captures_free_text_without_a_review_step(commands):
    service, owner, space, _ = commands
    service.memory_models = Models()
    service.memory_models.outputs = [
        {"memory_type": "factual", "index": "Diet", "value": "Mira is vegetarian."}
    ]
    writer = delegated(service, owner, write=True)
    request = RememberMemory(
        command_id=uuid4(), title="Diet note", text="Mira is vegetarian.", topics=["Health"]
    )
    saved = service.remember(writer, space, request)
    assert saved["recall"]["category"] == "memory"
    assert [claim["value"] for claim in saved["memory"]["claims"]] == ["Mira is vegetarian."]
    assert saved["memory"]["claims"][0]["index"] == request.title
    assert [call[0] for call in service.memory_models.calls] == ["embed"]
    assert len(index_rows(service, owner, space, saved["id"])) == 1
    calls = len(service.memory_models.calls)
    assert service.remember(writer, space, request) == saved
    assert len(service.memory_models.calls) == calls
    listing = service.list_memories(owner, space)["items"]
    assert [entry["id"] for entry in listing] == [saved["id"]]
    assert listing[0]["claims"][0]["edited"] is False
    assert listing[0]["stale"] is False
    other = fresh(service, memory_models=Models())
    result = other.context(owner, space, RecallRequest(query="vegetarian"))
    assert result["items"][0]["entry_id"] == saved["memory"]["claims"][0]["id"]
    assert result["items"][0]["verification"] == "memory"
    # Remembering against a saved revision cites it and refuses a moved source.
    ref = service.save(owner, space, change())
    cited = service.remember(
        writer,
        space,
        RememberMemory(
            command_id=uuid4(),
            title="Cited note",
            text="Mira is vegetarian.",
            source={"resource_id": ref["id"], "revision": 1},
        ),
    )
    assert str(index_rows(service, owner, space, cited["id"])[0]["source_resource_id"]) == ref["id"]
    with pytest.raises(CommandError) as error:
        service.remember(
            writer,
            space,
            RememberMemory(
                command_id=uuid4(),
                title="Moved source",
                text="Mira is vegetarian.",
                source={"resource_id": ref["id"], "revision": 2},
            ),
        )
    assert error.value.status == 409


def test_change_memory_edits_then_deletes_a_claim(commands):
    service, owner, space, _, request = setup(commands)
    service.memory_models.cues = [CUE]
    writer = delegated(service, owner, write=True)
    saved = service.build_memory(writer, space, request)
    car, rain = saved["memory"]["claims"]
    service.memory_models.calls.clear()
    edit = claim_change(saved, car["id"], "edit", value="Mira commutes by electric automobile.")
    edited = service.change_memory(writer, space, edit)
    assert edited["revision"] == 2
    assert edited["memory"]["claims"][0]["value"] == "Mira commutes by electric automobile."
    assert edited["memory"]["claims"][0]["edited"] is True
    assert edited["memory"]["claims"][1]["edited"] is False
    # Only the edited claim is embedded again, and the index follows the record.
    assert embeds(service) == [["Mira commutes by electric automobile."]]
    rows = index_rows(service, owner, space, saved["id"])
    assert bytes(rows[0]["text_hash"]) == brain.text_hash("Mira commutes by electric automobile.")
    assert bytes(rows[1]["text_hash"]) == brain.text_hash(RAIN)
    assert rows[0]["cue_hashes"] == []
    assert [bytes(cue) for cue in rows[1]["cue_hashes"]] == [brain.cue_hash(CUE)]
    assert {row["revision"] for row in rows} == {2}
    result = service.context(writer, space, RecallRequest(query="automobile", strategy="semantic"))
    assert result["items"][0]["excerpt"] == "Mira commutes by electric automobile."
    assert result["items"][0]["edited"] is True
    # Reusing the command ID and payload repeats no model or embedding call.
    service.memory_models.calls.clear()
    assert service.change_memory(writer, space, edit) == edited
    assert service.memory_models.calls == []
    forgotten = service.change_memory(writer, space, claim_change(edited, rain["id"], "forget"))
    assert [claim["id"] for claim in forgotten["memory"]["claims"]] == [car["id"]]
    # Forgotten memory is deleted, not hidden: the record records the removal.
    assert record(service, owner, space, forgotten)["forgotten"] == [rain["id"]]
    assert [str(row["claim_id"]) for row in index_rows(service, owner, space, saved["id"])] == [
        car["id"]
    ]
    assert all(
        item.get("entry_id") != rain["id"]
        for item in service.context(owner, space, RecallRequest(query="heavy rain"))["items"]
    )


def test_forgetting_with_a_disabled_provider_preserves_surviving_embeddings(commands):
    service, owner, space, _, request = setup(commands)
    saved = service.build_memory(owner, space, request)
    car, rain = saved["memory"]["claims"]
    original_rows = index_rows(service, owner, space, saved["id"])
    provider = service.memory_models
    provider.calls.clear()
    service.memory_models = MemoryModels(Settings(environment="test"))

    forget = claim_change(saved, car["id"], "forget")
    changed = service.change_memory(owner, space, forget)
    assert [claim["id"] for claim in changed["memory"]["claims"]] == [rain["id"]]
    remaining = index_rows(service, owner, space, saved["id"])
    assert len(remaining) == 1
    assert remaining[0]["embedding_model"] == original_rows[1]["embedding_model"]
    assert bytes(remaining[0]["text_hash"]) == bytes(original_rows[1]["text_hash"])
    assert remaining[0]["revision"] == changed["revision"]
    assert service.change_memory(owner, space, forget) == changed
    assert provider.calls == []

    # Restoring the original provider still recalls the surviving stored embedding.
    service.memory_models = provider
    result = service.context(owner, space, RecallRequest(query="rain", strategy="semantic"))
    assert result["items"][0]["entry_id"] == rain["id"]
    service.memory_models = MemoryModels(Settings(environment="test"))
    forgotten = service.change_memory(owner, space, claim_change(changed, rain["id"], "forget"))
    assert forgotten["memory"]["claims"] == []
    assert index_rows(service, owner, space, saved["id"]) == []


def test_forgetting_every_claim_preserves_the_source_memory_and_rebuild_tombstones(commands):
    service, owner, space, _, request = setup(commands)
    saved = service.build_memory(owner, space, request)
    claim_ids = [claim["id"] for claim in saved["memory"]["claims"]]
    forgotten = saved
    for claim_id in claim_ids:
        forgotten = service.change_memory(owner, space, claim_change(forgotten, claim_id, "forget"))
    assert forgotten["memory"]["claims"] == []
    assert index_rows(service, owner, space, saved["id"]) == []
    service.memory_models.calls.clear()
    with pytest.raises(CommandError, match="Memory already exists"):
        service.build_memory(owner, space, request.model_copy(update={"command_id": uuid4()}))
    assert service.memory_models.calls == []
    rebuilt = service.build_memory(
        owner,
        space,
        BuildMemory(
            command_id=uuid4(),
            source=request.source,
            resource_id=UUID(saved["id"]),
            expected_revision=forgotten["revision"],
        ),
    )
    assert rebuilt["id"] == saved["id"]
    assert rebuilt["revision"] == forgotten["revision"] + 1
    assert rebuilt["memory"]["claims"] == []
    assert set(record(service, owner, space, rebuilt)["forgotten"]) == set(claim_ids)
    assert index_rows(service, owner, space, saved["id"]) == []


def test_forgetting_is_sticky_and_a_human_edit_survives_a_rebuild(commands):
    service, owner, space, _, request = setup(commands)
    saved = service.build_memory(owner, space, request)
    car, rain = saved["memory"]["claims"]
    edited = service.change_memory(
        owner,
        space,
        claim_change(saved, car["id"], "edit", value="Mira commutes by electric automobile."),
    )
    forgotten = service.change_memory(owner, space, claim_change(edited, rain["id"], "forget"))
    rebuilt = service.build_memory(
        owner,
        space,
        BuildMemory(
            command_id=uuid4(),
            source=request.source,
            resource_id=UUID(saved["id"]),
            expected_revision=forgotten["revision"],
        ),
    )
    claims = rebuilt["memory"]["claims"]
    assert [claim["id"] for claim in claims] == [car["id"]]
    assert claims[0]["value"] == "Mira commutes by electric automobile."
    assert claims[0]["edited"] is True
    assert record(service, owner, space, rebuilt)["forgotten"] == [rain["id"]]
    rows = index_rows(service, owner, space, saved["id"])
    assert [str(row["claim_id"]) for row in rows] == [car["id"]]
    assert bytes(rows[0]["text_hash"]) == brain.text_hash("Mira commutes by electric automobile.")
    # The retained edit is still the recallable text after the rebuild.
    result = service.context(owner, space, RecallRequest(query="automobile", strategy="semantic"))
    assert result["items"][0]["excerpt"] == "Mira commutes by electric automobile."
    again = service.build_memory(
        owner,
        space,
        BuildMemory(
            command_id=uuid4(),
            source=request.source,
            resource_id=UUID(saved["id"]),
            expected_revision=rebuilt["revision"],
        ),
    )
    assert [claim["id"] for claim in again["memory"]["claims"]] == [car["id"]]
    assert len(index_rows(service, owner, space, saved["id"])) == 1


def test_vector_recall_recovers_a_synonym_and_respects_the_configured_provider(commands):
    service, owner, space, _, request = setup(commands)
    service.memory_models.outputs = [
        {"memory_type": "factual", "index": "Telescope", "value": "The telescope needs service."}
    ]
    service.remember(
        owner,
        space,
        RememberMemory(
            command_id=uuid4(), title="Telescope note", text="The telescope needs service."
        ),
    )
    service.memory_models.outputs = [
        {"memory_type": "factual", "index": "Mira commute", "value": CAR},
        {"memory_type": "factual", "index": "Outdoor forecast", "value": RAIN},
    ]
    saved = service.build_memory(owner, space, request)
    result = service.context(owner, space, RecallRequest(query="automobile", strategy="semantic"))
    assert [item["id"] for item in result["items"]] == [saved["id"]]
    assert result["items"][0]["excerpt"] == CAR
    # A lexical request makes no model call and invents no memory.
    service.memory_models.calls.clear()
    lexical = service.context(owner, space, RecallRequest(query="automobile", strategy="lexical"))
    assert lexical["items"] == [] and lexical["engine"] == "memora-brain-lexical-v1"
    assert service.memory_models.calls == []
    # An explicit semantic request fails clearly without a configured provider.
    service.memory_models = MemoryModels(Settings(environment="test"))
    with pytest.raises(CommandError) as error:
        service.context(owner, space, RecallRequest(query="automobile", strategy="semantic"))
    assert error.value.status == 409


def test_rebuild_refreshes_source_provenance_without_reembedding_unchanged_claims(commands):
    service, owner, space, source, request = setup(commands)
    saved = service.build_memory(owner, space, request)
    source = service.save(
        owner,
        space,
        change().model_copy(
            update={
                "resource_id": UUID(source["id"]),
                "expected_revision": source["revision"],
                "title": source["title"],
                "body": source["body"] + " Updated source context.",
            }
        ),
    )
    service.memory_models.calls.clear()
    rebuilt = service.build_memory(
        owner,
        space,
        BuildMemory(
            command_id=uuid4(),
            source={"resource_id": source["id"], "revision": source["revision"]},
            resource_id=UUID(saved["id"]),
            expected_revision=saved["revision"],
        ),
    )
    rows = index_rows(service, owner, space, saved["id"])
    assert embeds(service) == []
    assert {row["revision"] for row in rows} == {rebuilt["revision"]}
    assert {str(row["source_resource_id"]) for row in rows} == {source["id"]}
    assert {row["source_revision"] for row in rows} == {source["revision"]}


def test_rebuild_re_embeds_nothing_and_each_change_embeds_only_itself(commands):
    service, owner, space, _, request = setup(commands)
    saved = service.build_memory(owner, space, request)
    assert embeds(service) == [[CAR, RAIN]]
    service.memory_models.calls.clear()
    rebuilt = service.build_memory(
        owner,
        space,
        BuildMemory(
            command_id=uuid4(),
            source=request.source,
            resource_id=UUID(saved["id"]),
            expected_revision=1,
        ),
    )
    assert embeds(service) == []
    assert (
        pending(
            service,
            owner,
            space,
            saved["id"],
            memory_pipeline.index_rows(record(service, owner, space, rebuilt)),
        )
        == []
    )
    assert {row["revision"] for row in index_rows(service, owner, space, saved["id"])} == {2}
    edited = service.change_memory(
        owner,
        space,
        claim_change(
            rebuilt,
            rebuilt["memory"]["claims"][0]["id"],
            "edit",
            value="Mira commutes by electric automobile.",
        ),
    )
    assert embeds(service) == [["Mira commutes by electric automobile."]]
    service.memory_models.calls.clear()
    before = len(index_rows(service, owner, space, saved["id"]))
    forgotten = service.change_memory(
        owner, space, claim_change(edited, edited["memory"]["claims"][1]["id"], "forget")
    )
    assert embeds(service) == []
    assert len(index_rows(service, owner, space, saved["id"])) == before - 1
    # A changed embedding model invalidates every stored row, once.
    service.memory_models.embedding_model = "other-embedding"
    service.memory_models.calls.clear()
    remodelled = service.build_memory(
        owner,
        space,
        BuildMemory(
            command_id=uuid4(),
            source=request.source,
            resource_id=UUID(saved["id"]),
            expected_revision=forgotten["revision"],
        ),
    )
    assert sorted(embeds(service)[0]) == ["Mira commutes by electric automobile."]
    assert {row["embedding_model"] for row in index_rows(service, owner, space, saved["id"])} == {
        "other-embedding"
    }
    assert (
        pending(
            service,
            owner,
            space,
            saved["id"],
            memory_pipeline.index_rows(record(service, owner, space, remodelled)),
        )
        == []
    )


def test_context_returns_stale_memory_for_history_but_not_as_a_current_fact(commands):
    service, owner, space, ref, request = setup(commands)
    saved = service.build_memory(owner, space, request)
    assert service.list_memories(owner, space)["items"][0]["stale"] is False
    service.save(
        owner,
        space,
        change().model_copy(
            update={
                "resource_id": UUID(ref["id"]),
                "expected_revision": 1,
                "body": f"{CAR} A new revision.",
            }
        ),
    )
    listing = service.list_memories(owner, space)["items"][0]
    assert listing["stale"] is True
    result = service.context(owner, space, RecallRequest(query="automobile", strategy="semantic"))
    assert result["items"] == []
    result = service.context(
        owner, space, RecallRequest(query="previous automobile", strategy="semantic")
    )
    assert [item["id"] for item in result["items"]] == [saved["id"]]
    assert result["items"][0]["stale"] is True
    assert result["items"][0]["verification"] == "memory"
    rebuilt = service.build_memory(
        owner,
        space,
        BuildMemory(
            command_id=uuid4(),
            source={"resource_id": ref["id"], "revision": 2},
            resource_id=UUID(saved["id"]),
            expected_revision=1,
        ),
    )
    assert rebuilt["recall"]["sources"][0]["revision"] == 2
    assert service.list_memories(owner, space)["items"][0]["stale"] is False


def test_shared_cues_link_current_claims_and_stop_after_forgetting(commands):
    service, owner, space, _, request = setup(commands)
    service.memory_models.cues = [CUE]
    one = service.build_memory(owner, space, request)
    second = service.save(
        owner,
        space,
        change().model_copy(update={"title": "Second source", "body": RAIN}),
    )
    service.memory_models.outputs = [
        {"memory_type": "factual", "index": "Outdoor forecast", "value": RAIN}
    ]
    two = service.build_memory(
        owner,
        space,
        BuildMemory(command_id=uuid4(), source={"resource_id": second["id"], "revision": 1}),
    )
    with service.db.transaction(owner) as connection:
        service._scope(connection, owner, space)
        cues = brain.cues_for(connection, one["id"], [one["memory"]["claims"][0]["id"]])
        assert cues and all(len(cue) == 32 for cue in cues)
        related = brain.expand(connection, cues, brain.MAX_CUE_CANDIDATES)
        assert {str(row["memory_id"]) for row in related} == {one["id"], two["id"]}
    service.change_memory(
        owner, space, claim_change(two, two["memory"]["claims"][0]["id"], "forget")
    )
    with service.db.transaction(owner) as connection:
        service._scope(connection, owner, space)
        remaining = brain.expand(connection, cues, brain.MAX_CUE_CANDIDATES)
        assert {str(row["memory_id"]) for row in remaining} == {one["id"]}


def test_declared_importance_is_indexed_and_returned_while_a_build_defaults_to_undeclared(commands):
    service, owner, space, _, request = setup(commands)
    saved = service.remember(
        owner,
        space,
        RememberMemory(command_id=uuid4(), title="Commute constraint", text=CAR, importance=3),
    )
    rows = index_rows(service, owner, space, saved["id"])
    assert {row["importance"] for row in rows} == {3}
    result = service.context(owner, space, RecallRequest(query="automobile", strategy="semantic"))
    assert result["items"][0]["importance"] == 3

    # An extracted build has no declared importance, so it stays undeclared rather than
    # inheriting a value the writer never gave.
    built = service.build_memory(owner, space, request)
    assert {row["importance"] for row in index_rows(service, owner, space, built["id"])} == {0}
    listed = service.list_memories(owner, space)["items"]
    assert {claim["importance"] for item in listed for claim in item["claims"]} == {0, 3}


def test_related_results_are_a_separate_tier_that_cannot_displace_a_ranked_item(commands):
    service, owner, space, _, request = setup(commands)
    service.memory_models.cues = [CUE]
    service.build_memory(owner, space, request)
    second = service.save(
        owner, space, change().model_copy(update={"title": "Second source", "body": RAIN})
    )
    service.memory_models.outputs = [
        {"memory_type": "factual", "index": "Outdoor forecast", "value": RAIN}
    ]
    two = service.build_memory(
        owner,
        space,
        BuildMemory(command_id=uuid4(), source={"resource_id": second["id"], "revision": 1}),
    )

    plain = service.context(owner, space, RecallRequest(query="automobile", strategy="semantic"))
    opted = service.context(
        owner,
        space,
        RecallRequest(query="automobile", strategy="semantic", include_related=True),
    )

    # Asking for related results must not change the ranked tier at all.
    assert "related" not in plain
    assert [item["entry_id"] for item in opted["items"]] == [
        item["entry_id"] for item in plain["items"]
    ]
    # The cue-linked neighbour is offered separately and labelled, not ranked.
    ranked = {item["entry_id"] for item in opted["items"]}
    related = {item["entry_id"] for item in opted["related"]}
    assert related and two["memory"]["claims"][0]["id"] in related
    assert not related & ranked
    assert all(item["reason"] == "related" for item in opted["related"])
    assert "not ranked by relevance" in opted["related_note"]
    assert opted["found"] is True
    # The related tier is capped, so it can never flood the caller's context.
    assert len(opted["related"]) <= MAX_RELATED
    # A plain recall still reports the same engine and never grows a related tier.
    assert "related" not in service.context(
        owner, space, RecallRequest(query="automobile", strategy="lexical")
    )


def test_claim_text_is_readable_while_index_fields_stay_in_the_index(commands):
    service, owner, space, _, request = setup(commands)
    service.memory_models.cues = [CUE]
    saved = service.build_memory(owner, space, request)
    service.memory_models.outputs = [
        {"memory_type": "factual", "index": "Diet", "value": "Mira is vegetarian."}
    ]
    remembered = service.remember(
        owner,
        space,
        RememberMemory(command_id=uuid4(), title="Diet note", text="Mira is vegetarian."),
    )
    stored = ciphertexts(service, owner, space)
    for text in (CAR, "Mira is vegetarian."):
        assert any(text.encode() in value for value in stored)
    for value in stored:
        for internal in ("text_hash", "vectors"):
            assert internal.encode() not in value
    # The claim index holds hashes and embeddings only, never the claim text.
    secrets = (CAR, RAIN, CUE, "Mira commute", "Mira is vegetarian.")
    with service.db.transaction(owner) as connection:
        service._scope(connection, owner, space)
        rows = connection.execute("SELECT * FROM space.memory_claims").fetchall()
    assert len(rows) == 3
    assert set(rows[0]) == {
        "organization_id",
        "space_id",
        "memory_id",
        "claim_id",
        "revision",
        "entry_index",
        "memory_type",
        # Readable by design, like the embedding: ranking needs it in SQL and it is a
        # non-secret scalar. docs/PRIVACY.md records it.
        "importance",
        "text_hash",
        "cue_hashes",
        "embedding",
        "embedding_model",
        "source_resource_id",
        "source_revision",
        "created_at",
    }
    for row in rows:
        for name, value in row.items():
            raw = value if isinstance(value, (bytes, bytearray)) else str(value).encode()
            for secret in secrets:
                assert secret.encode() not in raw, name
        assert len(bytes(row["text_hash"])) == 32
        assert all(len(bytes(cue)) == 32 for cue in row["cue_hashes"])
    assert {str(row["memory_id"]) for row in rows} == {saved["id"], remembered["id"]}


def test_grants_deny_or_allow_every_memory_operation(commands):
    service, owner, space, _, request = setup(commands)
    writer = delegated(service, owner, write=True)
    reader = delegated(service, owner)
    denied = [
        lambda: service.build_memory(reader, space, request),
        lambda: service.remember(
            reader,
            space,
            RememberMemory(command_id=uuid4(), title="Denied", text=CAR),
        ),
    ]
    service.memory_models.calls.clear()
    for call in denied:
        with pytest.raises(CommandError) as error:
            call()
        assert error.value.status == 403
    assert service.memory_models.calls == []
    # Write access is enough: memory has no human-only gate.
    saved = service.build_memory(writer, space, request)
    service.remember(
        writer,
        space,
        RememberMemory(command_id=uuid4(), title="Note", text="Mira is vegetarian."),
    )
    edited = service.change_memory(
        writer,
        space,
        claim_change(saved, saved["memory"]["claims"][0]["id"], "edit", value=CAR),
    )
    assert edited["memory"]["claims"][0]["edited"] is True
    with pytest.raises(CommandError) as error:
        service.change_memory(
            reader,
            space,
            claim_change(edited, edited["memory"]["claims"][0]["id"], "forget"),
        )
    assert error.value.status == 403
    # A revoked or absent grant denies every memory operation.
    service.revoke(owner, UUID(writer.client_id))
    for call in (
        lambda: service.change_memory(
            writer,
            space,
            claim_change(edited, edited["memory"]["claims"][0]["id"], "forget"),
        ),
        lambda: service.remember(
            writer, space, RememberMemory(command_id=uuid4(), title="Denied", text=CAR)
        ),
        lambda: service.context(writer, space, RecallRequest(query="automobile")),
    ):
        with pytest.raises(CommandError) as error:
            call()
        assert error.value.status == 403
    absent = replace(owner, client_id=str(uuid4()), grant_id=str(uuid4()))
    with pytest.raises(CommandError) as error:
        service.remember(
            absent, space, RememberMemory(command_id=uuid4(), title="Absent", text=CAR)
        )
    assert error.value.status == 403


def test_change_memory_requires_the_current_revision_in_this_space(commands):
    service, owner, space, ref, request = setup(commands)
    saved = service.build_memory(owner, space, request)
    car = saved["memory"]["claims"][0]
    other = replace(owner, subject=str(uuid4()))
    other_space = UUID(service.bootstrap(other)["id"])
    with pytest.raises(CommandError):
        service.build_memory(other, other_space, request.model_copy(update={"command_id": uuid4()}))
    with pytest.raises(CommandError):
        service.change_memory(
            other,
            other_space,
            ChangeMemory(
                command_id=uuid4(),
                resource_id=UUID(saved["id"]),
                expected_revision=1,
                entry_id=UUID(car["id"]),
                action="edit",
                value=CAR,
            ),
        )
    edited = service.change_memory(
        owner,
        space,
        claim_change(saved, car["id"], "edit", value="Mira commutes by electric automobile."),
    )
    with pytest.raises(CommandError, match="changed"):
        service.change_memory(
            owner,
            space,
            claim_change(saved, car["id"], "edit", value="Another wording"),
        )
    with pytest.raises(CommandError, match="entry"):
        service.change_memory(owner, space, claim_change(edited, str(uuid4()), "forget"))
    assert (
        service.get_resource(owner, space, UUID(saved["id"]))["memory"]["claims"][0]["value"]
        == "Mira commutes by electric automobile."
    )
    assert ref["id"] != saved["id"]


@pytest.mark.parametrize("operation", ["build", "recall"])
@pytest.mark.parametrize("mutation", ["revoke", "source"])
def test_races_stop_disclosure_before_the_model_call(commands, operation, mutation):
    service, owner, space, ref, request = setup(commands)
    if operation == "recall":
        service.build_memory(owner, space, request)
    actor = delegated(service, owner, write=True)
    service.memory_models.calls.clear()

    def race():
        service.memory_models.before_call = lambda: None
        if mutation == "revoke":
            service.revoke(owner, UUID(actor.client_id))
        else:
            service.save(
                owner,
                space,
                change().model_copy(
                    update={
                        "resource_id": UUID(ref["id"]),
                        "expected_revision": 1,
                        "body": f"{CAR} Changed",
                    }
                ),
            )

    service.memory_models.before_call = race
    with pytest.raises(CommandError) as error:
        if operation == "build":
            service.build_memory(actor, space, request)
        else:
            service.context(actor, space, RecallRequest(query="automobile", strategy="semantic"))
    assert error.value.status == (403 if mutation == "revoke" else 409)
    assert service.memory_models.calls == []
    if operation == "build":
        assert service.list_memories(owner, space)["items"] == []


def test_mcp_and_rest_share_memory_without_a_review_interface(commands, signing):
    service = commands[0]
    service.memory_models = Models()
    verifier, token = signing
    browser_token = token()
    owner = verifier.verify(browser_token)
    space = service.bootstrap(owner)["id"]
    source = service.save(
        owner, UUID(space), change().model_copy(update={"title": "Commute source", "body": CAR})
    )
    actor = delegated(service, owner, write=True)
    agent_token = token(
        aud=verifier.resource, client_id=actor.client_id, space_grant_id=actor.grant_id
    )
    auth = {"Authorization": "Bearer " + browser_token}
    settings = Settings(environment="test", allowed_hosts=["testserver"])
    with TestClient(create_app(settings, workspace=service, verifier=verifier)) as http:

        def rpc(name, arguments):
            response = http.post(
                "/mcp/",
                headers={
                    "Authorization": "Bearer " + agent_token,
                    "X-Space-Agent-ID": actor.agent_id,
                    "Accept": "application/json, text/event-stream",
                },
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": {"space_id": space, **arguments}},
                },
            )
            assert response.status_code == 200, response.text
            return response.json()["result"]

        names = [
            tool["name"]
            for tool in http.post(
                "/mcp/",
                headers={
                    "Authorization": "Bearer " + agent_token,
                    "Accept": "application/json, text/event-stream",
                },
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            ).json()["result"]["tools"]
        ]
        assert {
            "remember",
            "change_memory",
            "build_memory",
            "remember_claims",
            "extract_memory",
            "memory_job",
        } <= set(names)
        assert "review_memory" not in names
        request = BuildMemory(
            command_id=uuid4(), source={"resource_id": source["id"], "revision": 1}
        ).model_dump(mode="json")
        built = rpc("build_memory", {"change": request})["structuredContent"]
        assert built["memory"]["claims"][0]["edited"] is False
        assert "state" not in json.dumps(built) and "vectors" not in json.dumps(built)
        # Removed review interfaces are gone from both transports.
        assert rpc("review_memory", {"change": {"command_id": str(uuid4())}})["isError"]
        assert http.post(
            f"/v1/spaces/{space}/memory/review", headers=auth, json={}
        ).status_code == (404)
        # Command identities stay actor-bound across transports.
        assert (
            http.post(f"/v1/spaces/{space}/memory/build", headers=auth, json=request).status_code
            == 409
        )
        assert rpc("build_memory", {"change": request})["structuredContent"] == built
        remembered = http.post(
            f"/v1/spaces/{space}/memory/remember",
            headers=auth,
            json=RememberMemory(
                command_id=uuid4(), title="Diet note", text="Mira is vegetarian."
            ).model_dump(mode="json"),
        )
        assert remembered.status_code == 200, remembered.text
        assert remembered.json()["recall"]["category"] == "memory"
        changed = http.post(
            f"/v1/spaces/{space}/memory/change",
            headers=auth,
            json=ChangeMemory(
                command_id=uuid4(),
                resource_id=UUID(built["id"]),
                expected_revision=1,
                entry_id=UUID(built["memory"]["claims"][0]["id"]),
                action="edit",
                value="Mira commutes by electric automobile.",
            ).model_dump(mode="json"),
        )
        assert changed.status_code == 200, changed.text
        assert changed.json()["memory"]["claims"][0]["edited"] is True
        listing = rpc("list_memories", {})["structuredContent"]
        visible = http.get(f"/v1/spaces/{space}/memories", headers=auth)
        assert visible.json() == listing and visible.headers["Cache-Control"] == "no-store"
        first = rpc("list_memories", {"limit": 1})["structuredContent"]
        assert len(first["items"]) == 1 and first["next_cursor"]
        assert (
            http.get(f"/v1/spaces/{space}/memories", headers=auth, params={"limit": 1}).json()
            == first
        )
        second = rpc("list_memories", {"limit": 1, "after": first["next_cursor"]})[
            "structuredContent"
        ]
        assert len(second["items"]) == 1 and second["next_cursor"] is None
        recalled = rpc(
            "get_context",
            {"request": {"query": "automobile", "strategy": "semantic"}},
        )["structuredContent"]
        # Equal fixture vectors tie by claim ID, not title BM25. Inspect the edited
        # record by identity even if the second fixture memory sorts before it.
        edited = next(item for item in recalled["items"] if item["id"] == built["id"])
        assert edited["verification"] == "memory"
        assert edited["excerpt"] == "Mira commutes by electric automobile."
        assert edited["edited"] is True
        assert edited["revision"] == 2

        prepared = RememberClaims(
            command_id=uuid4(),
            title="Exact statement",
            claims=[{"index": "Decision", "value": "Retain the supplied claim verbatim."}],
        ).model_dump(mode="json")
        service.memory_models.calls.clear()
        direct = rpc("remember_claims", {"change": prepared})["structuredContent"]
        assert direct["memory"]["claims"][0]["value"] == prepared["claims"][0]["value"]
        assert all(call[0] == "embed" for call in service.memory_models.calls)
        prepared["command_id"] = str(uuid4())
        assert (
            http.post(f"/v1/spaces/{space}/memory/claims", headers=auth, json=prepared).status_code
            == 200
        )
        service.memory_models.calls.clear()
        extraction = RememberMemory(
            command_id=uuid4(), title="Raw source", text="Mira drives a car."
        ).model_dump(mode="json")
        queued = rpc("extract_memory", {"change": extraction})["structuredContent"]
        assert queued["status"] == "queued" and queued["source_saved"]
        assert rpc("memory_job", {"job_id": queued["id"]})["structuredContent"] == queued
        assert (
            http.get(f"/v1/spaces/{space}/memory/jobs/{queued['id']}", headers=auth).json()
            == queued
        )
        extraction["command_id"] = str(uuid4())
        assert (
            http.post(f"/v1/spaces/{space}/memory/jobs", headers=auth, json=extraction).status_code
            == 202
        )
        assert len(http.get(f"/v1/spaces/{space}/memory/jobs", headers=auth).json()["items"]) == 2
        assert service.memory_models.calls == []


def test_the_browser_search_covers_every_memory_not_only_the_page(commands):
    """A query is answered in SQL, so a memory outside the page is still found.

    The page size is deliberately smaller than the Space: with filtering over the page the
    caller already holds, the last memory would be invisible. Paging without a query must
    still walk the whole Space.
    """
    service, owner, space, _ = commands
    service.memory_models = Models()
    for index in range(5):
        service.memory_models.outputs = [
            {"memory_type": "factual", "index": "note", "value": f"Fact {index}"}
        ]
        service.remember(
            owner,
            space,
            RememberMemory(
                command_id=uuid4(),
                title=f"Note {index} needle{index}",
                text=f"note {index}",
            ),
        )

    first = service.list_memories(owner, space, limit=2)
    assert len(first["items"]) == 2
    assert first["next_cursor"] is not None

    # The needle is in the newest memory; the query must reach every memory regardless.
    found = service.list_memories(owner, space, query="needle4")
    assert [item["title"] for item in found["items"]] == ["Memory: Note 4 needle4"]
    assert found["next_cursor"] is None
    assert service.list_memories(owner, space, query="needle0", limit=2)["items"][0]["title"] == (
        "Memory: Note 0 needle0"
    )

    seen, cursor = [], None
    for _ in range(5):
        page = service.list_memories(owner, space, limit=2, after=cursor)
        seen += [item["title"] for item in page["items"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert seen == [f"Memory: Note {index} needle{index}" for index in (4, 3, 2, 1, 0)]
