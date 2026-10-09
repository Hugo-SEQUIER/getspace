"""Parameters: authenticated human Space owners and revision-checked webhook commands.
What it does: manages encrypted subscriptions, one-time signing keys and delivery receipts.
Output: scoped management responses; record writers never receive subscription secrets.
"""

import hmac
import secrets
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from space import workspace_items
from space.memory import Input
from space.webhook_http import resolve_endpoint, validate_endpoint
from space.workspace import CommandError, encode

MAX_SUBSCRIPTIONS = 20


class Transition(Input):
    field_id: str = Field(min_length=1, max_length=100)
    to: str = Field(min_length=1, max_length=80)


class CreateSubscription(Input):
    command_id: UUID
    database_id: UUID
    endpoint_url: str = Field(min_length=1, max_length=2048)
    transition: Transition | None = None

    @field_validator("endpoint_url")
    @classmethod
    def endpoint(cls, value):
        return validate_endpoint(value)


class ChangeSubscription(Input):
    command_id: UUID
    expected_revision: int = Field(ge=1, strict=True)
    action: Literal["pause", "resume", "remove", "rotate"]


class Redeliver(Input):
    command_id: UUID


def owner_scope(workspace, connection, identity, space_id):
    """Parameters: live authenticated scope.
    What it does: rejects agents and every role except the selected Space's owner.
    Output: the verified owner scope or a fixed access error.
    """
    scope = workspace._scope(connection, identity, space_id, human=True, write=True)
    if scope["role"] != "owner":
        raise CommandError("Only this Space's owner can manage webhooks", 403)
    return scope


def owner_key(workspace, identity, space_id):
    with workspace.db.transaction(identity) as connection:
        scope = owner_scope(workspace, connection, identity, space_id)
        row = workspace._key_row(connection, scope)
    return scope, workspace._unwrap_key(scope["organization_id"], space_id, row)


def configuration(workspace, connection, scope, row, key=None):
    if key is None or str(row["key_id"]) != key.key.key_id:
        key = workspace._unwrap_key(
            scope["organization_id"],
            scope["space_id"],
            workspace._key_row(connection, scope, row["key_id"]),
        )
    return workspace._unpack(
        key,
        scope,
        row["id"],
        row["revision"],
        "webhook_subscription",
        row["encrypted_configuration"],
    )


def public_subscription(row, config):
    return {
        "id": str(row["id"]),
        "space_id": str(row["space_id"]),
        "database_id": str(row["database_id"]),
        "revision": row["revision"],
        "paused": row["paused"],
        "endpoint_url": config.get("endpoint_url", ""),
        "transition": config.get("transition"),
        "removed_at": row["removed_at"].isoformat() if row["removed_at"] else None,
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
    }


def subscription_response(row, config, *, include_secret=False):
    """Parameters: a subscription and the caller's existing secret-release decision.
    What it does: formats the command response without changing secret eligibility.
    Output: public subscription data and, only when requested, its signing secret.
    """
    result = {"subscription": public_subscription(row, config)}
    if include_secret:
        result["signing_secret"] = config["signing_secret"]
    return result


def fingerprint(key, scope, action, request, subscription_id=None, delivery_id=None):
    return hmac.digest(
        key.key.material,
        b"space.webhook-command.v1\0"
        + encode(
            {
                "principal_id": str(scope["principal_id"]),
                "action": action,
                "subscription_id": str(subscription_id) if subscription_id else None,
                "delivery_id": str(delivery_id) if delivery_id else None,
                "request": request.model_dump(mode="json"),
            }
        ),
        "sha256",
    )


def previous_command(connection, command_id, digest):
    previous = connection.execute(
        "SELECT * FROM space.webhook_commands WHERE id=%s", (command_id,)
    ).fetchone()
    if previous and not hmac.compare_digest(bytes(previous["fingerprint"]), digest):
        raise CommandError("This command ID was already used for another operation", 409)
    return previous


def receipt(
    connection, scope, request, digest, subscription_id, action, revision, delivery_id=None
):
    connection.execute(
        "INSERT INTO space.webhook_commands (organization_id,space_id,id,subscription_id,"
        "fingerprint,action,revision,delivery_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            scope["organization_id"],
            scope["space_id"],
            request.command_id,
            subscription_id,
            digest,
            action,
            revision,
            delivery_id,
        ),
    )


def subscription_row(connection, subscription_id, *, lock=False):
    statement = (
        "SELECT * FROM space.webhook_subscriptions WHERE id=%s FOR UPDATE"
        if lock
        else "SELECT * FROM space.webhook_subscriptions WHERE id=%s"
    )
    row = connection.execute(statement, (subscription_id,)).fetchone()
    if not row:
        raise CommandError("Webhook subscription not found", 404)
    return row


