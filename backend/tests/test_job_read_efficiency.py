"""Status reads preserve progress without transferring encrypted worker inputs."""

from contextlib import contextmanager

import pytest
from test_database_setup import database as database_fixture
from test_memora_jobs import request as import_request
from test_memora_pipeline import Models
from test_memory_jobs import request as memory_request
from test_workspace import command_database as command_database_fixture
from test_workspace import commands as commands_fixture

from space import jobs, memora_jobs

database = database_fixture
command_database = command_database_fixture
commands = commands_fixture


@pytest.mark.parametrize("kind", ["memory", "import"])
@pytest.mark.parametrize("listing", [False, True])
def test_status_reads_only_transfer_public_progress(commands, monkeypatch, kind, listing):
    service, owner, space, _ = commands
    service.memory_models = Models()
    memory = memory_request()
    imported = import_request(300)
    expected = {
        "memory": jobs.enqueue(service, owner, space, memory),
        "import": memora_jobs.start(service, owner, space, imported),
    }
    # A realistic import stores over 100 KB that no status response needs.
    with service.db.transaction(owner) as connection:
        service._scope(connection, owner, space)
        size = connection.execute(
            "SELECT octet_length(encrypted_request) AS bytes FROM space.memory_jobs WHERE id=%s",
            (imported.command_id,),
        ).fetchone()["bytes"]
    assert size > 100_000

    transaction = service.db.transaction
    projections = []

    class Cursor:
        def __init__(self, wrapped):
            self.wrapped = wrapped

        def fetchone(self):
            result = self.wrapped.fetchone()
            self.record_projection()
            return result

        def fetchall(self):
            result = self.wrapped.fetchall()
            self.record_projection()
            return result

        def record_projection(self):
            # Fetch first: pipeline mode fills description when the result arrives.
            # Inspect the database result before public_job strips any fields.
            projections.append({column.name for column in self.wrapped.description})

    class Connection:
        def __init__(self, wrapped):
            self.wrapped = wrapped

        def execute(self, query, *args, **kwargs):
            cursor = self.wrapped.execute(query, *args, **kwargs)
            if "FROM space.memory_jobs" in str(query):
                return Cursor(cursor)
            return cursor

        def __getattr__(self, name):
            return getattr(self.wrapped, name)

    @contextmanager
    def measured(identity):
        with transaction(identity) as connection:
            yield Connection(connection)

    monkeypatch.setattr(service.db, "transaction", measured)
    if listing:
        module = jobs if kind == "memory" else memora_jobs
        assert module.recent(service, owner, space) == {"items": [expected[kind]]}
    else:
        request = memory if kind == "memory" else imported
        assert jobs.get(service, owner, space, request.command_id) == expected[kind]
    assert len(projections) == 1
    assert projections[0] <= {
        "id",
        "status",
        "attempts",
        "result_resource_id",
        "result_revision",
        "error_code",
        "request_kind",
        "import_total",
        "import_completed",
        "created_at",
    }
