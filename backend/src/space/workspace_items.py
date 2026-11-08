"""Parameters: scoped workspace items and revision-checked commands.
What it does: stores projects, folders, databases and labels as packed revisions. A
database revision holds the schema and the metadata; its records are rows of their own in
`space.database_records` (see `database_records.py`), so a write stores the rows it changed.
Output: versioned items with independent visibility and no document cascades.
"""

import hmac
import json
import math
from datetime import date, datetime
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import Field, ValidationError, field_validator, model_validator

from space import agents, content_changes
from space.memory import Input

MAX_DATABASE_RECORDS = 10_000
# The schema and metadata of one item revision; records are stored and bounded per row.
MAX_ITEM_BYTES = 900 * 1024
# One incremental change writes at most this many rows and removes at most this many.
MAX_CHANGE_UPSERTS = 1000
MAX_CHANGE_DELETES = 10_000


class ProjectData(Input):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=10000)
    status: Literal["todo", "in_progress", "done"] = "todo"
    section_id: UUID | None = None

    @field_validator("name")
    @classmethod
    def visible_name(cls, value):
        if not value.strip():
            raise ValueError("A name is required")
        return value.strip()


class LabelData(Input):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=1000)

    @field_validator("name")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Name cannot be blank")
        return value.strip()


class FolderData(LabelData):
    section_id: UUID | None = None
    # Where the folder belongs; documents inside keep their own project and folder.
    project_id: UUID | None = Field(default=None, exclude_if=lambda value: value is None)


class DatabaseField(Input):
    id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=80)
    type: Literal["text", "number", "select", "date", "email", "phone", "url", "checkbox"]
    options: list[str] = Field(default_factory=list, max_length=30)
    optionColors: dict[
        str,
        Literal["#62675c", "#456a9c", "#86692e", "#76549b", "#3d806b", "#9a536b"],
    ] = Field(default_factory=dict, max_length=30, exclude_if=lambda value: not value)

    @model_validator(mode="after")
    def valid(self):
        if not self.name.strip():
            raise ValueError("Field name is required")
        self.name = self.name.strip()
        if self.type == "select":
            if not self.options or any(not o.strip() or len(o) > 80 for o in self.options):
                raise ValueError("Select fields need 1–30 named options")
            if len({option.lower() for option in self.options}) != len(self.options):
                raise ValueError("Select options must be distinct")
        elif self.options:
            raise ValueError("Only select fields have options")
        if self.optionColors:
            if self.type != "select":
                raise ValueError("Only select fields have option colors")
            if self.optionColors.keys() - set(self.options):
                raise ValueError("Option colors must refer to known select options")
        return self


class DatabaseRecord(Input):
    id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    notes: str = Field(default="", max_length=20000)
    values: dict[str, str | float | bool | None] = Field(default_factory=dict, max_length=30)
    updatedAt: str = Field(max_length=64)
    createdAt: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def valid(self):
        if not self.name.strip():
            raise ValueError("Record name is required")
        self.name = self.name.strip()
        datetime.fromisoformat(self.updatedAt.replace("Z", "+00:00"))
        if self.createdAt is not None:
            datetime.fromisoformat(self.createdAt.replace("Z", "+00:00"))
        return self


def record_values_problem(values, fields):
    """Parameters: a record's values and the schema's fields by ID (models or dicts).
    What it does: checks each value against its field's type and options.
    Output: the first problem as a sentence, or None when every value fits.
    """
    if set(values) - set(fields):
        return "Record contains an unknown field"
    for key, value in values.items():
        if value is None:
            continue
        field = fields[key]
        kind = field["type"] if isinstance(field, dict) else field.type
        if kind == "number":
            if (
                isinstance(value, bool)
                or not isinstance(value, (float, int))
                or not math.isfinite(value)
            ):
                return "Expected a finite number"
        elif kind == "checkbox":
            if type(value) is not bool:
                return "Expected a checkbox value"
        else:
            if not isinstance(value, str) or len(value) > 2000:
                return "Expected text of at most 2,000 characters"
            if not value:
                continue
            options = field.get("options", []) if isinstance(field, dict) else field.options
            if kind == "select" and value not in options:
                return "Unknown select option"
            if kind == "date":
                try:
                    date.fromisoformat(value)
                except ValueError:
                    return "Expected a date"
            if kind == "url" and (
                urlsplit(value).scheme not in {"http", "https"} or not urlsplit(value).netloc
            ):
                return "Expected an HTTP or HTTPS URL"
            if kind == "email" and ("@" not in value or any(c.isspace() for c in value)):
                return "Expected an email address"
    return None


