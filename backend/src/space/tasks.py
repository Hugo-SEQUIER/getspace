"""Parameters: authorized task commands and the canonical Tasks collection.
What it does: appends status columns and saves status-only immutable task revisions.
Output: column settings or committed task resources shared by REST and MCP.
"""

import hmac
from typing import Annotated
from uuid import UUID, uuid4

from pydantic import Field, field_validator

from space import agents, overview
from space.memory import Input
from space.providers import key_operation

Status = Annotated[str, Field(min_length=1, max_length=80, pattern=r"^[a-z0-9][a-z0-9_-]*$")]
# needs_you is accepted everywhere without being a stored column: the board shows it
# only while a task carries it, and the home page lists such tasks as waiting on the person.
DEFAULT_LABELS = {
    "todo": "To do",
    "in_progress": "In progress",
    "needs_you": "Needs you",
    "done": "Done",
}
MAX_COLUMNS = 30


class Column(Input):
    value: Status
    label: str = Field(min_length=1, max_length=80)

    @field_validator("label")
    @classmethod
    def visible_label(cls, value):
        if not value.strip() or any(ord(char) < 32 for char in value):
            raise ValueError("A visible column label is required")
        return value.strip()


class AddColumn(Column):
    command_id: UUID
    expected_revision: int = Field(ge=1, strict=True)


class SetStatus(Input):
    command_id: UUID
    expected_revision: int = Field(ge=1, strict=True)
    status: Status


def _collection(workspace, connection, scope):
    from space.workspace import CommandError

    row = connection.execute(
        "SELECT id,key_id,encrypted_schema FROM space.collections WHERE system_role='tasks'"
    ).fetchone()
    if row is None:
        raise CommandError("Tasks collection not found", 404)
    return row, workspace._key_row(connection, scope, row["key_id"])


def _schema(workspace, scope, row, key):
    return workspace._unpack(key, scope, row["id"], 1, "schema", row["encrypted_schema"])


def _columns(schema, *, revision=None, count=None):
    labels = DEFAULT_LABELS | schema.get("status_labels", {})
    return {
        "revision": revision if revision is not None else schema.get("column_revision", 1),
        "columns": [
            {"value": value, "label": labels.get(value, value)}
            for value in schema["fields"]["status"][:count]
        ],
    }


@key_operation
def columns(workspace, identity, space_id):
    with workspace.db.transaction(identity) as connection:
        scope = workspace._scope(connection, identity, space_id)
        row, key_row = _collection(workspace, connection, scope)
    key = workspace._unwrap_key(scope["organization_id"], space_id, key_row)
    result = _columns(_schema(workspace, scope, row, key))
    workspace._recheck(identity, space_id)
    return result


def require_status(workspace, identity, space_id, status):
    from space.workspace import CommandError

    # Default values are permanent. A custom value must belong to this Space.
    if status not in DEFAULT_LABELS and status not in {
        column["value"] for column in columns(workspace, identity, space_id)["columns"]
    }:
        raise CommandError("Choose a configured task status", 400)


@key_operation
def add_column(workspace, identity, space_id, change: AddColumn):
    from space.workspace import CommandError, encode

    with workspace.db.transaction(identity) as connection:
        scope = workspace._scope(connection, identity, space_id, write=True)
        row, key_row = _collection(workspace, connection, scope)
    key = workspace._unwrap_key(scope["organization_id"], space_id, key_row)
    fingerprint = hmac.digest(
        key.key.material,
        b"space.task-column.v1\0"
        + encode(
            {
                "change": change.model_dump(mode="json"),
                "agent_id": identity.agent_id,
            }
        ),
        "sha256",
    )
    with workspace.db.transaction(identity) as connection:
        scope = workspace._scope(connection, identity, space_id, write=True)
        connection.execute("SELECT id FROM space.spaces FOR UPDATE")
        current = connection.execute(
            "SELECT id,key_id,encrypted_schema FROM space.collections WHERE id=%s FOR UPDATE",
            (row["id"],),
        ).fetchone()
        if current is None or current["key_id"] != row["key_id"]:
            raise CommandError("Task columns changed. Refresh before saving.", 409)
        schema = _schema(workspace, scope, current, key)
        # Append-only columns bound receipts to MAX_COLUMNS and keep original responses
        # reconstructible without a second collection or plaintext configuration table.
        receipts = schema.setdefault("column_commands", {})
        previous = receipts.get(str(change.command_id))
        if previous:
            workspace._validate_previous(
                identity,
                scope,
                {
                    **previous,
                    "principal_id": UUID(previous["principal_id"]),
                    "fingerprint": bytes.fromhex(previous["fingerprint"]),
                },
                fingerprint,
            )
            result = _columns(schema, revision=previous["revision"], count=previous["count"])
        else:
            result = _columns(schema)
            if result["revision"] != change.expected_revision:
                raise CommandError("Task columns changed. Refresh before saving.", 409)
            if len(result["columns"]) >= MAX_COLUMNS:
                raise CommandError("Tasks support at most 30 status columns", 400)
            if any(
                column["value"] == change.value
                or column["label"].casefold() == change.label.casefold()
                for column in result["columns"]
            ):
                raise CommandError("A task column with this value or label already exists", 409)
            schema["fields"]["status"].append(change.value)
            schema.setdefault("status_labels", {})[change.value] = change.label
            schema["column_revision"] = change.expected_revision + 1
            receipts[str(change.command_id)] = {
                "principal_id": str(scope["principal_id"]),
                "client_id": identity.client_id,
                "fingerprint": fingerprint.hex(),
                "revision": schema["column_revision"],
                "count": len(schema["fields"]["status"]),
            }
            encrypted = workspace._pack(key, scope, row["id"], 1, "schema", schema)
            connection.execute(
                "UPDATE space.collections SET encrypted_schema=%s WHERE id=%s",
                (encrypted, row["id"]),
            )
            connection.execute("UPDATE space.spaces SET content_revision=content_revision+1")
            result = _columns(schema)
    workspace._recheck(identity, space_id)
    return result


