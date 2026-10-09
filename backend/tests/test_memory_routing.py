from uuid import UUID, uuid4

import pytest
from test_database_setup import database as database_fixture
from test_item_placement import archive_item, save
from test_memora_pipeline import Models
from test_restore_staging import stage
from test_workspace import command_database as command_database_fixture
from test_workspace import commands as commands_fixture
from test_workspace_archive import read_archive

from space import archive_schema, restore_merge, space_map, workspace_items
from space.memory import ChangeMemory, RememberClaims, RememberMemory
from space.space_map import MapRequest
from space.workspace import CommandError

database = database_fixture
command_database = command_database_fixture
commands = commands_fixture


def claims(service, owner, space, *pairs):
    return service.remember_claims(
        owner,
        space,
        RememberClaims(
            command_id=uuid4(),
            title="Placement",
            claims=[
                {"index": text[:40], "value": text, **({"routes_to": targets} if targets else {})}
                for text, targets in pairs
            ],
        ),
    )


def mapped(service, owner, space):
    return space_map.build(service, owner, space, MapRequest())


def rules_on(value, name):
    for entry in space_map.containers(value):
        if entry["name"] == name:
            return [rule["text"] for rule in entry["rules"]]
    raise AssertionError(name)


def route(service, owner, space, memory, claim_index, targets):
    return service.change_memory(
        owner,
        space,
        ChangeMemory(
            command_id=uuid4(),
            resource_id=UUID(memory["id"]),
            expected_revision=memory["revision"],
            entry_id=memory["memory"]["claims"][claim_index]["id"],
            action="route",
            routes_to=targets,
        ),
    )


def setup(commands):
    service, owner, space, _ = commands
    service.memory_models = Models()
    outreach = save(service, owner, space, "database", name="Clients & Outreach")
    experiments = save(service, owner, space, "database", name="Outreach Experiments")
    return service, owner, space, outreach, experiments


def test_explicit_rules_sit_on_their_containers_in_the_map(commands):
    service, owner, space, outreach, experiments = setup(commands)
    saved = claims(
        service,
        owner,
        space,
        (
            "Outreach contacts go in Clients & Outreach, never in experiments.",
            [outreach["id"], experiments["id"]],
        ),
        ("Mira travels by car.", []),
    )
    first, second = saved["memory"]["claims"]
    assert first["routing"] == {
        "target_ids": [outreach["id"], experiments["id"]],
        "method": "explicit",
    }
    assert "routing" not in second
    value = mapped(service, owner, space)
    expected = ["Outreach contacts go in Clients & Outreach, never in experiments."]
    assert rules_on(value, "Clients & Outreach") == expected
    assert rules_on(value, "Outreach Experiments") == expected
    [rule] = next(e for e in space_map.containers(value) if e["id"] == outreach["id"])["rules"]
    assert rule["origin"] == "explicit" and rule["stale"] is False
    assert rule["memory_id"] == saved["id"] and rule["entry_id"] == first["id"]
    remembered = service.remember(
        owner,
        space,
        RememberMemory(
            command_id=uuid4(),
            title="Rule",
            text="Log cohorts in experiments.",
            routes_to=[experiments["id"]],
        ),
    )
    assert all(
        c["routing"]["target_ids"] == [experiments["id"]] for c in remembered["memory"]["claims"]
    )
    assert rules_on(mapped(service, owner, space), "Outreach Experiments")[0] == (
        "Log cohorts in experiments."
    )


@pytest.mark.parametrize("invalid", ["missing", "category", "archived", "foreign"])
def test_invalid_targets_are_rejected_before_any_provider_work(commands, invalid):
    service, owner, space, outreach, _ = setup(commands)
    if invalid == "missing":
        target = str(uuid4())
    elif invalid == "category":
        target = save(service, owner, space, "category", name="Business")["id"]
    elif invalid == "archived":
        target = archive_item(service, owner, space, outreach)["id"]
    else:
        from space.security.auth import Identity

        other = Identity("https://identity.invalid", str(uuid4()), 4102444800)
        other_space = UUID(service.bootstrap(other)["id"])
        target = save(service, other, other_space, "database", name="Foreign")["id"]
    with pytest.raises(CommandError) as error:
        claims(service, owner, space, ("A rule", [target]))
    assert error.value.status == 404
    assert service.memory_models.calls == []