class TaskTracking(Input):
    """Marks a database as a task list: its rows join the planned-work view.

    Fields are named by ID so renaming a column keeps the setting.
    """

    status: str = Field(min_length=1, max_length=100)
    done: list[str] = Field(min_length=1, max_length=30)
    planned: str = Field(min_length=1, max_length=100)


class DatabaseData(Input):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=10000)
    # Optional placement; a project wins over a section in the Space map.
    project_id: UUID | None = Field(default=None, exclude_if=lambda value: value is None)
    section_id: UUID | None = Field(default=None, exclude_if=lambda value: value is None)
    fields: list[DatabaseField] = Field(default_factory=list, max_length=30)
    # The whole table when present: records it omits are deleted. Absent, the stored records
    # stay as they are; the website and SQL writes change rows through `DatabaseChange`.
    records: list[DatabaseRecord] | None = Field(
        default=None, max_length=MAX_DATABASE_RECORDS, exclude_if=lambda value: value is None
    )
    view: Literal["table", "board"] = "table"
    groupBy: str | None = Field(default=None, max_length=100)
    tasks: TaskTracking | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def valid(self):
        if not self.name.strip():
            raise ValueError("Database name is required")
        self.name = self.name.strip()
        fields = {field.id: field for field in self.fields}
        if len(fields) != len(self.fields):
            raise ValueError("Field IDs must be distinct")
        if len({field.name.casefold() for field in self.fields}) != len(self.fields):
            raise ValueError("Field names must be distinct")
        if self.records is not None and len({record.id for record in self.records}) != len(
            self.records
        ):
            raise ValueError("Record IDs must be distinct")
        if self.groupBy and (self.groupBy not in fields or fields[self.groupBy].type != "select"):
            raise ValueError("Boards must group by a select field")
        if self.tasks:
            status = fields.get(self.tasks.status)
            if not status or status.type != "select":
                raise ValueError("Task tracking needs a select field for status")
            if len(set(self.tasks.done)) != len(self.tasks.done) or set(self.tasks.done) - set(
                status.options
            ):
                raise ValueError("Done statuses must be distinct options of the status field")
            if self.tasks.planned not in fields or fields[self.tasks.planned].type != "date":
                raise ValueError("Task tracking needs a date field for the planned day")
        for record in self.records or ():
            problem = record_values_problem(record.values, fields)
            if problem:
                raise ValueError(problem)
        return self


class SaveItem(Input):
    command_id: UUID
    item_id: UUID
    expected_revision: int = Field(ge=0, strict=True)
    kind: Literal["project", "database", "category", "section", "folder"]
    data: dict

    @model_validator(mode="after")
    def valid(self):
        model = item_model(self.kind)
        self.data = model.model_validate(self.data).model_dump(mode="json")
        check_item_size(self.data)
        return self


def check_item_size(data):
    """The schema and metadata of one revision; records are bounded per row, not here."""
    payload = {key: value for key, value in data.items() if key != "records"}
    if len(json.dumps(payload, ensure_ascii=False).encode()) > MAX_ITEM_BYTES:
        raise ValueError("Workspace item exceeds the supported 900 KiB size")


class DatabaseChange(Input):
    """One revision of a database: its schema, some of its rows, or both.

    `data` is the schema and metadata without `records`; absent, the stored schema stays.
    `upsert` writes the given rows and `delete` removes the given IDs; other rows are untouched.
    """

    command_id: UUID
    expected_revision: int = Field(ge=1, strict=True)
    data: dict | None = None
    upsert: list[DatabaseRecord] = Field(default_factory=list, max_length=MAX_CHANGE_UPSERTS)
    delete: list[str] = Field(default_factory=list, max_length=MAX_CHANGE_DELETES)

    @model_validator(mode="after")
    def valid(self):
        if self.data is not None:
            if "records" in self.data:
                raise ValueError("Send record changes as upsert and delete")
            self.data = DatabaseData.model_validate(self.data).model_dump(mode="json")
            check_item_size(self.data)
        elif not self.upsert and not self.delete:
            raise ValueError("A change needs data, upsert or delete")
        ids = [record.id for record in self.upsert] + list(self.delete)
        if len(set(ids)) != len(ids):
            raise ValueError("Record IDs must be distinct")
        if any(not 1 <= len(id_) <= 100 for id_ in self.delete):
            raise ValueError("Record IDs are 1 to 100 characters")
        return self


