"""Parameters: scoped workspace items and revision-checked commands.
What it does: stores projects, folders, databases and labels as revisions.
Output: versioned items with independent visibility and no document cascades.
"""

import hmac
import math
from datetime import date, datetime
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from space import agents
from space.memory import Input

MAX_DATABASE_RECORDS = 1000
MAX_ITEM_BYTES = 900 * 1024


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
    records: list[DatabaseRecord] = Field(default_factory=list, max_length=MAX_DATABASE_RECORDS)
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
        if len({record.id for record in self.records}) != len(self.records):
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
        for record in self.records:
            if set(record.values) - set(fields):
                raise ValueError("Record contains an unknown field")
            for key, value in record.values.items():
                if value is None:
                    continue
                kind = fields[key].type
                if kind == "number":
                    if (
                        isinstance(value, bool)
                        or not isinstance(value, (float, int))
                        or not math.isfinite(value)
                    ):
                        raise ValueError("Expected a finite number")
                elif kind == "checkbox":
                    if type(value) is not bool:
                        raise ValueError("Expected a checkbox value")
                else:
                    if not isinstance(value, str) or len(value) > 2000:
                        raise ValueError("Expected text of at most 2,000 characters")
                    if not value:
                        continue
                    if kind == "select" and value not in fields[key].options:
                        raise ValueError("Unknown select option")
                    if kind == "date":
                        date.fromisoformat(value)
                    if kind == "url" and (
                        urlsplit(value).scheme not in {"http", "https"}
                        or not urlsplit(value).netloc
                    ):
                        raise ValueError("Expected an HTTP or HTTPS URL")
                    if kind == "email" and ("@" not in value or any(c.isspace() for c in value)):
                        raise ValueError("Expected an email address")
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
        if len(self.model_dump_json().encode()) > MAX_ITEM_BYTES:
            raise ValueError("Workspace item exceeds the supported 900 KiB snapshot size")
        return self


class ChangeVisibility(Input):
    command_id: UUID
    expected_revision: int = Field(ge=1, strict=True)
    deleted: bool


def item_model(kind):
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
    "SELECT i.id,i.kind,v.revision,v.key_id,v.encrypted_payload,v.deleted,v.created_at "
    "FROM space.workspace_items i JOIN space.workspace_item_revisions v "
    "ON (v.organization_id,v.space_id,v.item_id)=(i.organization_id,i.space_id,i.id) "
)


def item_keys(workspace, connection, scope, rows):
    return {
        key_id: workspace._key_row(connection, scope, key_id)
        for key_id in {row["key_id"] for row in rows}
    }


def decode(workspace, identity, space_id, scope, rows, key_rows):
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
    workspace._recheck(identity, space_id)
    return result


def get(workspace, identity, space_id, item_id, revision=None):
    from space.workspace import CommandError

    with workspace.db.transaction(identity) as connection:
        scope = workspace._scope(connection, identity, space_id)
        row = connection.execute(
            ROW + "WHERE i.id=%s AND v.revision=coalesce(%s,i.current_revision)",
            (item_id, revision),
        ).fetchone()
        key_rows = item_keys(workspace, connection, scope, [row]) if row else {}
    if not row:
        raise CommandError("Workspace item not found", 404)
    return decode(workspace, identity, space_id, scope, [row], key_rows)[0]


def without_records(item):
    """Database metadata and schema with a record count, for listings agents can afford."""
    if item.get("kind") != "database":
        return item
    return {k: v for k, v in item.items() if k != "records"} | {
        "record_count": len(item.get("records", []))
    }


def listing(workspace, identity, space_id, kind, after=None, deleted=False, *, records=True):
    with workspace.db.transaction(identity) as connection:
        scope = workspace._scope(connection, identity, space_id)
        rows = connection.execute(
            ROW + "WHERE i.kind=%s AND i.deleted=%s AND v.revision=i.current_revision "
            "AND (%s::uuid IS NULL OR i.id>%s) ORDER BY i.id LIMIT 26",
            (kind, deleted, after, after),
        ).fetchall()
        key_rows = item_keys(workspace, connection, scope, rows[:25])
    # Empty results contain no content and were authorized in the read transaction.
    if not rows:
        return {"items": [], "next_cursor": None}
    items = decode(workspace, identity, space_id, scope, rows[:25], key_rows)
    next_cursor = items[-1]["id"] if len(rows) > 25 else None
    if not records:
        items = [without_records(item) for item in items]
    return {"items": items, "next_cursor": next_cursor}


