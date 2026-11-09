"""The Systems area: only promoted lab entries reach recall, and demotion takes them out."""

import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from test_auth import signing as signing_fixture
from test_database_setup import database as database_fixture
from test_memora_pipeline import Models
from test_workspace import command_database as command_database_fixture
from test_workspace import commands as commands_fixture
from test_workspace import delegated

from space import archive_schema, space_map, systems, workspace_items
from space.memory import RecallRequest
from space.workspace import CommandError

database = database_fixture
signing = signing_fixture
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


def item_revisions(archive):
    return [entry for entry in archive["entries"] if entry["entity"] == "item"]


def archived_readings(archive):
    return [entry["value"] for entry in archive["entries"] if entry["entity"] == "reading"]


def test_readings_replace_a_date_list_newest_first_and_show_their_age(lab):
    from test_workspace_archive import read_archive

    service, owner, space, system, _ = lab
    [summary] = systems.overview(service, owner, space)["systems"]
    assert summary["latest_reading"] is None and summary["reading_age_days"] is None
    revisions = len(item_revisions(read_archive(service, owner, space)))
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
    # Readings keep no revisions: five writes add four archive entries and no revision.
    archive = read_archive(service, owner, space)
    assert len(item_revisions(archive)) == revisions
    assert sorted(r["value"] for r in archived_readings(archive)) == [8, 10, 13, 15]

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
    # Later readings landed, and the retry still answers with what it wrote.
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
    outreach = next(
        item
        for item in systems.overview(service, owner, space)["systems"]
        if item["name"] == "Outreach"
    )
    with pytest.raises(CommandError, match="already used"):
        reading(service, owner, space, outreach, today, 42, command_id=command_id)


def test_a_reading_is_refused_without_a_number_a_source_or_an_active_system(lab):
    from pydantic import ValidationError

    from space import workspace_search

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
    with pytest.raises(CommandError) as error:
        listed(service, owner, space, {"id": str(uuid4())})
    assert error.value.status == 404

    reading(service, owner, space, system, today, 5)
    viewer = delegated(service, owner, write=False)
    with pytest.raises(CommandError) as error:
        reading(service, viewer, space, system, today, 6)
    assert error.value.status == 403
    # Readings are neither items nor content: the map and search leave them out.
    assert "Search Console" not in json.dumps(
        space_map.build(service, owner, space, space_map.MapRequest())
    )
    indexed = json.dumps(workspace_search.index(service, owner, space))
    assert "Search Console" not in indexed and system["id"] in indexed
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


def restore(service, owner, space, archive, choices=None):
    from test_restore_staging import stage

    from space import restore_merge as merge

    upload = stage(service, owner, space, archive)
    review = merge.review(service, owner, space, upload.id, merge.Review(choices=choices or {}))
    result = merge.commit(
        service,
        owner,
        space,
        upload.id,
        merge.Commit(snapshot=review["snapshot"], choices=choices or {}),
    )
    return {item["key"]: item["target_id"] for item in review["items"]}, result, upload.id


