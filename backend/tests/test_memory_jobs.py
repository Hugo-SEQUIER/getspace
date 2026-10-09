import secrets
import time
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.rows import dict_row
from test_database_setup import database as database_fixture
from test_memora_workspace import Models, delegated
from test_workspace import change as source_change
from test_workspace import command_database as command_database_fixture
from test_workspace import commands as commands_fixture

from space import jobs
from space.job_context import active_job
from space.job_worker import restrict_connection
from space.memory import RememberClaims, RememberMemory
from space.memory_pipeline import BuildMemory
from space.model_provider import ModelError
from space.security.auth import Identity, Unauthenticated
from space.workspace import CommandError

database = database_fixture
command_database = command_database_fixture
commands = commands_fixture


@pytest.fixture
def queue(commands):
    # The disposable database fixture is shared across cases; the worker queue is global.
    with commands[3]() as maintenance:
        maintenance.execute("DELETE FROM space.memory_jobs")
    with commands[3](admin=True) as connection:
        connection.row_factory = dict_row
        connection.execute("SET ROLE space_job_worker")
        connection.commit()
        yield connection


def request(**changes):
    return RememberMemory(
        command_id=uuid4(), title="Commute", text="Mira drives a car."
    ).model_copy(update=changes)


def test_prepared_claims_are_verbatim_and_use_embeddings_only(commands):
    service, owner, space, _ = commands
    service.memory_models = Models()
    prepared = RememberClaims(
        command_id=uuid4(),
        title="Decision",
        claims=[{"index": "Commute", "value": "Mira drives a car.", "importance": 3}],
    )
    result = service.remember_claims(owner, space, prepared)
    assert result["memory"]["claims"][0]["value"] == "Mira drives a car."
    assert result["memory"]["claims"][0]["importance"] == 3
    assert service.memory_models.calls and all(
        call[0] == "embed" for call in service.memory_models.calls
    )
    calls = len(service.memory_models.calls)
    assert service.remember_claims(owner, space, prepared) == result
    assert len(service.memory_models.calls) == calls


def test_queue_saves_input_then_worker_makes_memory_ready(commands, queue):
    service, owner, space, _ = commands
    service.memory_models = Models()
    change = request(text="PRIVATE-JOB-TEXT Mira drives a car.")
    queued = service.extract_memory(owner, space, change)
    assert queued["status"] == "queued" and queued["source_saved"] and not queued["recall_ready"]
    assert service.memory_models.calls == []
    assert service.extract_memory(owner, space, change) == queued
    job = jobs.claim(queue)
    assert b"PRIVATE-JOB-TEXT" in bytes(job["encrypted_request"])  # readable JSON
    assert "token" not in job
    result = jobs.process(service, queue, job)
    assert any(call[0] == "model" for call in service.memory_models.calls)
    assert [claim["value"] for claim in result["memory"]["claims"]] == [
        entry["value"] for entry in service.memory_models.outputs
    ]
    ready = service.memory_job(owner, space, change.command_id)
    assert ready["status"] == "ready" and ready["result"]["resource_id"] == result["id"]
    assert ready["recall_ready"] and jobs.claim(queue) is None
    calls = len(service.memory_models.calls)
    assert service.remember(owner, space, change) == result
    assert len(service.memory_models.calls) == calls


def test_enqueued_job_outlives_session_expiry(commands, queue, monkeypatch):
    service, owner, space, _ = commands
    service.memory_models = Models()
    # Long enough to enqueue on a busy machine, then the session is made to expire.
    short = Identity(owner.issuer, owner.subject, int(time.time()) + 5)
    change = request()
    service.extract_memory(short, space, change)
    # Worker reconstructs a bounded execution identity from the durable receipt.
    now = time.time()
    monkeypatch.setattr(time, "time", lambda: now + 6)
    with pytest.raises(Unauthenticated):
        short.check()
    job = jobs.claim(queue)
    assert jobs.process(service, queue, job)
    assert service.memory_job(owner, space, change.command_id)["status"] == "ready"


