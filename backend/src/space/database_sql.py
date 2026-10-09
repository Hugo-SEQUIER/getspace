"""SQL over one database snapshot, in a throwaway in-memory SQLite table.

A database's records are stored as one JSON revision, not as PostgreSQL rows. Each call
loads one snapshot, runs agent SQL under a deny-by-default authorizer and discards the
SQLite database. Writes are validated as a whole snapshot and saved as one revision.
"""

import hmac
import json
import sqlite3
import time
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import Field, ValidationError, model_validator

from space import workspace_items
from space.memory import Input

MIN_SQLITE = (3, 35, 0)
MAX_RESULT_CHARS = 20_000
MAX_STATEMENT_CHARS = 10_000
MAX_STATEMENT_PARAMS = 100
MAX_BATCH = 20
MAX_BATCH_CHARS = 20_000
MAX_BATCH_PARAMS = 200
MAX_STRING_BYTES = 1_000_000
# Progress callbacks every 1,000 virtual machine steps; this stops runaway queries.
PROGRESS_STEPS = 1_000
MAX_PROGRESS_CALLS = 5_000
MAX_SECONDS = 0.5
BUILT_IN = ("id", "name", "notes", "created_at", "updated_at")
READ_ONLY = {"id", "created_at", "updated_at"}
# fmt: off
ALLOWED_FUNCTIONS = {
    # Core scalar functions, without blob generators or extension loading.
    "abs", "char", "coalesce", "concat", "concat_ws", "format", "glob", "hex", "iif",
    "ifnull", "instr", "length", "like", "lower", "ltrim", "max", "min", "nullif",
    "octet_length", "printf", "quote", "replace", "round", "rtrim", "sign", "substr",
    "substring", "trim", "typeof", "unhex", "unicode", "upper",
    # Math.
    "acos", "asin", "atan", "atan2", "ceil", "ceiling", "cos", "exp", "floor", "ln", "log",
    "log10", "log2", "mod", "pi", "pow", "power", "sin", "sqrt", "tan", "trunc",
    # Date and time.
    "date", "datetime", "julianday", "strftime", "time", "unixepoch", "timediff",
    # Aggregates and windows.
    "avg", "count", "group_concat", "string_agg", "sum", "total", "row_number", "rank",
    "dense_rank", "percent_rank", "cume_dist", "ntile", "lag", "lead", "first_value",
    "last_value", "nth_value",
    # JSON.
    "json", "json_array", "json_array_length", "json_extract", "json_group_array",
    "json_group_object", "json_object", "json_quote", "json_type", "json_valid",
    "json_each", "json_tree",
}
# fmt: on


class Statement(Input):
    sql: str = Field(min_length=1, max_length=MAX_STATEMENT_CHARS)
    params: list[str | int | float | bool | None] = Field(
        default_factory=list, max_length=MAX_STATEMENT_PARAMS
    )


class Query(Input):
    """One statement (sql, params) or a batch (statements) against one database."""

    sql: str | None = Field(default=None, min_length=1, max_length=MAX_STATEMENT_CHARS)
    params: list[str | int | float | bool | None] = Field(
        default_factory=list, max_length=MAX_STATEMENT_PARAMS
    )
    statements: list[Statement] | None = Field(default=None, min_length=1, max_length=MAX_BATCH)
    command_id: UUID | None = None
    expected_revision: int | None = Field(default=None, ge=1, strict=True)
    max_rows: int = Field(default=100, ge=1, le=500)
    include_schema: bool = False

    @model_validator(mode="after")
    def valid(self):
        if (self.sql is None) == (self.statements is None):
            raise ValueError("Pass either sql or statements")
        if self.sql is None and self.params:
            raise ValueError("Batch parameters belong to each statement")
        if (self.command_id is None) != (self.expected_revision is None):
            raise ValueError("Writes need both command_id and expected_revision")
        batch = self.batch()
        if sum(len(s.sql) for s in batch) > MAX_BATCH_CHARS:
            raise ValueError("A batch is limited to 20,000 characters of SQL")
        if sum(len(s.params) for s in batch) > MAX_BATCH_PARAMS:
            raise ValueError("A batch is limited to 200 parameters")
        return self

    def batch(self):
        return self.statements or [Statement(sql=self.sql, params=self.params)]

    @property
    def writable(self):
        return self.command_id is not None


class QueryRejected(Exception):
    """A content-free reason, safe to return to the caller who sent the SQL."""


def columns(fields):
    """Map fields to SQLite columns; built-in names keep priority over field names."""
    taken = {name.casefold() for name in BUILT_IN}
    mapping = []
    for field in fields:
        name = field["name"]
        if name.casefold() in taken:
            name = "field_" + field["name"]
            n = 2
            while name.casefold() in taken:
                name, n = f"field_{field['name']}_{n}", n + 1
        taken.add(name.casefold())
        mapping.append((name, field))
    return mapping


def quoted(name):
    return '"' + name.replace('"', '""') + '"'


def schema(fields):
    return [
        {
            "column": column,
            "field_id": field["id"],
            "type": field["type"],
            "options": field.get("options", []),
        }
        for column, field in columns(fields)
    ]