def test_readings_survive_export_and_restore_as_rows_beside_the_revisions(lab, monkeypatch):
    from dataclasses import replace

    from test_workspace_archive import read_archive

    from space import workspace_archive

    service, owner, space, system, _ = lab
    today = datetime.now(UTC).date()
    yesterday = today - timedelta(days=1)
    reading(service, owner, space, system, today, 7, unit="replies")
    reading(service, owner, space, system, today, 3, metric="counter.unsubscribes")
    reading(service, owner, space, system, yesterday, 6)
    # Small pages: readings continue from their own cursor after the revisions.
    monkeypatch.setattr(workspace_archive, "PAGE_READINGS", 2)
    archive = read_archive(service, owner, space)
    assert [(r["date"], r["metric"], r["value"]) for r in archived_readings(archive)] == [
        (yesterday.isoformat(), "goal", 6.0),
        (today.isoformat(), "counter.unsubscribes", 3.0),
        (today.isoformat(), "goal", 7.0),
    ]
    assert archive_schema.validate_archive(archive)
    # Readings do not count toward the revision limit.
    monkeypatch.setattr(
        archive_schema,
        "MAX_ARCHIVE_ENTRIES",
        len(archive["entries"]) - len(archived_readings(archive)),
    )
    archive_schema.validate_archive(archive)
    monkeypatch.undo()
    monkeypatch.setattr(archive_schema, "MAX_ARCHIVE_READINGS", 2)
    with pytest.raises(ValueError, match="2 reading limit"):
        archive_schema.validate_archive(archive)
    monkeypatch.undo()

    target = replace(owner, subject=str(uuid4()))
    target_space = UUID(service.bootstrap(target)["id"])
    _, result, _ = restore(service, target, target_space, archive)
    assert result["result"]["readings"] == 3
    [summary] = systems.overview(service, target, target_space)["systems"]
    assert summary["latest_reading"] == {
        "date": today.isoformat(),
        "value": 7.0,
        "unit": "replies",
        "metric": "goal",
    }
    assert [r["value"] for r in listed(service, target, target_space, system)["readings"]] == [
        3,
        7,
        6,
    ]

    # Keep: the Space's own number stays and a missing reading comes back.
    reading(service, owner, space, system, today, 70, unit="replies")
    reading(service, owner, space, system, today - timedelta(days=2), 5)
    restore(service, owner, space, archive)
    values = {
        (r["date"], r["metric"]): r["value"]
        for r in listed(service, owner, space, system)["readings"]
    }
    assert values[(today.isoformat(), "goal")] == 70
    assert values[(yesterday.isoformat(), "goal")] == 6
    # Replace: the archived number wins on a shared date and metric; others stay.
    restore(service, owner, space, archive, {"item:" + system["id"]: "replace"})
    values = {
        (r["date"], r["metric"]): r["value"]
        for r in listed(service, owner, space, system)["readings"]
    }
    assert values[(today.isoformat(), "goal")] == 7
    assert values[((today - timedelta(days=2)).isoformat(), "goal")] == 5
    # Copy: the copy takes the readings, and each records on its own.
    targets, _, _ = restore(service, owner, space, archive, {"item:" + system["id"]: "copy"})
    copy = {"id": targets["item:" + system["id"]]}
    assert len(listed(service, owner, space, copy)["readings"]) == 3
    reading(service, owner, space, copy, today, 9)
    assert listed(service, owner, space, copy, metric="goal", limit=1)["readings"][0]["value"] == 9
    assert (
        listed(service, owner, space, system, metric="goal", limit=1)["readings"][0]["value"] == 7
    )


def test_a_restore_over_the_reading_limit_changes_nothing(lab, monkeypatch):
    from dataclasses import replace

    from test_workspace_archive import read_archive

    from space import restore_merge as merge

    service, owner, space, system, _ = lab
    today = datetime.now(UTC).date()
    for days in range(3):
        reading(service, owner, space, system, today - timedelta(days=days), days)
    archive = read_archive(service, owner, space)
    target = replace(owner, subject=str(uuid4()))
    target_space = UUID(service.bootstrap(target)["id"])
    monkeypatch.setattr(systems, "MAX_SPACE_READINGS", 2)
    with pytest.raises(CommandError, match="limit of 2 readings") as error:
        restore(service, target, target_space, archive)
    assert error.value.status == 413
    assert systems.overview(service, target, target_space)["systems"] == []
    [session] = merge.restore_staging.listing(service, target, target_space)["items"]
    assert session["state"] == "staged"