def test_revoked_original_grant_stops_queued_work(commands, queue):
    service, owner, space, _ = commands
    service.memory_models = Models()
    actor = delegated(service, owner, write=True)
    change = request()
    service.extract_memory(actor, space, change)
    service.revoke(owner, UUID(actor.client_id))
    # A new grant for the same client must not revive the old receipt.
    service.grant(owner, UUID(actor.client_id), True)
    jobs.process(service, queue, jobs.claim(queue))
    state = service.memory_job(owner, space, change.command_id)
    assert state["status"] == "failed" and state["error_code"] == "access_revoked"
    assert service.memory_models.calls == []


@pytest.mark.parametrize("attempts", [1, jobs.MAX_ATTEMPTS])
def test_expired_lease_recovers_committed_command_without_reextracting(commands, queue, attempts):
    service, owner, space, connect_as = commands
    service.memory_models = Models()
    change = request()
    service.extract_memory(owner, space, change)
    job = jobs.claim(queue)
    receipt = active_job.set((job["id"], job["lease_id"]))
    try:
        result = service.remember(owner, space, change, extract=True)
    finally:
        active_job.reset(receipt)
    calls = len(service.memory_models.calls)
    with connect_as() as maintenance:
        maintenance.execute(
            "UPDATE space.memory_jobs SET lease_until=now()-interval '1 second',attempts=%s "
            "WHERE id=%s",
            (attempts, job["id"]),
        )
    assert not jobs.finish(queue, job, error_code="expired_worker")
    recovered = jobs.claim(queue)
    assert recovered["lease_id"] != job["lease_id"]
    assert not jobs.finish(queue, job, error_code="stale_worker")
    assert jobs.process(service, queue, recovered) == result
    assert len(service.memory_models.calls) == calls


def test_queue_scope_payload_reuse_and_least_privilege(commands, queue):
    service, owner, space, connect_as = commands
    service.memory_models = Models()
    change = request()
    service.extract_memory(owner, space, change)
    with pytest.raises(CommandError):
        service.extract_memory(owner, space, change.model_copy(update={"text": "different"}))
    other = Identity(owner.issuer, str(uuid4()), 4102444800)
    other_space = UUID(service.bootstrap(other)["id"])
    with pytest.raises(CommandError):
        service.memory_job(other, other_space, change.command_id)
    with queue.transaction():
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with queue.transaction():
                queue.execute("SELECT * FROM space.resource_revisions")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with queue.transaction():
                queue.execute("UPDATE space.memory_jobs SET subject='forged'")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with queue.transaction():
                queue.execute("UPDATE space.memory_job_schedule SET space_id=gen_random_uuid()")
    with service.db.transaction(owner) as runtime:
        service._scope(runtime, owner, space)
        assert runtime.execute("SELECT space_id FROM space.memory_job_schedule").fetchall() == [
            {"space_id": space}
        ]
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with runtime.transaction():
                runtime.execute("UPDATE space.memory_job_schedule SET last_claimed_at=NULL")
    with connect_as(runtime=True) as runtime:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            runtime.execute("UPDATE space.memory_jobs SET status='ready'")


def available_now(connect_as, job_id):
    with connect_as() as maintenance:
        maintenance.execute(
            "UPDATE space.memory_jobs SET available_at=now() WHERE id=%s", (job_id,)
        )


def expire_lease(connect_as, job_id):
    with connect_as() as maintenance:
        maintenance.execute(
            "UPDATE space.memory_jobs SET lease_until=now()-interval '1 second' WHERE id=%s",
            (job_id,),
        )


def test_repeated_crashes_exhaust_even_when_processing_never_reaches_attempt_check(commands, queue):
    service, owner, space, connect_as = commands
    service.memory_models = Models()
    change = request()
    service.extract_memory(owner, space, change)
    for attempt in range(1, jobs.MAX_ATTEMPTS + jobs.MAX_RECOVERY_ATTEMPTS + 1):
        claimed = jobs.claim(queue)
        assert claimed["id"] == change.command_id and claimed["attempts"] == attempt
        # Simulate death before decrypt/recovery/status publication, including the
        # final recovery-only attempt. No process() error handler gets to run.
        expire_lease(connect_as, claimed["id"])
    assert jobs.claim(queue) is None
    state = service.memory_job(owner, space, change.command_id)
    assert state["status"] == "failed" and state["error_code"] == "recovery_failed"
    assert service.memory_models.calls == []
    assert not jobs.finish(queue, claimed, error_code="stale_worker")


