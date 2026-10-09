import hmac
from copy import deepcopy
from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_archive_limits import archive
from test_auth import signing as signing_fixture
from test_database_setup import database as database_fixture
from test_restore_staging import stage
from test_workspace import change
from test_workspace import command_database as command_database_fixture
from test_workspace import commands as commands_fixture
from test_workspace_archive import read_archive
from test_workspace_items import project

from space import archive_schema, restore_merge, workspace_items, workspace_search
from space.api.mcp import create_mcp
from space.app import create_app
from space.memory import RecallMetadata
from space.settings import Settings
from space.workspace import CommandError, SaveResource, Workspace, encode

database = database_fixture
command_database = command_database_fixture
commands = commands_fixture
signing = signing_fixture


def item(kind="folder", **data):
    return workspace_items.SaveItem(
        command_id=uuid4(),
        item_id=uuid4(),
        expected_revision=0,
        kind=kind,
        data={"name": "PRIVATE FOLDER 731", **data},
    )


def folder_archive():
    section_id, folder_id, document_id = (str(uuid4()) for _ in range(3))
    common = {"revision": 1, "updated_at": "2026-09-16T00:00:00Z"}
    return archive(
        entries=[
            {
                "entity": "item",
                "current": True,
                "value": {
                    **common,
                    "id": section_id,
                    "kind": "section",
                    "name": "Notes",
                    "description": "",
                    "deleted": True,
                },
            },
            {
                "entity": "item",
                "current": True,
                "value": {
                    **common,
                    "id": folder_id,
                    "kind": "folder",
                    "name": "Recipes",
                    "description": "",
                    "section_id": section_id,
                    "deleted": True,
                },
            },
            {
                "entity": "resource",
                "current": True,
                "value": {
                    **common,
                    "id": document_id,
                    "kind": "document",
                    "title": "Soup",
                    "body": "Recipe",
                    "status": "todo",
                    "folder": folder_id,
                    "recall": RecallMetadata().model_dump(mode="json"),
                },
            },
        ]
    )


def test_folder_model_defaults_and_document_only_assignment():
    assert item(name="  Notes  ").data == {
        "name": "Notes",
        "description": "",
        "section_id": None,
    }
    with pytest.raises(ValidationError):
        item(name="   ")
    with pytest.raises(ValidationError):
        item(project=str(uuid4()))
    folder_id = uuid4()
    assert change(folder=folder_id).folder == folder_id
    with pytest.raises(ValidationError, match="Only documents"):
        SaveResource.model_validate(change(folder=folder_id).model_dump() | {"kind": "task"})


def test_folder_kinds_and_membership_are_exposed_by_rest_and_mcp(signing):
    settings = Settings(environment="test")
    service = Workspace(None, None, None, "http://127.0.0.1:8000/mcp/")
    server = create_mcp(settings, service)
    tools = {tool.name: tool for tool in server._tool_manager.list_tools()}
    assert "folder" in tools["list_workspace_items"].parameters["properties"]["kind"]["enum"]
    assert (
        "folder"
        in tools["save_workspace_item"].parameters["$defs"]["SaveItem"]["properties"]["kind"][
            "enum"
        ]
    )
    assert (
        tools["save_resource"].parameters["$defs"]["SaveResource"]["properties"]["folder"][
            "default"
        ]
        is None
    )
    schema = create_app(settings, workspace=service, verifier=signing[0]).openapi()
    kinds = next(
        parameter["schema"]["enum"]
        for parameter in schema["paths"]["/v1/spaces/{space_id}/items"]["get"]["parameters"]
        if parameter["name"] == "kind"
    )
    assert "folder" in kinds
    assert "folder" in schema["components"]["schemas"]["SaveResource"]["properties"]


def test_archives_allow_archived_folder_and_section_references_and_legacy_documents():
    value = folder_archive()
    assert len(archive_schema.validate_archive(value)) == 3
    legacy = deepcopy(value)
    legacy["entries"] = legacy["entries"][-1:]
    legacy["entries"][0]["value"].pop("folder")
    assert len(archive_schema.validate_archive(legacy)) == 1


