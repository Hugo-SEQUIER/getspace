"""Document and task deletion against an isolated PostgreSQL database."""

from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from test_database_setup import database as database_fixture
from test_memora_workspace import claim_change, delegated, setup
from test_teams import create_team, join
from test_workspace import change
from test_workspace import command_database as command_database_fixture
from test_workspace import commands as commands_fixture

from space.resource_deletion import delete_memory, delete_resource
from space.workspace import CommandError

database = database_fixture
command_database = command_database_fixture
commands = commands_fixture


def test_deletion_removes_every_revision(commands):
    service, owner, space, connect = commands
    first = service.save(owner, space, change())
    rid = UUID(first["id"])
    service.save(
        owner,
        space,
        change().model_copy(
            update={
                "resource_id": rid,
                "expected_revision": 1,
                "body": "Second version",
            }
        ),
    )
    with pytest.raises(CommandError, match="changed"):
        delete_resource(service, owner, space, rid, 1)
    assert service.get_resource(owner, space, rid)["revision"] == 2
    with connect() as connection:
        connection.execute("UPDATE space.usage_limits SET storage_limit_bytes=retained_bytes")
    assert delete_resource(service, owner, space, rid, 2) == {"deleted": True}
    assert delete_resource(service, owner, space, rid, 2) == {"deleted": True}
    assert service.list_resources(owner, space, "document")["items"] == []
    for revision in (None, 1, 2):
        with pytest.raises(CommandError, match="not found"):
            service.get_resource(owner, space, rid, revision)
    with connect() as connection:
        remaining = connection.execute(
            "SELECT count(*) FROM space.resource_revisions WHERE resource_id=%s", (rid,)
        ).fetchone()[0]
        assert remaining == 0
        assert connection.execute("SELECT count(*) FROM space.restore_objects").fetchone()[0] == 0


def test_delete_preserves_memories_and_they_remain_editable(commands):
    service, owner, space, source, request = setup(commands)
    memory = service.build_memory(owner, space, request)
    rid = UUID(source["id"])
    with pytest.raises(CommandError, match="change_memory"):
        delete_resource(service, owner, space, UUID(memory["id"]), 1)
    delete_resource(service, owner, space, rid, 1)
    saved = service.get_resource(owner, space, UUID(memory["id"]))
    assert saved["memory"]["claims"] == memory["memory"]["claims"]
    edited = service.change_memory(
        owner,
        space,
        claim_change(
            memory,
            memory["memory"]["claims"][0]["id"],
            "edit",
            value="Updated memory.",
        ),
    )
    assert edited["revision"] == 2


def test_delete_rejects_delegated_access_and_cannot_cross_spaces(commands):
    service, owner, space, _ = commands
    saved = service.save(owner, space, change())
    rid = UUID(saved["id"])
    with pytest.raises(CommandError, match="Agents can delete tasks only") as error:
        delete_resource(service, delegated(service, owner, write=True), space, rid, 1)
    assert error.value.status == 403
    stranger = replace(owner, subject=str(uuid4()))
    other_space = UUID(service.bootstrap(stranger)["id"])
    with pytest.raises(CommandError):
        delete_resource(service, stranger, space, rid, 1)
    delete_resource(service, stranger, other_space, rid, 1)
    assert service.get_resource(owner, space, rid)["id"] == saved["id"]


def test_team_viewer_cannot_delete_but_member_can(commands):
    service, owner, _, _ = commands
    team = create_team(service, owner)
    space = UUID(team["id"])
    viewer, _ = join(service, owner, team, role="viewer")
    member, _ = join(service, owner, team)
    saved = service.save(owner, space, change())
    rid = UUID(saved["id"])
    with pytest.raises(CommandError) as error:
        delete_resource(service, viewer, space, rid, 1)
    assert error.value.status == 403
    assert service.get_resource(owner, space, rid)["id"] == saved["id"]
    assert delete_resource(service, member, space, rid, 1) == {"deleted": True}


