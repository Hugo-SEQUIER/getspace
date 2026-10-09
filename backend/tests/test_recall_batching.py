"""Bound work by selected records and preserve candidate ordering through hydration."""

from contextlib import contextmanager
from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from test_database_setup import database as database_fixture
from test_memora_pipeline import Models
from test_resource_terms import change, term_key_for
from test_workspace import command_database as command_database_fixture
from test_workspace import commands as commands_fixture

from space import brain
from space.memory import RecallMetadata, RecallRequest, RememberClaims
from space.terms import query_fingerprints

database = database_fixture
command_database = command_database_fixture
commands = commands_fixture


def test_recall_decrypts_selected_memory_only_and_pages_batch_their_bodies(commands, monkeypatch):
    service, owner, space, _ = commands
    service.memory_models = Models()
    service.retrieval = replace(brain.DEFAULT_RETRIEVAL, candidate_multiplier=1)
    for index in range(24):
        service.remember_claims(
            owner,
            space,
            RememberClaims(
                command_id=uuid4(),
                title=f"Commute {index}",
                claims=[{"index": "Commute", "value": "Mira travels by car."}],
            ),
        )
    source = service.save(owner, space, change(title="matchsource", body="matchsource"))
    counts = {"metadata": 0, "transactions": 0}
    decrypt, transaction = service._unpack, service.db.transaction

    def count_decrypt(key, scope, resource_id, revision, purpose, value):
        if purpose == "metadata":
            counts["metadata"] += 1
        return decrypt(key, scope, resource_id, revision, purpose, value)

    @contextmanager
    def count_transaction(identity):
        counts["transactions"] += 1
        with transaction(identity) as connection:
            yield connection

    monkeypatch.setattr(service, "_unpack", count_decrypt)
    monkeypatch.setattr(service.db, "transaction", count_transaction)
    lexical = service.context(owner, space, RecallRequest(query="matchsource", strategy="lexical"))
    assert lexical["items"][0]["id"] == source["id"]
    assert counts["metadata"] == 1
    counts["metadata"] = 0
    semantic = service.context(
        owner, space, RecallRequest(query="automobile", strategy="semantic", limit=1)
    )
    assert semantic["found"] and semantic["items"][0]["verification"] == "memory"
    assert counts["metadata"] == 1
    counts["transactions"] = 0
    assert len(service.list_memories(owner, space, limit=1)["items"]) == 1
    single_page_transactions = counts["transactions"]
    counts["transactions"] = 0
    assert len(service.list_memories(owner, space, limit=24)["items"]) == 24
    assert counts["transactions"] == single_page_transactions


def test_candidate_order_survives_metadata_selection_and_bodies_stay_inline(commands, monkeypatch):
    service, owner, space, _ = commands
    for i in range(36):
        service.save(owner, space, change(title=f"Common {i}", body="policy enterprise"))
    rare = service.save(owner, space, change(title="Rare", body="refund"))
    key = term_key_for(service, owner, space)
    candidates = service._candidates(
        owner, space, query_fingerprints("refund policy enterprise", key), 37
    )
    ids = [item[0] for item in candidates]
    assert str(ids[0]) == rare["id"]
    selected, _, _ = service._items_for(owner, space, ids)
    assert [item["id"] for item in selected] == list(map(str, ids))
    monkeypatch.setattr(service.objects, "get", lambda *_: pytest.fail("Unexpected object read"))
    result = service.context(
        owner, space, RecallRequest(query="refund policy enterprise", strategy="lexical")
    )
    assert any(item["id"] == rare["id"] for item in result["items"])


def test_source_selection_batches_metadata_without_changing_order_or_visibility(
    commands, monkeypatch
):
    service, owner, space, _ = commands
    first = service.save(owner, space, change(title="First", body="common signal"))
    rare = service.save(owner, space, change(title="Rare", body="rare signal"))
    hidden = service.save(owner, space, change(title="Hidden", body="rare common"))
    service.save(
        owner,
        space,
        change(title="Hidden", body="rare common").model_copy(
            update={
                "resource_id": UUID(hidden["id"]),
                "expected_revision": 1,
                "recall": RecallMetadata(excluded=True),
            }
        ),
    )
    terms = query_fingerprints("rare common signal", term_key_for(service, owner, space))
    shortlist = service._candidates(owner, space, terms, 3)
    expected, _, _ = service._items_for(owner, space, [item[0] for item in shortlist])
    transaction, unwrap, decrypt = service.db.transaction, service.keys.unwrap, service._unpack
    counts = {"transactions": 0, "active": 0}

    @contextmanager
    def counted(identity):
        counts["transactions"] += 1
        with transaction(identity) as connection:
            counts["active"] += 1
            try:
                yield connection
            finally:
                counts["active"] -= 1

    def checked_unwrap(*args):
        assert counts["active"] == 0
        return unwrap(*args)

    def checked_decrypt(*args):
        assert counts["active"] == 0
        return decrypt(*args)

    def no_body(*args, **kwargs):
        pytest.fail("Source metadata selection must not hydrate bodies")

    monkeypatch.setattr(service.db, "transaction", counted)
    monkeypatch.setattr(service.keys, "unwrap", checked_unwrap)
    monkeypatch.setattr(service, "_unpack", checked_decrypt)
    monkeypatch.setattr(service, "_bodies", no_body)
    selected, _, _ = service._source_items(owner, space, terms, 3)
    assert selected == expected
    assert {item["id"] for item in selected} == {first["id"], rare["id"]}
    assert counts["transactions"] == 1
    assert service._source_items(owner, space, [], 3) == ([], {}, None)
    assert counts["transactions"] == 1


def test_vector_shortlist_reads_metadata_in_the_same_authorized_transaction(commands, monkeypatch):
    service, owner, space, _ = commands
    service.memory_models = Models()
    memory = service.remember_claims(
        owner,
        space,
        RememberClaims(
            command_id=uuid4(),
            title="Commute",
            claims=[
                {"index": "Commute", "value": "Mira travels by car."},
            ],
        ),
    )
    search, item_rows, transaction = brain.search, service._item_rows, service.db.transaction
    generation, active, selected_generation, checked = 0, None, None, False

    @contextmanager
    def counted_transaction(identity):
        nonlocal generation, active
        generation += 1
        with transaction(identity) as connection:
            active = generation
            try:
                yield connection
            finally:
                active = None

    def capture_search(connection, *args, **kwargs):
        nonlocal selected_generation
        selected_generation = active
        return search(connection, *args, **kwargs)

    def check_rows(connection, scope, space_id, ids, **kwargs):
        nonlocal checked
        if not checked and selected_generation is not None and memory["id"] in ids:
            assert active == selected_generation
            checked = True
        return item_rows(connection, scope, space_id, ids, **kwargs)

    monkeypatch.setattr(service.db, "transaction", counted_transaction)
    monkeypatch.setattr(brain, "search", capture_search)
    monkeypatch.setattr(service, "_item_rows", check_rows)
    response = service.context(owner, space, RecallRequest(query="automobile", strategy="semantic"))
    assert checked and response["found"]
    assert response["items"][0]["id"] == memory["id"]
    assert response["items"][0]["excerpt"] == "Mira travels by car."