def test_an_archive_reading_needs_its_system_and_a_valid_number(lab):
    from test_workspace_archive import read_archive

    service, owner, space, system, entry = lab
    reading(service, owner, space, system, datetime.now(UTC).date(), 7)
    archive = read_archive(service, owner, space)

    def broken(change):
        copy = json.loads(json.dumps(archive))
        [value] = archived_readings(copy)
        change(copy, value)
        return copy

    cases = [
        (lambda a, v: v.update(system_id=str(uuid4())), "reading's system"),
        (lambda a, v: v.update(system_id=entry["id"]), "reading's system"),
        (lambda a, v: a["entries"].append({**a["entries"][-1]}), "duplicate reading"),
        (lambda a, v: v.update(value="7"), None),
        (lambda a, v: v.update(metric="Goal"), None),
        (lambda a, v: v.update(extra=1), None),
        (lambda a, v: v.pop("recorded_at"), None),
        (lambda a, v: a["entries"][-1].update(current=False), "Invalid archive entry"),
    ]
    for change, message in cases:
        with pytest.raises(ValueError, match=message):
            archive_schema.validate_archive(broken(change))
    # The structural budget grows by two containers and 24 separators per reading: one
    # entry's keys need a colon each and a comma between them and after the entry.
    [entry] = [e for e in archive["entries"] if e["entity"] == "reading"]
    assert 2 * (len(entry) + len(entry["value"])) - 1 <= 24


def test_an_export_leaves_out_readings_of_a_system_created_after_its_snapshot(lab):
    from space import workspace_archive
    from space.workspace_archive import ExportPage, export_page

    service, owner, space, system, _ = lab
    today = datetime.now(UTC).date()
    reading(service, owner, space, system, today, 1)
    first = export_page(service, owner, space, ExportPage())
    later = systems.save_system(
        service,
        owner,
        space,
        systems.SaveSystem(
            command_id=uuid4(), expected_revision=0, data=systems.SystemData(name="Later")
        ),
    )
    reading(service, owner, space, later, today, 2)
    entries, page = list(first["entries"]), first
    while page["next_cursor"]:
        page = export_page(
            service, owner, space, ExportPage(after=page["next_cursor"], snapshot=first["snapshot"])
        )
        entries.extend(page["entries"])
    values = [e["value"]["value"] for e in entries if e["entity"] == "reading"]
    assert values == [1.0]
    with pytest.raises(ValueError, match="Unknown export cursor"):
        ExportPage(after="s:nonsense", snapshot=0)
    assert workspace_archive.READING_CURSOR.match("s:")


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

    # A full Space refuses a new date and metric and still replaces one it holds.
    monkeypatch.setattr(systems, "MAX_SPACE_READINGS", 2)
    reading(service, owner, space, system, today, 1)
    reading(service, owner, space, system, today, 2, metric="replies")
    with pytest.raises(CommandError, match="at most 2 readings") as error:
        reading(service, owner, space, system, today, 3, metric="clicks")
    assert error.value.status == 413
    reading(service, owner, space, system, today, 4, metric="replies")
    values = {r["metric"]: r["value"] for r in listed(service, owner, space, system)["readings"]}
    assert values == {"goal": 1.0, "replies": 4.0}


def batch(service, actor, space, system, readings, command_id=None):
    return systems.record_readings(
        service,
        actor,
        space,
        UUID(system["id"]),
        systems.RecordReadings.model_validate(
            {"command_id": str(command_id or uuid4()), "readings": readings}
        ),
    )


