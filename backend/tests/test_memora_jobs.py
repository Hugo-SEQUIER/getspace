import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_database_setup import database as database_fixture
from test_memora_import import export, record
from test_memora_pipeline import Models
from test_memora_workspace import delegated, embeds
from test_memory_jobs import queue as queue_fixture
from test_workspace import command_database as command_database_fixture
from test_workspace import commands as commands_fixture

from space import jobs, memora_jobs
from space.api.rest import create_routes
from space.job_context import active_job
from space.memora_import import plan_import
from space.memora_jobs import Preview, Retry, Start
from space.security.auth import Unauthenticated
from space.settings import Settings
from space.workspace import CommandError

database = database_fixture
command_database = command_database_fixture
commands = commands_fixture
queue = queue_fixture


def request(count=1, **changes):
    return Start(
        command_id=uuid4(),
        source_id="fixture-store",
        namespaces=["private-work"],
        text=json.dumps(export(*(record(str(n)) for n in range(count)))),
    ).model_copy(update=changes)


def process_next(service, queue):
    job = jobs.claim(queue)
    assert job
    return jobs.process(service, queue, job)


def test_browser_routes_preview_enqueue_and_read_durable_progress(commands, queue):
    service, owner, space, _ = commands
    service.memory_models = Models()
    app = FastAPI()
    app.include_router(
        create_routes(
            service, SimpleNamespace(verify=lambda _: owner), Settings(environment="test")
        )
    )
    path = f"/v1/spaces/{space}/memory/imports"
    body = request().model_dump(mode="json")
    with TestClient(app, headers={"Authorization": "Bearer fixture"}) as client:
        preview = client.post(
            path + "/preview", json={"source_id": body["source_id"], "text": body["text"]}
        )
        assert preview.status_code == 200 and preview.json()["valid_records"] == 1
        assert not service.memory_models.calls
        queued = client.post(path, json=body)
        assert queued.status_code == 202 and queued.json()["status"] == "queued"
        process_next(service, queue)
        recent = client.get(path)
        assert recent.status_code == 200
        assert recent.json()["items"][0]["completed"] == 1
        assert recent.json()["items"][0]["status"] == "ready"


def test_preview_and_encrypted_job_progress_survive_worker_turns(commands, queue):
    service, owner, space, _ = commands
    service.memory_models = Models()
    change = request(5)
    preview = memora_jobs.preview(
        service, owner, space, Preview(source_id=change.source_id, text=change.text)
    )
    assert preview["records"] == 5 and preview["previously_imported"] == 0
    assert not service.list_memories(owner, space)["items"]
    queued = memora_jobs.start(service, owner, space, change)
    assert queued["total"] == 5 and queued["completed"] == 0
    assert memora_jobs.start(service, owner, space, change) == queued
    job = jobs.claim(queue)
    assert b"Mira" in bytes(job["encrypted_request"])  # queued input is readable JSON
    assert not service.memory_models.calls
    assert jobs.process(service, queue, job) is None  # A bounded turn yields the queue.
    progress = memora_jobs.recent(service, owner, space)["items"][0]
    assert progress["completed"] == 4 and progress["status"] == "queued"
    assert len(service.list_memories(owner, space)["items"]) == 4
    process_next(service, queue)
    complete = memora_jobs.recent(service, owner, space)["items"][0]
    assert complete["completed"] == 5 and complete["status"] == "ready"
    assert len(embeds(service)) == 5
    assert service.list_resources(owner, space, "document")["items"] == []
    assert service.memory_jobs(owner, space)["items"] == []  # Separate from extraction UI.
    preview = memora_jobs.preview(
        service, owner, space, Preview(source_id=change.source_id, text=change.text)
    )
    assert preview["previously_imported"] == 5 and not preview["conflict_rows"]
    assert memora_jobs.start(service, owner, space, change)["status"] == "ready"
    assert len(embeds(service)) == 5
    assert service.list_resources(owner, space, "document")["items"] == []


