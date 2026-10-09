"""Exact SQL contract under restricted runtime scope, ties and mixed models."""

from dataclasses import replace
from uuid import uuid4

import pytest
from test_memora_pipeline import Models, fixture_embedding
from test_workspace import command_database as command_database
from test_workspace import commands as commands
from test_workspace import database as database

from space import brain
from space.memory import RememberClaims


def test_exact_default_fills_available_candidates_and_filters_owner_and_model(commands):
    service, owner, space, _ = commands
    service.memory_models = Models()
    request = RememberClaims(
        command_id=uuid4(),
        title="Tied candidates",
        claims=[
            {"index": f"claim {i}", "value": f"Mira travels by car on route {i}."}
            for i in range(24)
        ],
    )
    saved = service.remember_claims(owner, space, request)
    ids = sorted(claim["id"] for claim in saved["memory"]["claims"])
    foreign = replace(owner, subject=str(uuid4()))
    other_space = service.bootstrap(foreign)["id"]
    service.remember_claims(
        foreign, other_space, request.model_copy(update={"command_id": uuid4()})
    )
    with service.db.transaction(owner) as connection:
        service._scope(connection, owner, space)
        # Neither a tiny HNSW exploration setting nor ties may under-fill exact search.
        connection.execute("SELECT '[1]'::vector")
        connection.execute("SET LOCAL hnsw.ef_search=1")
        for _ in range(7):
            hits = brain.search(connection, fixture_embedding("car"), "fixture-embedding", 40)
            assert [str(hit["claim_id"]) for hit in hits] == ids
            assert all(hit["score"] == 1 for hit in hits)
        connection.execute(
            "UPDATE space.memory_claims SET embedding_model='previous-model' "
            "WHERE claim_id=ANY(%s::uuid[])",
            (ids[:12],),
        )
        hits = brain.search(connection, fixture_embedding("car"), "fixture-embedding", 40)
        assert [str(hit["claim_id"]) for hit in hits] == ids[12:]
        assert brain.search(connection, fixture_embedding("car"), "missing-model", 40) == []
        assert brain.search(connection, fixture_embedding("car"), "fixture-embedding", 0) == []


def test_unknown_selection_is_not_silently_approximate():
    assert brain.DEFAULT_RETRIEVAL.selection == "exact"
    with pytest.raises(ValueError, match="selection"):
        brain.RetrievalConfig(selection="automatic")