def test_a_batch_records_a_sensor_run_all_or_none_and_retries_once(lab, monkeypatch):
    from pydantic import ValidationError

    service, owner, space, system, _ = lab
    today = datetime.now(UTC).date().isoformat()
    run = [
        {"date": today, "value": 40, "source": "Gmail", "metric": "replies"},
        {"date": today, "value": 12, "source": "Gmail"},
        {"date": today, "value": 1, "source": "Gmail", "metric": "counter.unsubscribes"},
    ]
    command_id = uuid4()
    agent = delegated(service, owner, write=True)
    saved = batch(service, agent, space, system, run, command_id)
    assert saved["system_id"] == system["id"]
    assert [(r["metric"], r["value"]) for r in saved["readings"]] == [
        ("replies", 40.0),
        ("goal", 12.0),
        ("counter.unsubscribes", 1.0),
    ]
    assert {r["recorded_by"] for r in saved["readings"]} == {saved["readings"][0]["recorded_by"]}
    assert batch(service, agent, space, system, run, command_id) == saved
    # One of its readings replaced later: the retry still answers with what it wrote.
    reading(service, owner, space, system, datetime.now(UTC).date(), 13)
    assert batch(service, agent, space, system, run, command_id) == saved
    assert listed(service, owner, space, system, metric="goal")["readings"][0]["value"] == 13
    with pytest.raises(CommandError, match="already used"):
        batch(service, agent, space, system, [run[0] | {"value": 41}, *run[1:]], command_id)
    with pytest.raises(CommandError, match="already used"):
        batch(service, agent, space, system, run[1:], command_id)

    for bad in ([], [run[0]] * 2, [run[0] | {"metric": f"m{n}"} for n in range(21)]):
        with pytest.raises(ValidationError):
            systems.RecordReadings.model_validate({"command_id": str(uuid4()), "readings": bad})
    late = (datetime.now(UTC).date() + timedelta(days=3)).isoformat()
    with pytest.raises(CommandError, match="after tomorrow"):
        batch(service, owner, space, system, [run[0] | {"value": 99}, run[1] | {"date": late}])
    monkeypatch.setattr(systems, "MAX_SPACE_READINGS", 4)
    new = [run[0] | {"value": 99}, run[0] | {"metric": "a"}, run[0] | {"metric": "b"}]
    with pytest.raises(CommandError, match="at most 4 readings"):
        batch(service, owner, space, system, new)
    values = {r["metric"]: r["value"] for r in listed(service, owner, space, system)["readings"]}
    assert values == {"replies": 40.0, "goal": 13.0, "counter.unsubscribes": 1.0}


@contextmanager
def mcp_client(service, signing):
    """Parameters: the command service and the signing fixture.
    What it does: starts the app with a person and a writing agent of a new Space.
    Output: (owner, space, rpc(name, arguments), rest(method, path, json)).
    """
    from fastapi.testclient import TestClient

    from space.app import create_app
    from space.settings import Settings

    verifier, token = signing
    browser_token = token()
    owner = verifier.verify(browser_token)
    space = UUID(service.bootstrap(owner)["id"])
    writer = delegated(service, owner, write=True)
    agent_token = token(
        aud=verifier.resource, client_id=writer.client_id, space_grant_id=writer.grant_id
    )
    settings = Settings(environment="test", allowed_hosts=["testserver"])
    with TestClient(create_app(settings, workspace=service, verifier=verifier)) as http:
        headers = {
            "Authorization": "Bearer " + agent_token,
            "X-Space-Agent-ID": writer.agent_id,
            "Accept": "application/json, text/event-stream",
        }

        def rpc(name, arguments):
            response = http.post(
                "/mcp/",
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": {"space_id": str(space), **arguments}},
                },
            )
            assert response.status_code == 200
            result = response.json()["result"]
            assert not result.get("isError"), result
            return result["structuredContent"]

        def rest(method, path, body=None):
            response = http.request(
                method,
                f"/v1/spaces/{space}{path}",
                headers={"Authorization": "Bearer " + browser_token},
                json=body,
            )
            assert response.status_code == 200, response.text
            return response.json()

        yield owner, space, rpc, rest


def test_mcp_and_rest_record_a_batch_and_list_it(commands, signing):
    service = commands[0]
    today = datetime.now(UTC).date().isoformat()
    with mcp_client(service, signing) as (owner, space, rpc, rest):
        system = rpc("create_system", {"command_id": str(uuid4()), "data": {"name": "SEO"}})
        run = [
            {"date": today, "value": 120, "source": "Search Console", "unit": "clicks"},
            {"date": today, "value": 9, "source": "Search Console", "metric": "pages"},
        ]
        request = {"command_id": str(uuid4()), "readings": run}
        saved = rpc("record_system_readings", {"system_id": system["id"], "request": request})
        assert [(r["metric"], r["value"]) for r in saved["readings"]] == [
            ("goal", 120),
            ("pages", 9),
        ]
        assert (
            rpc("record_system_readings", {"system_id": system["id"], "request": request}) == saved
        )
        path = f"/systems/{system['id']}/readings"
        again = rest("POST", path + "/batch", request)
        assert again == saved
        single = rpc(
            "record_system_reading",
            {
                "system_id": system["id"],
                "request": {"command_id": str(uuid4()), **run[1], "value": 10},
            },
        )
        assert (
            single["metric"] == "pages"
            and single["recorded_by"] == saved["readings"][0]["recorded_by"]
        )
        listed_mcp = rpc("list_system_readings", {"system_id": system["id"], "metric": "pages"})
        assert [r["value"] for r in listed_mcp["readings"]] == [10]
        assert rest("GET", path + "?metric=pages") == listed_mcp
        [summary] = rpc("list_systems", {})["systems"]
        assert summary["latest_reading"]["value"] == 120 and summary["reading_age_days"] == 0