class ChangeVisibility(Input):
    command_id: UUID
    expected_revision: int = Field(ge=1, strict=True)
    deleted: bool


def item_model(kind):
    if kind in {"system", "lab_entry", "system_readings"}:
        from space import systems

        return {
            "system": systems.SystemData,
            "lab_entry": systems.LabEntryData,
            "system_readings": systems.ReadingLog,
        }[kind]
    return {
        "project": ProjectData,
        "folder": FolderData,
        "database": DatabaseData,
        "category": LabelData,
        "section": LabelData,
    }[kind]


PLACEMENT = {
    "project": {"section_id": "section"},
    "folder": {"section_id": "section", "project_id": "project"},
    "database": {"section_id": "section", "project_id": "project"},
}

ROW = (
    "SELECT i.id,i.kind,v.revision,v.key_id,v.encrypted_payload,v.deleted,v.created_at,"
    "k.wrapping_provider,k.wrapping_key_reference,k.wrapped_key "
    "FROM space.workspace_items i JOIN space.workspace_item_revisions v "
    "ON (v.organization_id,v.space_id,v.item_id)=(i.organization_id,i.space_id,i.id) "
    "JOIN space.space_keys k ON (k.organization_id,k.space_id,k.id)="
    "(v.organization_id,v.space_id,v.key_id) "
)


def item_keys(workspace, connection, scope, rows):
    """The wrapping keys of a page, already carried by ROW; no further query is needed."""
    return workspace._key_rows(rows)


def decode(workspace, identity, space_id, scope, rows, key_rows, *, recheck=True):
    keys = {
        key_id: workspace._unwrap_key(scope["organization_id"], space_id, row)
        for key_id, row in key_rows.items()
    }
    result = []
    for row in rows:
        payload = workspace._unpack(
            keys[row["key_id"]],
            scope,
            row["id"],
            row["revision"],
            "workspace_item",
            row["encrypted_payload"],
        )
        result.append(
            {
                **payload["data"],
                "id": str(row["id"]),
                "kind": row["kind"],
                "revision": row["revision"],
                "deleted": row["deleted"],
                "updated_at": row["created_at"].isoformat(),
                "author_agent": payload.get("author_agent"),
            }
        )
    if recheck:
        workspace._recheck(identity, space_id)
    return result


def get(workspace, identity, space_id, item_id, revision=None, *, records=True):
    """Parameters: Space, item, optional historical revision and whether a database's records
    are wanted; without them it carries `record_count` only.
    What it does: reads the item revision and, for a database, its rows at that revision.
    Output: the item with its revision.
    """
    from space import database_records
    from space.workspace import CommandError

    with workspace.db.transaction(identity) as connection:
        scope = workspace._scope(connection, identity, space_id)
        row = connection.execute(
            ROW + "WHERE i.id=%s AND v.revision=coalesce(%s,i.current_revision)",
            (item_id, revision),
        ).fetchone()
        rows = []
        if row and row["kind"] == "database":
            if revision is None:
                rows = database_records.current_rows(connection, item_id)
                if rows and rows[0]["database_revision"] != row["revision"]:
                    # A write landed between the two statements: the rows name the revision
                    # they belong to, so read the item at that one instead.
                    row = connection.execute(
                        ROW + "WHERE i.id=%s AND v.revision=%s",
                        (item_id, rows[0]["database_revision"]),
                    ).fetchone()
            else:
                rows = database_records.rows_at(connection, item_id, revision)
        key_rows = item_keys(workspace, connection, scope, [row]) if row else {}
    if not row:
        raise CommandError("Workspace item not found", 404)
    item = decode(workspace, identity, space_id, scope, [row], key_rows, recheck=False)[0]
    item = with_records(workspace, scope, item, rows, records)
    workspace._recheck(identity, space_id)
    return item


