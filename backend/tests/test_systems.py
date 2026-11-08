"""The Systems area: only promoted lab entries reach recall, and demotion takes them out."""

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4, uuid5

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


def reading(service, actor, space, system, day, value, command_id=None, **fields):
    return systems.record_reading(
        service,
        actor,
        space,
        UUID(system["id"]),
        systems.RecordReading(
            command_id=command_id or uuid4(),
            date=day,
            value=value,
            source=fields.pop("source", "Search Console"),
            **fields,
        ),
    )


def listed(service, owner, space, system, **query):
    return systems.readings(
        service, owner, space, UUID(system["id"]), systems.ReadingQuery(**query)
    )


def month_log(system, day):
    return uuid5(UUID(system["id"]), f"readings:{day:%Y-%m}")


def test_readings_replace_a_date_list_newest_first_and_show_their_age(lab):
    service, owner, space, system, _ = lab
    [summary] = systems.overview(service, owner, space)["systems"]
    assert summary["latest_reading"] is None and summary["reading_age_days"] is None
    today = datetime.now(UTC).date()
    days = [today - timedelta(days=n) for n in (40, 14, 7, 1)]
    agent = delegated(service, owner, write=True)
    for day, value in zip(days, (8, 10, 12, 15), strict=True):
        saved = reading(service, agent, space, system, day, value, unit=" clicks ")
    assert saved == {
        "system_id": system["id"],
        "date": days[-1].isoformat(),
        "value": 15.0,
        "unit": "clicks",
        "note": "",
        "source": "Search Console",
        "metric": "goal",
        "recorded_by": saved["recorded_by"],
    }
    assert saved["recorded_by"].startswith("Test ")
    replaced = reading(service, owner, space, system, days[2], 13, note="Recounted")
    assert replaced["recorded_by"] is None

    result = listed(service, owner, space, system)
    assert [(r["date"], r["value"]) for r in result["readings"]] == [
        (days[3].isoformat(), 15.0),
        (days[2].isoformat(), 13.0),
        (days[1].isoformat(), 10.0),
        (days[0].isoformat(), 8.0),
    ]
    assert result["more"] is False and result["readings"][1]["note"] == "Recounted"
    since = listed(service, owner, space, system, since=days[1], limit=2)
    assert [r["date"] for r in since["readings"]] == [days[3].isoformat(), days[2].isoformat()]
    assert since["more"] is True
    # The month's revisions keep the replaced number.
    log = workspace_items.get(service, owner, space, month_log(system, days[2]))
    earlier = workspace_items.get(
        service, owner, space, month_log(system, days[2]), log["revision"] - 1
    )
    assert days[2].isoformat() in {r["date"] for r in earlier["readings"]}
    assert 12.0 in {r["value"] for r in earlier["readings"]}

    latest = {"date": days[3].isoformat(), "value": 15.0, "unit": "clicks", "metric": "goal"}
    [summary] = systems.overview(service, owner, space)["systems"]
    assert summary["latest_reading"] == latest and summary["reading_age_days"] == 1
    [summary] = systems.verdicts(service, owner, space, systems.VerdictQuery())["systems"]
    assert summary["latest_reading"] == latest and summary["reading_age_days"] == 1
    assert set(summary) >= {"id", "name", "status", "goal_metric", "stats"}


def test_a_retried_reading_lands_once_and_a_reused_command_id_is_refused(lab):
    service, owner, space, system, _ = lab
    today = datetime.now(UTC).date()
    command_id = uuid4()
    first = reading(service, owner, space, system, today, 42, command_id=command_id)
    reading(service, owner, space, system, today - timedelta(days=1), 41)
    # The month log moved on, and the retry still answers with what it wrote.
    again = reading(service, owner, space, system, today, 42, command_id=command_id)
    assert again == first
    assert [r["value"] for r in listed(service, owner, space, system)["readings"]] == [42, 41]
    for changed in ({"value": 43}, {"day": today - timedelta(days=2)}, {"source": "Gmail"}):
        args = {"day": today, "value": 42} | changed
        with pytest.raises(CommandError, match="already used"):
            reading(service, owner, space, system, command_id=command_id, **args)
    other = uuid4()
    systems.save_system(
        service,
        owner,
        space,
        systems.SaveSystem(
            command_id=other, expected_revision=0, data=systems.SystemData(name="Outreach")
        ),
    )
    with pytest.raises(CommandError, match="already used"):
        reading(service, owner, space, system, today, 1, command_id=other)


