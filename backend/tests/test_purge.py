"""Purging a Space removes its rows and its key, and leaves others alone."""

from uuid import UUID, uuid4

import pytest
from test_database_setup import database as database_fixture
from test_memora_workspace import Models
from test_workspace import command_database as command_database_fixture
from test_workspace import commands as commands_fixture

from space.memory import RecallMetadata, RememberMemory
from space.purge import purge_space
from space.security.auth import Identity
from space.workspace import SaveResource

database = database_fixture
command_database = command_database_fixture
commands = commands_fixture


def note(title, body):
    return SaveResource(
        command_id=uuid4(),
        expected_revision=0,
        kind="document",
        title=title,
        body=body,
        recall=RecallMetadata(category="source", topics=[]),
    )


def test_purging_an_account_removes_its_rows_and_keys(commands):
    """Every trace goes: rows and the Space key.

    Another account's Space is written before the purge and must survive it untouched.
    """
    service, owner, space, connect_as = commands
    service.save(owner, space, note("Doomed note", "doomed"))
    other = Identity("https://identity.invalid", str(uuid4()), 4102444800)
    kept_space = UUID(service.bootstrap(other)["id"])
    service.save(other, kept_space, note("Kept note", "kept"))
    service.memory_models = Models()
    for actor, target in ((owner, space), (other, kept_space)):
        service.extract_memory(
            actor, target, RememberMemory(command_id=uuid4(), title="Queued", text="A fact.")
        )

    scope, _key = service._key(owner, space)
    organization_id = scope["organization_id"]

    with connect_as() as connection:
        counts = purge_space(connection, organization_id, space, account=True)

    assert counts["resources"] == 1
    assert counts["space_keys"] == 1
    assert counts["pending_cleanup"] == 0
    with connect_as(admin=True) as connection:
        gone = connection.execute(
            "SELECT count(*) AS rows FROM space.spaces WHERE id=%s", (space,)
        ).fetchone()
        kept = connection.execute(
            "SELECT count(*) AS rows FROM space.resources WHERE space_id=%s", (kept_space,)
        ).fetchone()
        schedules = connection.execute(
            "SELECT space_id FROM space.memory_job_schedule WHERE space_id IN (%s,%s)",
            (space, kept_space),
        ).fetchall()
    assert gone[0] == 0
    assert kept[0] == 1
    assert schedules == [(kept_space,)]


def test_a_dry_run_reports_without_deleting(commands):
    """A dry run has to be safe to run against production, so it must write nothing."""
    service, owner, space, connect_as = commands
    service.save(owner, space, note("Doomed note", "doomed"))
    scope, _key = service._key(owner, space)
    organization_id = scope["organization_id"]

    with connect_as(admin=True) as connection:
        counts = purge_space(
            connection, organization_id, space, objects=service.objects, account=True, dry_run=True
        )
    assert counts["resources"] == 1
    assert counts["dry_run"] is True
    assert "objects" not in counts
    with connect_as(admin=True) as connection:
        still = connection.execute(
            "SELECT count(*) AS rows FROM space.resources WHERE space_id=%s", (space,)
        ).fetchone()
    assert still[0] == 1


def test_content_reset_keeps_workspace_and_tasks_usable(commands):
    service, owner, space, connect_as = commands
    service.save(owner, space, note("Old", "old"))
    scope, _ = service._key(owner, space)
    before = service.info(owner)
    with connect_as() as connection:
        purge_space(connection, scope["organization_id"], space, objects=service.objects)
    assert service.bootstrap(owner) == before
    saved = service.save(
        owner,
        space,
        SaveResource(command_id=uuid4(), expected_revision=0, kind="task", title="After reset"),
    )
    assert service.get_resource(owner, space, UUID(saved["id"]))["title"] == "After reset"


def test_runtime_cannot_read_or_delete_cleanup_work(commands):
    _, _, _, connect_as = commands
    import psycopg

    with connect_as(runtime=True) as connection:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute("SELECT * FROM space.purge_cleanup")
