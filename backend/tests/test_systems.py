"""The Systems area: only promoted lab entries reach recall, and demotion takes them out."""

import json
from uuid import UUID, uuid4

import pytest
from test_database_setup import database as database_fixture
from test_memora_pipeline import Models
from test_workspace import command_database as command_database_fixture
from test_workspace import commands as commands_fixture
from test_workspace import delegated

from space import archive_schema, space_map, systems, workspace_items
from space.memory import RecallRequest
from space.workspace import CommandError

database = database_fixture
command_database = command_database_fixture
commands = commands_fixture

PHRASE = "zephyrine hook"


@pytest.fixture
def lab(commands):
    service, owner, space, _ = commands
    service.memory_models = Models()
    system = systems.save_system(
        service,
        owner,
        space,
        systems.SaveSystem(
            command_id=uuid4(),
            expected_revision=0,
            data=systems.SystemData(
                name="X audience", goal_metric="Median views", goal_target="300"
            ),
        ),
    )
    entry = systems.save_entry(
        service,
        owner,
        space,
        systems.SaveLabEntry(
            command_id=uuid4(),
            expected_revision=0,
            data=systems.LabEntryInput(
                name=f"Open tweets with a {PHRASE} in first person",
                system_id=system["id"],
                domain="Copy & hooks",
            ),
        ),
    )
    return service, owner, space, system, entry


def recall(service, owner, space):
    result = service.context(owner, space, RecallRequest(query=PHRASE, strategy="lexical"))
    return [item for item in result["items"] if item.get("verdict")]


def update(service, owner, space, entry, **fields):
    data = {
        key: entry[key]
        for key in systems.LabEntryInput.model_fields
        if key in entry and key != "note"
    } | fields
    return systems.save_entry(
        service,
        owner,
        space,
        systems.SaveLabEntry(
            command_id=uuid4(),
            entry_id=UUID(entry["id"]),
            expected_revision=entry["revision"],
            data=systems.LabEntryInput.model_validate(data),
        ),
    )


def promote(service, actor, space, entry, verdict="validated", command_id=None):
    return systems.promote(
        service,
        actor,
        space,
        UUID(entry["id"]),
        systems.Promote(
            command_id=command_id or uuid4(),
            expected_revision=entry["revision"],
            verdict=verdict,
        ),
    )


def demote(service, owner, space, entry, status):
    return systems.demote(
        service,
        owner,
        space,
        UUID(entry["id"]),
        systems.Demote(command_id=uuid4(), expected_revision=entry["revision"], status=status),
    )


def test_testing_entries_stay_out_of_recall_until_promoted_and_after_demotion(lab):
    service, owner, space, system, entry = lab
    assert entry["status"] == "testing" and entry["verdict"] is None
    assert recall(service, owner, space) == []

    with pytest.raises(CommandError, match="Measured or Owner's call; Name the source"):
        promote(service, owner, space, entry)

    entry = update(
        service, owner, space, entry, evidence="measured", source="X weekly review", why="3x"
    )
    entry = promote(service, owner, space, entry)
    assert entry["status"] == "validated" and entry["verdict"]["label"] == "works"
    [found] = recall(service, owner, space)
    assert found["verdict"]["label"] == "works"
    assert found["verdict"]["evidence"] == "Measured"
    assert found["verdict"]["system"] == {"id": system["id"], "name": "X audience"}
    assert found["verdict"]["domain"] == "Copy & hooks"
    assert found["verdict"]["url"].endswith(
        f"/app/systems/{system['id']}?entry={entry['id']}&space_id={space}"
    )

    with pytest.raises(CommandError, match="Move it back to Testing"):
        update(service, owner, space, entry, why="changed")
    with pytest.raises(CommandError, match="back to Testing or retire"):
        workspace_items.visibility(
            service,
            owner,
            space,
            UUID(entry["id"]),
            workspace_items.ChangeVisibility(
                command_id=uuid4(), expected_revision=entry["revision"], deleted=True
            ),
        )

    memory_id = UUID(entry["verdict"]["memory_id"])
    entry = demote(service, owner, space, entry, "testing")
    assert entry["status"] == "testing" and entry["retries"] == 1
    assert recall(service, owner, space) == []
    kept = service.get_resource(owner, space, memory_id)
    assert kept["recall"]["excluded"] is True and kept["verdict"]["label"] == "works"

    entry = promote(service, owner, space, entry, "invalidated")
    [found] = recall(service, owner, space)
    assert found["verdict"]["label"] == "does_not_work"
    assert UUID(found["id"]) != memory_id

    entry = demote(service, owner, space, entry, "retired")
    assert recall(service, owner, space) == []
    with pytest.raises(CommandError, match="back to Testing"):
        promote(service, owner, space, entry)
    statuses = [
        revision["status"]
        for revision in systems.history(service, owner, space, UUID(entry["id"]))["revisions"]
    ]
    assert statuses == ["testing", "testing", "validated", "testing", "invalidated", "retired"]


