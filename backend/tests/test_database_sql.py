from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from test_database_setup import database as database_fixture
from test_workspace import command_database as command_database_fixture
from test_workspace import commands as commands_fixture
from test_workspace import delegated

from space import database_sql, workspace_items
from space.database_sql import Query
from space.workspace import CommandError

database = database_fixture
command_database = command_database_fixture
commands = commands_fixture

STAMP = "2026-09-01T00:00:00Z"


def leads(service, owner, space):
    fields = [
        {"id": "company", "name": "Company", "type": "text"},
        {"id": "status", "name": "Status", "type": "select", "options": ["New", "Won"]},
        {"id": "value", "name": "Value", "type": "number"},
        {"id": "active", "name": "Active", "type": "checkbox"},
        {"id": "next", "name": "Next step", "type": "text"},
        {"id": "label", "name": "name", "type": "text"},
    ]
    records = [
        {
            "id": "r1",
            "name": "Acme",
            "notes": "",
            "updatedAt": STAMP,
            "values": {"company": "Acme Ltd", "status": "New", "value": 100, "active": True},
        },
        {
            "id": "r2",
            "name": "Bolt",
            "notes": "warm",
            "updatedAt": STAMP,
            "values": {
                "company": "Bolt SA",
                "status": "Won",
                "value": 250,
                "active": False,
                "label": "VIP",
            },
        },
        {
            "id": "r3",
            "name": "Cedar",
            "notes": "",
            "updatedAt": STAMP,
            "values": {"status": "New", "value": 40},
        },
    ]
    return workspace_items.save(
        service,
        owner,
        space,
        workspace_items.SaveItem(
            command_id=uuid4(),
            item_id=uuid4(),
            expected_revision=0,
            kind="database",
            data={"name": "Leads", "fields": fields, "records": records},
        ),
    )


def run(service, identity, space, db, sql=None, params=(), **request):
    if sql is not None:
        request |= {"sql": sql, "params": list(params)}
    return database_sql.execute(service, identity, space, UUID(db["id"]), Query(**request))


def write(service, identity, space, db, sql=None, params=(), **request):
    request.setdefault("command_id", uuid4())
    request.setdefault("expected_revision", db["revision"])
    return run(service, identity, space, db, sql, params, **request)


def test_select_filters_aggregates_quoted_names_and_schema(commands):
    service, owner, space, _ = commands
    db = leads(service, owner, space)
    result = run(
        service,
        owner,
        space,
        db,
        'SELECT name, "Next step", field_name, Active FROM records WHERE Value > ? ORDER BY Value',
        [50],
        include_schema=True,
    )
    [rows] = result["results"]
    assert rows["columns"] == ["name", "Next step", "field_name", "Active"]
    assert rows["rows"] == [["Acme", None, None, 1], ["Bolt", None, "VIP", 0]]
    assert result["changes"] is None and result["database"]["revision"] == db["revision"]
    assert {"column": "field_name", "field_id": "label", "type": "text", "options": []} in (
        result["schema"]
    )
    total = run(service, owner, space, db, "SELECT sum(Value), count(*) FROM records")
    assert total["results"][0]["rows"] == [[390.0, 3]]
    capped = run(service, owner, space, db, "SELECT id FROM records", max_rows=2)
    assert capped["results"][0]["truncated"] and capped["results"][0]["row_count"] == 3


def test_writes_need_a_command_and_never_change_unrelated_rows(commands):
    service, owner, space, _ = commands
    db = leads(service, owner, space)
    with pytest.raises(CommandError, match="not authorized"):
        run(service, owner, space, db, "DELETE FROM records")
    result = write(
        service,
        owner,
        space,
        db,
        "INSERT INTO records (name, Status, Value) VALUES (?, 'New', 10) RETURNING id",
        ["Delta"],
    )
    assert result["changes"] == {"inserted": 1, "updated": 0, "deleted": 0}
    new_id = result["results"][0]["rows"][0][0]
    saved = workspace_items.get(service, owner, space, UUID(db["id"]))
    assert saved["revision"] == db["revision"] + 1
    by_id = {r["id"]: r for r in saved["records"]}
    assert [r["id"] for r in saved["records"]][:3] == ["r1", "r2", "r3"]
    assert by_id["r1"] == db["records"][0]
    inserted = next(r for r in saved["records"] if r["id"] not in {"r1", "r2", "r3"})
    assert inserted["id"] == new_id
    assert inserted["createdAt"] == inserted["updatedAt"] != STAMP
    updated = write(
        service,
        owner,
        space,
        saved,
        "UPDATE records SET Status='Won', Active=1 WHERE id='r3'",
    )
    assert updated["changes"] == {"inserted": 0, "updated": 1, "deleted": 0}
    current = workspace_items.get(service, owner, space, UUID(db["id"]))
    r3 = next(r for r in current["records"] if r["id"] == "r3")
    assert r3["values"] == {"status": "Won", "value": 40, "active": True}
    assert r3["updatedAt"] != STAMP
    assert next(r for r in current["records"] if r["id"] == "r1")["updatedAt"] == STAMP
    deleted = write(service, owner, space, current, "DELETE FROM records WHERE Value < 50")
    assert deleted["changes"]["deleted"] == 2


@pytest.mark.parametrize(
    ("sql", "match"),
    [
        ("UPDATE records SET id='x'", "not authorized"),
        ("UPDATE records SET updated_at='x'", "not authorized"),
        ("UPDATE records SET Status='Lost'", "Unknown select option"),
        ("UPDATE records SET Active=2", "expects 0 or 1"),
        ("UPDATE records SET Value='lots'", "expects a number"),
        ("UPDATE records SET name=NULL", "NOT NULL"),
    ],
)
def test_invalid_writes_are_rejected_whole(commands, sql, match):
    service, owner, space, _ = commands
    db = leads(service, owner, space)
    with pytest.raises(CommandError, match=match):
        write(service, owner, space, db, sql)
    assert workspace_items.get(service, owner, space, UUID(db["id"]))["revision"] == db["revision"]