def active_database(workspace, connection, scope, database_id, key):
    row = connection.execute(
        workspace_items.ROW + "WHERE i.id=%s AND i.kind='database' AND NOT i.deleted "
        "AND v.revision=i.current_revision",
        (database_id,),
    ).fetchone()
    if not row:
        raise CommandError("Choose an active database in this Space", 404)
    if str(row["key_id"]) != key.key.key_id:
        key = workspace._unwrap_key(
            scope["organization_id"],
            scope["space_id"],
            workspace._key_row(connection, scope, row["key_id"]),
        )
    return workspace._unpack(
        key,
        scope,
        database_id,
        row["revision"],
        "workspace_item",
        row["encrypted_payload"],
    )["data"]


def create(workspace, identity, space_id, request: CreateSubscription):
    scope, key = owner_key(workspace, identity, space_id)
    digest = fingerprint(key, scope, "create", request)
    with workspace.db.transaction(identity) as connection:
        owner_scope(workspace, connection, identity, space_id)
        previous = previous_command(connection, request.command_id, digest)
    if not previous:
        try:
            resolve_endpoint(request.endpoint_url)
        except ValueError:
            raise CommandError(
                "Choose an HTTPS receiver that resolves only to public addresses", 400
            ) from None
    with workspace.db.transaction(identity) as connection:
        scope = owner_scope(workspace, connection, identity, space_id)
        connection.execute("SELECT id FROM space.spaces FOR UPDATE")
        previous = previous_command(connection, request.command_id, digest)
        if previous:
            row = subscription_row(connection, previous["subscription_id"])
            config = configuration(workspace, connection, scope, row, key)
            return subscription_response(
                row,
                config,
                include_secret=row["revision"] == previous["revision"] and not row["removed_at"],
            )
        data = active_database(workspace, connection, scope, request.database_id, key)
        transition = request.transition.model_dump(mode="json") if request.transition else None
        if transition:
            field = next((f for f in data["fields"] if f["id"] == transition["field_id"]), None)
            if not field or field["type"] != "select" or transition["to"] not in field["options"]:
                raise CommandError("Choose an existing select field and one of its options", 400)
        count = connection.execute(
            "SELECT count(*) AS n FROM space.webhook_subscriptions WHERE removed_at IS NULL"
        ).fetchone()["n"]
        if count >= MAX_SUBSCRIPTIONS:
            raise CommandError("Remove a subscription before creating another; limit is 20", 409)
        config = {
            "endpoint_url": request.endpoint_url,
            "transition": transition,
            "signing_secret": secrets.token_urlsafe(32),
        }
        row = connection.execute(
            "INSERT INTO space.webhook_subscriptions (organization_id,space_id,id,database_id,"
            "revision,key_id,encrypted_configuration,encrypted_filter,created_by,issuer,subject) "
            "VALUES (%s,%s,%s,%s,1,%s,%s,%s,%s,%s,%s) RETURNING *",
            (
                scope["organization_id"],
                space_id,
                request.command_id,
                request.database_id,
                key.key.key_id,
                workspace._pack(
                    key,
                    scope,
                    request.command_id,
                    1,
                    "webhook_subscription",
                    config,
                ),
                workspace._pack(
                    key,
                    scope,
                    request.command_id,
                    1,
                    "webhook_filter",
                    {"transition": transition},
                ),
                scope["principal_id"],
                identity.issuer,
                identity.subject,
            ),
        ).fetchone()
        receipt(connection, scope, request, digest, row["id"], "create", 1)
        return subscription_response(row, config, include_secret=True)


def listing(workspace, identity, space_id):
    with workspace.db.transaction(identity) as connection:
        scope = owner_scope(workspace, connection, identity, space_id)
        rows = connection.execute(
            "SELECT * FROM space.webhook_subscriptions WHERE removed_at IS NULL "
            "ORDER BY created_at,id"
        ).fetchall()
        result = [
            public_subscription(row, configuration(workspace, connection, scope, row))
            for row in rows
        ]
    with workspace.db.transaction(identity) as connection:
        owner_scope(workspace, connection, identity, space_id)
    return {"items": result}


def change(workspace, identity, space_id, subscription_id, request: ChangeSubscription):
    scope, key = owner_key(workspace, identity, space_id)
    digest = fingerprint(key, scope, request.action, request, subscription_id)
    with workspace.db.transaction(identity) as connection:
        scope = owner_scope(workspace, connection, identity, space_id)
        connection.execute("SELECT id FROM space.spaces FOR UPDATE")
        previous = previous_command(connection, request.command_id, digest)
        row = subscription_row(connection, subscription_id, lock=True)
        config = configuration(workspace, connection, scope, row, key)
        if previous:
            return subscription_response(
                row,
                config,
                include_secret=request.action == "rotate"
                and row["revision"] == previous["revision"]
                and not row["removed_at"],
            )
        if row["removed_at"]:
            raise CommandError("This webhook subscription was removed", 409)
        if row["revision"] != request.expected_revision:
            raise CommandError("This subscription changed. Refresh before saving.", 409)
        if request.action == "resume":
            active_database(workspace, connection, scope, row["database_id"], key)
        if request.action == "rotate":
            config["signing_secret"] = secrets.token_urlsafe(32)
        if request.action == "remove":
            config = {}
        revision = row["revision"] + 1
        paused = request.action == "pause" or (row["paused"] and request.action != "resume")
        row = connection.execute(
            "UPDATE space.webhook_subscriptions SET revision=%s,paused=%s,key_id=%s,"
            "encrypted_configuration=%s,encrypted_filter=%s,removed_at=CASE WHEN %s THEN "
            "clock_timestamp() ELSE removed_at END,updated_at=clock_timestamp() "
            "WHERE id=%s RETURNING *",
            (
                revision,
                paused,
                key.key.key_id,
                workspace._pack(
                    key,
                    scope,
                    subscription_id,
                    revision,
                    "webhook_subscription",
                    config,
                ),
                workspace._pack(
                    key,
                    scope,
                    subscription_id,
                    revision,
                    "webhook_filter",
                    {"transition": config.get("transition")},
                ),
                request.action == "remove",
                subscription_id,
            ),
        ).fetchone()
        receipt(connection, scope, request, digest, subscription_id, request.action, revision)
        return subscription_response(row, config, include_secret=request.action == "rotate")