def stored(value, field):
    if value is None or value == "":
        return None
    if field["type"] == "checkbox":
        return 1 if value else 0
    return value


def loaded(value, field):
    """SQLite value back to a record value; validation happens on the whole snapshot."""
    if value is None or value == "":
        return None
    if field["type"] == "checkbox":
        if value in (0, 1) and not isinstance(value, float):
            return bool(value)
        raise QueryRejected(f"Column for field {field['name']!r} expects 0 or 1")
    if field["type"] == "number":
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return value
        raise QueryRejected(f"Column for field {field['name']!r} expects a number")
    if isinstance(value, (int, float)):
        raise QueryRejected(f"Column for field {field['name']!r} expects text")
    return value


class Sandbox:
    """One in-memory table per call; the authorizer is installed after setup."""

    def __init__(self, data, writable, new_ids=()):
        if sqlite3.sqlite_version_info < MIN_SQLITE:
            raise QueryRejected("SQL access requires SQLite 3.35 or newer")
        self.mapping = columns(data["fields"])
        self.writable = writable
        self.wrote = False
        self.calls = 0
        self.deadline = 0.0
        self.connection = sqlite3.connect(":memory:", isolation_level=None)
        ids = iter(new_ids)
        # Assigned inside SQLite, so INSERT ... RETURNING id reports the stored ID.
        self.connection.create_function("space_record_id", 0, lambda: next(ids))
        self.connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_STRING_BYTES)
        self.connection.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, MAX_STATEMENT_CHARS)
        self.connection.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
        self.connection.setconfig(sqlite3.SQLITE_DBCONFIG_DEFENSIVE, True)
        self.connection.setconfig(sqlite3.SQLITE_DBCONFIG_ENABLE_TRIGGER, False)
        self.connection.setconfig(sqlite3.SQLITE_DBCONFIG_ENABLE_VIEW, False)
        # The schema is Space's own and CREATE is denied, so its ID default may call a function.
        self.connection.setconfig(sqlite3.SQLITE_DBCONFIG_TRUSTED_SCHEMA, True)
        definitions = [
            '"id" TEXT PRIMARY KEY NOT NULL DEFAULT (space_record_id())',
            '"name" TEXT NOT NULL',
            "\"notes\" TEXT NOT NULL DEFAULT ''",
            '"created_at" TEXT',
            '"updated_at" TEXT',
        ] + [
            quoted(column)
            + (
                " REAL"
                if field["type"] == "number"
                else " INTEGER"
                if field["type"] == "checkbox"
                else " TEXT"
            )
            for column, field in self.mapping
        ]
        self.connection.execute("CREATE TABLE records (" + ",".join(definitions) + ")")
        names = ["id", "name", "notes", "created_at", "updated_at"] + [
            column for column, _ in self.mapping
        ]
        # Identifiers are quoted field names in a private in-memory table, never agent SQL.
        columns_sql = ",".join(quoted(n) for n in names)
        placeholders = ",".join("?" for _ in names)
        insert = f"INSERT INTO records ({columns_sql}) VALUES ({placeholders})"  # noqa: S608
        for record in data["records"]:
            self.connection.execute(
                insert,
                [
                    record["id"],
                    record["name"],
                    record.get("notes", ""),
                    record.get("createdAt"),
                    record["updatedAt"],
                ]
                + [stored(record["values"].get(field["id"]), field) for _, field in self.mapping],
            )
        self.connection.set_authorizer(self.authorize)
        self.connection.set_progress_handler(self.progress, PROGRESS_STEPS)

    def authorize(self, action, first, second, database, trigger):
        if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_RECURSIVE):
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ:
            return sqlite3.SQLITE_OK if first == "records" else sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_FUNCTION:
            allowed = second.lower() in ALLOWED_FUNCTIONS or (
                self.writable and second == "space_record_id"
            )
            return sqlite3.SQLITE_OK if allowed else sqlite3.SQLITE_DENY
        if action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_DELETE) and first == "records":
            if self.writable:
                self.wrote = True
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_UPDATE and first == "records":
            if self.writable and second not in READ_ONLY:
                self.wrote = True
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_DENY

    def progress(self):
        self.calls += 1
        return int(self.calls > MAX_PROGRESS_CALLS or time.monotonic() > self.deadline)

    def run(self, batch, max_rows):
        self.deadline = time.monotonic() + MAX_SECONDS
        results = []
        if self.writable:
            self.connection.set_authorizer(None)
            self.connection.execute("BEGIN")
            self.connection.set_authorizer(self.authorize)
        for index, statement in enumerate(batch):
            try:
                cursor = self.connection.execute(statement.sql, statement.params)
                rows = cursor.fetchmany(max_rows + 1)
                extra = len(cursor.fetchall()) if len(rows) > max_rows else 0
            except sqlite3.Error as error:
                message = str(error)
                if "interrupted" in message:
                    message = "query too expensive; narrow it or add LIMIT"
                raise QueryRejected(f"Statement {index}: {message}") from None
            results.append(
                {
                    "columns": [d[0] for d in cursor.description or []],
                    "rows": [list(row) for row in rows[:max_rows]],
                    "row_count": len(rows) + extra,
                    "truncated": len(rows) > max_rows,
                }
            )
        return results

    def records(self, original, stamp):
        """Rebuild records in stored order; only changed rows get a new updatedAt."""
        self.connection.set_authorizer(None)
        names = ["id", "name", "notes"] + [column for column, _ in self.mapping]
        rows = self.connection.execute(
            "SELECT " + ",".join(quoted(n) for n in names) + " FROM records ORDER BY rowid"  # noqa: S608
        ).fetchall()
        before = {record["id"]: record for record in original}
        rebuilt, counts = [], {"inserted": 0, "updated": 0, "deleted": 0}
        for row in rows:
            values = {}
            for (_, field), value in zip(self.mapping, row[3:], strict=True):
                value = loaded(value, field)
                if value is not None:
                    values[field["id"]] = value
            record = {"id": str(row[0]), "name": row[1], "notes": row[2] or "", "values": values}
            previous = before.get(record["id"])
            if previous is None:
                counts["inserted"] += 1
                rebuilt.append(record | {"createdAt": stamp, "updatedAt": stamp})
            elif comparable(previous) == comparable(record):
                rebuilt.append(previous)
            else:
                counts["updated"] += 1
                rebuilt.append(previous | record | {"updatedAt": stamp})
        counts["deleted"] = len(set(before) - {r["id"] for r in rebuilt})
        return rebuilt, counts

    def close(self):
        self.connection.close()