@key_operation
def set_status(workspace, identity, space_id, resource_id, change: SetStatus):
    from space.workspace import CommandError, SaveResource, encode

    with workspace.db.transaction(identity) as connection:
        scope = workspace._scope(connection, identity, space_id, write=True)
        row = connection.execute(
            "SELECT v.*,r.kind,r.memory_kind FROM space.resources r "
            "JOIN space.resource_revisions v ON (r.organization_id,r.space_id,r.id,"
            "r.current_revision)=(v.organization_id,v.space_id,v.resource_id,v.revision) "
            "JOIN space.collections c ON (r.organization_id,r.space_id,r.collection_id)="
            "(c.organization_id,c.space_id,c.id) "
            "WHERE r.id=%s AND r.kind='record' AND c.system_role='tasks'",
            (resource_id,),
        ).fetchone()
        if row is None or row["memory_kind"] is not None:
            raise CommandError("Task not found", 404)
        key_row = workspace._key_row(connection, scope, row["key_id"])
        previous = connection.execute(
            "SELECT * FROM space.commands WHERE id=%s", (change.command_id,)
        ).fetchone()
        collection, schema_key_row = _collection(workspace, connection, scope)
    key = workspace._unwrap_key(scope["organization_id"], space_id, key_row)
    fingerprint = hmac.digest(
        key.key.material,
        b"space.task-status.v1\0"
        + encode(
            {
                "resource_id": str(resource_id),
                "change": change.model_dump(mode="json"),
                "agent_id": identity.agent_id,
            }
        ),
        "sha256",
    )
    previous = workspace._validate_previous(identity, scope, previous, fingerprint)
    if previous:
        return workspace.get_resource(
            identity, space_id, previous["resource_id"], previous["revision"]
        )
    if row["revision"] != change.expected_revision:
        raise CommandError("This task changed. Reload its latest revision before saving.", 409)
    schema_key = workspace._unwrap_key(scope["organization_id"], space_id, schema_key_row)
    schema = _schema(workspace, scope, collection, schema_key)
    if change.status not in schema["fields"]["status"]:
        raise CommandError("Choose a configured task status", 400)
    content = workspace._unpack(
        key, scope, resource_id, row["revision"], "record", row["encrypted_record"]
    )
    metadata = workspace._unpack(
        key, scope, resource_id, row["revision"], "metadata", row["encrypted_metadata"]
    )
    author = agents.current_profile(workspace, identity) if scope.get("agent_id") else None
    content.update(status=change.status, author_agent=author)
    metadata.update(status=change.status, author_agent=author)
    revision = change.expected_revision + 1
    encrypted = workspace._pack(key, scope, resource_id, revision, "record", content)
    encrypted_metadata = workspace._pack(key, scope, resource_id, revision, "metadata", metadata)
    saved_row = None
    with workspace.db.transaction(identity) as connection:
        scope = workspace._scope(connection, identity, space_id, write=True)
        connection.execute("SELECT id FROM space.spaces FOR UPDATE")
        previous = workspace._previous(connection, identity, scope, change.command_id, fingerprint)
        if previous is None:
            current = connection.execute(
                "UPDATE space.resources SET current_revision=%s "
                "WHERE id=%s AND current_revision=%s RETURNING id",
                (revision, resource_id, change.expected_revision),
            ).fetchone()
            if current is None:
                raise CommandError(
                    "This task changed. Reload its latest revision before saving.", 409
                )
            saved = connection.execute(
                "INSERT INTO space.resource_revisions(organization_id,space_id,resource_id,"
                "revision,key_id,encrypted_metadata,encrypted_record,author_principal_id,"
                "author_client_id,content_bytes) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "RETURNING resource_id,revision,created_at,author_principal_id,author_client_id",
                (
                    scope["organization_id"],
                    space_id,
                    resource_id,
                    revision,
                    key.key.key_id,
                    encrypted_metadata,
                    encrypted,
                    scope["principal_id"],
                    identity.client_id,
                    len(encrypted),
                ),
            )
            # Status is not indexed. Keep the same key and move existing title/body terms
            # to the new head atomically, instead of reading and tokenizing the body again.
            connection.execute(
                "UPDATE space.resource_terms SET revision=%s WHERE resource_id=%s",
                (revision, resource_id),
            )
            connection.execute(
                "INSERT INTO space.commands(organization_id,space_id,id,principal_id,"
                "client_id,fingerprint,resource_id,revision) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    scope["organization_id"],
                    space_id,
                    change.command_id,
                    scope["principal_id"],
                    identity.client_id,
                    fingerprint,
                    resource_id,
                    revision,
                ),
            )
            connection.execute(
                "INSERT INTO space.events(organization_id,space_id,id,command_id,event_type) "
                "VALUES(%s,%s,%s,%s,'resource.updated')",
                (scope["organization_id"], space_id, uuid4(), change.command_id),
            )
            connection.execute("UPDATE space.spaces SET content_revision=content_revision+1")
            overview.record_write(
                connection,
                scope,
                identity,
                resource_id,
                SaveResource(
                    command_id=change.command_id,
                    resource_id=resource_id,
                    expected_revision=change.expected_revision,
                    kind="task",
                    title=content["title"],
                    body=content["body"],
                    status=change.status,
                ),
                None,
                None,
            )
            saved_row = saved.fetchone()
    if saved_row is None:
        return workspace.get_resource(
            identity, space_id, previous["resource_id"], previous["revision"]
        )
    workspace._recheck(identity, space_id)
    return {
        **workspace._summary(saved_row | {"kind": "record"}, content, scope["principal_id"]),
        "body": content["body"],
    }


