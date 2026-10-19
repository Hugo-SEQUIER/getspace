"""Parameters: a successful database snapshot command and its owning transaction.
What it does: compares stable record IDs and writes versioned delivery intents.
Output: an atomic outbox visible only when the record revision commits.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from uuid import uuid4

MAX_PENDING_DELIVERIES = 10000
# Set on a successful response whose request queued at least one delivery, so the
# hosting Worker can start a delivery run instead of waiting for its schedule.
QUEUED_HEADER = "X-Space-Webhook-Queued"
_queued: ContextVar[list | None] = ContextVar("webhook_deliveries_queued", default=None)


@contextmanager
def watch_queued_deliveries():
    """Parameters: none; wraps one request.
    What it does: collects a mark whenever that request inserts a delivery.
    Output: a list that is non-empty once a delivery was queued. A rolled-back
    transaction can leave a mark, which costs one empty delivery run at most.
    """
    marks = []
    token = _queued.set(marks)
    try:
        yield marks
    finally:
        _queued.reset(token)


def record_changes(before, after):
    """Parameters: old and new database data.
    What it does: ignores row order and updatedAt-only changes; compares actual values.
    Output: changed records with property/field IDs and private before/after inputs.
    """
    old = {record["id"]: record for record in before.get("records", [])}
    new = {record["id"]: record for record in after.get("records", [])}
    for record_id in sorted(old.keys() | new.keys()):
        previous, current = old.get(record_id), new.get(record_id)
        old_values = previous.get("values", {}) if previous else {}
        new_values = current.get("values", {}) if current else {}
        fields = sorted(
            field
            for field in old_values.keys() | new_values.keys()
            if old_values.get(field) != new_values.get(field)
        )
        properties = [
            field
            for field in ("name", "notes", "createdAt")
            if (previous or {}).get(field) != (current or {}).get(field)
        ]
        if previous is not None and current is not None and not fields and not properties:
            continue
        if previous is None:
            operation = "created"
        elif current is None:
            operation = "deleted"
        else:
            operation = "updated"
        yield (
            {
                "record_id": record_id,
                "operation": operation,
                "changed_fields": fields,
                "changed_properties": properties,
            },
            previous,
            current,
        )


def projection(event, transition, before, after, fields):
    """Parameters: metadata event, explicitly approved select filter and changed row.
    What it does: matches genuine transitions, without disclosing other field values.
    Output: projected notification or None when the selected transition did not occur.
    """
    if transition is None:
        return event
    field_id, target = transition["field_id"], transition["to"]
    field = fields.get(field_id)
    if not field or field["type"] != "select" or target not in field["options"] or after is None:
        return None
    old_value = (before or {}).get("values", {}).get(field_id)
    new_value = after.get("values", {}).get(field_id)
    if old_value == new_value or new_value != target:
        return None
    return {**event, "transition": {"field_id": field_id, "before": old_value, "after": new_value}}


def enqueue(workspace, connection, scope, key, database_id, revision, data):
    """Parameters: scoped snapshot transaction, database ID and intended new revision.
    What it does: reads the locked predecessor and inserts row events and matching deliveries.
    Output: no network work; rollback discards the outbox with a failed snapshot.
    """
    from space.workspace import CommandError

    subscribers = connection.execute(
        "SELECT * FROM space.active_webhook_subscriptions(%s)", (database_id,)
    ).fetchall()
    if not subscribers:
        return
    row = connection.execute(
        "SELECT i.current_revision,i.deleted,v.key_id,v.encrypted_payload "
        "FROM space.workspace_items i JOIN space.workspace_item_revisions v ON "
        "(v.organization_id,v.space_id,v.item_id,v.revision)="
        "(i.organization_id,i.space_id,i.id,i.current_revision) WHERE i.id=%s",
        (database_id,),
    ).fetchone()
    previous_revision = row["current_revision"] if row else 0
    before = {}
    keys = {key.key.key_id: key}

    def scoped_key(key_id):
        key_reference = str(key_id)
        if key_reference not in keys:
            keys[key_reference] = workspace._unwrap_key(
                scope["organization_id"],
                scope["space_id"],
                workspace._key_row(connection, scope, key_id),
            )
        return keys[key_reference]

    if row and not row["deleted"]:
        before = workspace._unpack(
            scoped_key(row["key_id"]),
            scope,
            database_id,
            previous_revision,
            "workspace_item",
            row["encrypted_payload"],
        )["data"]
    filters = [
        (
            sub["id"],
            workspace._unpack(
                scoped_key(sub["key_id"]),
                scope,
                sub["id"],
                sub["revision"],
                "webhook_filter",
                sub["encrypted_filter"],
            )["transition"],
            sub["delivery"],
        )
        for sub in subscribers
    ]
    fields = {field["id"]: field for field in data.get("fields", [])}
    occurred_at = datetime.now(UTC).isoformat()
    intents = []
    for diff, old_record, new_record in record_changes(before, data):
        event = {
            "version": 1,
            "id": str(uuid4()),
            "type": "database.record.changed",
            "occurred_at": occurred_at,
            "space_id": str(scope["space_id"]),
            "database_id": str(database_id),
            "previous_revision": previous_revision,
            "revision": revision,
            **diff,
            "transition": None,
        }
        deliveries = [
            (sub_id, payload, delivery)
            for sub_id, transition, delivery in filters
            if (payload := projection(event, transition, old_record, new_record, fields))
            is not None
        ]
        intents.append((event, deliveries))
    if not intents:
        return
    pending = connection.execute("SELECT space.webhook_queue_size() AS n").fetchone()["n"]
    if pending + sum(len(deliveries) for _, deliveries in intents) > MAX_PENDING_DELIVERIES:
        raise CommandError("This Space's webhook queue is full; retry after deliveries finish", 503)
    org, sid = scope["organization_id"], scope["space_id"]
    for event, deliveries in intents:
        connection.execute(
            "INSERT INTO space.webhook_events (organization_id,space_id,id,database_id,"
            "record_id,previous_revision,revision,operation,key_id,encrypted_payload) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                org,
                sid,
                event["id"],
                database_id,
                event["record_id"],
                previous_revision,
                revision,
                event["operation"],
                key.key.key_id,
                workspace._pack(
                    key,
                    scope,
                    event["id"],
                    1,
                    "webhook_event",
                    event,
                ),
            ),
        )
        for sub_id, payload, _ in deliveries:
            delivery_id = uuid4()
            connection.execute(
                "INSERT INTO space.webhook_deliveries (organization_id,space_id,id,"
                "subscription_id,event_id,key_id,encrypted_payload) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (
                    org,
                    sid,
                    delivery_id,
                    sub_id,
                    event["id"],
                    key.key.key_id,
                    workspace._pack(
                        key,
                        scope,
                        delivery_id,
                        1,
                        "webhook_delivery",
                        payload,
                    ),
                ),
            )
    # Only endpoint deliveries need the HTTPS worker; agents pull inbox deliveries.
    if (marks := _queued.get()) is not None and any(
        delivery == "endpoint" for _, deliveries in intents for _, _, delivery in deliveries
    ):
        marks.append(True)
