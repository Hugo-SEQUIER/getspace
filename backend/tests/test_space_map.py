import json
from uuid import UUID, uuid4

from test_database_setup import database as database_fixture
from test_item_placement import archive_item, save
from test_workspace import command_database as command_database_fixture
from test_workspace import commands as commands_fixture
from test_workspace import delegated

from space import space_map, workspace_items
from space.space_map import MapRequest

database = database_fixture
command_database = command_database_fixture
commands = commands_fixture


def records_database(service, owner, space, **placement):
    return workspace_items.save(
        service,
        owner,
        space,
        workspace_items.SaveItem(
            command_id=uuid4(),
            item_id=uuid4(),
            expected_revision=0,
            kind="database",
            data={
                "name": "Leads",
                "fields": [{"id": "company", "name": "Company", "type": "text"}],
                "records": [
                    {
                        "id": "r1",
                        "name": "PRIVATE ROW 553",
                        "updatedAt": "2026-09-01T00:00:00Z",
                        "values": {"company": "PRIVATE VALUE 554"},
                    },
                ],
                **placement,
            },
        ),
    )


def read(service, identity, space, **request):
    return space_map.build(service, identity, space, MapRequest(**request))


def test_map_nests_containers_and_never_includes_records(commands):
    service, owner, space, _ = commands
    clients = save(service, owner, space, "section", name="Clients")
    work = save(service, owner, space, "section", name="alpha work")
    project = save(service, owner, space, "project", name="Predella", section_id=clients["id"])
    save(service, owner, space, "folder", name="Briefs", project_id=project["id"])
    save(service, owner, space, "folder", name="Loose", section_id=work["id"])
    records_database(service, owner, space, project_id=project["id"])
    save(service, owner, space, "database", name="Unplaced")
    save(service, owner, space, "category", name="Business")
    value = read(service, owner, space)
    assert [s["name"] for s in value["sections"]] == ["alpha work", "Clients"]
    [predella] = value["sections"][1]["projects"]
    assert predella["status"] == "todo" and predella["rules"] == []
    assert [f["name"] for f in predella["folders"]] == ["Briefs"]
    [leads] = predella["databases"]
    assert leads["records"] == 1
    assert leads["fields"] == [{"name": "Company", "type": "text"}]
    assert [f["name"] for f in value["sections"][0]["folders"]] == ["Loose"]
    assert [d["name"] for d in value["unsectioned"]["databases"]] == ["Unplaced"]
    assert [c["name"] for c in value["categories"]] == ["Business"]
    assert value["task_statuses"][0]["value"] == "todo"
    assert value["global_rules"] == [] and value["truncated"] is False
    assert "PRIVATE ROW 553" not in json.dumps(value)
    assert "PRIVATE VALUE 554" not in json.dumps(value)


def test_archived_items_are_hidden_and_children_marked(commands):
    service, owner, space, _ = commands
    section = save(service, owner, space, "section", name="Old area")
    project = save(service, owner, space, "project", name="Old project", section_id=section["id"])
    folder = save(service, owner, space, "folder", name="Kept", project_id=project["id"])
    archive_item(service, owner, space, project)
    archive_item(service, owner, space, folder | {"revision": 1})
    other = save(service, owner, space, "folder", name="Orphan", project_id=None)
    child = save(service, owner, space, "project", name="Child", section_id=section["id"])
    archive_item(service, owner, space, section)
    value = read(service, owner, space)
    assert value["sections"] == []
    [moved] = value["unsectioned"]["projects"]
    assert moved["id"] == child["id"] and moved["archived_parent"] is True
    assert [f["id"] for f in value["unsectioned"]["folders"]] == [other["id"]]


def test_budget_reduces_in_order_and_keeps_every_id_and_name(commands):
    service, owner, space, _ = commands
    long = "x" * 900
    names = [f"Project {n:02}" for n in range(30)]
    for name in names:
        project = save(service, owner, space, "project", name=name, description=long)
        records_database(service, owner, space, project_id=project["id"])
    value = read(service, owner, space, max_chars=4000)
    assert value["truncated"] is True
    listed = value["unsectioned"]["projects"]
    assert [p["name"] for p in listed] == names
    assert all(len(p["description"]) <= 120 for p in listed)
    assert all("fields" not in d for p in listed for d in p["databases"])
    assert read(service, owner, space, max_chars=4000) == value


def test_map_needs_only_read_access_and_info_points_to_it(commands):
    service, owner, space, _ = commands
    save(service, owner, space, "project", name="Visible")
    reader = delegated(service, owner)
    assert read(service, reader, space)["unsectioned"]["projects"][0]["name"] == "Visible"
    assert service.info(owner, UUID(str(space)))["space_map"] == {"tool": "get_space_map"}


def test_map_names_the_fields_of_a_task_database(commands):
    service, owner, space, _ = commands
    workspace_items.save(
        service,
        owner,
        space,
        workspace_items.SaveItem(
            command_id=uuid4(),
            item_id=uuid4(),
            expected_revision=0,
            kind="database",
            data={
                "name": "Launch tasks",
                "fields": [
                    {"id": "s", "name": "Status", "type": "select", "options": ["To do", "Done"]},
                    {"id": "p", "name": "Planned for", "type": "date"},
                ],
                "tasks": {"status": "s", "done": ["Done"], "planned": "p"},
            },
        ),
    )
    records_database(service, owner, space)
    listed = {d["name"]: d for d in read(service, owner, space)["unsectioned"]["databases"]}
    tasks, leads = listed["Launch tasks"], listed["Leads"]
    assert tasks["tasks"] == {"status": "Status", "done": ["Done"], "planned": "Planned for"}
    assert "tasks" not in leads