def test_route_action_replaces_and_clears_and_forget_removes_rules(commands):
    service, owner, space, outreach, experiments = setup(commands)
    saved = claims(service, owner, space, ("Contacts live in outreach.", [outreach["id"]]))
    moved = route(service, owner, space, saved, 0, [experiments["id"]])
    value = mapped(service, owner, space)
    assert rules_on(value, "Clients & Outreach") == []
    assert rules_on(value, "Outreach Experiments") == ["Contacts live in outreach."]
    cleared = route(service, owner, space, moved, 0, [])
    assert cleared["memory"]["claims"][0]["routing"] == {"target_ids": [], "method": "manual"}
    assert rules_on(mapped(service, owner, space), "Outreach Experiments") == []
    again = route(service, owner, space, cleared, 0, [outreach["id"]])
    service.change_memory(
        owner,
        space,
        ChangeMemory(
            command_id=uuid4(),
            resource_id=UUID(again["id"]),
            expected_revision=again["revision"],
            entry_id=again["memory"]["claims"][0]["id"],
            action="forget",
        ),
    )
    assert rules_on(mapped(service, owner, space), "Clients & Outreach") == []
    with pytest.raises(ValueError):
        ChangeMemory(
            command_id=uuid4(),
            resource_id=uuid4(),
            expected_revision=1,
            entry_id=uuid4(),
            action="edit",
            value="x",
            routes_to=[],
        )


def test_archived_containers_hide_rules_until_restored_and_rules_cap_at_five(commands):
    service, owner, space, outreach, _ = setup(commands)
    for n in range(7):
        claims(service, owner, space, (f"Outreach rule {n}", [outreach["id"]]))
    assert rules_on(mapped(service, owner, space), "Clients & Outreach") == [
        f"Outreach rule {n}" for n in (6, 5, 4, 3, 2)
    ]
    archived = archive_item(service, owner, space, outreach)
    remaining = space_map.containers(mapped(service, owner, space))
    assert all(e["id"] != outreach["id"] for e in remaining)
    workspace_items.visibility(
        service,
        owner,
        space,
        UUID(outreach["id"]),
        workspace_items.ChangeVisibility(
            command_id=uuid4(), expected_revision=archived["revision"], deleted=False
        ),
    )
    assert len(rules_on(mapped(service, owner, space), "Clients & Outreach")) == 5


def test_copy_restore_remaps_routing_targets_and_rebuilds_the_index(commands):
    service, owner, space, outreach, _ = setup(commands)
    saved = claims(service, owner, space, ("Contacts live in outreach.", [outreach["id"]]))
    exported = read_archive(service, owner, space)
    assert archive_schema.validate_archive(exported)
    broken = read_archive(service, owner, space)
    for entry in broken["entries"]:
        for claim in entry["value"].get("_memory", {}).get("claims", []):
            if claim.get("routing"):
                claim["routing"]["target_ids"] = [str(uuid4())]
    with pytest.raises(ValueError, match="routing target"):
        archive_schema.validate_archive(broken)
    upload = stage(service, owner, space, exported)
    choices = {"item:" + outreach["id"]: "copy", "resource:" + saved["id"]: "copy"}
    reviewed = restore_merge.review(
        service, owner, space, upload.id, restore_merge.Review(choices=choices)
    )
    mappings = {value["id"]: value["target_id"] for value in reviewed["items"]}
    restore_merge.commit(
        service,
        owner,
        space,
        upload.id,
        restore_merge.Commit(snapshot=reviewed["snapshot"], choices=choices),
    )
    copied = service.get_resource(owner, space, UUID(mappings[saved["id"]]))
    target = mappings[outreach["id"]]
    assert copied["memory"]["claims"][0]["routing"]["target_ids"] == [target]
    value = mapped(service, owner, space)
    copy_rules = next(e for e in space_map.containers(value) if e["id"] == target)["rules"]
    assert [r["memory_id"] for r in copy_rules] == [mappings[saved["id"]]]