def test_failed_import_resumes_at_checkpoint_without_reembedding(commands, queue, monkeypatch):
    service, owner, space, _ = commands
    service.memory_models = Models()
    change = request(3)
    memora_jobs.start(service, owner, space, change)
    remember = service.remember_claims
    calls = 0

    def fail_second(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise CommandError("Synthetic nonretryable failure", 400)
        return remember(*args)

    monkeypatch.setattr(service, "remember_claims", fail_second)
    process_next(service, queue)
    failed = memora_jobs.recent(service, owner, space)["items"][0]
    assert failed["status"] == "failed" and failed["completed"] == 1
    retry = Retry(command_id=uuid4())
    queued = memora_jobs.retry(service, owner, space, change.command_id, retry)
    assert queued["completed"] == 1
    assert memora_jobs.retry(service, owner, space, change.command_id, retry) == queued
    monkeypatch.setattr(service, "remember_claims", remember)
    process_next(service, queue)
    ready = service.memory_job(owner, space, retry.command_id)
    assert ready["status"] == "ready" and ready["completed"] == 3
    assert len(embeds(service)) == 3
    assert len(service.list_memories(owner, space)["items"]) == 3


def test_lost_commit_response_recovers_on_final_attempt_without_model_calls(
    commands, queue, monkeypatch
):
    service, owner, space, connect = commands
    service.memory_models = Models()
    change = request()
    memora_jobs.start(service, owner, space, change)
    remember = service.remember_claims

    def lose_response(*args):
        remember(*args)
        raise CommandError("Lost commit response", 503)

    monkeypatch.setattr(service, "remember_claims", lose_response)
    process_next(service, queue)
    assert len(embeds(service)) == 1
    with connect() as maintenance:
        maintenance.execute(
            "UPDATE space.memory_jobs SET status='processing',"
            "lease_until=now()-interval '1 second',"
            "attempts=%s WHERE id=%s",
            (jobs.MAX_ATTEMPTS, change.command_id),
        )
    monkeypatch.setattr(
        service, "remember_claims", lambda *_: pytest.fail("Recovery cannot generate")
    )
    process_next(service, queue)
    assert service.memory_job(owner, space, change.command_id)["status"] == "ready"
    assert len(embeds(service)) == 1


def test_final_checkpoint_recovers_after_status_update_crash(commands, queue):
    service, owner, space, connect = commands
    service.memory_models = Models()
    change = request()
    memora_jobs.start(service, owner, space, change)
    job = jobs.claim(queue)
    receipt = active_job.set((job["id"], job["lease_id"]))
    try:
        payload = {"source_id": change.source_id, "records": [record("0")]}
        result, yielded = memora_jobs.process_batch(service, queue, job, owner, payload)
        assert result and not yielded
    finally:
        active_job.reset(receipt)
    with connect() as maintenance:
        maintenance.execute(
            "UPDATE space.memory_jobs SET lease_until=now()-interval '1 second',attempts=%s "
            "WHERE id=%s",
            (jobs.MAX_ATTEMPTS, change.command_id),
        )
    process_next(service, queue)
    assert service.memory_job(owner, space, change.command_id)["status"] == "ready"
    assert len(embeds(service)) == 1


def test_conflicting_source_detected_before_queueing_and_cli_receipts_match(commands, queue):
    service, owner, space, _ = commands
    service.memory_models = Models()
    change = request()
    plan = plan_import(json.loads(change.text), change.source_id, space_id=space)
    source = service.save(owner, space, memora_jobs._source_request(plan.writes[0]))
    assert source
    assert not memora_jobs.preview(service, owner, space, change)["conflict_rows"]
    changed = json.loads(change.text)
    changed["records"][0]["metadata"]["value"] = "Changed fact"
    change = change.model_copy(update={"text": json.dumps(changed)})
    assert memora_jobs.preview(service, owner, space, change)["conflict_rows"] == [1]
    with pytest.raises(CommandError, match="differ"):
        memora_jobs.start(service, owner, space, change)
    assert jobs.claim(queue) is None


def test_namespace_selection_preserves_other_namespaces_and_rejects_invalid_exports(
    commands, queue
):
    service, owner, space, _ = commands
    service.memory_models = Models()
    data = export(record("0"), record("1", namespace="personal"))
    change = request(text=json.dumps(data))
    queued = memora_jobs.start(service, owner, space, change)
    assert queued["total"] == 1
    process_next(service, queue)
    assert len(service.list_memories(owner, space)["items"]) == 1
    data["records"][1]["metadata"]["value"] = ""
    with pytest.raises(CommandError, match="record errors"):
        memora_jobs.start(service, owner, space, request(text=json.dumps(data)))


def test_import_requires_human_write_access_and_active_lease(commands, queue):
    service, owner, space, connect = commands
    service.memory_models = Models()
    actor = delegated(service, owner, write=True)
    with pytest.raises(Unauthenticated):
        memora_jobs.start(service, actor, space, request())
    change = request()
    memora_jobs.start(service, owner, space, change)
    job = jobs.claim(queue)
    with connect() as maintenance:
        maintenance.execute(
            "UPDATE space.memory_jobs SET lease_until=now()-interval '1 second' WHERE id=%s",
            (job["id"],),
        )
    jobs.process(service, queue, job)
    assert not service.memory_models.calls
    assert not service.list_memories(owner, space)["items"]


@pytest.mark.parametrize("completed", [False, True])
def test_legacy_import_retries_reuse_memory_and_never_add_documents(commands, queue, completed):
    from space.memory import SourceReference

    service, owner, space, _ = commands
    service.memory_models = Models()
    change = request()
    item = plan_import(json.loads(change.text), change.source_id, space_id=space).writes[0]
    source = service.save(owner, space, memora_jobs._source_request(item))
    original = None
    if completed:
        original = service.remember_claims(
            owner,
            space,
            item.memory(SourceReference(resource_id=source["id"], revision=source["revision"])),
        )
    preview = memora_jobs.preview(service, owner, space, change)
    assert preview["previously_imported"] == int(completed)
    assert preview["conflict_rows"] == []
    saved = service.remember_claims(owner, space, item.memory())
    if original:
        assert saved == original
    else:
        assert saved["memory"]["provenance"]["record"]["id"] == "0"
        assert saved["recall"]["sources"] == []
    memora_jobs.start(service, owner, space, change)
    process_next(service, queue)
    assert len(service.list_memories(owner, space)["items"]) == 1
    assert len(service.list_resources(owner, space, "document")["items"]) == 1
    assert len(embeds(service)) == 1


def test_import_evidence_must_match_claim_and_canonical_command(commands):
    service, owner, space, _ = commands
    service.memory_models = Models()
    item = plan_import(export(record()), "fixture", space_id=space).writes[0]
    invalid = item.memory().model_copy(update={"command_id": uuid4()})
    with pytest.raises(CommandError, match="must match"):
        service.remember_claims(owner, space, invalid)
    assert not service.memory_models.calls
    assert service.list_memories(owner, space)["items"] == []