FIELDS = {
    "area": "Growth",
    "controllable_inputs": "Posts per week, hook style",
    "stop_conditions": "Pause when replies drop under 1% for two weeks",
    "outcome_delay": "2 to 4 weeks",
    "machine": [{"job_id": "x-daily", "label": "Daily post"}, {"job_id": "x-sensor"}],
    "skills": ["viral-video-writing", " social-account-operations "],
}


def new_project(service, owner, space, name="Audience"):
    return workspace_items.save(
        service,
        owner,
        space,
        workspace_items.SaveItem(
            command_id=uuid4(),
            item_id=uuid4(),
            expected_revision=0,
            kind="project",
            data={"name": name},
        ),
    )


def save_fields(service, owner, space, system=None, command_id=None, revision=None, **data):
    """Save a system the way MCP and REST send it: only the keys given are in the request."""
    request = {"command_id": str(command_id or uuid4()), "expected_revision": 0, "data": data}
    if system is not None:
        request |= {
            "system_id": system["id"],
            "expected_revision": system["revision"] if revision is None else revision,
        }
    return systems.save_system(service, owner, space, systems.SaveSystem.model_validate(request))


def test_structured_fields_are_saved_listed_and_kept_when_an_update_leaves_them_out(lab):
    service, owner, space, old, _ = lab
    project = new_project(service, owner, space)
    created = save_fields(
        service,
        owner,
        space,
        name="Outreach",
        goal_metric="Replies",
        description="Cold email to founders",
        project_id=project["id"],
        **FIELDS,
    )
    stored = {key: created[key] for key in systems.SUMMARY_FIELDS}
    assert stored == FIELDS | {
        "project_id": project["id"],
        "machine": [
            {"job_id": "x-daily", "label": "Daily post"},
            {"job_id": "x-sensor", "label": ""},
        ],
        "skills": ["viral-video-writing", "social-account-operations"],
    }
    by_name = {s["name"]: s for s in systems.overview(service, owner, space)["systems"]}
    assert {key: by_name["Outreach"][key] for key in systems.SUMMARY_FIELDS} == stored
    # A system saved before the fields existed lists them empty.
    assert {key: by_name["X audience"][key] for key in systems.SUMMARY_FIELDS} == {
        "area": "",
        "project_id": None,
        "controllable_inputs": "",
        "stop_conditions": "",
        "outcome_delay": "",
        "machine": [],
        "skills": [],
    }
    summaries = systems.verdicts(service, owner, space, systems.VerdictQuery())["systems"]
    by_name = {s["name"]: s for s in summaries}
    assert {key: by_name["Outreach"][key] for key in systems.SUMMARY_FIELDS} == stored
    assert by_name["X audience"]["machine"] == [] and by_name["X audience"]["area"] == ""

    # An update that sends one field keeps every other one, the name included.
    command_id = uuid4()
    updated = save_fields(service, owner, space, created, command_id=command_id, current_value="12")
    unchanged = [key for key in systems.SystemData.model_fields if key != "current_value"]
    assert updated["current_value"] == "12" and updated["revision"] == created["revision"] + 1
    assert {key: updated[key] for key in unchanged} == {key: created[key] for key in unchanged}
    # Its retry merges onto the revision it read, so it lands once.
    again = save_fields(service, owner, space, created, command_id=command_id, current_value="12")
    assert again["revision"] == updated["revision"]
    with pytest.raises(CommandError) as error:
        save_fields(service, owner, space, created, current_value="13")
    assert error.value.status == 409
    # The old system gains fields without losing its goal.
    grown = save_fields(service, owner, space, old, area="Audience", skills=["humanizer"])
    assert grown["goal_metric"] == "Median views" and grown["goal_target"] == "300"
    assert grown["area"] == "Audience" and grown["skills"] == ["humanizer"]

    # A null or empty value clears; a null status goes back to not_measured.
    updated = save_fields(service, owner, space, updated, status="healthy")
    cleared = save_fields(
        service,
        owner,
        space,
        updated,
        area="",
        machine=[],
        skills=None,
        project_id=None,
        stop_conditions=None,
        status=None,
        description="",
    )
    assert cleared["area"] == "" and cleared["machine"] == [] and cleared["skills"] == []
    assert cleared["project_id"] is None and cleared["stop_conditions"] == ""
    assert cleared["status"] == "not_measured" and cleared["description"] == ""
    assert cleared["name"] == "Outreach" and cleared["goal_metric"] == "Replies"
    assert cleared["controllable_inputs"] == FIELDS["controllable_inputs"]
    assert cleared["outcome_delay"] == FIELDS["outcome_delay"]
    assert cleared["current_value"] == "12"