def test_promotion_retries_land_on_one_memory_and_credit_the_agent(lab):
    service, owner, space, _, entry = lab
    entry = update(service, owner, space, entry, evidence="owner_call", source="Owner")
    agent = delegated(service, owner, write=True)
    command_id = uuid4()
    first = promote(service, agent, space, entry, command_id=command_id)
    again = promote(service, agent, space, entry, command_id=command_id)
    assert again["verdict"] == first["verdict"]
    assert first["verdict"]["promoted_by"].startswith("Test ")
    memories = service.list_memories(owner, space)["items"]
    assert [m["id"] for m in memories] == [first["verdict"]["memory_id"]]
    assert memories[0]["title"].startswith("Verdict: ")


def test_a_stale_promotion_writes_no_memory(lab):
    service, owner, space, _, entry = lab
    entry = update(service, owner, space, entry, evidence="measured", source="Review")
    stale = entry | {"revision": entry["revision"] - 1}
    with pytest.raises(CommandError, match="changed"):
        promote(service, owner, space, stale)
    assert service.list_memories(owner, space)["items"] == []


def test_uses_count_toward_the_reuse_rate(lab):
    service, owner, space, system, entry = lab
    entry = update(service, owner, space, entry, evidence="measured", source="Review")
    with pytest.raises(CommandError, match="Only a verdict"):
        systems.record_use(
            service,
            owner,
            space,
            UUID(entry["id"]),
            systems.RecordUse(
                command_id=uuid4(), expected_revision=entry["revision"], used_in="Post"
            ),
        )
    entry = promote(service, owner, space, entry)
    entry = systems.record_use(
        service,
        owner,
        space,
        UUID(entry["id"]),
        systems.RecordUse(
            command_id=uuid4(), expected_revision=entry["revision"], used_in="Post 12 Nov"
        ),
    )
    assert entry["last_used"] == entry["uses"][0]["at"] and entry["uses"][0]["by"] is None
    area = systems.overview(service, owner, space)
    [summary] = area["systems"]
    assert summary["stats"] | {} == {
        "entries": 1,
        "testing": 0,
        "works": 1,
        "does_not_work": 0,
        "retired": 0,
        "verdicts": 1,
        "reused_30d": 1,
        "reuse_share": 1.0,
    }
    listed = systems.verdicts(service, owner, space, systems.VerdictQuery(domain="Copy & hooks"))
    assert [v["id"] for v in listed["verdicts"]] == [entry["id"]]
    assert listed["verdicts"][0]["reused_30d"] is True
    assert (
        systems.verdicts(service, owner, space, systems.VerdictQuery(domain="Video"))["verdicts"]
        == []
    )


def test_systems_stay_out_of_the_map_and_generic_item_saves(lab):
    service, owner, space, system, _ = lab
    built = space_map.build(service, owner, space, space_map.MapRequest())
    assert "X audience" not in json.dumps(built)
    with pytest.raises(ValueError):
        workspace_items.SaveItem(
            command_id=uuid4(),
            item_id=UUID(system["id"]),
            expected_revision=system["revision"],
            kind="system",
            data={"name": "Bypass"},
        )


def test_a_playbook_must_be_a_document(lab):
    service, owner, space, system, entry = lab
    with pytest.raises(CommandError) as error:
        systems.save_system(
            service,
            owner,
            space,
            systems.SaveSystem(
                command_id=uuid4(),
                system_id=UUID(system["id"]),
                expected_revision=system["revision"],
                data=systems.SystemData(name="X audience", playbook_id=uuid4()),
            ),
        )
    assert error.value.status == 404


def promoted(service, owner, space, entry):
    entry = update(service, owner, space, entry, evidence="measured", source="Review")
    return promote(service, owner, space, entry)