def with_records(workspace, scope, item, rows, include):
    """Parameters: a decoded item, the raw record rows read with it and whether to include them.
    What it does: decrypts a database's rows, or takes the records a pre-migration revision
    carries in its payload, and counts them.
    Output: the item with `record_count` and, when asked, `records`.
    """
    from space import database_records

    if item.get("kind") != "database":
        return item
    if item.get("records") is not None:
        records = item["records"]
    else:
        records = database_records.decode(
            workspace, scope, item["id"], rows, item.get("fields", [])
        )
    item = {k: v for k, v in item.items() if k != "records"} | {"record_count": len(records)}
    if include:
        item["records"] = records
    return item


def record_counts(connection, rows):
    """Record counts for the database rows of one page, read in the page's transaction."""
    from space import database_records

    return database_records.counts(
        connection, [row["id"] for row in rows if row["kind"] == "database"]
    )


def without_records(item, counts=None):
    """Database metadata and schema with a record count, for listings agents can afford."""
    if item.get("kind") != "database":
        return item
    if item.get("records") is not None:
        count = len(item["records"])
    else:
        count = item.get("record_count", (counts or {}).get(item["id"], 0))
    return {k: v for k, v in item.items() if k != "records"} | {"record_count": count}


def listing(workspace, identity, space_id, kind, after=None, deleted=False):
    """One page of items. Databases carry their schema and `record_count`, never records."""
    with workspace.db.transaction(identity) as connection:
        scope = workspace._scope(connection, identity, space_id)
        rows = connection.execute(
            ROW + "WHERE i.kind=%s AND i.deleted=%s AND v.revision=i.current_revision "
            "AND (%s::uuid IS NULL OR i.id>%s) ORDER BY i.id LIMIT 26",
            (kind, deleted, after, after),
        ).fetchall()
        key_rows = item_keys(workspace, connection, scope, rows[:25])
        counts = record_counts(connection, rows[:25]) if kind == "database" else {}
    # Empty results contain no content and were authorized in the read transaction.
    if not rows:
        return {"items": [], "next_cursor": None}
    items = decode(workspace, identity, space_id, scope, rows[:25], key_rows)
    next_cursor = items[-1]["id"] if len(rows) > 25 else None
    items = [without_records(item, counts) for item in items]
    return {"items": items, "next_cursor": next_cursor}


def save(
    workspace,
    identity,
    space_id,
    change: SaveItem,
    *,
    deleted=False,
    emit_changes=True,
    records=True,
):
    """Parameters: Space, a validated item save and whether the result carries a database's
    records.
    What it does: writes one revision. A database save with `records` replaces the whole table
    and stores only the rows that differ; without `records` it changes the schema and metadata
    and leaves the rows alone.
    Output: the saved item at its new revision.
    """
    data = {key: value for key, value in change.data.items() if key != "records"}
    result = _commit(
        workspace,
        identity,
        space_id,
        kind=change.kind,
        item_id=change.item_id,
        expected_revision=change.expected_revision,
        command_id=change.command_id,
        fingerprint_input={
            "change": change.model_dump(mode="json"),
            "deleted": deleted,
            "client_id": identity.client_id,
            "agent_id": identity.agent_id,
        },
        data=data,
        records=change.data.get("records") if change.kind == "database" else None,
        deleted=deleted,
        emit_changes=emit_changes,
    )
    return get(workspace, identity, space_id, change.item_id, result["revision"], records=records)


def change_database(workspace, identity, space_id, database_id, change: DatabaseChange):
    """Parameters: Space, database and a validated change to its schema or rows.
    What it does: writes one revision holding the schema change and the rows the change names.
    Output: the database without its records, the change counts and the rows as stored; a
    retried command returns the committed revision with `replayed` true and no rows.
    """
    result = _commit(
        workspace,
        identity,
        space_id,
        kind="database",
        item_id=database_id,
        expected_revision=change.expected_revision,
        command_id=change.command_id,
        fingerprint_input={
            "database_change": change.model_dump(mode="json"),
            "client_id": identity.client_id,
            "agent_id": identity.agent_id,
        },
        data=change.data,
        upsert=[record.model_dump(mode="json") for record in change.upsert],
        delete=list(change.delete),
        rows_only=True,
    )
    item = get(workspace, identity, space_id, database_id, result["revision"], records=False)
    return {
        "database": item,
        "changes": result["counts"],
        "replayed": result["replayed"],
        "records": [] if result["replayed"] else [r.model_dump(mode="json") for r in change.upsert],
        "deleted": [] if result["replayed"] else list(change.delete),
    }


