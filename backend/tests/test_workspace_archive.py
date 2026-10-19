from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from test_database_setup import database as database_fixture
from test_workspace import change, delegated
from test_workspace import command_database as command_database_fixture
from test_workspace import commands as commands_fixture
from test_workspace_items import project

from space import agents, workspace_archive, workspace_items
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


def test_archive_pages_stop_after_their_time_budget(commands, monkeypatch):
    service, owner, space, _ = commands
    for title in ("One", "Two", "Three"):
        service.save(owner, space, change().model_copy(update={"title": title}))
    monkeypatch.setattr(workspace_archive, "PAGE_SECONDS", 0)
    first = export_page(service, owner, space, ExportPage())
    assert len(first["entries"]) == 1  # Always at least one entry, then out of time.
    assert first["next_cursor"]
    assert len(read_archive(service, owner, space)["entries"]) == 3


def test_archive_keeps_its_snapshot_while_the_space_changes(commands, monkeypatch):
    service, owner, space, _ = commands
    monkeypatch.setattr(workspace_archive, "PAGE_ROWS", 1)
    agent = agents.save_profile(service, owner, agents.AgentProfile(name="Before", harness="codex"))
    saved = service.save(owner, space, change())
    item = workspace_items.save(service, owner, space, project())
    first = export_page(service, owner, space, ExportPage())
    assert first["next_cursor"]
    service.save(
        owner,
        space,
        change().model_copy(
            update={"resource_id": UUID(saved["id"]), "expected_revision": 1, "title": "Edited"}
        ),
    )
    workspace_items.visibility(
        service,
        owner,
        space,
        UUID(item["id"]),
        workspace_items.ChangeVisibility(command_id=uuid4(), expected_revision=1, deleted=True),
    )
    service.save(owner, space, change().model_copy(update={"title": "Later"}))
    agents.save_profile(
        service,
        owner,
        agents.EditAgent(name="After", harness="codex", expected_revision=1),
        UUID(agent["id"]),
    )
    entries = list(first["entries"])
    page = first
    while page["next_cursor"]:
        page = export_page(
            service,
            owner,
            space,
            ExportPage(after=page["next_cursor"], snapshot=first["snapshot"]),
        )
        assert page["snapshot"] == first["snapshot"]
        entries.extend(page["entries"])
    assert [(e["entity"], e["current"], e["value"]["revision"]) for e in entries] == [
        ("agent", True, 1),
        ("item", True, 1),
        ("resource", True, 1),
    ]
    assert entries[0]["value"]["name"] == "Before"
    assert entries[2]["value"]["title"] != "Edited"
    assert len(read_archive(service, owner, space)["entries"]) == 6
    with pytest.raises(CommandError, match="Unknown snapshot"):
        export_page(service, owner, space, ExportPage(snapshot=first["snapshot"] + 99))


def test_archive_keeps_snapshot_memory_vectors_after_an_edit_and_a_forget(commands, monkeypatch):
    from test_memora_workspace import claim_change, setup

    from space import archive_schema

    service, owner, space, _, request = setup(commands)
    writer = delegated(service, owner, write=True)
    saved = service.build_memory(writer, space, request)
    car, rain = saved["memory"]["claims"]
    before = read_archive(service, owner, space)
    [memory] = [e for e in before["entries"] if e["current"] and e["value"].get("_memory")]
    monkeypatch.setattr(workspace_archive, "PAGE_ROWS", 1)
    first = export_page(service, owner, space, ExportPage())
    edited = service.change_memory(
        writer, space, claim_change(saved, car["id"], "edit", value="Mira cycles to work.")
    )
    service.change_memory(writer, space, claim_change(edited, rain["id"], "forget"))
    entries = list(first["entries"])
    page = first
    while page["next_cursor"]:
        page = export_page(
            service,
            owner,
            space,
            ExportPage(after=page["next_cursor"], snapshot=first["snapshot"]),
        )
        entries.extend(page["entries"])
    [exported] = [e for e in entries if e["current"] and e["value"].get("_memory")]
    assert exported["value"]["revision"] == saved["revision"]
    assert [c["value"] for c in exported["value"]["_memory"]["claims"]] == [
        car["value"],
        rain["value"],
    ]
    assert sorted(exported["value"]["memory_index"], key=lambda c: c["id"]) == sorted(
        memory["value"]["memory_index"], key=lambda c: c["id"]
    )
    archive = {key: value for key, value in first.items() if key != "next_cursor"}
    assert archive_schema.validate_archive(archive | {"entries": entries})