def test_a_reading_is_refused_without_a_number_a_source_or_an_active_system(lab):
    from pydantic import ValidationError

    service, owner, space, system, _ = lab
    today = datetime.now(UTC).date()
    base = {"command_id": uuid4(), "date": today.isoformat(), "value": 1, "source": "Gmail"}
    for bad in (
        {"value": "12"},
        {"value": True},
        {"value": float("nan")},
        {"value": float("inf")},
        {"source": "  "},
        {"source": None},
        {"date": "2026-13-01"},
        {"note": "x" * 501},
        {"unit": "x" * 41},
        {"recorded_by": "Me"},
    ):
        with pytest.raises(ValidationError):
            systems.RecordReading.model_validate(base | bad)
    with pytest.raises(ValidationError):
        systems.ReadingQuery(limit=systems.MAX_READINGS + 1)
    with pytest.raises(CommandError, match="after tomorrow") as error:
        reading(service, owner, space, system, today + timedelta(days=3), 1)
    assert error.value.status == 422
    with pytest.raises(CommandError) as error:
        reading(service, owner, space, {"id": str(uuid4())}, today, 1)
    assert error.value.status == 404

    reading(service, owner, space, system, today, 5)
    log = workspace_items.get(service, owner, space, month_log(system, today))
    with pytest.raises(CommandError, match="not archived on their own"):
        workspace_items.visibility(
            service,
            owner,
            space,
            UUID(log["id"]),
            workspace_items.ChangeVisibility(
                command_id=uuid4(), expected_revision=log["revision"], deleted=True
            ),
        )
    with pytest.raises(ValueError):
        workspace_items.SaveItem(
            command_id=uuid4(),
            item_id=UUID(log["id"]),
            expected_revision=log["revision"],
            kind="system_readings",
            data={"name": "Bypass"},
        )
    assert "Search Console" not in json.dumps(
        space_map.build(service, owner, space, space_map.MapRequest())
    )
    # A month of readings has no name, so search must not list it.
    from space import workspace_search

    indexed = workspace_search.index(service, owner, space)
    assert log["id"] not in json.dumps(indexed) and system["id"] in json.dumps(indexed)
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
    with pytest.raises(CommandError, match="active system"):
        reading(service, owner, space, system, today, 6)
    assert [r["value"] for r in listed(service, owner, space, system)["readings"]] == [5]