def change_rows(
    workspace, identity, space_id, database_id, *, command_id, expected_revision, upsert, delete
):
    """Parameters: Space, database, the caller's command ID, the revision it read, and the
    records a SQL write produced and the IDs it removed.
    What it does: writes those rows as one revision under the current schema.
    Output: the new revision and change counts, or the committed revision of a retried command.
    """
    return _commit(
        workspace,
        identity,
        space_id,
        kind="database",
        item_id=database_id,
        expected_revision=expected_revision,
        command_id=command_id,
        fingerprint_input={
            "database_rows": {"upsert": upsert, "delete": delete},
            "client_id": identity.client_id,
            "agent_id": identity.agent_id,
        },
        data=None,
        upsert=upsert,
        delete=delete,
        rows_only=True,
    )


def _commit(
    workspace,
    identity,
    space_id,
    *,
    kind,
    item_id,
    expected_revision,
    command_id,
    fingerprint_input,
    data,
    records=None,
    upsert=(),
    delete=(),
    deleted=False,
    emit_changes=True,
    rows_only=False,
):
    """The one write path of every item revision.

    Packing happens inside the transaction, because a database change may keep the stored
    schema, but no network call does: keys are unwrapped before or come from the key cache.
    """
    from space import database_records
    from space.workspace import CommandError, encode

    scope, key = workspace._key(identity, space_id, write=True)
    fingerprint = hmac.digest(key.key.material, encode(fingerprint_input), "sha256")
    author = agents.current_profile(workspace, identity) if identity.client_id else None
    # Projects always need an active section; folders and databases may keep an unchanged
    # reference to a parent archived later.
    placement = PLACEMENT.get(kind, {})
    prior = {}
    if (
        data is not None
        and kind != "project"
        and expected_revision
        and any(data.get(reference) for reference in placement)
    ):
        prior = get(workspace, identity, space_id, item_id, expected_revision, records=False)
    revision = expected_revision + 1
    with workspace.db.transaction(identity) as connection:
        scope = workspace._scope(connection, identity, space_id, write=True)
        connection.execute("SELECT id FROM space.spaces FOR UPDATE")
        previous = connection.execute(
            "SELECT * FROM space.workspace_item_commands WHERE id=%s", (command_id,)
        ).fetchone()
        if previous:
            if not hmac.compare_digest(bytes(previous["fingerprint"]), fingerprint):
                raise CommandError("This command ID was already used for another operation", 409)
            return {"revision": previous["revision"], "replayed": True, "counts": None}
        current = connection.execute(
            "SELECT kind,current_revision,deleted FROM space.workspace_items WHERE id=%s",
            (item_id,),
        ).fetchone()
        if (current["current_revision"] if current else 0) != expected_revision:
            raise CommandError("This workspace item changed. Refresh before saving.", 409)
        if current and current["kind"] != kind:
            raise CommandError("Item kind cannot change", 409)
        if rows_only and current["deleted"]:
            raise CommandError("Restore this database before changing its records", 409)
        current_data = None
        if kind == "database" and current:
            row = connection.execute(
                ROW + "WHERE i.id=%s AND v.revision=i.current_revision", (item_id,)
            ).fetchone()
            row_key = (
                key
                if str(row["key_id"]) == key.key.key_id
                else workspace._unwrap_key(
                    scope["organization_id"],
                    scope["space_id"],
                    workspace._key_rows([row])[row["key_id"]],
                )
            )
            current_data = workspace._unpack(
                row_key, scope, item_id, row["revision"], "workspace_item", row["encrypted_payload"]
            )["data"]
        if data is None:
            data = {k: v for k, v in current_data.items() if k != "records"}
        for reference, parent in placement.items():
            value = data.get(reference)
            if (
                value
                and not deleted
                and (kind == "project" or value != prior.get(reference))
                and not connection.execute(
                    "SELECT id FROM space.workspace_items WHERE id=%s AND kind=%s AND NOT deleted",
                    (value, parent),
                ).fetchone()
            ):
                raise CommandError(f"Choose an active {parent} in this Space", 409)
        writes, existing, counts = [], set(), None
        if kind == "database":
            fields = {field["id"]: field for field in data.get("fields", [])}
            try:
                upserts = [
                    DatabaseRecord.model_validate(record).model_dump(mode="json")
                    for record in upsert
                ]
            except ValidationError as error:
                problem = error.errors()[0]["msg"].removeprefix("Value error, ")
                raise CommandError(f"Rejected change: {problem}", 400) from None
            for record in upserts:
                problem = record_values_problem(record.get("values", {}), fields)
                if problem:
                    raise CommandError(f"Rejected change: {problem}", 400)
            ids = None if records is not None else [*(r["id"] for r in upserts), *delete]
            before, existing, legacy, total = database_records.baseline(
                workspace, connection, scope, item_id, current_data, ids
            )
            if records is not None:
                upserts, delete = database_records.snapshot_changes(before, records)
            writes, events, counts = database_records.plan(
                before, upserts, delete, legacy=legacy, total=total
            )
            if not deleted and emit_changes and events:
                from space.webhook_events import enqueue

                enqueue(
                    workspace,
                    connection,
                    scope,
                    key,
                    item_id,
                    expected_revision,
                    revision,
                    events,
                    data.get("fields", []),
                )
        payload = workspace._pack(
            key, scope, item_id, revision, "workspace_item", {"data": data, "author_agent": author}
        )
        if current:
            connection.execute(
                "UPDATE space.workspace_items SET current_revision=%s,deleted=%s WHERE id=%s",
                (revision, deleted, item_id),
            )
        else:
            connection.execute(
                "INSERT INTO space.workspace_items (organization_id,space_id,id,kind,"
                "current_revision) VALUES (%s,%s,%s,%s,%s)",
                (scope["organization_id"], space_id, item_id, kind, revision),
            )
        connection.execute(
            "INSERT INTO space.workspace_item_revisions (organization_id,space_id,item_id,"
            "revision,key_id,encrypted_payload,deleted,author_principal_id,author_client_id) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                scope["organization_id"],
                space_id,
                item_id,
                revision,
                key.key.key_id,
                payload,
                deleted,
                scope["principal_id"],
                identity.client_id,
            ),
        )
        database_records.write(
            workspace, connection, scope, key, item_id, revision, writes, existing
        )
        connection.execute(
            "INSERT INTO space.workspace_item_commands (organization_id,space_id,id,"
            "item_id,revision,client_id,agent_id,fingerprint) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                scope["organization_id"],
                space_id,
                command_id,
                item_id,
                revision,
                identity.client_id,
                identity.agent_id,
                fingerprint,
            ),
        )
        content_changes.changed(connection, (kind,))
    return {"revision": revision, "replayed": False, "counts": counts}


