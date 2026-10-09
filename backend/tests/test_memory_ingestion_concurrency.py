"""Incoming memories depend on their source and catalog, not unrelated workspace writes."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from test_database_setup import database as database_fixture
from test_memora_pipeline import Models
from test_memory_classification_workspace import ClassifyingModels, item
from test_workspace import change, delegated
from test_workspace import command_database as command_database_fixture
from test_workspace import commands as commands_fixture

from space.memory import RememberClaims, RememberMemory
from space.workspace import CommandError

database = database_fixture
command_database = command_database_fixture
commands = commands_fixture


def incoming(commands, operation):
    service, owner, space, _ = commands
    service.memory_models = ClassifyingModels()
    source = service.save(owner, space, change())
    writer = delegated(service, owner, write=True)
    values = {
        "command_id": uuid4(),
        "title": "Commute",
        "source": {"resource_id": source["id"], "revision": 1},
    }
    if operation == "prepared":
        request = RememberClaims(
            **values, claims=[{"index": "Commute", "value": "Mira travels by car."}]
        )
        write = service.remember_claims
    else:
        request = RememberMemory(**values, text="Mira travels by car.")
        write = service.remember
    return service, owner, writer, space, source, request, write


def install_race(service, monkeypatch, phase, race):
    if phase == "classification":
        service.memory_models.before_classify = race
    elif phase == "embedding":
        session = service.memory_models.session

        def with_race(authorize):
            result = session(authorize)
            embed = result.embed

            def embedding(texts):
                race()
                return embed(texts)

            result.embed = embedding
            return result

        monkeypatch.setattr(service.memory_models, "session", with_race)
    else:
        encrypt = service._pack

        def during_commit(*args, **kwargs):
            result = encrypt(*args, **kwargs)
            race()
            return result

        monkeypatch.setattr(service, "_pack", during_commit)


@pytest.mark.parametrize("operation", ["prepared", "raw"])
@pytest.mark.parametrize("phase", ["classification", "embedding", "commit"])
def test_unrelated_document_write_does_not_abort_incoming_memory(
    commands, monkeypatch, operation, phase
):
    service, owner, writer, space, source, request, write = incoming(commands, operation)
    fired = []

    def race():
        if not fired:
            fired.append(True)
            service.save(owner, space, change())

    install_race(service, monkeypatch, phase, race)
    saved = write(writer, space, request)
    assert fired
    assert saved["recall"]["sources"] == [{"resource_id": source["id"], "revision": 1}]
    assert saved["memory"]["claims"]
    calls = list(service.memory_models.calls)
    assert write(writer, space, request) == saved
    assert service.memory_models.calls == calls
    with service.db.transaction(owner) as connection:
        service._scope(connection, owner, space)
        indexed = connection.execute(
            "SELECT count(*) AS n FROM space.memory_claims WHERE memory_id=%s", (saved["id"],)
        ).fetchone()
    assert indexed["n"] == len(saved["memory"]["claims"])


@pytest.mark.parametrize("operation", ["prepared", "raw"])
@pytest.mark.parametrize("phase", ["classification", "embedding", "commit"])
@pytest.mark.parametrize("mutation", ["source", "catalog", "revoke"])
def test_relevant_changes_still_stop_incoming_memory(
    commands, monkeypatch, operation, phase, mutation
):
    service, owner, writer, space, source, request, write = incoming(commands, operation)
    fired = []

    def race():
        if fired:
            return
        fired.append(True)
        if mutation == "source":
            service.save(
                owner,
                space,
                change().model_copy(
                    update={"resource_id": UUID(source["id"]), "expected_revision": 1}
                ),
            )
        elif mutation == "catalog":
            item(service, owner, space, "category", "New category")
        else:
            service.revoke(owner, UUID(writer.client_id))

    install_race(service, monkeypatch, phase, race)
    with pytest.raises(CommandError) as error:
        write(writer, space, request)
    assert error.value.status == (403 if mutation == "revoke" else 409)
    assert fired
    if phase == "classification":
        # Embedding now precedes classification and is guarded by its own phase.
        assert not any(c[0] == "classify" for c in service.memory_models.calls)
    elif phase == "embedding":
        assert not any(c[0] == "embed" for c in service.memory_models.calls)
    assert service.list_memories(owner, space)["items"] == []


def test_source_less_memory_ignores_catalog_when_classification_is_disabled(commands):
    service, owner, space, _ = commands
    service.memory_models = Models()
    service.memory_models.before_call = lambda: item(service, owner, space, "category", "New")
    saved = service.remember_claims(
        owner,
        space,
        RememberClaims(
            command_id=uuid4(), title="Commute", claims=[{"index": "Mira", "value": "By car"}]
        ),
    )
    assert saved["memory"]["claims"][0]["value"] == "By car"


def test_concurrent_incoming_memories_both_commit(commands, monkeypatch):
    service, owner, writer, space, _, request, write = incoming(commands, "prepared")
    barrier = Barrier(2)
    session = service.memory_models.session

    def concurrent_session(authorize):
        result = session(authorize)
        embed = result.embed

        def embedding(texts):
            vectors = embed(texts)
            barrier.wait(timeout=10)
            return vectors

        result.embed = embedding
        return result

    monkeypatch.setattr(service.memory_models, "session", concurrent_session)
    requests = [request, request.model_copy(update={"command_id": uuid4()})]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(write, writer, space, r) for r in requests]
        saved = [future.result(timeout=15) for future in futures]
    assert len({r["id"] for r in saved}) == 2
    assert len(service.list_memories(owner, space)["items"]) == 2
