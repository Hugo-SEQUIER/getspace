from copy import deepcopy
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from test_database_setup import database as database_fixture
from test_memora_pipeline import Models
from test_memora_workspace import claim_change, setup
from test_memory_classification import response
from test_restore_staging import stage
from test_workspace import command_database as command_database_fixture
from test_workspace import commands as commands_fixture
from test_workspace import delegated
from test_workspace_archive import read_archive

from space import archive_schema, memory_classification, restore_merge, workspace_items
from space.memory import ChangeMemory, RecallRequest, RememberClaims
from space.model_provider import DecisionError, MemoryModels
from space.settings import Settings
from space.workspace import CommandError

database = database_fixture
command_database = command_database_fixture
commands = commands_fixture


class ClassifyingModels(Models):
    settings = SimpleNamespace(memory_classification_provider="decision")

    def __init__(self):
        super().__init__()
        self.before_classify = lambda: None
        self.failure = False

    def session(self, authorize):
        session = super().session(authorize)

        def evaluate(payload):
            self.before_classify()
            authorize()
            self.calls.append(("classify", deepcopy(payload)))
            if self.failure:
                raise DecisionError("Unavailable")
            scores = {
                f"i{n}": 0.99
                for n, item in enumerate(payload["state"]["catalog"])
                if item["kind"] in ("category", "project")
            }
            return response(payload, scores)

        session.decide = evaluate
        return session


def item(service, owner, space, kind, name, **data):
    return workspace_items.save(
        service,
        owner,
        space,
        workspace_items.SaveItem(
            command_id=uuid4(),
            item_id=uuid4(),
            expected_revision=0,
            kind=kind,
            data={"name": name, "description": "PRIVATE CLASSIFICATION 991", **data},
        ),
    )


def organized(commands):
    service, owner, space, ref, build = setup(commands)
    section = item(service, owner, space, "section", "Work")
    category = item(service, owner, space, "category", "Dev")
    project = item(service, owner, space, "project", "Atlas", section_id=section["id"])
    service.memory_models = ClassifyingModels()
    saved = service.build_memory(owner, space, build)
    return service, owner, space, saved, build, section, category, project


def assign(saved, **assignment):
    return ChangeMemory(
        command_id=uuid4(),
        resource_id=saved["id"],
        expected_revision=saved["revision"],
        entry_id=saved["memory"]["claims"][0]["id"],
        action="classify",
        classification=assignment,
    )


def test_create_list_recall_edit_and_manual_pin_preserve_content(commands):
    service, owner, space, saved, build, section, category, project = organized(commands)
    claim = saved["memory"]["claims"][0]
    assert claim["classification"]["project_ids"] == [project["id"]]
    assert claim["classification"]["section_ids"] == [section["id"]]
    assert claim["memory_type"] == "factual"
    assert (
        service.list_memories(owner, space)["items"][0]["claims"][0]["classification"]
        == claim["classification"]
    )
    calls = len(service.memory_models.calls)
    recalled = service.context(owner, space, RecallRequest(query="automobile", strategy="semantic"))
    assert any(i.get("classification") == claim["classification"] for i in recalled["items"])
    assert not any(call[0] == "classify" for call in service.memory_models.calls[calls:])
    service.memory_models.calls.clear()
    manual_request = assign(saved, scope="global", category_ids=[category["id"]])
    manual = service.change_memory(owner, space, manual_request)
    assert not service.memory_models.calls  # No embedding or classifier request for a label edit.
    assert service.change_memory(owner, space, manual_request) == manual
    assert manual["memory"]["claims"][0]["value"] == claim["value"]
    edited = service.change_memory(
        owner, space, claim_change(manual, claim["id"], "edit", value="Mira travels by automobile.")
    )
    assert edited["memory"]["claims"][0]["classification"]["method"] == "manual"
    assert not any(call[0] == "classify" for call in service.memory_models.calls)
    rebuilt = service.build_memory(
        owner,
        space,
        build.model_copy(
            update={
                "command_id": uuid4(),
                "resource_id": UUID(saved["id"]),
                "expected_revision": edited["revision"],
            }
        ),
    )
    assert (
        rebuilt["memory"]["claims"][0]["classification"]
        == manual["memory"]["claims"][0]["classification"]
    )
    assert rebuilt["memory"]["claims"][0]["value"] == "Mira travels by automobile."