def test_batches_see_earlier_changes_and_fail_atomically(commands):
    service, owner, space, _ = commands
    db = leads(service, owner, space)
    batch = write(
        service,
        owner,
        space,
        db,
        statements=[
            {"sql": "UPDATE records SET Status='Won' WHERE id='r1'"},
            {"sql": "INSERT INTO records (id, name) VALUES ('r4', 'Echo')"},
            {"sql": "SELECT count(*) FROM records WHERE Status='Won'"},
        ],
    )
    assert batch["results"][2]["rows"] == [[2]]
    assert batch["changes"] == {"inserted": 1, "updated": 1, "deleted": 0}
    current = workspace_items.get(service, owner, space, UUID(db["id"]))
    assert current["revision"] == db["revision"] + 1
    with pytest.raises(CommandError, match="Statement 1"):
        write(
            service,
            owner,
            space,
            current,
            statements=[
                {"sql": "DELETE FROM records"},
                {"sql": "SELECT nope FROM records"},
            ],
        )
    assert len(workspace_items.get(service, owner, space, UUID(db["id"]))["records"]) == 4
    gone = write(
        service,
        owner,
        space,
        current,
        statements=[
            {"sql": "INSERT INTO records (id, name) VALUES ('tmp', 'Temp')"},
            {"sql": "DELETE FROM records WHERE id='tmp'"},
        ],
    )
    assert gone["changes"] is None and gone["database"]["revision"] == current["revision"]


def test_retry_replays_the_committed_revision_and_stale_revisions_conflict(commands):
    service, owner, space, _ = commands
    db = leads(service, owner, space)
    command = uuid4()
    delete = "DELETE FROM records WHERE id='r3'"
    first = write(service, owner, space, db, delete, command_id=command)
    again = write(service, owner, space, db, delete, command_id=command)
    assert again == {
        "database": first["database"],
        "results": [],
        "changes": None,
        "replayed": True,
    }
    with pytest.raises(CommandError) as error:
        write(service, owner, space, db, "DELETE FROM records WHERE id='r2'", command_id=command)
    assert error.value.status == 409


def test_read_grants_cannot_write_and_archived_databases_refuse_writes(commands):
    service, owner, space, _ = commands
    db = leads(service, owner, space)
    reader = delegated(service, owner)
    assert run(service, reader, space, db, "SELECT count(*) FROM records")["results"]
    with pytest.raises(CommandError) as error:
        write(service, reader, space, db, "DELETE FROM records")
    assert error.value.status == 403
    archived = workspace_items.visibility(
        service,
        owner,
        space,
        UUID(db["id"]),
        workspace_items.ChangeVisibility(command_id=uuid4(), expected_revision=1, deleted=True),
    )
    with pytest.raises(CommandError, match="Restore"):
        write(service, owner, space, archived, "DELETE FROM records")


@pytest.mark.parametrize(
    "sql",
    [
        "ATTACH DATABASE ':memory:' AS other",
        "PRAGMA table_info(records)",
        "CREATE TABLE other (x)",
        "DROP TABLE records",
        "BEGIN",
        "SELECT * FROM sqlite_master",
        "SELECT zeroblob(1000000000)",
        "SELECT load_extension('x')",
        "SELECT 1; SELECT 2",
        "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n) SELECT count(*) FROM n",
        "SELECT length(group_concat(printf('%900000s', id))) FROM records",
    ],
)
def test_sandbox_refuses_everything_outside_one_table(commands, sql):
    service, owner, space, _ = commands
    db = leads(service, owner, space)
    with pytest.raises(CommandError) as error:
        write(service, owner, space, db, sql)
    assert error.value.status == 400
    assert workspace_items.get(service, owner, space, UUID(db["id"]))["revision"] == db["revision"]


def test_requests_and_listings_are_bounded(commands):
    service, owner, space, _ = commands
    db = leads(service, owner, space)
    for invalid in (
        {},
        {"sql": "SELECT 1", "statements": [{"sql": "SELECT 1"}]},
        {"sql": "SELECT 1", "command_id": str(uuid4())},
        {"statements": [{"sql": "SELECT 1"}] * 21},
    ):
        with pytest.raises(ValidationError):
            Query(**invalid)
    listed = workspace_items.listing(service, owner, space, "database", records=False)
    [item] = listed["items"]
    assert "records" not in item and item["record_count"] == 3
    assert item["fields"] == db["fields"]
    full = workspace_items.listing(service, owner, space, "database")
    assert len(full["items"][0]["records"]) == 3


def test_sql_writes_reach_the_webhook_outbox_once_per_changed_record(commands, monkeypatch):
    from test_webhooks import event_payloads, saved_database, subscribe

    service, owner, space, _ = commands
    request = saved_database(service, owner, space)
    subscribe(service, owner, space, request.item_id, monkeypatch, transition=False)
    db = workspace_items.get(service, owner, space, request.item_id)
    write(
        service,
        owner,
        space,
        db,
        statements=[
            {"sql": "UPDATE records SET Stage='Review' WHERE id='selected'"},
            {"sql": "INSERT INTO records (id, name) VALUES ('tmp', 'Temp')"},
            {"sql": "DELETE FROM records WHERE id='tmp'"},
        ],
    )
    events = event_payloads(service, owner, space)
    assert [event["record_id"] for event in events] == ["selected"]