def comparable(record):
    values = {k: v for k, v in record.get("values", {}).items() if v not in (None, "")}
    return record["name"], record.get("notes", ""), values


def bounded(results):
    """Cap the serialized result size; later rows are dropped first."""
    for result in results:
        while len(json.dumps(results, default=str)) > MAX_RESULT_CHARS and result["rows"]:
            result["rows"].pop()
            result["truncated"] = True
    return results


def execute(workspace, identity, space_id, database_id, request: Query) -> dict[str, Any]:
    from space.workspace import CommandError

    item = workspace_items.get(
        workspace,
        identity,
        space_id,
        database_id,
        request.expected_revision if request.writable else None,
    )
    if item["kind"] != "database":
        raise CommandError("Workspace item is not a database", 404)
    if request.writable:
        if item["deleted"]:
            raise CommandError("Restore this database before changing its records", 409)
        derived = command_for(workspace, identity, space_id, database_id, request)
        replay = previous_command(workspace, identity, space_id, derived, database_id)
        if replay is not None:
            return {
                "database": {"id": str(database_id), "revision": replay},
                "results": [],
                "changes": None,
                "replayed": True,
            }
    new_ids = (
        (str(uuid5(derived, str(n))) for n in range(workspace_items.MAX_DATABASE_RECORDS))
        if request.writable
        else ()
    )
    try:
        sandbox = Sandbox(item, request.writable, new_ids)
    except QueryRejected as error:
        raise CommandError(str(error), 503) from None
    try:
        results = sandbox.run(request.batch(), request.max_rows)
        response = {
            "database": {"id": str(database_id), "revision": item["revision"]},
            "results": bounded(results),
            "changes": None,
        }
        if request.include_schema:
            response["schema"] = schema(item["fields"])
        if not sandbox.wrote:
            return response
        stamp = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        records, counts = sandbox.records(item["records"], stamp)
    except QueryRejected as error:
        raise CommandError(str(error), 400) from None
    finally:
        sandbox.close()
    if records == item["records"]:
        return response
    data = {key: item[key] for key in workspace_items.DatabaseData.model_fields if key in item}
    try:
        change = workspace_items.SaveItem(
            command_id=derived,
            item_id=database_id,
            expected_revision=item["revision"],
            kind="database",
            data=data | {"records": records},
        )
    except ValidationError as error:
        problem = error.errors()[0]["msg"].removeprefix("Value error, ")
        raise CommandError(f"Rejected change: {problem}", 400) from None
    saved = workspace_items.save(workspace, identity, space_id, change)
    response["database"]["revision"] = saved["revision"]
    response["changes"] = counts
    return response


def command_for(workspace, identity, space_id, database_id, request):
    """Bind the caller's command ID to this exact request, so a reuse cannot replay."""
    _, key = workspace._key(identity, space_id, write=True)
    digest = hmac.digest(
        key.key.material,
        b"space.database-sql.v1\0"
        + json.dumps(
            {
                "database": str(database_id),
                "request": request.model_dump(mode="json"),
                "agent": identity.agent_id,
            },
            sort_keys=True,
        ).encode(),
        "sha256",
    )
    return uuid5(NAMESPACE_URL, f"{request.command_id}/{digest.hex()}")


def previous_command(workspace, identity, space_id, command_id, database_id):
    with workspace.db.transaction(identity) as connection:
        workspace._scope(connection, identity, space_id, write=True)
        row = connection.execute(
            "SELECT item_id,revision FROM space.workspace_item_commands WHERE id=%s",
            (command_id,),
        ).fetchone()
    if row and str(row["item_id"]) == str(database_id):
        return row["revision"]
    return None