def save(workspace, identity, space_id, change: SaveItem, *, deleted=False, emit_changes=True):
    from space.workspace import CommandError, encode

    scope, key = workspace._key(identity, space_id, write=True)
    fingerprint = hmac.digest(
        key.key.material,
        encode(
            {
                "change": change.model_dump(mode="json"),
                "deleted": deleted,
                "client_id": identity.client_id,
                "agent_id": identity.agent_id,
            }
        ),
        "sha256",
    )
    author = agents.current_profile(workspace, identity) if identity.client_id else None
    # Projects always need an active section; folders and databases may keep an unchanged
    # reference to a parent archived later.
    placement = PLACEMENT.get(change.kind, {})
    prior = {}
    if (
        change.kind != "project"
        and change.expected_revision
        and any(change.data.get(reference) for reference in placement)
    ):
        prior = get(workspace, identity, space_id, change.item_id, change.expected_revision)
    revision = change.expected_revision + 1
    payload = workspace._pack(
        key,
        scope,
        change.item_id,
        revision,
        "workspace_item",
        {"data": change.data, "author_agent": author},
    )
    with workspace.db.transaction(identity) as connection:
        scope = workspace._scope(connection, identity, space_id, write=True)
        connection.execute("SELECT id FROM space.spaces FOR UPDATE")
        previous = connection.execute(
            "SELECT * FROM space.workspace_item_commands WHERE id=%s", (change.command_id,)
        ).fetchone()
        if previous:
            if not hmac.compare_digest(bytes(previous["fingerprint"]), fingerprint):
                raise CommandError("This command ID was already used for another operation", 409)
            revision = previous["revision"]
        else:
            current = connection.execute(
                "SELECT kind,current_revision FROM space.workspace_items WHERE id=%s",
                (change.item_id,),
            ).fetchone()
            if (current["current_revision"] if current else 0) != change.expected_revision:
                raise CommandError("This workspace item changed. Refresh before saving.", 409)
            if current and current["kind"] != change.kind:
                raise CommandError("Item kind cannot change", 409)
            for reference, parent in placement.items():
                value = change.data.get(reference)
                if (
                    value
                    and not deleted
                    and (change.kind == "project" or value != prior.get(reference))
                    and not connection.execute(
                        "SELECT id FROM space.workspace_items "
                        "WHERE id=%s AND kind=%s AND NOT deleted",
                        (value, parent),
                    ).fetchone()
                ):
                    raise CommandError(f"Choose an active {parent} in this Space", 409)
            if change.kind == "database" and not deleted and emit_changes:
                from space.webhook_events import enqueue

                enqueue(workspace, connection, scope, key, change.item_id, revision, change.data)
            if current:
                connection.execute(
                    "UPDATE space.workspace_items SET current_revision=%s,deleted=%s WHERE id=%s",
                    (revision, deleted, change.item_id),
                )
            else:
                connection.execute(
                    "INSERT INTO space.workspace_items "
                    "(organization_id,space_id,id,kind,current_revision) VALUES (%s,%s,%s,%s,%s)",
                    (scope["organization_id"], space_id, change.item_id, change.kind, revision),
                )
            connection.execute(
                "INSERT INTO space.workspace_item_revisions (organization_id,space_id,item_id,"
                "revision,key_id,encrypted_payload,deleted,author_principal_id,author_client_id) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    scope["organization_id"],
                    space_id,
                    change.item_id,
                    revision,
                    key.key.key_id,
                    payload,
                    deleted,
                    scope["principal_id"],
                    identity.client_id,
                ),
            )
            connection.execute(
                "INSERT INTO space.workspace_item_commands (organization_id,space_id,id,"
                "item_id,revision,client_id,agent_id,fingerprint) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    scope["organization_id"],
                    space_id,
                    change.command_id,
                    change.item_id,
                    revision,
                    identity.client_id,
                    identity.agent_id,
                    fingerprint,
                ),
            )
            connection.execute("UPDATE space.spaces SET content_revision=content_revision+1")
    return get(workspace, identity, space_id, change.item_id, revision)


def visibility(workspace, identity, space_id, item_id, change: ChangeVisibility):
    item = get(workspace, identity, space_id, item_id, change.expected_revision)
    model = item_model(item["kind"])
    data = {key: item[key] for key in model.model_fields if key in item}
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