@pytest.mark.parametrize("invalid", ["missing-folder", "wrong-folder", "wrong-section", "task"])
def test_archive_folder_references_are_validated(invalid):
    value = folder_archive()
    section, folder, document = [entry["value"] for entry in value["entries"]]
    if invalid == "missing-folder":
        document["folder"] = str(uuid4())
    elif invalid == "wrong-folder":
        document["folder"] = section["id"]
    elif invalid == "wrong-section":
        folder["section_id"] = folder["id"]
    else:
        document["kind"] = "task"
    with pytest.raises(ValueError):
        archive_schema.validate_archive(value)


def test_folders_file_documents_without_projects_and_store_membership(commands):
    service, owner, space, _ = commands
    section = workspace_items.save(service, owner, space, item("section", name="Personal"))
    request = item(section_id=section["id"])
    folder = workspace_items.save(service, owner, space, request)
    assert workspace_items.save(service, owner, space, request) == folder
    assert workspace_items.listing(service, owner, space, "project")["items"] == []
    document = service.save(owner, space, change(folder=folder["id"]))
    assert document["project"] is None and document["folder"] == folder["id"]
    assert service.get_resource(owner, space, UUID(document["id"])) == document
    assert service.list_resources(owner, space, "document")["items"][0]["folder"] == folder["id"]
    assert workspace_items.listing(service, owner, space, "folder")["items"] == [folder]
    search = workspace_search.search(service, owner, space, workspace_search.Search())
    assert {result["id"] for result in search["items"]} == {
        section["id"],
        folder["id"],
        document["id"],
    }
    with service.db.transaction(owner) as connection:
        service._scope(connection, owner, space)
        metadata = connection.execute(
            "SELECT encrypted_metadata FROM space.resource_revisions"
        ).fetchone()["encrypted_metadata"]
        payloads = connection.execute(
            "SELECT encrypted_payload FROM space.workspace_item_revisions"
        ).fetchall()
    # Membership and folder names are stored as readable JSON.
    assert folder["id"].encode() in bytes(metadata)
    assert any(b"PRIVATE FOLDER" in bytes(payload["encrypted_payload"]) for payload in payloads)


def test_optional_folder_preserves_pre_folder_command_receipts(commands):
    service, owner, space, _ = commands
    request = change()
    saved = service.save(owner, space, request)
    _, key = service._key(owner, space)
    legacy_fingerprint = hmac.digest(
        key.key.material,
        b"space.command.v1\0"
        + encode(
            request.model_dump(
                mode="json",
                exclude={"recall", "project", "folder", "priority"},
            )
        ),
        "sha256",
    )
    with service.db.transaction(owner) as connection:
        service._scope(connection, owner, space)
        receipt = connection.execute(
            "SELECT fingerprint FROM space.commands WHERE id=%s",
            (request.command_id,),
        ).fetchone()
    assert bytes(receipt["fingerprint"]) == legacy_fingerprint
    assert service.save(owner, space, request) == saved


def test_document_moves_preserve_project_and_explicit_clear_only_unfiles(commands):
    service, owner, space, _ = commands
    project_item = workspace_items.save(service, owner, space, project())
    first = workspace_items.save(service, owner, space, item(name="First"))
    second = workspace_items.save(service, owner, space, item(name="Second"))
    saved = service.save(
        owner, space, change(project=project_item["id"], folder=first["id"], priority="High")
    )
    edited = service.save(
        owner,
        space,
        SaveResource(
            command_id=uuid4(),
            resource_id=saved["id"],
            expected_revision=1,
            kind="document",
            title="Edited",
            body="New body",
            status="done",
        ),
    )
    assert edited["folder"] == first["id"]
    moved = service.save(
        owner,
        space,
        SaveResource(
            command_id=uuid4(),
            resource_id=saved["id"],
            expected_revision=2,
            kind="document",
            title=edited["title"],
            body=edited["body"],
            status=edited["status"],
            folder=second["id"],
        ),
    )
    assert moved["folder"] == second["id"]
    cleared = service.save(
        owner,
        space,
        SaveResource(
            command_id=uuid4(),
            resource_id=saved["id"],
            expected_revision=3,
            kind="document",
            title=moved["title"],
            body=moved["body"],
            status=moved["status"],
            folder=None,
        ),
    )
    assert cleared["folder"] is None
    for document in (edited, moved, cleared):
        assert document["project"] == project_item["id"] and document["priority"] == "High"
        assert (document["title"], document["body"], document["status"]) == (
            "Edited",
            "New body",
            "done",
        )
    assert service.get_resource(owner, space, UUID(saved["id"]), 1)["folder"] == first["id"]