def test_a_restored_space_keeps_its_verdicts_and_a_copy_points_at_the_copies(lab):
    from dataclasses import replace

    from test_restore_staging import stage
    from test_workspace_archive import read_archive

    from space import restore_merge as merge

    service, owner, space, system, entry = lab
    entry = promoted(service, owner, space, entry)
    archive = read_archive(service, owner, space)

    target = replace(owner, subject=str(uuid4()))
    target_space = UUID(service.bootstrap(target)["id"])
    upload = stage(service, target, target_space, archive)
    review = merge.review(service, target, target_space, upload.id, merge.Review())
    merge.commit(
        service, target, target_space, upload.id, merge.Commit(snapshot=review["snapshot"])
    )
    [restored] = systems.overview(service, target, target_space)["entries"]
    assert restored["status"] == "validated" and restored["system_id"] == system["id"]
    [found] = recall(service, target, target_space)
    assert found["verdict"]["entry_id"] == entry["id"]

    upload = stage(service, owner, space, archive)
    keys = [
        "item:" + system["id"],
        "item:" + entry["id"],
        "resource:" + entry["verdict"]["memory_id"],
    ]
    choices = dict.fromkeys(keys, "copy")
    review = merge.review(service, owner, space, upload.id, merge.Review(choices=choices))
    target_ids = {item["key"]: item["target_id"] for item in review["items"]}
    merge.commit(
        service, owner, space, upload.id, merge.Commit(snapshot=review["snapshot"], choices=choices)
    )
    copy = workspace_items.get(service, owner, space, UUID(target_ids["item:" + entry["id"]]))
    assert copy["system_id"] == target_ids["item:" + system["id"]]
    memory_copy = target_ids["resource:" + entry["verdict"]["memory_id"]]
    assert copy["verdict"]["memory_id"] == memory_copy
    verdict = service.get_resource(owner, space, UUID(memory_copy))["verdict"]
    assert verdict["entry_id"] == copy["id"] and verdict["system"]["id"] == copy["system_id"]


def test_activity_pairs_each_change_with_the_revision_it_replaced(lab):
    service, owner, space, system, entry = lab
    entry = promoted(service, owner, space, entry)
    other = systems.save_system(
        service,
        owner,
        space,
        systems.SaveSystem(
            command_id=uuid4(), expected_revision=0, data=systems.SystemData(name="SEO")
        ),
    )
    systems.save_entry(
        service,
        owner,
        space,
        systems.SaveLabEntry(
            command_id=uuid4(),
            expected_revision=0,
            data=systems.LabEntryInput(
                name="Refresh pages that started ranking", system_id=other["id"], domain="SEO/GEO"
            ),
        ),
    )
    events = systems.activity(service, owner, space)["events"]
    assert [(e["entry"]["name"][:7], e["entry"]["status"]) for e in events] == [
        ("Refresh", "testing"),
        ("Open tw", "validated"),
        ("Open tw", "testing"),
        ("Open tw", "testing"),
    ]
    assert events[0]["previous"] is None and events[0]["author"] == "you"
    assert events[1]["previous"]["status"] == "testing" and events[1]["entry"]["verdict"]
    mine = systems.activity(service, owner, space, UUID(system["id"]))["events"]
    assert {e["entry"]["system_id"] for e in mine} == {system["id"]} and len(mine) == 3


def test_a_system_with_live_verdicts_is_not_archived(lab):
    service, owner, space, system, entry = lab
    entry = promoted(service, owner, space, entry)

    def archive():
        current = workspace_items.get(service, owner, space, UUID(system["id"]))
        return workspace_items.visibility(
            service,
            owner,
            space,
            UUID(system["id"]),
            workspace_items.ChangeVisibility(
                command_id=uuid4(), expected_revision=current["revision"], deleted=True
            ),
        )

    with pytest.raises(CommandError, match="verdicts back to Testing"):
        archive()
    demote(service, owner, space, entry, "retired")
    assert archive()["deleted"] is True


def test_a_reused_command_id_is_refused_for_another_entry_or_move(lab):
    service, owner, space, system, entry = lab
    entry = update(service, owner, space, entry, evidence="measured", source="Review")
    second = systems.save_entry(
        service,
        owner,
        space,
        systems.SaveLabEntry(
            command_id=uuid4(),
            expected_revision=0,
            data=systems.LabEntryInput(
                name="Second idea", system_id=system["id"], domain="Social", source="Review"
            ),
        ),
    )
    command_id = uuid4()
    entry = promote(service, owner, space, entry, command_id=command_id)
    with pytest.raises(CommandError, match="already used"):
        promote(service, owner, space, second, command_id=command_id)
    with pytest.raises(CommandError, match="already used"):
        systems.demote(
            service,
            owner,
            space,
            UUID(entry["id"]),
            systems.Demote(
                command_id=command_id, expected_revision=entry["revision"] - 1, status="testing"
            ),
        )


