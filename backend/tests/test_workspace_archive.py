from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from test_database_setup import database as database_fixture
from test_workspace import change, delegated
from test_workspace import command_database as command_database_fixture
from test_workspace import commands as commands_fixture
from test_workspace_items import project

from space import agents, workspace_items
from space.security.auth import Unauthenticated
from space.workspace import CommandError
from space.workspace_archive import ExportPage, export_page

database = database_fixture
command_database = command_database_fixture
commands = commands_fixture


def read_archive(service, owner, space):
    first = page = export_page(service, owner, space, ExportPage())
    entries = list(page["entries"])
    while page["next_cursor"]:
        page = export_page(
            service,
            owner,
            space,
            ExportPage(
                after=page["next_cursor"],
                snapshot=page["snapshot"],
            ),
        )
        entries.extend(page["entries"])
    return {key: value for key, value in first.items() if key != "next_cursor"} | {
        "entries": entries
    }


def test_archive_covers_all_revisions_and_trash_without_credentials(commands):
    service, owner, space, _ = commands
    profile = agents.save_profile(
        service, owner, agents.AgentProfile(name="Partner", harness="codex")
    )
    saved = service.save(owner, space, change())
    service.save(
        owner,
        space,
        change().model_copy(
            update={"resource_id": UUID(saved["id"]), "expected_revision": 1, "title": "Edited"}
        ),
    )
    item = workspace_items.save(service, owner, space, project())
    workspace_items.visibility(
        service,
        owner,
        space,
        UUID(item["id"]),
        workspace_items.ChangeVisibility(command_id=uuid4(), expected_revision=1, deleted=True),
    )
    archive = read_archive(service, owner, space)
    assert len(archive["entries"]) == 5
    assert sum(entry["current"] for entry in archive["entries"]) == 3
    assert (
        next(e for e in archive["entries"] if e["entity"] == "agent")["value"]["id"]
        == profile["id"]
    )
    assert any(e["value"].get("deleted") for e in archive["entries"])
    assert {e["entity"] for e in archive["entries"]} == {"agent", "item", "resource"}
    reader = delegated(service, owner)
    with pytest.raises(Unauthenticated):
        export_page(service, reader, space, ExportPage())
    other = replace(owner, subject=str(uuid4()))
    other_space = UUID(service.bootstrap(other)["id"])
    assert read_archive(service, other, other_space)["entries"] == []


def test_archive_rejects_snapshot_changes_including_agent_rename(commands):
    service, owner, space, _ = commands
    agent = agents.save_profile(service, owner, agents.AgentProfile(name="Before", harness="codex"))
    first = export_page(service, owner, space, ExportPage())
    agents.save_profile(
        service,
        owner,
        agents.EditAgent(name="After", harness="codex", expected_revision=1),
        UUID(agent["id"]),
    )
    with pytest.raises(CommandError, match="Space changed"):
        export_page(service, owner, space, ExportPage(snapshot=first["snapshot"]))