def test_structured_fields_are_bounded_and_a_project_must_be_active(lab):
    from pydantic import ValidationError

    service, owner, space, system, _ = lab
    job = {"job_id": "cron-1"}
    for good in (
        {"area": "a" * 200, "outcome_delay": "d" * 200},
        {"controllable_inputs": "i" * 2000, "stop_conditions": "s" * 2000},
        {"machine": [job] * 50, "skills": [f"skill-{n}" for n in range(30)]},
        {"machine": [{"job_id": "j" * 100, "label": "l" * 200}], "skills": ["s" * 100]},
        {"machine": [{"job_id": "j", "label": None}]},
    ):
        systems.SystemData.model_validate({"name": "Ok"} | good)
    for bad in (
        {"area": "a" * 201},
        {"outcome_delay": "d" * 201},
        {"controllable_inputs": "i" * 2001},
        {"stop_conditions": "s" * 2001},
        {"machine": [job] * 51},
        {"machine": [{"label": "No job"}]},
        {"machine": [{"job_id": "  "}]},
        {"machine": [{"job_id": "j" * 101}]},
        {"machine": [{"job_id": "j", "label": "l" * 201}]},
        {"machine": [{"job_id": "j", "schedule": "daily"}]},
        {"machine": ["cron-1"]},
        {"skills": [f"skill-{n}" for n in range(31)]},
        {"skills": ["s" * 101]},
        {"skills": ["  "]},
        {"skills": "humanizer"},
        {"project_id": "not-a-uuid"},
    ):
        for model in (systems.SystemData, systems.SystemChange):
            with pytest.raises(ValidationError):
                model.model_validate({"name": "Ok"} | bad)
    with pytest.raises(ValidationError):
        systems.SystemData.model_validate({"name": None})
    # A create needs a name; an update may leave it out but never clears it.
    for request in (
        {"command_id": str(uuid4()), "expected_revision": 0, "data": {"area": "x"}},
        {
            "command_id": str(uuid4()),
            "system_id": system["id"],
            "expected_revision": system["revision"],
            "data": {"name": None},
        },
        {
            "command_id": str(uuid4()),
            "system_id": system["id"],
            "expected_revision": system["revision"],
            "data": {"name": "  "},
        },
    ):
        with pytest.raises(ValidationError, match="name is required"):
            systems.SaveSystem.model_validate(request)

    with pytest.raises(CommandError) as error:
        save_fields(service, owner, space, system, project_id=str(uuid4()))
    assert error.value.status == 404
    project = new_project(service, owner, space)
    workspace_items.visibility(
        service,
        owner,
        space,
        UUID(project["id"]),
        workspace_items.ChangeVisibility(
            command_id=uuid4(), expected_revision=project["revision"], deleted=True
        ),
    )
    with pytest.raises(CommandError, match="active project"):
        save_fields(service, owner, space, system, project_id=project["id"])