def test_automatic_edit_and_explicit_reclassification(commands):
    service, owner, space, saved, _, _, _, _ = organized(commands)
    claim = saved["memory"]["claims"][0]
    service.memory_models.calls.clear()
    edited = service.change_memory(
        owner, space, claim_change(saved, claim["id"], "edit", value="New car policy")
    )
    payloads = [c[1] for c in service.memory_models.calls if c[0] == "classify"]
    assert len(payloads) == 1 and payloads[0]["state"]["memory"]["text"] == "New car policy"
    manual = service.change_memory(owner, space, assign(edited))
    automatic = service.change_memory(
        owner, space, assign(manual).model_copy(update={"classification": None})
    )
    assert automatic["memory"]["claims"][0]["classification"]["method"] == "decision"


def test_failed_explicit_reclassification_keeps_manual_assignment(commands):
    service, owner, space, saved, _, _, _, _ = organized(commands)
    manual = service.change_memory(owner, space, assign(saved, scope="global"))
    service.memory_models.failure = True
    with pytest.raises(CommandError, match="existing assignments kept"):
        service.change_memory(
            owner, space, assign(manual).model_copy(update={"classification": None})
        )
    current = service.get_resource(owner, space, UUID(manual["id"]))
    assert current["revision"] == manual["revision"]
    assert current["memory"]["claims"][0]["classification"]["method"] == "manual"


def test_missing_deleted_wrong_kind_and_read_only_assignments_are_rejected(commands):
    service, owner, space, saved, _, section, category, project = organized(commands)
    for assignment in ({"category_ids": [str(uuid4())]}, {"category_ids": [project["id"]]}):
        with pytest.raises(CommandError):
            service.change_memory(owner, space, assign(saved, **assignment))
    reader = delegated(service, owner)
    with pytest.raises(CommandError):
        service.change_memory(reader, space, assign(saved))
    workspace_items.visibility(
        service,
        owner,
        space,
        category["id"],
        workspace_items.ChangeVisibility(command_id=uuid4(), expected_revision=1, deleted=True),
    )
    with pytest.raises(CommandError):
        service.change_memory(owner, space, assign(saved, category_ids=[category["id"]]))
    with pytest.raises(CommandError, match="active section"):
        item(service, owner, space, "project", "Wrong", section_id=category["id"])
    service.memory_models = MemoryModels(Settings(environment="test"))
    manual = service.change_memory(
        owner, space, assign(saved, scope="project", project_ids=[project["id"]])
    )
    assert manual["memory"]["claims"][0]["classification"]["section_ids"] == [section["id"]]


@pytest.mark.parametrize("mutation", ["catalog", "revoke"])
def test_concurrent_catalog_or_authorization_change_stops_provider_disclosure(commands, mutation):
    service, owner, space, saved, _, _, _, _ = organized(commands)
    writer = delegated(service, owner, write=True)
    service.memory_models.calls.clear()

    def race():
        service.memory_models.before_classify = lambda: None
        if mutation == "catalog":
            item(service, owner, space, "category", "New category")
        else:
            service.revoke(owner, UUID(writer.client_id))

    service.memory_models.before_classify = race
    with pytest.raises(CommandError) as error:
        service.change_memory(
            writer, space, assign(saved).model_copy(update={"classification": None})
        )
    assert error.value.status == (409 if mutation == "catalog" else 403)
    assert service.memory_models.calls == []


