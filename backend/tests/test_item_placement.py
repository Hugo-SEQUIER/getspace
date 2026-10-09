from uuid import UUID, uuid4

import pytest
from test_database_setup import database as database_fixture
from test_document_folders import item
from test_restore_staging import stage
from test_workspace import command_database as command_database_fixture
from test_workspace import commands as commands_fixture
from test_workspace_archive import read_archive

from space import archive_schema, restore_merge, workspace_items
from space.workspace import CommandError

database = database_fixture
command_database = command_database_fixture
commands = commands_fixture


def save(service, owner, space, kind, **data):
    data.setdefault("name", f"PRIVATE {kind.upper()}")
    return workspace_items.save(service, owner, space, item(kind, **data))


def archive_item(service, owner, space, value):
    return workspace_items.visibility(
        service,
        owner,
        space,
        UUID(value["id"]),
        workspace_items.ChangeVisibility(
            command_id=uuid4(), expected_revision=value["revision"], deleted=True
        ),
    )


def resave(service, owner, space, value, **data):
    kept = {
        k: value[k] for k in workspace_items.item_model(value["kind"]).model_fields if k in value
    }
    return workspace_items.save(
        service,
        owner,
        space,
        workspace_items.SaveItem(
            command_id=uuid4(),
            item_id=UUID(value["id"]),
            expected_revision=value["revision"],
            kind=value["kind"],
            data=kept | data,
        ),
    )


def test_unplaced_items_keep_their_stored_shape():
    assert "project_id" not in item("folder").data
    database = item("database").data
    assert "project_id" not in database and "section_id" not in database


@pytest.mark.parametrize("kind", ["folder", "database"])
def test_folders_and_databases_accept_an_active_project_or_section(commands, kind):
    service, owner, space, _ = commands
    section = save(service, owner, space, "section")
    project = save(service, owner, space, "project", section_id=section["id"])
    placed = save(service, owner, space, kind, project_id=project["id"], section_id=section["id"])
    assert placed["project_id"] == project["id"]
    for invalid in (str(uuid4()), section["id"]):
        with pytest.raises(CommandError, match="active project"):
            save(service, owner, space, kind, project_id=invalid)
    with pytest.raises(CommandError, match="active section"):
        save(service, owner, space, kind, section_id=project["id"])
    archive_item(service, owner, space, project)
    renamed = resave(service, owner, space, placed, name="Renamed after archive")
    assert renamed["project_id"] == project["id"]
    other = save(service, owner, space, kind)
    with pytest.raises(CommandError, match="active project"):
        resave(service, owner, space, other, project_id=project["id"])


def test_archive_validation_and_copy_restore_remap_item_projects(commands):
    service, owner, space, _ = commands
    project = save(service, owner, space, "project")
    folder = save(service, owner, space, "folder", project_id=project["id"])
    database = save(service, owner, space, "database", project_id=project["id"])
    exported = read_archive(service, owner, space)
    assert len(archive_schema.validate_archive(exported)) == 3
    broken = read_archive(service, owner, space)
    for entry in broken["entries"]:
        if entry["value"]["id"] == folder["id"]:
            entry["value"]["project_id"] = folder["id"]
    with pytest.raises(ValueError):
        archive_schema.validate_archive(broken)
    upload = stage(service, owner, space, exported)
    choices = {"item:" + value["id"]: "copy" for value in (project, folder, database)}
    reviewed = restore_merge.review(
        service, owner, space, upload.id, restore_merge.Review(choices=choices)
    )
    assert reviewed["issues"] == []
    mappings = {value["id"]: value["target_id"] for value in reviewed["items"]}
    restore_merge.commit(
        service,
        owner,
        space,
        upload.id,
        restore_merge.Commit(snapshot=reviewed["snapshot"], choices=choices),
    )
    for original in (folder, database):
        copied = workspace_items.get(service, owner, space, UUID(mappings[original["id"]]))
        assert copied["project_id"] == mappings[project["id"]] != project["id"]
