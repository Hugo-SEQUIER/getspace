"""A person's seen marks follow their reading, never an agent's writing."""

from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from test_auth import signing as signing_fixture
from test_database_setup import database as database_fixture
from test_workspace import change, delegated
from test_workspace import command_database as command_database_fixture
from test_workspace import commands as commands_fixture

from space import resource_views, tasks
from space.resource_views import MarkSeen, SeenItem
from space.security.auth import ApiKeyScope, Unauthenticated
from space.workspace import SaveResource

database = database_fixture
command_database = command_database_fixture
commands = commands_fixture
signing = signing_fixture


def test_marks_follow_reading_and_never_move_backwards_or_past_the_current_revision(commands):
    service, owner, space, _ = commands
    agent = delegated(service, owner, write=True)
    first = service.save(agent, space, change())
    second = service.save(agent, space, change().model_copy(update={"title": "Second"}))
    assert resource_views.listing(service, owner, space) == {"items": []}

    seen = resource_views.mark(
        service, owner, space, MarkSeen(items=[SeenItem(resource_id=UUID(first["id"]), revision=1)])
    )
    assert [(item["resource_id"], item["seen_revision"]) for item in seen["items"]] == [
        (first["id"], 1)
    ]
    service.save(
        agent,
        space,
        SaveResource(
            command_id=uuid4(),
            resource_id=UUID(first["id"]),
            expected_revision=1,
            kind="document",
            title="Edited by the agent",
            body="Changed",
        ),
    )
    listing = resource_views.listing(service, owner, space)
    assert [(item["resource_id"], item["seen_revision"]) for item in listing["items"]] == [
        (first["id"], 1)
    ], "an agent's write does not count as the person having looked"

    # A mark past the current revision is capped, a lower mark keeps the higher one,
    # a duplicate keeps the highest requested, and an unknown resource is skipped.
    seen = resource_views.mark(
        service,
        owner,
        space,
        MarkSeen(
            items=[
                SeenItem(resource_id=UUID(first["id"]), revision=9),
                SeenItem(resource_id=UUID(second["id"]), revision=1),
                SeenItem(resource_id=UUID(second["id"]), revision=1),
                SeenItem(resource_id=uuid4(), revision=1),
            ]
        ),
    )
    assert sorted((item["resource_id"], item["seen_revision"]) for item in seen["items"]) == sorted(
        [(first["id"], 2), (second["id"], 1)]
    )
    resource_views.mark(
        service, owner, space, MarkSeen(items=[SeenItem(resource_id=UUID(first["id"]), revision=1)])
    )
    listing = resource_views.listing(service, owner, space)
    assert {item["resource_id"]: item["seen_revision"] for item in listing["items"]} == {
        first["id"]: 2,
        second["id"]: 1,
    }


def test_seen_state_is_personal_and_refuses_agents_and_keys(commands):
    service, owner, space, connect = commands
    agent = delegated(service, owner, write=True)
    saved = service.save(agent, space, change())
    item = MarkSeen(items=[SeenItem(resource_id=UUID(saved["id"]), revision=1)])
    with pytest.raises(Unauthenticated):
        resource_views.mark(service, agent, space, item)
    with pytest.raises(Unauthenticated):
        resource_views.listing(service, agent, space)
    key = replace(owner, api_key=ApiKeyScope(str(uuid4()), space, True))
    with pytest.raises(Unauthenticated):
        resource_views.mark(service, key, space, item)
    resource_views.mark(service, owner, space, item)
    with connect() as connection:
        rows = connection.execute(
            "SELECT principal_id,seen_revision FROM space.resource_views WHERE resource_id=%s",
            (saved["id"],),
        ).fetchall()
    assert len(rows) == 1 and rows[0][1] == 1
    assert "canary" not in str(rows)


def test_api_key_writes_are_a_client_and_needs_you_is_a_default_status(commands):
    service, owner, space, _ = commands
    key = replace(owner, api_key=ApiKeyScope(str(uuid4()), space, True))
    saved = service.save(
        key, space, change().model_copy(update={"kind": "task", "status": "needs_you"})
    )
    assert saved["author"] == "agent"
    assert saved["status"] == "needs_you"
    assert (
        service.save(owner, space, change().model_copy(update={"title": "Mine"}))["author"] == "you"
    )
    # Accepted without being stored: the board shows the column only while a task carries it.
    columns = tasks.columns(service, owner, space)["columns"]
    assert [column["value"] for column in columns] == ["todo", "in_progress", "done"]
    assert (
        service.save(
            owner, space, change().model_copy(update={"kind": "task", "status": "needs_you"})
        )["status"]
        == "needs_you"
    )


def test_only_tasks_can_wait_on_the_person():
    def flagged(kind, **values):
        return SaveResource(
            command_id=uuid4(), kind=kind, title="Report", status="needs_you", **values
        )

    for values in ({"expected_revision": 0}, {"resource_id": uuid4(), "expected_revision": 3}):
        with pytest.raises(ValueError, match="Only tasks can wait on the person"):
            flagged("document", **values)
    assert flagged("task", expected_revision=0).status == "needs_you"
