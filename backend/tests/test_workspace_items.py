from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_auth import signing as signing_fixture
from test_database_setup import database as database_fixture
from test_workspace import change, delegated
from test_workspace import command_database as command_database_fixture
from test_workspace import commands as commands_fixture

from space import workspace_items as items
from space.app import create_app
from space.settings import Settings
from space.workspace import CommandError

database = database_fixture
command_database = command_database_fixture
commands = commands_fixture
signing = signing_fixture


def project(**changes):
    return items.SaveItem(
        command_id=uuid4(),
        item_id=uuid4(),
        expected_revision=0,
        kind="project",
        data={"name": "PRIVATE PROJECT 731", "description": "Launch", "status": "todo"},
    ).model_copy(update=changes)


def database_data():
    return {
        "name": "Clients",
        "description": "PRIVATE DATABASE 731",
        "view": "table",
        "groupBy": "stage",
        "fields": [
            {"id": "stage", "name": "Stage", "type": "select", "options": ["New", "Won"]},
            {"id": "amount", "name": "Amount", "type": "number"},
            {"id": "paid", "name": "Paid", "type": "checkbox"},
            {"id": "date", "name": "Follow-up", "type": "date"},
        ],
        "records": [
            {
                "id": "record-1",
                "name": "PRIVATE CLIENT 915",
                "notes": "Notes",
                "updatedAt": "2026-09-15T10:00:00Z",
                "values": {"stage": "New", "amount": 0, "paid": False, "date": "2026-09-15"},
            }
        ],
    }


def test_projects_and_databases_are_persistent_and_versioned(commands):
    service, owner, space, _ = commands
    request = items.SaveItem(
        command_id=uuid4(),
        item_id=uuid4(),
        expected_revision=0,
        kind="database",
        data=database_data(),
    )
    first = items.save(service, owner, space, request)
    assert first["records"][0]["values"]["amount"] == 0
    assert first["records"][0]["values"]["paid"] is False
    assert items.save(service, owner, space, request) == first
    updated = request.model_copy(
        update={
            "expected_revision": 1,
            "command_id": uuid4(),
            "data": {**request.data, "name": "Customers"},
        }
    )
    second = items.save(service, owner, space, updated)
    assert second["revision"] == 2
    assert items.get(service, owner, space, request.item_id, 1)["name"] == "Clients"
    assert items.listing(service, owner, space, "database")["items"] == [second]
    with pytest.raises(CommandError, match="changed"):
        items.save(service, owner, space, updated.model_copy(update={"command_id": uuid4()}))
    with pytest.raises(CommandError, match="already used"):
        items.save(service, owner, space, request.model_copy(update={"data": updated.data}))
    with service.db.transaction(owner) as runtime:
        service._scope(runtime, owner, space)
        payloads = runtime.execute(
            "SELECT encrypted_payload FROM space.workspace_item_revisions"
        ).fetchall()
        assert any(b"PRIVATE" in bytes(row["encrypted_payload"]) for row in payloads)


def test_item_scope_grants_and_revocation_match_resources(commands):
    service, owner, space, _ = commands
    other = replace(owner, subject=str(uuid4()))
    other_space = UUID(service.bootstrap(other)["id"])
    writer = delegated(service, owner, write=True)
    reader = delegated(service, owner)
    request = project()
    saved = items.save(service, writer, space, request)
    assert saved["author_agent"]["id"] == writer.agent_id
    assert items.get(service, reader, space, request.item_id) == saved
    for actor, selected_space in ((other, other_space), (reader, space)):
        with pytest.raises(CommandError):
            items.save(service, actor, space, project())
        if actor is other:
            with pytest.raises(CommandError):
                items.get(service, actor, selected_space, request.item_id)
    service.revoke(owner, UUID(writer.client_id))
    with pytest.raises(CommandError):
        items.get(service, writer, space, request.item_id)


def test_simultaneous_edits_have_one_winner_and_trash_is_recoverable(commands):
    service, owner, space, _ = commands
    request = project()
    items.save(service, owner, space, request)

    def attempt(_):
        try:
            return items.save(
                service,
                owner,
                space,
                request.model_copy(update={"expected_revision": 1, "command_id": uuid4()}),
            )["revision"]
        except CommandError as error:
            return error.status

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, range(2))) == [2, 409]
    deleted = items.visibility(
        service,
        owner,
        space,
        request.item_id,
        items.ChangeVisibility(command_id=uuid4(), expected_revision=2, deleted=True),
    )
    assert deleted["deleted"] and items.listing(service, owner, space, "project")["items"] == []
    assert items.listing(service, owner, space, "project", deleted=True)["items"] == [deleted]
    restored = items.visibility(
        service,
        owner,
        space,
        request.item_id,
        items.ChangeVisibility(command_id=uuid4(), expected_revision=3, deleted=False),
    )
    assert not restored["deleted"] and restored["revision"] == 4
    assert items.get(service, owner, space, request.item_id, 1)["name"] == restored["name"]