DELIVERY_ROW = (
    "SELECT d.*,e.record_id,e.revision,e.previous_revision FROM space.webhook_deliveries d "
    "JOIN space.webhook_events e ON (e.organization_id,e.space_id,e.id)="
    "(d.organization_id,d.space_id,d.event_id) "
)


def public_delivery(connection, row):
    attempts = connection.execute(
        "SELECT cycle,attempt,started_at,finished_at,status_code,error_code "
        "FROM space.webhook_attempts "
        "WHERE delivery_id=%s ORDER BY cycle,attempt",
        (row["id"],),
    ).fetchall()
    return {
        "id": str(row["id"]),
        "event_id": str(row["event_id"]),
        "record_id": row["record_id"],
        "event_type": "database.record.changed",
        "revision": row["revision"],
        "previous_revision": row["previous_revision"],
        "status": row["status"],
        "attempts": row["attempts"],
        "cycle": row["cycle"],
        "error_code": row["error_code"],
        "status_code": row["status_code"],
        **{field: row[field].isoformat() for field in ("created_at", "updated_at", "available_at")},
        "receipts": [
            {
                **attempt,
                "started_at": attempt["started_at"].isoformat(),
                "finished_at": attempt["finished_at"].isoformat()
                if attempt["finished_at"]
                else None,
            }
            for attempt in attempts
        ],
    }


def deliveries(workspace, identity, space_id, subscription_id):
    with workspace.db.transaction(identity) as connection:
        owner_scope(workspace, connection, identity, space_id)
        subscription_row(connection, subscription_id)
        rows = connection.execute(
            DELIVERY_ROW
            + "WHERE d.subscription_id=%s ORDER BY d.created_at DESC,d.id DESC LIMIT 100",
            (subscription_id,),
        ).fetchall()
        result = [public_delivery(connection, row) for row in rows]
    with workspace.db.transaction(identity) as connection:
        owner_scope(workspace, connection, identity, space_id)
    return {"items": result}


def redeliver(workspace, identity, space_id, subscription_id, delivery_id, request: Redeliver):
    scope, key = owner_key(workspace, identity, space_id)
    digest = fingerprint(key, scope, "redeliver", request, subscription_id, delivery_id)
    with workspace.db.transaction(identity) as connection:
        scope = owner_scope(workspace, connection, identity, space_id)
        connection.execute("SELECT id FROM space.spaces FOR UPDATE")
        previous = previous_command(connection, request.command_id, digest)
        sub = subscription_row(connection, subscription_id, lock=True)
        row = connection.execute(
            DELIVERY_ROW + "WHERE d.id=%s AND d.subscription_id=%s FOR UPDATE OF d",
            (delivery_id, subscription_id),
        ).fetchone()
        if not row:
            raise CommandError("Webhook delivery not found", 404)
        if previous:
            return public_delivery(connection, row)
        if sub["paused"] or sub["removed_at"]:
            raise CommandError("Resume an active subscription before redelivery", 409)
        if row["status"] not in {"delivered", "failed"} or row["cycle"] >= 20:
            raise CommandError(
                "Only completed deliveries below the recovery limit can be redelivered", 409
            )
        expired = connection.execute(
            "SELECT created_at<clock_timestamp()-interval '30 days' AS expired "
            "FROM space.webhook_deliveries WHERE id=%s",
            (delivery_id,),
        ).fetchone()["expired"]
        if expired:
            raise CommandError("This delivery is outside the 30-day recovery window", 409)
        connection.execute(
            "UPDATE space.webhook_deliveries SET status='queued',attempts=0,cycle=cycle+1,"
            "available_at=clock_timestamp(),lease_id=NULL,lease_until=NULL,error_code=NULL,"
            "status_code=NULL,updated_at=clock_timestamp() WHERE id=%s",
            (delivery_id,),
        )
        receipt(
            connection,
            scope,
            request,
            digest,
            subscription_id,
            "redeliver",
            sub["revision"],
            delivery_id,
        )
        row = connection.execute(DELIVERY_ROW + "WHERE d.id=%s", (delivery_id,)).fetchone()
        return public_delivery(connection, row)