def archive_columns(value):
    """Parameters: optional archive task-column definitions.
    What it does: checks bounded values, labels and uniqueness without storing command receipts.
    Output: validated column dictionaries; old archives can omit the field.
    """
    if not isinstance(value, list) or len(value) > MAX_COLUMNS:
        raise ValueError("Invalid task columns")
    result = [Column.model_validate(column).model_dump() for column in value]
    if len({column["value"] for column in result}) != len(result) or len(
        {column["label"].casefold() for column in result}
    ) != len(result):
        raise ValueError("Task column values and labels must be distinct")
    return result


def prepare_restore(workspace, identity, space_id, entries, definitions):
    """Parameters: authorized restore entries and optional portable column definitions.
    What it does: merges missing columns without changing destination labels or existing receipts.
    Output: collection ID and stored schema for the restore's atomic publish transaction.
    """
    from space.workspace import CommandError

    incoming = {column["value"]: column["label"] for column in archive_columns(definitions)}
    for entry in entries:
        value = entry["value"]
        if entry["entity"] == "resource" and value["kind"] == "task":
            incoming.setdefault(
                value["status"], DEFAULT_LABELS.get(value["status"], value["status"])
            )
    if not incoming:
        return None
    with workspace.db.transaction(identity) as connection:
        scope = workspace._scope(connection, identity, space_id, write=True)
        row, key_row = _collection(workspace, connection, scope)
    key = workspace._unwrap_key(scope["organization_id"], space_id, key_row)
    schema = _schema(workspace, scope, row, key)
    existing = _columns(schema)["columns"]
    values = {column["value"] for column in existing}
    labels = {column["label"].casefold() for column in existing}
    missing = [(value, label) for value, label in incoming.items() if value not in values]
    if len(values) + len(missing) > MAX_COLUMNS:
        raise CommandError("Restoring these tasks would exceed the 30 status column limit", 409)
    if not missing:
        return None
    for value, label in missing:
        if label.casefold() in labels:
            raise CommandError(
                "A restored task column label conflicts with an existing column", 409
            )
        schema["fields"]["status"].append(value)
        schema.setdefault("status_labels", {})[value] = label
        labels.add(label.casefold())
    schema["column_revision"] = schema.get("column_revision", 1) + 1
    return row["id"], workspace._pack(key, scope, row["id"], 1, "schema", schema)