def test_readings_survive_export_and_a_copy_records_into_its_own_month(lab):
    from dataclasses import replace

    from test_restore_staging import stage
    from test_workspace_archive import read_archive

    from space import restore_merge as merge

    service, owner, space, system, _ = lab
    today = datetime.now(UTC).date()
    reading(service, owner, space, system, today, 7, unit="replies")
    archive = read_archive(service, owner, space)
    log_id = str(month_log(system, today))
    assert archive_schema.validate_archive(archive)

    target = replace(owner, subject=str(uuid4()))
    target_space = UUID(service.bootstrap(target)["id"])
    upload = stage(service, target, target_space, archive)
    review = merge.review(service, target, target_space, upload.id, merge.Review())
    merge.commit(
        service, target, target_space, upload.id, merge.Commit(snapshot=review["snapshot"])
    )
    [summary] = systems.overview(service, target, target_space)["systems"]
    assert summary["latest_reading"] == {
        "date": today.isoformat(),
        "value": 7.0,
        "unit": "replies",
        "metric": "goal",
    }

    choices = dict.fromkeys(["item:" + system["id"], "item:" + log_id], "copy")
    upload = stage(service, owner, space, archive)
    review = merge.review(service, owner, space, upload.id, merge.Review(choices=choices))
    target_ids = {item["key"]: item["target_id"] for item in review["items"]}
    merge.commit(
        service, owner, space, upload.id, merge.Commit(snapshot=review["snapshot"], choices=choices)
    )
    copy = {"id": target_ids["item:" + system["id"]]}
    reading(service, owner, space, copy, today, 9)
    assert [r["value"] for r in listed(service, owner, space, copy)["readings"]] == [9]
    if today.day > 1:
        reading(service, owner, space, copy, today - timedelta(days=1), 8)
        values = [r["value"] for r in listed(service, owner, space, copy)["readings"]]
        assert values == [9, 8]
    logs = systems._items(service, owner, space, (systems.READINGS,))
    assert sorted(log["system_id"] for log in logs) == sorted([system["id"], copy["id"]])
    assert [r["value"] for r in listed(service, owner, space, system)["readings"]] == [7]

    broken = read_archive(service, owner, space)
    for record in broken["entries"]:
        if record["value"]["id"] == log_id:
            record["value"]["readings"][0]["date"] = "1999-01-01"
    with pytest.raises(ValueError):
        archive_schema.validate_archive(broken)
    broken = read_archive(service, owner, space)
    for record in broken["entries"]:
        if record["value"]["id"] == log_id:
            record["value"]["system_id"] = str(uuid4())
    with pytest.raises(ValueError, match="linked system"):
        archive_schema.validate_archive(broken)


def test_a_log_restored_onto_a_copy_is_not_taken_back_by_the_original(lab):
    from test_restore_staging import stage
    from test_workspace_archive import read_archive

    from space import restore_merge as merge

    service, owner, space, system, _ = lab
    today = datetime.now(UTC).date()
    reading(service, owner, space, system, today, 7)
    archive = read_archive(service, owner, space)
    log_id = str(month_log(system, today))
    # The copy's month log replaces the original's under the original's derived ID.
    choices = {"item:" + system["id"]: "copy", "item:" + log_id: "replace"}
    upload = stage(service, owner, space, archive)
    review = merge.review(service, owner, space, upload.id, merge.Review(choices=choices))
    target_ids = {item["key"]: item["target_id"] for item in review["items"]}
    merge.commit(
        service, owner, space, upload.id, merge.Commit(snapshot=review["snapshot"], choices=choices)
    )
    copy = {"id": target_ids["item:" + system["id"]]}
    assert workspace_items.get(service, owner, space, UUID(log_id))["system_id"] == copy["id"]
    assert [r["value"] for r in listed(service, owner, space, copy)["readings"]] == [7]

    reading(service, owner, space, system, today, 11)
    reading(service, owner, space, system, today, 12)
    assert [r["value"] for r in listed(service, owner, space, system)["readings"]] == [12]
    reading(service, owner, space, copy, today, 8)
    assert [r["value"] for r in listed(service, owner, space, copy)["readings"]] == [8]
    assert workspace_items.get(service, owner, space, UUID(log_id))["system_id"] == copy["id"]
    logs = systems._items(service, owner, space, (systems.READINGS,))
    assert sorted(log["system_id"] for log in logs) == sorted([system["id"], copy["id"]])