def test_recovery_only_attempt_never_starts_new_model_work(commands, queue):
    service, owner, space, connect_as = commands
    service.memory_models = Models()
    change = request()
    service.extract_memory(owner, space, change)
    for _ in range(jobs.MAX_ATTEMPTS):
        claimed = jobs.claim(queue)
        expire_lease(connect_as, claimed["id"])
    recovery = jobs.claim(queue)
    assert recovery["attempts"] == jobs.MAX_ATTEMPTS + 1
    assert jobs.process(service, queue, recovery) is None
    assert service.memory_models.calls == []
    assert service.memory_job(owner, space, change.command_id)["status"] == "failed"


def test_new_space_gets_next_turn_and_rotation_survives_new_queue_connections(commands, queue):
    service, owner, space, connect_as = commands
    service.memory_models = Models()
    backlog = [request() for _ in range(3)]
    for change in backlog:
        service.extract_memory(owner, space, change)
    first = jobs.claim(queue)
    assert first["id"] == backlog[0].command_id
    jobs.finish(queue, first, error_code="fixture_done")
    other = Identity(owner.issuer, str(uuid4()), 4102444800)
    other_space = UUID(service.bootstrap(other)["id"])
    arrivals = [request() for _ in range(2)]
    for change in arrivals:
        service.extract_memory(other, other_space, change)
    # Each claim uses a new connection, as happens across supervised children/batches.
    expected = [arrivals[0], backlog[1], arrivals[1], backlog[2]]
    for change in expected:
        with connect_as(admin=True) as worker:
            worker.row_factory = dict_row
            worker.execute("SET ROLE space_job_worker")
            worker.commit()
            claimed = jobs.claim(worker)
            assert claimed["id"] == change.command_id
            jobs.finish(worker, claimed, error_code="fixture_done")


def test_idle_space_precedes_older_turn_of_space_already_running(commands, queue):
    service, owner, space, _ = commands
    service.memory_models = Models()
    for _ in range(3):
        service.extract_memory(owner, space, request())
    active = jobs.claim(queue)
    other = Identity(owner.issuer, str(uuid4()), 4102444800)
    other_space = UUID(service.bootstrap(other)["id"])
    for _ in range(2):
        service.extract_memory(other, other_space, request())
    served = jobs.claim(queue)
    assert served["space_id"] == other_space
    jobs.finish(queue, served, error_code="fixture_done")
    # Space A's last turn is older, but it already occupies a worker.
    assert jobs.claim(queue)["space_id"] == other_space
    jobs.finish(queue, active, error_code="fixture_done")


def test_concurrent_claimers_skip_locked_space_and_lone_space_can_use_capacity(commands, queue):
    service, owner, space, connect_as = commands
    service.memory_models = Models()
    for _ in range(3):
        service.extract_memory(owner, space, request())
    with connect_as(admin=True) as worker:
        worker.row_factory = dict_row
        worker.execute("SET ROLE space_job_worker")
        worker.execute("SET statement_timeout='2s'")
        worker.commit()
        with queue.transaction():
            first = jobs.claim(queue)
            other = Identity(owner.issuer, str(uuid4()), 4102444800)
            other_space = UUID(service.bootstrap(other)["id"])
            service.extract_memory(other, other_space, request())
            # The first claimer still holds its scheduler lock. The second must
            # claim the other Space without waiting for that transaction to end.
            second = jobs.claim(worker)
            assert first["space_id"] == space and second["space_id"] == other_space
        jobs.finish(worker, second, error_code="fixture_done")
        # A has two queued jobs and an active one. With no other eligible Space,
        # another worker can help A instead of leaving capacity idle.
        third = jobs.claim(worker)
        assert third["space_id"] == space and third["id"] != first["id"]


def test_delayed_space_does_not_block_eligible_work(commands, queue):
    service, owner, space, connect_as = commands
    service.memory_models = Models()
    delayed = request()
    service.extract_memory(owner, space, delayed)
    with connect_as() as maintenance:
        maintenance.execute(
            "UPDATE space.memory_jobs SET available_at=now()+interval '1 hour' WHERE id=%s",
            (delayed.command_id,),
        )
    other = Identity(owner.issuer, str(uuid4()), 4102444800)
    other_space = UUID(service.bootstrap(other)["id"])
    change = request()
    service.extract_memory(other, other_space, change)
    assert jobs.claim(queue)["id"] == change.command_id
    assert jobs.claim(queue) is None


