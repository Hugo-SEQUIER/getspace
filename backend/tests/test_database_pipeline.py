"""Parameters: disposable PostgreSQL and synthetic scoped identities.
What it does: verifies transaction boundaries, rollback, scope cleanup and protocol overhead.
Output: real database safety assertions and protocol synchronization counts, not cloud timings.
"""

from contextlib import contextmanager

import psycopg
import pytest
from test_database_setup import database as database_fixture
from test_workspace import change
from test_workspace import command_database as command_database_fixture
from test_workspace import commands as commands_fixture

database = database_fixture
command_database = command_database_fixture
commands = commands_fixture


def test_transaction_pipeline_order_removes_one_protocol_barrier(commands, monkeypatch):
    service, owner, space, _ = commands
    pool_connection = service.db.pool.connection
    enqueue_sync = psycopg.Pipeline._enqueue_sync
    exec_command = psycopg.Connection._exec_command
    tracked, barriers = None, []

    @contextmanager
    def tracked_connection(*args, **kwargs):
        nonlocal tracked
        with pool_connection(*args, **kwargs) as connection:
            tracked = connection
            try:
                yield connection
            finally:
                tracked = None

    def count_sync(pipeline):
        if pipeline._conn is tracked:
            barriers.append("sync")
        return enqueue_sync(pipeline)

    def count_command(connection, command, *args, **kwargs):
        if connection is tracked and not connection._pipeline and command in (b"BEGIN", b"COMMIT"):
            barriers.append(command.decode())
        return exec_command(connection, command, *args, **kwargs)

    monkeypatch.setattr(service.db.pool, "connection", tracked_connection)
    monkeypatch.setattr(psycopg.Pipeline, "_enqueue_sync", count_sync)
    monkeypatch.setattr(psycopg.Connection, "_exec_command", count_command)
    with service.db.pool.connection() as connection:
        with connection.pipeline(), connection.transaction():
            service.db.check_environment(connection)
            connection.execute(
                "SELECT set_config('space.identity_issuer', %s, true)", (owner.issuer,)
            )
            connection.execute(
                "SELECT set_config('space.identity_subject', %s, true)", (owner.subject,)
            )
            legacy_scope = service._scope(connection, owner, space)
    legacy = list(barriers)
    barriers.clear()
    with service.db.transaction(owner) as connection:
        current_scope = service._scope(connection, owner, space)
    assert current_scope == legacy_scope
    assert legacy == ["sync"] * 4
    assert barriers == ["BEGIN", "sync", "COMMIT"]


@pytest.mark.parametrize("failure", [None, "python", "sql"])
def test_pipeline_commits_or_rolls_back_and_clears_local_scope(commands, failure):
    service, owner, space, _ = commands
    service.db.pool.resize(0, 1)
    resource = service.save(owner, space, change())
    with service.db.transaction(owner) as connection:
        service._scope(connection, owner, space)
        before = connection.execute(
            "SELECT content_lines FROM space.resources WHERE id=%s", (resource["id"],)
        ).fetchone()["content_lines"]

    def update():
        with service.db.transaction(owner) as connection:
            service._scope(connection, owner, space, write=True)
            connection.execute(
                "UPDATE space.resources SET content_lines=content_lines+1 WHERE id=%s",
                (resource["id"],),
            )
            if failure == "python":
                raise RuntimeError("synthetic operation failure")
            if failure == "sql":
                # No fetch: a buffered SQL failure must still prevent commit on context exit.
                connection.execute("SELECT 1/0")

    if failure is None:
        update()
    else:
        with pytest.raises(RuntimeError if failure == "python" else psycopg.errors.DivisionByZero):
            update()
    with service.db.pool.connection() as connection:
        cleared = connection.execute(
            "SELECT current_setting('space.identity_issuer',true) AS issuer,"
            "current_setting('space.identity_subject',true) AS subject,"
            "current_setting('space.organization_id',true) AS organization_id,"
            "current_setting('space.space_id',true) AS space_id"
        ).fetchone()
        assert not any(cleared.values())
    with service.db.transaction(owner) as connection:
        service._scope(connection, owner, space)
        after = connection.execute(
            "SELECT content_lines FROM space.resources WHERE id=%s", (resource["id"],)
        ).fetchone()["content_lines"]
    assert after == before + (1 if failure is None else 0)
