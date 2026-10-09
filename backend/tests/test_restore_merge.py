from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from test_database_setup import database as database_fixture
from test_memora_workspace import claim_change
from test_memora_workspace import setup as memory_setup
from test_restore_staging import stage
from test_workspace import change
from test_workspace import command_database as command_database_fixture
from test_workspace import commands as commands_fixture
from test_workspace_archive import read_archive
from test_workspace_items import database_data, project

from space import restore_merge as merge
from space import workspace_items
from space.workspace import CommandError

database = database_fixture
command_database = command_database_fixture
commands = commands_fixture


def fixture_archive(service, owner, space):
    project_item = workspace_items.save(service, owner, space, project())
    document = service.save(owner, space, change(project=project_item["id"]))
    service.save(
        owner,
        space,
        change().model_copy(
            update={
                "resource_id": UUID(document["id"]),
                "expected_revision": 1,
                "title": "Second revision",
            }
        ),
    )
    workspace_items.save(
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
    return read_archive(service, owner, space), document


def test_restore_into_another_space_publishes_every_revision_and_is_idempotent(commands):
    service, owner, space, _ = commands
    archive, document = fixture_archive(service, owner, space)
    target = replace(owner, subject=str(uuid4()))
    target_space = UUID(service.bootstrap(target)["id"])
    upload = stage(service, target, target_space, archive)
    review = merge.review(service, target, target_space, upload.id, merge.Review())
    assert not review["issues"] and all(not item["conflict"] for item in review["items"])
    request = merge.Commit(snapshot=review["snapshot"])
    receipt = merge.commit(service, target, target_space, upload.id, request)
    assert receipt["state"] == "committed" and receipt["result"]["revisions"] == 4
    assert merge.commit(service, target, target_space, upload.id, request) == receipt
    assert (
        service.get_resource(target, target_space, UUID(document["id"]), 1)["title"]
        == document["title"]
    )
    assert (
        service.get_resource(target, target_space, UUID(document["id"]))["title"]
        == "Second revision"
    )
    assert len(workspace_items.listing(service, target, target_space, "database")["items"]) == 1


def test_merge_requires_current_review_and_replacement_preserves_old_history(commands):
    service, owner, space, _ = commands
    archive, document = fixture_archive(service, owner, space)
    upload = stage(service, owner, space, archive)
    choices = {"resource:" + document["id"]: "replace"}
    review = merge.review(service, owner, space, upload.id, merge.Review(choices=choices))
    service.save(owner, space, change())
    with pytest.raises(CommandError, match="changed since review"):
        merge.commit(
            service,
            owner,
            space,
            upload.id,
            merge.Commit(snapshot=review["snapshot"], choices=choices),
        )
    review = merge.review(service, owner, space, upload.id, merge.Review(choices=choices))
    receipt = merge.commit(
        service, owner, space, upload.id, merge.Commit(snapshot=review["snapshot"], choices=choices)
    )
    assert receipt["result"]["resources"] == 1 and receipt["result"]["items"] == 0
    assert service.get_resource(owner, space, UUID(document["id"]))["revision"] == 4
    assert service.get_resource(owner, space, UUID(document["id"]), 2)["title"] == "Second revision"


def test_merge_preserves_forget_choices_and_edited_memory_with_recall_vectors(commands):
    service, owner, space, _, build = memory_setup(commands)
    saved = service.build_memory(owner, space, build)
    archive = read_archive(service, owner, space)
    claims = saved["memory"]["claims"]
    edited = service.change_memory(
        owner,
        space,
        claim_change(saved, claims[0]["id"], "edit", value="Mira now travels by bicycle."),
    )
    service.change_memory(owner, space, claim_change(edited, claims[1]["id"], "forget"))
    upload = stage(service, owner, space, archive)
    choices = {"resource:" + saved["id"]: "replace"}
    reviewed = merge.review(service, owner, space, upload.id, merge.Review(choices=choices))
    merge.commit(
        service,
        owner,
        space,
        upload.id,
        merge.Commit(snapshot=reviewed["snapshot"], choices=choices),
    )
    result = service.get_resource(owner, space, UUID(saved["id"]), _internal=True)
    assert [c["value"] for c in result["memory"]["claims"]] == ["Mira now travels by bicycle."]
    assert claims[1]["id"] in result["_memory"]["forgotten"]
    assert result["revision"] == 4
    with service.db.transaction(owner) as connection:
        service._scope(connection, owner, space)
        rows = connection.execute(
            "SELECT claim_id,revision FROM space.memory_claims WHERE memory_id=%s", (saved["id"],)
        ).fetchall()
    assert [(str(row["claim_id"]), row["revision"]) for row in rows] == [(claims[0]["id"], 4)]


def test_copy_remaps_project_links_and_all_source_revisions(commands):
    service, owner, space, ref, build = memory_setup(commands)
    memory = service.build_memory(owner, space, build)
    archive = read_archive(service, owner, space)
    upload = stage(service, owner, space, archive)
    choices = {"resource:" + value["id"]: "copy" for value in (ref, memory)}
    reviewed = merge.review(service, owner, space, upload.id, merge.Review(choices=choices))
    mappings = {item["id"]: item["target_id"] for item in reviewed["items"]}
    merge.commit(
        service,
        owner,
        space,
        upload.id,
        merge.Commit(snapshot=reviewed["snapshot"], choices=choices),
    )
    copied = service.get_resource(owner, space, UUID(mappings[memory["id"]]))
    assert copied["recall"]["sources"] == [{"resource_id": mappings[ref["id"]], "revision": 1}]
    assert service.get_resource(owner, space, UUID(mappings[ref["id"]]))["body"] == ref["body"]