def test_metrics_share_a_date_replace_one_another_only_per_metric_and_filter(lab):
    service, owner, space, system, _ = lab
    today = datetime.now(UTC).date()
    yesterday = today - timedelta(days=1)
    reading(service, owner, space, system, today, 120, metric="replies")
    [summary] = systems.overview(service, owner, space)["systems"]
    # Only a goal reading is the system's latest; an input alone leaves it unmeasured.
    assert summary["latest_reading"] is None and summary["reading_age_days"] is None
    goal = reading(service, owner, space, system, yesterday, 30)
    assert goal["metric"] == "goal"
    reading(service, owner, space, system, today, 2, metric="counter.unsubscribes")
    reading(service, owner, space, system, today, 31, metric="goal")
    replaced = reading(service, owner, space, system, today, 125, metric="replies", note="Late")
    assert replaced["metric"] == "replies" and replaced["value"] == 125.0

    result = listed(service, owner, space, system)
    assert [(r["date"], r["metric"], r["value"]) for r in result["readings"]] == [
        (today.isoformat(), "counter.unsubscribes", 2.0),
        (today.isoformat(), "goal", 31.0),
        (today.isoformat(), "replies", 125.0),
        (yesterday.isoformat(), "goal", 30.0),
    ]
    only = listed(service, owner, space, system, metric="replies")["readings"]
    assert [(r["value"], r["note"]) for r in only] == [(125.0, "Late")]
    goals = listed(service, owner, space, system, metric="goal", limit=1)
    assert [r["value"] for r in goals["readings"]] == [31.0] and goals["more"] is True
    assert listed(service, owner, space, system, metric="absent")["readings"] == []

    latest = {"date": today.isoformat(), "value": 31.0, "unit": "", "metric": "goal"}
    [summary] = systems.overview(service, owner, space)["systems"]
    assert summary["latest_reading"] == latest and summary["reading_age_days"] == 0
    [summary] = systems.verdicts(service, owner, space, systems.VerdictQuery())["systems"]
    assert summary["latest_reading"] == latest

    # A command ID belongs to one metric: reusing it for another is refused.
    command_id = uuid4()
    first = reading(service, owner, space, system, today, 3, command_id=command_id, metric="a")
    assert reading(service, owner, space, system, today, 3, command_id=command_id, metric="a") == (
        first
    )
    with pytest.raises(CommandError, match="already used"):
        reading(service, owner, space, system, today, 3, command_id=command_id, metric="b")


def test_a_metric_is_a_slug_and_an_omitted_one_is_the_goal(lab, monkeypatch):
    from pydantic import ValidationError

    service, owner, space, system, _ = lab
    today = datetime.now(UTC).date()
    base = {"command_id": uuid4(), "date": today.isoformat(), "value": 1, "source": "Gmail"}
    assert systems.RecordReading.model_validate(base).metric == "goal"
    assert systems.RecordReading.model_validate(base | {"metric": None}).metric == "goal"
    for good in ("replies", "counter.unsubscribes", "input_2", "a-b", "9", "x" * 64):
        assert systems.RecordReading.model_validate(base | {"metric": good}).metric == good
    for bad in ("", "Goal", "-x", ".x", "a b", "x" * 65, "réponses", 3):
        with pytest.raises(ValidationError):
            systems.RecordReading.model_validate(base | {"metric": bad})
        with pytest.raises(ValidationError):
            systems.ReadingQuery.model_validate({"metric": bad})

    day = today.isoformat()
    month = f"{today:%Y-%m}"

    def entry(metric=None):
        value = {"date": day, "value": 1.0, "source": "Gmail"}
        return value if metric is None else value | {"metric": metric}

    log = {"system_id": system["id"], "month": month}
    # A month log written before metrics existed reads as goal readings.
    [old] = systems.ReadingLog.model_validate(log | {"readings": [entry()]}).readings
    assert old.metric == "goal"
    systems.ReadingLog.model_validate(log | {"readings": [entry("goal"), entry("replies")]})
    for readings in ([entry(), entry("goal")], [entry("replies"), entry("goal")]):
        with pytest.raises(ValidationError, match="one per date and metric"):
            systems.ReadingLog.model_validate(log | {"readings": readings})

    # A full month refuses a new date and metric and still replaces one it holds.
    monkeypatch.setattr(systems, "MAX_MONTH_READINGS", 2)
    reading(service, owner, space, system, today, 1)
    reading(service, owner, space, system, today, 2, metric="replies")
    with pytest.raises(CommandError, match="at most 2 readings") as error:
        reading(service, owner, space, system, today, 3, metric="clicks")
    assert error.value.status == 413
    reading(service, owner, space, system, today, 4, metric="replies")
    values = {r["metric"]: r["value"] for r in listed(service, owner, space, system)["readings"]}
    assert values == {"goal": 1.0, "replies": 4.0}
