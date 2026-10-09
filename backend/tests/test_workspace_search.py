from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from test_database_setup import database as database_fixture
from test_workspace import change, delegated
from test_workspace import command_database as command_database_fixture
from test_workspace import commands as commands_fixture
from test_workspace_items import database_data, project

from space import workspace_items
from space.workspace import CommandError
from space.workspace_search import Search, search

database = database_fixture
command_database = command_database_fixture
commands = commands_fixture


def test_search_reads_beyond_first_page_and_truncated_term_index(commands):
    service, owner, space, _ = commands
    ids = []
    for n in range(28):
        ids.append(
            service.save(
                owner,
                space,
                change().model_copy(update={"title": f"Source {n}", "body": "ordinary"}),
            )["id"]
        )
    last = max(ids)
    service.save(
        owner,
        space,
        change().model_copy(
            update={
                "resource_id": UUID(last),
                "expected_revision": 1,
                "body": " ".join(f"word{n}" for n in range(800)) + " finalcanary731",
            }
        ),
    )
    first = search(service, owner, space, Search(query="finalcanary731"))
    assert first["items"] == [] and first["next_cursor"]
    second = search(
        service,
        owner,
        space,
        Search(
            query="finalcanary731",
            after=first["next_cursor"],
            snapshot=first["snapshot"],
        ),
    )
    assert [item["id"] for item in second["items"]] == [last]
    assert second["next_cursor"] is None and second["scanned"] == 3


def test_search_database_records_project_links_trash_and_scope(commands):
    service, owner, space, _ = commands
    saved = workspace_items.save(
        service,
        owner,
        space,
        workspace_items.SaveItem(
            command_id=uuid4(),
            item_id=uuid4(),
            expected_revision=0,
            kind="database",
            data=database_data(),
        ),
    )
    found = search(service, owner, space, Search(query="PRIVATE CLIENT 915"))
    assert found["items"][0]["id"] == saved["id"]
    project_item = workspace_items.save(service, owner, space, project())
    linked = service.save(owner, space, change(project=project_item["id"]))
    assert [
        item["id"]
        for item in search(service, owner, space, Search(project=project_item["id"]))["items"]
    ] == [linked["id"]]
    workspace_items.visibility(
        service,
        owner,
        space,
        UUID(saved["id"]),
        workspace_items.ChangeVisibility(
            command_id=uuid4(),
            expected_revision=1,
            deleted=True,
        ),
    )
    assert search(service, owner, space, Search(query="PRIVATE CLIENT 915"))["items"] == []
    other = replace(owner, subject=str(uuid4()))
    other_space = UUID(service.bootstrap(other)["id"])
    assert search(service, other, other_space, Search(query="PRIVATE"))["items"] == []
    with pytest.raises(CommandError):
        search(service, other, space, Search())


def test_search_detects_changes_during_reads_and_rechecks_revocation(commands, monkeypatch):
    service, owner, space, _ = commands
    service.save(owner, space, change())
    first = search(service, owner, space, Search())
    service.save(owner, space, change())
    with pytest.raises(CommandError, match="Space changed"):
        search(service, owner, space, Search(snapshot=first["snapshot"]))
    actor = delegated(service, owner)
    original = service.get_resource

    def revoke_before_return(*args, **kwargs):
        result = original(*args, **kwargs)
        service.revoke(owner, UUID(actor.client_id))
        return result

    monkeypatch.setattr(service, "get_resource", revoke_before_return)
    with pytest.raises(CommandError):
        search(service, actor, space, Search())
