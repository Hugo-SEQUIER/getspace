"""Parameters: write-granted agents of a Space owner and that Space's inbox subscriptions.
What it does: leases queued inbox deliveries to the pulling agent and records acknowledgements.
Output: event payloads with stable delivery IDs; receipts stay metadata-only.
"""

from uuid import UUID, uuid4

from pydantic import Field

from space.memory import Input
from space.webhook_delivery import MAX_ATTEMPTS, RETENTION_DAYS
from space.workspace import CommandError

LEASE_SECONDS = 600
MAX_LIMIT = 50


class PullEvents(Input):
    subscription_id: UUID | None = None
    database_id: UUID | None = None
    limit: int = Field(default=20, ge=1, le=MAX_LIMIT, strict=True)


class AcknowledgeEvents(Input):
    delivery_ids: list[UUID] = Field(min_length=1, max_length=MAX_LIMIT)


ELIGIBLE = (
    "FROM space.webhook_deliveries d JOIN space.webhook_subscriptions s ON "
    "(s.organization_id,s.space_id,s.id)=(d.organization_id,d.space_id,d.subscription_id) "
    "JOIN space.webhook_events e ON "
    "(e.organization_id,e.space_id,e.id)=(d.organization_id,d.space_id,d.event_id) "
    "WHERE s.delivery='inbox' AND NOT s.paused AND s.removed_at IS NULL "
    "AND ((d.status='queued' AND d.available_at<=clock_timestamp()) OR "
    "(d.status='processing' AND d.lease_until<=clock_timestamp())) AND d.attempts<%s "
    "AND d.created_at>=clock_timestamp()-make_interval(days=>%s) "
    "AND e.created_at>=clock_timestamp()-make_interval(days=>%s) "
    "AND (%s::uuid IS NULL OR s.id=%s) AND (%s::uuid IS NULL OR s.database_id=%s) "
)


def agent_scope(workspace, connection, identity, space_id):
    """Parameters: the live agent identity and selected Space.
    What it does: requires a write grant held by the Space's owner, like webhook management.
    Output: the verified scope or a fixed access error; nothing about the inbox is disclosed.
    """
    scope = workspace._scope(connection, identity, space_id, write=True)
    if scope["role"] != "owner":
        raise CommandError("Only agents of this Space's owner can use its event inbox", 403)
    return scope


def _criteria(request):
    sid = request.subscription_id
    did = request.database_id
    return (MAX_ATTEMPTS, RETENTION_DAYS, RETENTION_DAYS, sid, sid, did, did)


def pull(workspace, identity, space_id, request: PullEvents):
    """Parameters: an identified write-granted agent and optional subscription/database filters.
    What it does: leases the oldest eligible inbox deliveries for ten minutes and opens receipts.
    Output: events to act on; an unacknowledged lease returns to the queue on expiry.
    """
    with workspace.db.transaction(identity) as connection:
        scope = agent_scope(workspace, connection, identity, space_id)
        rows = connection.execute(
            "SELECT d.*,s.database_id " + ELIGIBLE + "ORDER BY d.available_at,d.created_at,d.id "
            "FOR UPDATE OF d SKIP LOCKED LIMIT %s",
            (*_criteria(request), request.limit),
        ).fetchall()
        keys = {}

        def scoped_key(key_id):
            reference = str(key_id)
            if reference not in keys:
                keys[reference] = workspace._unwrap_key(
                    scope["organization_id"],
                    scope["space_id"],
                    workspace._key_row(connection, scope, key_id),
                )
            return keys[reference]

        items = []
        for row in rows:
            if row["status"] == "processing":
                # The previous lease expired without an acknowledgement.
                connection.execute(
                    "SELECT space.inbox_attempt_finished(%s,%s,%s,'worker_lost')",
                    (row["id"], row["cycle"], row["attempts"]),
                )
            leased = connection.execute(
                "UPDATE space.webhook_deliveries SET status='processing',attempts=attempts+1,"
                "lease_id=%s,lease_until=clock_timestamp()+make_interval(secs=>%s),"
                "updated_at=clock_timestamp() WHERE id=%s RETURNING *",
                (uuid4(), LEASE_SECONDS, row["id"]),
            ).fetchone()
            started = connection.execute(
                "SELECT space.inbox_attempt_started(%s,%s,%s) AS n",
                (leased["id"], leased["cycle"], leased["attempts"]),
            ).fetchone()["n"]
            if started != 1:
                raise CommandError("Only agents of this Space's owner can use its event inbox", 403)
            items.append(
                {
                    "delivery_id": str(leased["id"]),
                    "subscription_id": str(leased["subscription_id"]),
                    "database_id": str(row["database_id"]),
                    "attempt": leased["attempts"],
                    "cycle": leased["cycle"],
                    "lease_until": leased["lease_until"].isoformat(),
                    "event": workspace._unpack(
                        scoped_key(leased["key_id"]),
                        scope,
                        leased["id"],
                        1,
                        "webhook_delivery",
                        leased["encrypted_payload"],
                    ),
                }
            )
        has_more = connection.execute(
            "SELECT EXISTS (SELECT 1 " + ELIGIBLE + ") AS more", _criteria(request)
        ).fetchone()["more"]
    return {"items": items, "has_more": has_more, "lease_seconds": LEASE_SECONDS}


def acknowledge(workspace, identity, space_id, request: AcknowledgeEvents):
    """Parameters: an identified write-granted agent and the delivery IDs it finished.
    What it does: marks inbox deliveries delivered and closes their open receipts.
    Output: one status per requested ID; repeating an acknowledgement is harmless.
    """
    ids = [str(delivery_id) for delivery_id in dict.fromkeys(request.delivery_ids)]
    with workspace.db.transaction(identity) as connection:
        agent_scope(workspace, connection, identity, space_id)
        known = {
            str(row["id"]): row
            for row in connection.execute(
                "SELECT d.id,d.status,d.cycle,d.attempts FROM space.webhook_deliveries d "
                "JOIN space.webhook_subscriptions s ON (s.organization_id,s.space_id,s.id)="
                "(d.organization_id,d.space_id,d.subscription_id) "
                "WHERE s.delivery='inbox' AND d.id=ANY(%s::uuid[]) FOR UPDATE OF d",
                (ids,),
            ).fetchall()
        }
        items = []
        for delivery_id in ids:
            row = known.get(delivery_id)
            if row is None:
                items.append({"delivery_id": delivery_id, "status": "not_found"})
                continue
            if row["status"] not in {"queued", "processing"}:
                items.append({"delivery_id": delivery_id, "status": "already_done"})
                continue
            connection.execute(
                "UPDATE space.webhook_deliveries SET status='delivered',status_code=NULL,"
                "error_code=NULL,lease_id=NULL,lease_until=NULL,available_at=clock_timestamp(),"
                "updated_at=clock_timestamp() WHERE id=%s",
                (delivery_id,),
            )
            if row["attempts"] > 0:
                connection.execute(
                    "SELECT space.inbox_attempt_finished(%s,%s,%s,NULL)",
                    (delivery_id, row["cycle"], row["attempts"]),
                )
            items.append({"delivery_id": delivery_id, "status": "acknowledged"})
    return {"items": items}