@pytest.mark.parametrize("invalid", ["missing", "wrong-kind", "deleted", "cross-space"])
def test_new_folder_and_section_assignments_reject_inactive_or_foreign_items(commands, invalid):
    service, owner, space, _ = commands
    for target_kind, assigning_folder in (("folder", False), ("section", True)):
        actor, target_space = owner, space
        if invalid == "cross-space":
            actor = replace(owner, subject=str(uuid4()))
            target_space = UUID(service.bootstrap(actor)["id"])
        target_id = uuid4()
        if invalid != "missing":
            target = workspace_items.save(
                service,
                actor,
                target_space,
                item("category" if invalid == "wrong-kind" else target_kind),
            )
            target_id = UUID(target["id"])
            if invalid == "deleted":
                workspace_items.visibility(
                    service,
                    owner,
                    space,
                    target_id,
                    workspace_items.ChangeVisibility(
                        command_id=uuid4(),
                        expected_revision=1,
                        deleted=True,
                    ),
                )
        with pytest.raises(CommandError, match=f"active {target_kind} in this Space"):
            if assigning_folder:
                workspace_items.save(service, owner, space, item(section_id=target_id))
            else:
                service.save(owner, space, change(folder=target_id))


def test_archiving_sections_and_folders_never_changes_or_deletes_documents(commands):
    service, owner, space, _ = commands
    section = workspace_items.save(service, owner, space, item("section"))
    folder = workspace_items.save(service, owner, space, item(section_id=section["id"]))
    saved = service.save(owner, space, change(folder=folder["id"]))
    snapshot = workspace_search.revision(service, owner, space)
    for value in (section, folder):
        workspace_items.visibility(
            service,
            owner,
            space,
            UUID(value["id"]),
            workspace_items.ChangeVisibility(command_id=uuid4(), expected_revision=1, deleted=True),
        )
        assert service.get_resource(owner, space, UUID(saved["id"])) == saved
        assert service.list_resources(owner, space, "document")["items"][0]["id"] == saved["id"]
    assert workspace_search.revision(service, owner, space) > snapshot
    with pytest.raises(CommandError, match="Space changed"):
        workspace_search.search(service, owner, space, workspace_search.Search(snapshot=snapshot))
    assert workspace_items.listing(service, owner, space, "folder")["items"] == []
    edited = service.save(
        owner,
        space,
        SaveResource(
            command_id=uuid4(),
            resource_id=saved["id"],
            expected_revision=1,
            kind="document",
            title="Still editable",
            body=saved["body"],
            folder=folder["id"],
        ),
    )
    assert edited["folder"] == folder["id"] and edited["revision"] == 2
    restored = workspace_items.visibility(
        service,
        owner,
        space,
        UUID(folder["id"]),
        workspace_items.ChangeVisibility(command_id=uuid4(), expected_revision=2, deleted=False),
    )
    renamed = workspace_items.save(
        service,
        owner,
        space,
        workspace_items.SaveItem(
            command_id=uuid4(),
            item_id=folder["id"],
            expected_revision=3,
            kind="folder",
            data={"name": "Renamed after section archive", "section_id": restored["section_id"]},
        ),
    )
    assert renamed["section_id"] == section["id"]