def test_task_deletion_removes_every_revision(commands):
    service, owner, space, _ = commands
    task = service.save(owner, space, change().model_copy(update={"kind": "task"}))
    rid = UUID(task["id"])
    service.save(
        owner,
        space,
        change().model_copy(
            update={
                "kind": "task",
                "resource_id": rid,
                "expected_revision": 1,
                "body": "Second version",
            }
        ),
    )
    with pytest.raises(CommandError, match="changed"):
        delete_resource(service, owner, space, rid, 1)
    assert service.get_resource(owner, space, rid)["revision"] == 2
    assert delete_resource(service, owner, space, rid, 2) == {"deleted": True}
    assert delete_resource(service, owner, space, rid, 2) == {"deleted": True}
    assert service.list_resources(owner, space, "task")["items"] == []
    for revision in (None, 1, 2):
        with pytest.raises(CommandError, match="not found"):
            service.get_resource(owner, space, rid, revision)


def test_task_deletion_accepts_a_writer_grant_but_not_a_reader(commands):
    service, owner, space, _ = commands
    task = service.save(owner, space, change().model_copy(update={"kind": "task"}))
    rid = UUID(task["id"])
    with pytest.raises(CommandError) as error:
        delete_resource(service, delegated(service, owner), space, rid, 1)
    assert error.value.status == 403
    assert service.get_resource(owner, space, rid)["id"] == task["id"]
    unidentified = replace(delegated(service, owner, write=True), agent_id=None)
    with pytest.raises(CommandError, match="identification required"):
        delete_resource(service, unidentified, space, rid, 1)
    writer = delegated(service, owner, write=True)
    with pytest.raises(CommandError, match="changed"):
        delete_resource(service, writer, space, rid, 2)
    assert delete_resource(service, writer, space, rid, 1) == {"deleted": True}
    assert delete_resource(service, writer, space, rid, 1) == {"deleted": True}
    assert service.list_resources(owner, space, "task")["items"] == []


def test_memory_record_delete_removes_claims_index_and_history(commands):
    service, owner, space, connect = commands
    *_, request = setup(commands)
    memory = service.build_memory(owner, space, request)
    rid = UUID(memory["id"])
    with connect() as connection:
        before = connection.execute(
            "SELECT count(*) FROM space.memory_claims WHERE memory_id=%s", (rid,)
        ).fetchone()[0]
    assert before > 0
    assert delete_memory(service, owner, space, rid, memory["revision"]) == {"deleted": True}
    with connect() as connection:
        after = connection.execute(
            "SELECT count(*) FROM space.memory_claims WHERE memory_id=%s", (rid,)
        ).fetchone()[0]
        history = connection.execute(
            "SELECT count(*) FROM space.resource_revisions WHERE resource_id=%s", (rid,)
        ).fetchone()[0]
    assert after == 0 and history == 0
    assert service.list_memories(owner, space)["items"] == []
    with pytest.raises(CommandError, match="not found"):
        service.get_resource(owner, space, rid)
    # A record that is already absent is a success, so a retry is safe.
    assert delete_memory(service, owner, space, rid, memory["revision"]) == {"deleted": True}


def test_memory_record_delete_refuses_other_kinds_and_a_stale_revision(commands):
    service, owner, space, _ = commands
    document = service.save(owner, space, change())
    with pytest.raises(CommandError, match="generated memory record"):
        delete_memory(service, owner, space, UUID(document["id"]), 1)
    *_, request = setup(commands)
    memory = service.build_memory(owner, space, request)
    with pytest.raises(CommandError, match="changed"):
        delete_memory(service, owner, space, UUID(memory["id"]), 2)
    assert service.get_resource(owner, space, UUID(memory["id"]))["id"] == memory["id"]


def test_memory_record_delete_accepts_a_writer_grant(commands):
    """A memory record follows the write grant, unlike a source document.

    An agent may already remove a claim permanently with change_memory, so removing the
    whole record grants no new capability.
    """
    service, owner, space, _ = commands
    *_, request = setup(commands)
    memory = service.build_memory(owner, space, request)
    rid = UUID(memory["id"])
    with pytest.raises(CommandError) as error:
        delete_memory(service, delegated(service, owner), space, rid, memory["revision"])
    assert error.value.status == 403
    assert service.get_resource(owner, space, rid)["id"] == memory["id"]
    writer = delegated(service, owner, write=True)
    assert delete_memory(service, writer, space, rid, memory["revision"]) == {"deleted": True}
