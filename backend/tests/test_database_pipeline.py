"""Parameters: disposable PostgreSQL and synthetic scoped identities.
What it does: verifies transaction boundaries, rollback, scope cleanup and protocol overhead.
Output: real database safety assertions and protocol synchronization counts, not cloud timings.
"""

import socket
import weakref
from contextlib import contextmanager
from uuid import uuid4

import psycopg
import psycopg_pool
import pytest
from test_database_setup import database as database_fixture
from test_workspace import change
from test_workspace import command_database as command_database_fixture
from test_workspace import commands as commands_fixture

from space.workspace import CommandError, Database, deferred_fence

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


def test_deferred_environment_fence_rides_the_first_query_and_still_rejects(commands):
    service, owner, space, _ = commands
    with deferred_fence(), service.db.transaction(owner) as connection:
        scope = service._scope(connection, owner, space)
    assert scope["space_id"] == space
    service.db.environment_id = uuid4()
    try:
        with pytest.raises(CommandError, match="Environment unavailable"):
            with deferred_fence(), service.db.transaction(owner) as connection:
                service._scope(connection, owner, space)
        with pytest.raises(CommandError, match="Environment unavailable"):
            service._recheck(owner, space)
    finally:
        del service.db.environment_id


def test_recently_used_connections_skip_the_pool_ping_until_they_sit_idle(monkeypatch):
    pings = []
    monkeypatch.setattr(
        psycopg_pool.ConnectionPool, "check_connection", staticmethod(lambda c: pings.append(c))
    )
    database = Database.__new__(Database)
    database._returned = weakref.WeakKeyDictionary()
    server, client = socket.socketpair()

    class Connection:
        autocommit = True

        def fileno(self):
            return client.fileno()

    connection = Connection()
    try:
        database._check_idle_connection(connection)
        assert pings == [connection], "a connection never seen before is checked"
        database._mark_returned(connection)
        database._check_idle_connection(connection)
        assert pings == [connection], "a connection returned moments ago is trusted"
        # A backend terminated while idle writes its notice before closing.
        server.sendall(b"E")
        database._check_idle_connection(connection)
        assert pings == [connection, connection], "a readable socket is checked at once"
        client.recv(1)
        database._returned[connection] -= Database.IDLE_CHECK_SECONDS
        database._check_idle_connection(connection)
        assert pings == [connection] * 3, "an idle connection is checked again"
    finally:
        server.close()
        client.close()


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