def test_project_links_persist_and_cannot_reference_another_space(commands):
    service, owner, space, _ = commands
    item = items.save(service, owner, space, project())
    saved = service.save(owner, space, change(project=item["id"], priority="High"))
    assert saved["project"] == item["id"] and saved["priority"] == "High"
    updated = service.save(
        owner,
        space,
        change().model_copy(update={"resource_id": UUID(saved["id"]), "expected_revision": 1}),
    )
    assert updated["project"] == item["id"] and updated["priority"] == "High"
    with pytest.raises(CommandError, match="Project not found"):
        service.save(owner, space, change(project=uuid4()))


@pytest.mark.parametrize(
    "field,value",
    [
        ("amount", True),
        ("paid", "false"),
        ("amount", float("inf")),
        ("stage", "Missing"),
        ("date", "2026-99-99"),
    ],
)
def test_database_record_types_are_validated_before_storage(field, value):
    data = database_data()
    data["records"][0]["values"][field] = value
    with pytest.raises(ValidationError):
        items.DatabaseData.model_validate(data)


def test_database_schema_rejects_duplicate_fields_records_and_unknown_values():
    for mutate in (
        lambda data: data["fields"].append(data["fields"][0]),
        lambda data: data["records"].append(data["records"][0]),
        lambda data: data["records"][0]["values"].update({"missing": "data"}),
        lambda data: data.update(groupBy="amount"),
    ):
        data = database_data()
        mutate(data)
        with pytest.raises(ValidationError):
            items.DatabaseData.model_validate(data)


def test_database_date_added_round_trip_and_legacy_records():
    data = database_data()
    assert items.DatabaseData.model_validate(data).records[0].createdAt is None
    data["records"][0]["createdAt"] = "2026-09-01T09:00:00Z"
    saved = items.DatabaseData.model_validate(data).model_dump(mode="json")
    saved["records"][0]["updatedAt"] = "2026-10-01T09:00:00Z"
    assert items.DatabaseData.model_validate(saved).records[0].createdAt == "2026-09-01T09:00:00Z"
    data["records"][0]["createdAt"] = "invalid"
    with pytest.raises(ValidationError):
        items.DatabaseData.model_validate(data)


@pytest.mark.parametrize(
    "color", ["#62675c", "#456a9c", "#86692e", "#76549b", "#3d806b", "#9a536b"]
)
def test_database_select_colors_are_optional_and_preserve_options(color):
    data = database_data()
    legacy = items.DatabaseData.model_validate(data).model_dump(mode="json")
    assert all("optionColors" not in field for field in legacy["fields"])
    data["fields"][0]["optionColors"] = {}
    assert items.DatabaseData.model_validate(data).model_dump(mode="json") == legacy
    data["fields"][0]["optionColors"] = {"New": color}
    saved = items.DatabaseData.model_validate(data).model_dump(mode="json")
    assert saved["fields"][0] == legacy["fields"][0] | {"optionColors": {"New": color}}
    assert saved["records"] == legacy["records"]
    assert items.DatabaseData.model_validate(saved).model_dump(mode="json") == saved


@pytest.mark.parametrize(
    "changes,match",
    [
        (
            {"type": "text", "options": [], "optionColors": {"New": "#62675c"}},
            "Only select fields have option colors",
        ),
        ({"optionColors": {"Missing": "#62675c"}}, "known select options"),
        ({"optionColors": {"new": "#62675c"}}, "known select options"),
        ({"optionColors": {" New ": "#62675c"}}, "known select options"),
        ({"optionColors": {"New": "#ffffff"}}, "Input should be"),
        ({"optionColors": {"New": "#456A9C"}}, "Input should be"),
        ({"optionColors": {"New": "red"}}, "Input should be"),
        ({"optionColors": {str(i): "#62675c" for i in range(31)}}, "at most 30"),
        ({"options": ["New", "new"]}, "distinct"),
        ({"options": []}, "1–30 named options"),
        ({"options": [" "]}, "1–30 named options"),
        ({"options": ["x" * 81]}, "1–30 named options"),
        ({"options": [str(i) for i in range(31)]}, "at most 30"),
    ],
)
def test_database_select_options_and_colors_are_validated(changes, match):
    data = database_data()
    data["fields"][0].update(changes)
    with pytest.raises(ValidationError, match=match):
        items.SaveItem(
            command_id=uuid4(), item_id=uuid4(), expected_revision=0, kind="database", data=data
        )