def test_scheduler_migration_backfills_existing_queue_and_runtime_cannot_forge_turn(commands):
    service, owner, space, connect_as = commands
    service.memory_models = Models()
    service.extract_memory(owner, space, request())
    migration = (
        Path(__file__).parents[1] / "migrations" / "versions" / "0010_job_scheduling.sql"
    ).read_text()
    with connect_as() as maintenance:
        # Exercise the forward migration against a pre-scheduler queue, then undo
        # the fixture's DDL so this test cannot affect other cases in the module.
        with maintenance.transaction(force_rollback=True):
            maintenance.execute("DROP TRIGGER memory_jobs_schedule ON space.memory_jobs")
            maintenance.execute("DROP FUNCTION space.schedule_memory_job()")
            maintenance.execute("DROP TABLE space.memory_job_schedule")
            maintenance.execute("DROP INDEX space.memory_jobs_space_ready_idx")
            maintenance.execute(migration)
            assert maintenance.execute(
                "SELECT last_claimed_at FROM space.memory_job_schedule WHERE space_id=%s",
                (space,),
            ).fetchone() == (None,)
    with service.db.transaction(owner) as runtime:
        service._scope(runtime, owner, space)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with runtime.transaction():
                runtime.execute(
                    "INSERT INTO space.memory_job_schedule "
                    "(organization_id,space_id,last_claimed_at) VALUES (%s,%s,NULL)",
                    (uuid4(), uuid4()),
                )


def test_source_job_builds_the_requested_revision_and_rejects_changed_source(commands, queue):
    service, owner, space, _ = commands
    service.memory_models = Models()
    source = service.save(owner, space, source_change())
    change = BuildMemory(command_id=uuid4(), source={"resource_id": source["id"], "revision": 1})
    service.extract_memory(owner, space, change)
    result = jobs.process(service, queue, jobs.claim(queue))
    assert result["recall"]["sources"] == [{"resource_id": source["id"], "revision": 1}]
    rebuild = change.model_copy(
        update={"command_id": uuid4(), "resource_id": UUID(result["id"]), "expected_revision": 1}
    )
    service.extract_memory(owner, space, rebuild)
    service.save(
        owner,
        space,
        source_change().model_copy(
            update={"resource_id": UUID(source["id"]), "expected_revision": 1}
        ),
    )
    service.memory_models.calls.clear()
    jobs.process(service, queue, jobs.claim(queue))
    state = service.memory_job(owner, space, rebuild.command_id)
    assert state["status"] == "failed" and state["error_code"] == "source_or_revision_changed"
    assert service.memory_models.calls == []


@pytest.mark.parametrize("recover", [False, True])
def test_transient_errors_retry_with_a_bound_and_no_provider_text(commands, queue, recover):
    service, owner, space, connect_as = commands
    service.memory_models = Models()
    change = request()
    service.extract_memory(owner, space, change)

    def unavailable():
        raise ModelError("PRIVATE-FAILURE-CONTENT")

    service.memory_models.before_call = unavailable
    for attempt in range(1, jobs.MAX_ATTEMPTS + 1):
        available_now(connect_as, change.command_id)
        if recover and attempt == jobs.MAX_ATTEMPTS:
            service.memory_models.before_call = lambda: None
        jobs.process(service, queue, jobs.claim(queue))
        state = service.memory_job(owner, space, change.command_id)
        assert state["attempts"] == attempt
        assert "PRIVATE" not in str(state)
        assert state["status"] == (
            "queued" if attempt < jobs.MAX_ATTEMPTS else "ready" if recover else "failed"
        )
    assert jobs.claim(queue) is None