def test_export_restore_remaps_folder_section_and_project_independently(commands):
    service, owner, space, _ = commands
    section = workspace_items.save(service, owner, space, item("section", name="Documents"))
    folder = workspace_items.save(service, owner, space, item(section_id=section["id"]))
    project_item = workspace_items.save(service, owner, space, project())
    document = service.save(owner, space, change(folder=folder["id"], project=project_item["id"]))
    for value in (section, folder):
        workspace_items.visibility(
            service,
            owner,
            space,
            UUID(value["id"]),
            workspace_items.ChangeVisibility(command_id=uuid4(), expected_revision=1, deleted=True),
        )
    exported = read_archive(service, owner, space)
    assert len(archive_schema.validate_archive(exported)) == 4
    upload = stage(service, owner, space, exported)
    choices = {"item:" + value["id"]: "copy" for value in (section, folder, project_item)}
    choices["resource:" + document["id"]] = "copy"
    reviewed = restore_merge.review(
        service,
        owner,
        space,
        upload.id,
        restore_merge.Review(
            choices=choices,
        ),
    )
    assert reviewed["issues"] == []
    mappings = {value["id"]: value["target_id"] for value in reviewed["items"]}
    request = restore_merge.Commit(snapshot=reviewed["snapshot"], choices=choices)
    receipt = restore_merge.commit(service, owner, space, upload.id, request)
    assert restore_merge.commit(service, owner, space, upload.id, request) == receipt
    copied = service.get_resource(owner, space, UUID(mappings[document["id"]]))
    assert copied["folder"] == mappings[folder["id"]] != folder["id"]
    assert copied["project"] == mappings[project_item["id"]] != project_item["id"]
    copied_folder = workspace_items.get(service, owner, space, UUID(copied["folder"]))
    assert copied_folder["section_id"] == mappings[section["id"]] != section["id"]
    assert copied_folder["deleted"]
    assert service.get_resource(owner, space, UUID(document["id"])) == document
    assert len(service.list_resources(owner, space, "document")["items"]) == 2
    assert archive_schema.validate_archive(read_archive(service, owner, space))


def test_restore_can_keep_an_archived_folder_but_not_a_wrong_kind_collision(commands):
    service, owner, space, _ = commands
    value = folder_archive()
    folder_id = value["entries"][1]["value"]["id"]
    workspace_items.save(
        service,
        owner,
        space,
        item("category").model_copy(
            update={"item_id": UUID(folder_id)},
        ),
    )
    upload = stage(service, owner, space, value)
    review = restore_merge.review(service, owner, space, upload.id, restore_merge.Review())
    assert any("folder reference" in issue for issue in review["issues"])
    choices = {"item:" + folder_id: "copy"}
    review = restore_merge.review(
        service,
        owner,
        space,
        upload.id,
        restore_merge.Review(
            choices=choices,
        ),
    )
    assert not review["issues"]
    restore_merge.commit(
        service,
        owner,
        space,
        upload.id,
        restore_merge.Commit(
            snapshot=review["snapshot"],
            choices=choices,
        ),
    )
    exported = read_archive(service, owner, space)
    upload = stage(service, owner, space, exported)
    document = next(e["value"] for e in exported["entries"] if e["entity"] == "resource")
    choices = {"resource:" + document["id"]: "copy"}
    review = restore_merge.review(
        service,
        owner,
        space,
        upload.id,
        restore_merge.Review(
            choices=choices,
        ),
    )
    assert not review["issues"]
    restore_merge.commit(
        service,
        owner,
        space,
        upload.id,
        restore_merge.Commit(
            snapshot=review["snapshot"],
            choices=choices,
        ),
    )
    assert len(service.list_resources(owner, space, "document")["items"]) == 2


def test_rest_folder_create_list_and_document_assignment_without_project(commands, signing):
    service = commands[0]
    verifier, token = signing
    bearer = token()
    owner = verifier.verify(bearer)
    space = UUID(service.bootstrap(owner)["id"])
    settings = Settings(
        environment="test",
        allowed_hosts=["testserver"],
    )
    auth = {"Authorization": "Bearer " + bearer}
    endpoint = f"/v1/spaces/{space}"
    with TestClient(create_app(settings, workspace=service, verifier=verifier)) as http:
        response = http.post(endpoint + "/items", headers=auth, json=item().model_dump(mode="json"))
        assert response.status_code == 200, response.text
        folder = response.json()
        assert http.get(endpoint + "/items?kind=folder", headers=auth).json()["items"] == [folder]
        document = change(folder=folder["id"]).model_dump(mode="json")
        response = http.post(endpoint + "/resources", headers=auth, json=document)
        assert response.status_code == 200, response.text
        assert response.json()["folder"] == folder["id"] and response.json()["project"] is None
        response = http.post(
            endpoint + "/resources",
            headers=auth,
            json={
                **document,
                "command_id": str(uuid4()),
                "kind": "task",
            },
        )
        assert response.status_code == 422