def test_provider_outage_keeps_prepared_memory_with_one_failed_attempt(commands):
    service, owner, space, _ = commands
    service.memory_models = ClassifyingModels()
    service.memory_models.failure = True
    request = RememberClaims(
        command_id=uuid4(),
        title="Claims",
        claims=[
            {"index": "First", "value": "First exact value"},
            {"index": "Second", "value": "Second exact value"},
        ],
    )
    saved = service.remember_claims(owner, space, request)
    assert [c["value"] for c in saved["memory"]["claims"]] == [
        "First exact value",
        "Second exact value",
    ]
    assert all(
        c["classification"] == memory_classification.empty("unavailable")
        for c in saved["memory"]["claims"]
    )
    assert len([c for c in service.memory_models.calls if c[0] == "classify"]) == 1


@pytest.mark.parametrize("count", [0, 1, 33, 34])
def test_catalog_reads_keys_in_same_transaction_and_never_returns_partial_catalog(
    commands, monkeypatch, count
):
    from test_memory_write_efficiency import instrument

    service, owner, space, _ = commands
    # A catalog past the whole-catalog size is still read whole, up to the read bound.
    monkeypatch.setattr(memory_classification, "MAX_CATALOG_READ", 33)
    for n in range(count):
        item(service, owner, space, "category", f"Category {n}")
    counts = instrument(service, monkeypatch)
    result = memory_classification.catalog(service, owner, space)
    if count > 33:
        assert result is None
    else:
        assert len(result) == count
    assert counts["transactions"] == (2 if 0 < count <= 33 else 1)
    assert counts["unwraps"] == (1 if 0 < count <= 33 else 0)


def test_catalog_revocation_during_unwrap_denies_provider_disclosure(commands, monkeypatch):
    service, owner, space, _ = commands
    item(service, owner, space, "category", "Development")
    writer = delegated(service, owner, write=True)
    service.memory_models = ClassifyingModels()
    original = service.keys.unwrap

    def revoked(*args):
        key = original(*args)
        service.revoke(owner, UUID(writer.client_id))
        return key

    monkeypatch.setattr(service.keys, "unwrap", revoked)
    with pytest.raises(CommandError) as error:
        memory_classification.apply(
            service,
            writer,
            space,
            [{"index": "title", "value": "private"}],
            service.memory_models.session(lambda: None),
        )
    assert error.value.status == 403
    assert service.memory_models.calls == []


def test_catalog_and_assignments_export_restore_as_copy_with_remapped_ids(commands):
    service, owner, space, saved, _, section, category, project = organized(commands)
    archive = read_archive(service, owner, space)
    archive_schema.validate_archive(archive)
    broken = deepcopy(archive)
    next(e for e in broken["entries"] if e["value"].get("_memory"))["value"]["_memory"]["claims"][
        0
    ]["classification"]["category_ids"] = [str(uuid4())]
    with pytest.raises(ValueError, match="classification item"):
        archive_schema.validate_archive(broken)
    upload = stage(service, owner, space, archive)
    choices = {
        f"{e['entity']}:{e['value']['id']}": "copy"
        for e in archive["entries"]
        if e["entity"] != "agent"
    }
    review = restore_merge.review(
        service, owner, space, upload.id, restore_merge.Review(choices=choices)
    )
    restore_merge.commit(
        service,
        owner,
        space,
        upload.id,
        restore_merge.Commit(snapshot=review["snapshot"], choices=choices),
    )
    mapping = {i["id"]: i["target_id"] for i in review["items"]}
    restored = service.get_resource(owner, space, UUID(mapping[saved["id"]]))
    result = restored["memory"]["claims"][0]["classification"]
    assert result["category_ids"] == [mapping[category["id"]]]
    assert result["section_ids"] == [mapping[section["id"]]]
    assert result["project_ids"] == [mapping[project["id"]]]
    assert (
        workspace_items.get(service, owner, space, mapping[project["id"]])["section_id"]
        == mapping[section["id"]]
    )
    with service.db.transaction(owner) as connection:
        service._scope(connection, owner, space)
        payloads = connection.execute(
            "SELECT encrypted_payload FROM space.workspace_item_revisions"
        ).fetchall()
        assert any(b"PRIVATE CLASSIFICATION" in bytes(row["encrypted_payload"]) for row in payloads)