def test_rest_text_to_select_preserves_values_colors_and_access_guards(commands, signing):
    service = commands[0]
    verifier, token = signing
    bearer = token()
    owner = verifier.verify(bearer)
    space = UUID(service.bootstrap(owner)["id"])
    settings = Settings(environment="test", allowed_hosts=["testserver"])
    auth = {"Authorization": "Bearer " + bearer}
    endpoint = f"/v1/spaces/{space}/items"
    data = database_data()
    data["fields"][0].update(type="text", options=[])
    data["groupBy"] = None
    body = {
        "command_id": str(uuid4()),
        "item_id": str(uuid4()),
        "expected_revision": 0,
        "kind": "database",
        "data": data,
    }
    with TestClient(create_app(settings, workspace=service, verifier=verifier)) as http:
        response = http.post(endpoint, headers=auth, json=body)
        assert response.status_code == 200, response.text
        first = response.json()
        converted = deepcopy(data)
        converted["fields"][0].update(
            type="select",
            options=["New", "Won"],
            optionColors={"New": "#456a9c", "Won": "#3d806b"},
        )
        converted["groupBy"] = "stage"
        body.update(command_id=str(uuid4()), expected_revision=1, data=converted)
        response = http.post(endpoint, headers=auth, json=body)
        assert response.status_code == 200, response.text
        saved = response.json()
        assert saved["id"] == first["id"] and saved["revision"] == 2
        assert [field["id"] for field in saved["fields"]] == [
            field["id"] for field in first["fields"]
        ]
        assert saved["records"] == first["records"]
        assert saved["fields"][0]["optionColors"] == converted["fields"][0]["optionColors"]
        assert http.get(endpoint + f"/{saved['id']}", headers=auth).json() == saved
        assert http.get(endpoint + f"/{saved['id']}?revision=1", headers=auth).json() == first
        assert http.get(endpoint + "?kind=database", headers=auth).json()["items"] == [saved]
        response = http.post(endpoint, headers=auth, json=body | {"command_id": str(uuid4())})
        assert response.status_code == 409
        assert http.get(endpoint + f"/{saved['id']}", headers=auth).json() == saved

    request = items.SaveItem.model_validate(
        body | {"command_id": str(uuid4()), "expected_revision": 2}
    )
    reader = delegated(service, owner)
    assert items.get(service, reader, space, request.item_id) == saved
    with pytest.raises(CommandError) as error:
        items.save(service, reader, space, request)
    assert error.value.status == 403
    other = replace(owner, subject=str(uuid4()))
    other_space = UUID(service.bootstrap(other)["id"])
    with pytest.raises(CommandError) as error:
        items.save(service, other, space, request)
    assert error.value.status == 404
    with pytest.raises(CommandError) as error:
        items.get(service, other, other_space, request.item_id)
    assert error.value.status == 404
    assert items.get(service, owner, space, request.item_id) == saved


@pytest.mark.parametrize("field_index,options", [(0, ["Won"]), (1, ["0"]), (2, ["false"])])
def test_rest_select_conversion_rejects_incompatible_values(
    commands, signing, field_index, options
):
    service = commands[0]
    verifier, token = signing
    bearer = token()
    owner = verifier.verify(bearer)
    space = UUID(service.bootstrap(owner)["id"])
    settings = Settings(environment="test", allowed_hosts=["testserver"])
    auth = {"Authorization": "Bearer " + bearer}
    endpoint = f"/v1/spaces/{space}/items"
    data = database_data()
    data["fields"][0].update(type="text", options=[])
    data["groupBy"] = None
    body = {
        "command_id": str(uuid4()),
        "item_id": str(uuid4()),
        "expected_revision": 0,
        "kind": "database",
        "data": data,
    }
    with TestClient(create_app(settings, workspace=service, verifier=verifier)) as http:
        response = http.post(endpoint, headers=auth, json=body)
        assert response.status_code == 200, response.text
        first = response.json()
        converted = deepcopy(data)
        converted["fields"][field_index].update(
            type="select", options=options, optionColors={options[0]: "#62675c"}
        )
        body.update(command_id=str(uuid4()), expected_revision=1, data=converted)
        response = http.post(endpoint, headers=auth, json=body)
        assert response.status_code == 422
        assert http.get(endpoint + f"/{first['id']}", headers=auth).json() == first


def task_database_data():
    data = database_data()
    data["fields"][0]["options"] = ["New", "Doing", "Won"]
    data["tasks"] = {"status": "stage", "done": ["Won"], "planned": "date"}
    return data


def test_task_tracking_round_trips_and_is_omitted_when_off():
    saved = items.DatabaseData.model_validate(task_database_data()).model_dump(mode="json")
    assert saved["tasks"] == {"status": "stage", "done": ["Won"], "planned": "date"}
    assert "tasks" not in items.DatabaseData.model_validate(database_data()).model_dump(mode="json")


@pytest.mark.parametrize(
    "tracking",
    [
        {"status": "amount", "done": ["Won"], "planned": "date"},
        {"status": "missing", "done": ["Won"], "planned": "date"},
        {"status": "stage", "done": ["Lost"], "planned": "date"},
        {"status": "stage", "done": ["Won", "Won"], "planned": "date"},
        {"status": "stage", "done": [], "planned": "date"},
        {"status": "stage", "done": ["Won"], "planned": "paid"},
        {"status": "stage", "done": ["Won"], "planned": "missing"},
    ],
)
def test_task_tracking_must_name_a_status_select_and_a_date_field(tracking):
    data = task_database_data()
    data["tasks"] = tracking
    with pytest.raises(ValidationError):
        items.DatabaseData.model_validate(data)


def test_task_tracking_fails_when_its_field_is_removed():
    data = task_database_data()
    data["fields"] = [f for f in data["fields"] if f["id"] != "date"]
    del data["records"][0]["values"]["date"]
    with pytest.raises(ValidationError):
        items.DatabaseData.model_validate(data)
