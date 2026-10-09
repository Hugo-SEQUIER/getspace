from uuid import UUID, uuid4

from test_database_setup import database as database_fixture
from test_memora_pipeline import Models
from test_memory_classification_workspace import ClassifyingModels, assign, item
from test_memory_jobs import queue as queue_fixture
from test_workspace import command_database as command_database_fixture
from test_workspace import commands as commands_fixture

from space import jobs, memory_classification, reclassify_jobs
from space.memory import RememberClaims

database = database_fixture
command_database = command_database_fixture
commands = commands_fixture
queue = queue_fixture


def remember(service, owner, space, *values):
    return service.remember_claims(
        owner,
        space,
        RememberClaims(
            command_id=uuid4(),
            title="Claims",
            claims=[{"index": v, "value": v} for v in values],
        ),
    )


def reclassify_jobs_in(service, owner, space):
    with service.db.transaction(owner) as connection:
        service._scope(connection, owner, space)
        return connection.execute(
            "SELECT status,import_total,import_completed FROM space.memory_jobs "
            "WHERE request_kind='reclassify'"
        ).fetchall()


def limited_memories(service, owner, space, monkeypatch, count):
    """Store memories the way the old catalog limit left them: unassigned, catalog_limit."""
    item(service, owner, space, "category", "Dev")
    project = item(service, owner, space, "project", "Atlas")
    service.memory_models = ClassifyingModels()
    monkeypatch.setattr(memory_classification, "MAX_CATALOG_READ", 1)
    monkeypatch.setattr(reclassify_jobs, "maybe_enqueue", lambda *args: None)
    saved = [remember(service, owner, space, f"Atlas fact {n}") for n in range(count)]
    monkeypatch.undo()
    for memory in saved:
        assert memory["memory"]["claims"][0]["classification"]["reason"] == "catalog_limit"
    return saved, project


def test_next_write_queues_one_catch_up_that_reclassifies_only_limited_claims(
    commands, queue, monkeypatch
):
    service, owner, space, _ = commands
    saved, project = limited_memories(service, owner, space, monkeypatch, 7)
    pinned = service.change_memory(owner, space, assign(saved[0], scope="global"))
    assert [r["status"] for r in reclassify_jobs_in(service, owner, space)] == ["queued"]
    remember(service, owner, space, "Another Atlas fact")
    assert len(reclassify_jobs_in(service, owner, space)) == 1
    first = jobs.claim(queue)
    assert jobs.process(service, queue, first) is None
    [row] = reclassify_jobs_in(service, owner, space)
    assert row["status"] == "queued" and row["import_completed"] == 5
    assert jobs.process(service, queue, jobs.claim(queue)) == {}
    [row] = reclassify_jobs_in(service, owner, space)
    assert row["status"] == "ready" and row["import_completed"] == row["import_total"]
    current = {m["id"]: m for m in service.list_memories(owner, space)["items"]}
    assert current[pinned["id"]]["claims"][0]["classification"]["method"] == "manual"
    for memory in saved[1:]:
        claim = current[memory["id"]]["claims"][0]
        assert claim["classification"]["project_ids"] == [project["id"]]
        assert claim["value"] == memory["memory"]["claims"][0]["value"]


def test_catch_up_needs_classification_and_never_fails_the_write(commands, monkeypatch):
    service, owner, space, _ = commands
    service.memory_models = Models()
    remember(service, owner, space, "Plain fact")
    assert reclassify_jobs_in(service, owner, space) == []
    service.memory_models = ClassifyingModels()

    def broken(*args):
        raise RuntimeError("queue unavailable")

    monkeypatch.setattr(reclassify_jobs, "enqueue", broken)
    assert remember(service, owner, space, "Still saved")["memory"]["claims"]
    assert reclassify_jobs_in(service, owner, space) == []


def test_concurrent_edit_wins_and_failed_catch_up_waits_a_day(commands, queue, monkeypatch):
    service, owner, space, _ = commands
    saved, _ = limited_memories(service, owner, space, monkeypatch, 1)
    remember(service, owner, space, "Trigger")
    original = service.reclassify_limited
    edited = []

    def edit_first(identity, space_id, resource_id, job_id):
        if not edited and resource_id == UUID(saved[0]["id"]):
            edited.append(service.change_memory(owner, space, assign(saved[0], scope="global")))
        return original(identity, space_id, resource_id, job_id)

    monkeypatch.setattr(service, "reclassify_limited", edit_first)
    while (job := jobs.claim(queue)) is not None:
        jobs.process(service, queue, job)
    current = service.get_resource(owner, space, UUID(saved[0]["id"]))
    assert current["memory"]["claims"][0]["classification"]["method"] == "manual"
    with queue.transaction():
        queue.execute(
            "UPDATE space.memory_jobs SET status='failed',error_code='extraction_failed' "
            "WHERE request_kind='reclassify'"
        )
    remember(service, owner, space, "Within a day")
    assert len(reclassify_jobs_in(service, owner, space)) == 1


def test_catch_up_costs_one_transaction_per_write_and_reuses_the_write_key(commands, monkeypatch):
    from test_memory_write_efficiency import instrument

    service, owner, space, _ = commands
    item(service, owner, space, "category", "Dev")
    service.memory_models = ClassifyingModels()
    monkeypatch.setattr(reclassify_jobs, "maybe_enqueue", lambda *args: None)
    remember(service, owner, space, "First")
    baseline = instrument(service, monkeypatch)
    remember(service, owner, space, "Second")
    base = dict(baseline)
    monkeypatch.undo()
    service.memory_models = ClassifyingModels()
    first = instrument(service, monkeypatch)
    remember(service, owner, space, "Queues the catch-up")
    assert first["transactions"] == base["transactions"] + 1
    assert first["key_queries"] == base["key_queries"] + 1
    assert first["unwraps"] == base["unwraps"]
    later = instrument(service, monkeypatch)
    remember(service, owner, space, "Only checks")
    assert later["transactions"] == base["transactions"] + 1
    assert later["key_queries"] == base["key_queries"]
    assert len(reclassify_jobs_in(service, owner, space)) == 1