def large_catalog(service, owner, space, topics=33):
    section = item(service, owner, space, "section", "Clients")
    project = item(service, owner, space, "project", "Atlas", section_id=section["id"])
    for n in range(topics):
        item(service, owner, space, "category", f"Topic {n}")
    return section, project


def remember_atlas(service, owner, space, value="Atlas ships invoices"):
    return service.remember_claims(
        owner,
        space,
        RememberClaims(
            command_id=uuid4(), title="Atlas", claims=[{"index": "Atlas", "value": value}]
        ),
    )


def calls(service, kind):
    return [c[1] for c in service.memory_models.calls if c[0] == kind]


def test_large_catalog_is_shortlisted_with_cached_encrypted_item_vectors(commands):
    service, owner, space, _ = commands
    section, project = large_catalog(service, owner, space)
    service.memory_models = ClassifyingModels()
    saved = remember_atlas(service, owner, space)
    [payload] = calls(service, "classify")
    names = [i["name"] for i in payload["state"]["catalog"]]
    # Sections are never asked about; the project's section is derived instead.
    assert names[0] == "Atlas" and "Clients" not in names
    assert len(names) <= memory_classification.catalog_shortlist.MAX_SHORTLIST
    claim = saved["memory"]["claims"][0]
    assert claim["classification"]["project_ids"] == [project["id"]]
    assert claim["classification"]["section_ids"] == [section["id"]]
    embeds = calls(service, "embed")
    assert embeds[0] == ["Atlas ships invoices"] and len(embeds[1]) == 35
    with service.db.transaction(owner) as connection:
        service._scope(connection, owner, space)
        rows = connection.execute(
            "SELECT encrypted_vector FROM space.workspace_item_vectors"
        ).fetchall()
    assert len(rows) == 35
    assert all(b"Topic" not in bytes(r["encrypted_vector"]) for r in rows)
    assert all(b"[1.0" not in bytes(r["encrypted_vector"]) for r in rows)
    service.memory_models.calls.clear()
    remember_atlas(service, owner, space, "Atlas bills monthly")
    assert calls(service, "embed") == [["Atlas bills monthly"]]


def test_edited_item_is_reembedded_and_reclassification_reuses_the_claim_vector(commands):
    service, owner, space, _ = commands
    _, project = large_catalog(service, owner, space)
    service.memory_models = ClassifyingModels()
    saved = remember_atlas(service, owner, space)
    workspace_items.save(
        service,
        owner,
        space,
        workspace_items.SaveItem(
            command_id=uuid4(),
            item_id=project["id"],
            expected_revision=project["revision"],
            kind="project",
            data={"name": "Atlas", "description": "Invoices", "section_id": project["section_id"]},
        ),
    )
    service.memory_models.calls.clear()
    service.change_memory(owner, space, assign(saved).model_copy(update={"classification": None}))
    assert calls(service, "embed") == [["project: Atlas\nInvoices"]]
    [payload] = calls(service, "classify")
    assert payload["state"]["catalog"][0]["description"] == "Invoices"


def test_small_catalog_is_sent_whole_without_item_embeddings(commands):
    service, owner, space, saved, *_ = organized(commands)
    names = {i["name"] for i in calls(service, "classify")[0]["state"]["catalog"]}
    assert names == {"Dev", "Atlas"}
    assert not any(
        text.startswith(("category:", "project:", "section:"))
        for texts in calls(service, "embed")
        for text in texts
    )


def test_oversized_descriptions_are_trimmed_instead_of_left_unassigned(commands):
    service, owner, space, _ = commands
    project = item(service, owner, space, "project", "Atlas", description="x" * 7000)
    item(service, owner, space, "project", "Mobile", description="y" * 7000)
    service.memory_models = ClassifyingModels()
    saved = remember_atlas(service, owner, space)
    claim = saved["memory"]["claims"][0]
    assert claim["classification"]["reason"] == "matched"
    assert project["id"] in claim["classification"]["project_ids"]
    [payload] = calls(service, "classify")
    assert all(i["description"].endswith("…") for i in payload["state"]["catalog"])
    stored = workspace_items.get(service, owner, space, project["id"])
    assert stored["description"] == "x" * 7000