@pytest.mark.parametrize("operation", ["remember", "build"])
def test_queue_bound_admits_replay_and_snapshot_conflict_retries(
    commands, queue, monkeypatch, operation
):
    service, owner, space, connect_as = commands
    service.memory_models = Models()
    monkeypatch.setattr(jobs, "MAX_PENDING_PER_SPACE", 1)
    change = request()
    if operation == "build":
        source = service.save(owner, space, source_change())
        change = BuildMemory(
            command_id=uuid4(), source={"resource_id": source["id"], "revision": 1}
        )
    admitted = service.extract_memory(owner, space, change)
    assert service.extract_memory(owner, space, change) == admitted
    with pytest.raises(CommandError, match="queue is full"):
        service.extract_memory(owner, space, request())

    def concurrent_write():
        service.memory_models.before_call = lambda: None
        # Builds discover existing memories; incoming text has no such dependency.
        service.save(owner, space, source_change())

    service.memory_models.before_call = concurrent_write
    jobs.process(service, queue, jobs.claim(queue))
    if operation == "build":
        assert (
            service.memory_job(owner, space, change.command_id)["error_code"] == "context_changed"
        )
        available_now(connect_as, change.command_id)
        assert jobs.process(service, queue, jobs.claim(queue))
    else:
        assert service.memory_job(owner, space, change.command_id)["attempts"] == 1
        assert service.memory_job(owner, space, change.command_id)["error_code"] is None
    assert service.memory_job(owner, space, change.command_id)["status"] == "ready"


def test_revocation_during_extraction_prevents_index_commit(commands, queue):
    service, owner, space, _ = commands
    service.memory_models = Models()
    actor = delegated(service, owner, write=True)
    change = request()
    service.extract_memory(actor, space, change)

    revocation_errors = []

    def revoke_after_first_call():
        if service.memory_models.calls:
            try:
                service.revoke(owner, UUID(actor.client_id))
            except Exception as error:
                revocation_errors.append(repr(error))
                raise

    service.memory_models.before_call = revoke_after_first_call
    jobs.process(service, queue, jobs.claim(queue))
    assert service.memory_models.calls  # Work actually started before the revocation.
    assert revocation_errors == []
    state = service.memory_job(owner, space, change.command_id)
    assert state["status"] == "failed", state
    assert service.list_memories(owner, space)["items"] == []
    assert service.memory_info(owner, space)["index"]["claims"] == 0


def test_worker_login_is_restricted_before_claiming_jobs(commands):
    connect_as = commands[3]
    with connect_as() as migration:
        migration.row_factory = dict_row
        with pytest.raises(RuntimeError, match="non-privileged login"):
            restrict_connection(migration)
    password = secrets.token_urlsafe(32)
    with connect_as(admin=True) as setup:
        setup.execute(
            psycopg.sql.SQL("CREATE ROLE test_job_login LOGIN NOINHERIT PASSWORD {}").format(
                psycopg.sql.Literal(password)
            )
        )
        setup.execute("GRANT space_job_worker TO test_job_login")
        connection_args = {
            "host": setup.info.host,
            "port": setup.info.port,
            "dbname": setup.info.dbname,
            "user": "test_job_login",
            "password": password,
            "sslmode": "disable",
            "row_factory": dict_row,
        }
    with psycopg.connect(**connection_args) as worker:
        restrict_connection(worker)
        assert (
            worker.execute("SELECT current_user AS role").fetchone()["role"] == "space_job_worker"
        )
        worker.commit()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            worker.execute("SELECT * FROM space.space_keys")


def test_optional_empty_links_preserve_legacy_extraction_job_retry(commands, queue):
    import hmac

    from space.workspace import encode

    service, owner, space, _ = commands
    service.memory_models = Models()
    change = request()
    original = service.extract_memory(owner, space, change)
    scope, key = service._key(owner, space, write=True)
    legacy_payload = change.model_dump(mode="json")
    legacy_payload.pop("links")
    fingerprint = hmac.digest(
        key.key.material, b"space.job.v1\0remember" + encode(legacy_payload), "sha256"
    )
    with service.db.transaction(owner) as connection:
        service._scope(connection, owner, space, write=True)
        stored = connection.execute(
            "SELECT fingerprint FROM space.memory_jobs WHERE id=%s", (change.command_id,)
        ).fetchone()
        assert bytes(stored["fingerprint"]) == fingerprint
    assert service.extract_memory(owner, space, change) == original
    assert service.memory_models.calls == []