def visibility(workspace, identity, space_id, item_id, change: ChangeVisibility):
    from space.workspace import CommandError

    item = get(workspace, identity, space_id, item_id, change.expected_revision, records=False)
    if item["kind"] == "system_readings":
        # A month of readings follows its system: archiving the system hides them.
        raise CommandError("Readings are archived with their system", 409)
    if item["kind"] == "lab_entry" and item.get("verdict") and change.deleted:
        # An archived entry would leave its verdict in recall with no entry to review.
        raise CommandError("Move this verdict back to Testing or retire it first", 409)
    if item["kind"] == "system" and change.deleted:
        from space import systems

        if systems.live_verdicts(workspace, identity, space_id, item_id):
            raise CommandError(
                "Move this system's verdicts back to Testing or retire them first", 409
            )
    model = item_model(item["kind"])
    data = {key: item[key] for key in model.model_fields if key in item}
    if item["kind"] in {"system", "lab_entry"}:
        # Systems and lab entries are written only by `systems`; archiving keeps their data.
        data = model.model_validate(data).model_dump(mode="json")
        _commit(
            workspace,
            identity,
            space_id,
            kind=item["kind"],
            item_id=item_id,
            expected_revision=change.expected_revision,
            command_id=change.command_id,
            fingerprint_input={
                "visibility": {"data": data, "deleted": change.deleted},
                "client_id": identity.client_id,
                "agent_id": identity.agent_id,
            },
            data=data,
            deleted=change.deleted,
            emit_changes=False,
        )
        return get(workspace, identity, space_id, item_id, records=False)
    return save(
        workspace,
        identity,
        space_id,
        SaveItem(
            command_id=change.command_id,
            item_id=item_id,
            expected_revision=change.expected_revision,
            kind=item["kind"],
            data=data,
        ),
        deleted=change.deleted,
        emit_changes=False,
    )