def test_structured_fields_survive_export_and_a_copy_points_at_the_copied_project(lab):
    from dataclasses import replace

    from test_restore_staging import stage
    from test_workspace_archive import read_archive

    from space import restore_merge as merge

    service, owner, space, _, _ = lab
    project = new_project(service, owner, space)
    system = save_fields(service, owner, space, name="Outreach", project_id=project["id"], **FIELDS)
    expected = {key: system[key] for key in systems.SUMMARY_FIELDS}
    archive = read_archive(service, owner, space)
    assert archive_schema.validate_archive(archive)

    target = replace(owner, subject=str(uuid4()))
    target_space = UUID(service.bootstrap(target)["id"])
    upload = stage(service, target, target_space, archive)
    review = merge.review(service, target, target_space, upload.id, merge.Review())
    merge.commit(
        service, target, target_space, upload.id, merge.Commit(snapshot=review["snapshot"])
    )
    by_name = {s["name"]: s for s in systems.overview(service, target, target_space)["systems"]}
    assert {key: by_name["Outreach"][key] for key in systems.SUMMARY_FIELDS} == expected

    keys = ["item:" + system["id"], "item:" + project["id"]]
    choices = dict.fromkeys(keys, "copy")
    upload = stage(service, owner, space, archive)
    review = merge.review(service, owner, space, upload.id, merge.Review(choices=choices))
    target_ids = {item["key"]: item["target_id"] for item in review["items"]}
    merge.commit(
        service, owner, space, upload.id, merge.Commit(snapshot=review["snapshot"], choices=choices)
    )
    copy = workspace_items.get(service, owner, space, UUID(target_ids["item:" + system["id"]]))
    assert copy["project_id"] == target_ids["item:" + project["id"]]
    assert copy["machine"] == expected["machine"] and copy["skills"] == expected["skills"]

    def broken(change):
        archive = read_archive(service, owner, space)
        for record in archive["entries"]:
            if record["value"]["id"] == system["id"]:
                change(record["value"])
        return archive

    # An archive written before the fields existed still validates.
    assert archive_schema.validate_archive(
        broken(lambda value: [value.pop(key) for key in systems.SUMMARY_FIELDS])
    )
    for change in (
        lambda value: value["machine"].append({"label": "No job"}),
        lambda value: value.update(machine="x-daily"),
        lambda value: value.update(skills=[{"name": "humanizer"}]),
        lambda value: value["machine"][0].pop("job_id"),
    ):
        with pytest.raises(ValueError):
            archive_schema.validate_archive(broken(change))
    with pytest.raises(ValueError, match="classification item"):
        archive_schema.validate_archive(broken(lambda value: value.update(project_id=str(uuid4()))))


def test_mcp_update_system_keeps_the_fields_it_is_not_sent(commands, signing):
    service = commands[0]
    with mcp_client(service, signing) as (owner, space, rpc, rest):
        data = {
            "name": "X audience",
            "goal_metric": "Median views",
            "counter_metric": "Unfollows",
            "current_value": "180",
            "machine": FIELDS["machine"],
        }
        created = rpc("create_system", {"command_id": str(uuid4()), "data": data})
        common = {"system_id": created["id"], "command_id": str(uuid4())}
        # The tool receives a SystemChange holding only current_value.
        updated = rpc(
            "update_system",
            {**common, "expected_revision": created["revision"], "data": {"current_value": "240"}},
        )
        assert updated["current_value"] == "240" and updated["revision"] == created["revision"] + 1
        for field in ("name", "goal_metric", "counter_metric", "machine"):
            assert updated[field] == created[field], field
        assert [job["job_id"] for job in updated["machine"]] == ["x-daily", "x-sensor"]
        # A field sent as null is cleared; the others still keep their values.
        cleared = rpc(
            "update_system",
            {
                "system_id": created["id"],
                "command_id": str(uuid4()),
                "expected_revision": updated["revision"],
                "data": {"counter_metric": None},
            },
        )
        assert not cleared["counter_metric"]
        assert cleared["current_value"] == "240" and cleared["machine"] == created["machine"]
        [listed_system] = rest("GET", "/systems")["systems"]
        assert listed_system["goal_metric"] == "Median views"
        assert listed_system["machine"] == created["machine"]