def test_a_promotion_that_fails_after_its_memory_recovers_on_retry(lab, monkeypatch):
    service, owner, space, _, entry = lab
    entry = update(service, owner, space, entry, evidence="measured", source="Review")
    commit = systems._commit

    def broken(*args, **kwargs):
        raise RuntimeError("database went away")

    monkeypatch.setattr(systems, "_commit", broken)
    command_id = uuid4()
    with pytest.raises(RuntimeError):
        promote(service, owner, space, entry, command_id=command_id)
    assert recall(service, owner, space) == []

    monkeypatch.setattr(systems, "_commit", commit)
    entry = promote(service, owner, space, entry, command_id=command_id)
    [found] = recall(service, owner, space)
    assert found["id"] == entry["verdict"]["memory_id"]


def test_a_verdict_topic_drops_the_characters_a_memory_topic_forbids(lab):
    service, owner, space, _, _ = lab

    def promoted_named(name, domain="Copy & hooks"):
        system = systems.save_system(
            service,
            owner,
            space,
            systems.SaveSystem(
                command_id=uuid4(), expected_revision=0, data=systems.SystemData(name=name)
            ),
        )
        entry = systems.save_entry(
            service,
            owner,
            space,
            systems.SaveLabEntry(
                command_id=uuid4(),
                expected_revision=0,
                data=systems.LabEntryInput(
                    name="A hook with a measured lift",
                    system_id=system["id"],
                    domain=domain,
                    evidence="measured",
                    source="Review",
                ),
            ),
        )
        return promote(service, owner, space, entry)

    entry = promoted_named("Pipes | and\nnewlines")
    memory = service.get_resource(owner, space, UUID(entry["verdict"]["memory_id"]))
    assert memory["recall"]["topics"] == ["Pipes and newlines", "Copy & hooks"]

    fallback = promoted_named("|||\n", domain="Social")
    memory = service.get_resource(owner, space, UUID(fallback["verdict"]["memory_id"]))
    assert memory["recall"]["topics"] == ["Social"]


def test_a_restored_verdict_without_its_system_is_still_recalled(lab):
    from dataclasses import replace

    from test_restore_staging import stage
    from test_workspace_archive import read_archive

    from space import restore_merge as merge

    service, owner, space, _, entry = lab
    entry = promoted(service, owner, space, entry)
    archive = read_archive(service, owner, space)
    for record in archive["entries"]:
        verdict = record["value"].get("verdict")
        if record["entity"] == "resource" and verdict:
            verdict.pop("system")
    target = replace(owner, subject=str(uuid4()))
    target_space = UUID(service.bootstrap(target)["id"])
    upload = stage(service, target, target_space, archive)
    review = merge.review(service, target, target_space, upload.id, merge.Review())
    merge.commit(
        service, target, target_space, upload.id, merge.Commit(snapshot=review["snapshot"])
    )
    [found] = recall(service, target, target_space)
    assert found["verdict"]["label"] == "works"
    assert "system" not in found["verdict"] and "url" not in found["verdict"]


def test_archive_validation_rejects_a_dangling_system_or_playbook(lab):
    from test_workspace_archive import read_archive

    service, owner, space, system, entry = lab
    broken = read_archive(service, owner, space)
    for record in broken["entries"]:
        if record["value"]["id"] == entry["id"]:
            record["value"]["system_id"] = str(uuid4())
    with pytest.raises(ValueError, match="linked system"):
        archive_schema.validate_archive(broken)

    broken = read_archive(service, owner, space)
    for record in broken["entries"]:
        if record["value"]["id"] == system["id"]:
            record["value"]["playbook_id"] = str(uuid4())
    with pytest.raises(ValueError, match="linked playbook"):
        archive_schema.validate_archive(broken)


def test_a_retried_create_answers_with_the_entry_after_its_system_is_archived(lab):
    service, owner, space, system, _ = lab
    command_id = uuid4()
    data = systems.LabEntryInput(name="Third idea", system_id=system["id"], domain="Social")
    created = systems.save_entry(
        service,
        owner,
        space,
        systems.SaveLabEntry(command_id=command_id, expected_revision=0, data=data),
    )
    current = workspace_items.get(service, owner, space, UUID(system["id"]))
    workspace_items.visibility(
        service,
        owner,
        space,
        UUID(system["id"]),
        workspace_items.ChangeVisibility(
            command_id=uuid4(), expected_revision=current["revision"], deleted=True
        ),
    )
    again = systems.save_entry(
        service,
        owner,
        space,
        systems.SaveLabEntry(command_id=command_id, expected_revision=0, data=data),
    )
    assert again["id"] == created["id"] and again["name"] == "Third idea"
