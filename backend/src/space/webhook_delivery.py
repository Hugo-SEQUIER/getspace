"""Parameters: the webhook queue, runtime Workspace and leased delivery receipts.
What it does: claims bounded work, checks current ownership and sends signed attempts.
Output: durable status transitions and metadata-only attempt receipts.
"""

import secrets
import time
from uuid import uuid4

from space import webhook_http
from space.security.auth import Identity, Unauthenticated
from space.webhook_http import AttemptResult

MAX_ATTEMPTS = 5
LEASE_SECONDS = 60
RETENTION_DAYS = 30
MAINTENANCE_BATCH = 100
RETRY_DELAYS = (30, 120, 480, 1920)
RETRYABLE_ERRORS = frozenset(
    {
        "dns_error",
        "timeout",
        "network_error",
        "http_5xx",
        "rate_limited",
        "temporarily_unavailable",
        "subscription_paused",
    }
)
SAFE_ERRORS = RETRYABLE_ERRORS | frozenset(
    {
        "invalid_endpoint",
        "unsafe_address",
        "tls_error",
        "redirect",
        "http_4xx",
        "invalid_response",
        "invalid_request",
        "access_revoked",
        "subscription_removed",
        "expired",
        "obsolete_lease",
        "invalid_payload",
        "worker_lost",
        "attempts_exhausted",
    }
)


def retry_delay(attempt, result):
    """Parameters: the completed attempt number and safe transport result.
    What it does: applies bounded exponential backoff, jitter and eligible Retry-After.
    Output: a delay of at most 3600 seconds.
    """
    base = RETRY_DELAYS[max(0, min(len(RETRY_DELAYS) - 1, attempt - 1))]
    delay = base + secrets.randbelow(max(1, base // 5 + 1))
    if result.status_code in (429, 503) and type(result.retry_after) is int:
        delay = max(delay, max(1, min(3600, result.retry_after)))
    return min(3600, delay)


def _safe_result(result):
    """Parameters: an attempt outcome supplied to the queue.
    What it does: retains only valid HTTP status and fixed diagnostics, not raw errors.
    Output: a controlled AttemptResult suitable for storage.
    """
    status = result.status_code
    if type(status) is not int or not 100 <= status <= 599:
        status = None
    code = result.error_code
    if code not in SAFE_ERRORS and code is not None:
        code = "temporarily_unavailable"
    if code is None and (status is None or not 200 <= status < 300):
        code = "invalid_response"
    return AttemptResult(status, code, result.retry_after)


def _lost_attempt(connection, job):
    """Parameters: the locked delivery whose worker lease expired.
    What it does: finishes its unfinished attempt receipt without receiver content.
    Output: an audited worker_lost receipt, if the old attempt was unfinished.
    """
    connection.execute(
        "UPDATE space.webhook_attempts SET finished_at=clock_timestamp(),"
        "status_code=NULL,error_code='worker_lost' WHERE organization_id=%s AND space_id=%s "
        "AND delivery_id=%s AND cycle=%s AND attempt=%s AND finished_at IS NULL",
        (job["organization_id"], job["space_id"], job["id"], job["cycle"], job["attempts"]),
    )


def claim(connection):
    """Parameters: a connection restricted to space_webhook_worker.
    What it does: expires old work, recovers crashed receipts and leases one eligible row.
    Output: a stable delivery with a fresh UUID lease and attempt receipt, or None.
    """
    with connection.transaction():
        stale = connection.execute(
            "SELECT d.*,s.removed_at,"
            "(d.created_at<clock_timestamp()-make_interval(days=>%s) OR "
            "e.created_at<clock_timestamp()-make_interval(days=>%s)) AS expired "
            "FROM space.webhook_deliveries d JOIN space.webhook_subscriptions s ON "
            "(s.organization_id,s.space_id,s.id)=(d.organization_id,d.space_id,d.subscription_id) "
            "JOIN space.webhook_events e ON "
            "(e.organization_id,e.space_id,e.id)=(d.organization_id,d.space_id,d.event_id) "
            "WHERE (d.status='queued' OR (d.status='processing' AND "
            "d.lease_until<=clock_timestamp())) AND (s.removed_at IS NOT NULL OR "
            "d.attempts>=%s OR d.created_at<clock_timestamp()-make_interval(days=>%s) OR "
            "e.created_at<clock_timestamp()-make_interval(days=>%s) OR d.status='processing') "
            "ORDER BY d.available_at,d.created_at,d.id "
            "FOR UPDATE OF d SKIP LOCKED LIMIT %s",
            (
                RETENTION_DAYS,
                RETENTION_DAYS,
                MAX_ATTEMPTS,
                RETENTION_DAYS,
                RETENTION_DAYS,
                MAINTENANCE_BATCH,
            ),
        ).fetchall()
        for old in stale:
            if old["status"] == "processing":
                _lost_attempt(connection, old)
            code = (
                "expired"
                if old["expired"]
                else "subscription_removed"
                if old["removed_at"]
                else "attempts_exhausted"
                if old["attempts"] >= MAX_ATTEMPTS
                else "worker_lost"
            )
            connection.execute(
                "UPDATE space.webhook_deliveries SET status=%s,error_code=%s,status_code=NULL,"
                "available_at=clock_timestamp(),lease_id=NULL,lease_until=NULL,"
                "updated_at=clock_timestamp() WHERE organization_id=%s AND space_id=%s AND id=%s",
                (
                    "queued" if code == "worker_lost" else "failed",
                    code,
                    old["organization_id"],
                    old["space_id"],
                    old["id"],
                ),
            )
        job = connection.execute(
            "SELECT d.* FROM space.webhook_deliveries d JOIN space.webhook_subscriptions s ON "
            "(s.organization_id,s.space_id,s.id)=(d.organization_id,d.space_id,d.subscription_id) "
            "JOIN space.webhook_events e ON "
            "(e.organization_id,e.space_id,e.id)=(d.organization_id,d.space_id,d.event_id) "
            "WHERE ((d.status='queued' AND d.available_at<=clock_timestamp()) OR "
            "(d.status='processing' AND d.lease_until<=clock_timestamp())) AND d.attempts<%s "
            "AND s.delivery='endpoint' AND NOT s.paused AND s.removed_at IS NULL "
            "AND d.created_at>=clock_timestamp()-make_interval(days=>%s) "
            "AND e.created_at>=clock_timestamp()-make_interval(days=>%s) "
            "ORDER BY d.available_at,d.created_at,d.id FOR UPDATE OF d SKIP LOCKED LIMIT 1",
            (MAX_ATTEMPTS, RETENTION_DAYS, RETENTION_DAYS),
        ).fetchone()
        if job is None:
            return None
        if job["status"] == "processing":
            _lost_attempt(connection, job)
        claimed = connection.execute(
            "UPDATE space.webhook_deliveries SET status='processing',attempts=attempts+1,"
            "lease_id=%s,lease_until=clock_timestamp()+make_interval(secs=>%s),"
            "updated_at=clock_timestamp() WHERE organization_id=%s AND space_id=%s AND id=%s "
            "RETURNING *",
            (uuid4(), LEASE_SECONDS, job["organization_id"], job["space_id"], job["id"]),
        ).fetchone()
        connection.execute(
            "INSERT INTO space.webhook_attempts "
            "(organization_id,space_id,delivery_id,cycle,attempt,started_at) "
            "VALUES (%s,%s,%s,%s,%s,clock_timestamp())",
            (
                claimed["organization_id"],
                claimed["space_id"],
                claimed["id"],
                claimed["cycle"],
                claimed["attempts"],
            ),
        )
        return claimed


def finish(connection, job, result):
    """Parameters: a leased delivery and its metadata-only attempt result.
    What it does: publishes a bounded outcome and receipt only while its lease is current.
    Output: True for a committed lease CAS; False for an expired or obsolete worker.
    """
    result = _safe_result(result)
    delivered = result.error_code is None and result.status_code is not None
    retry = result.error_code in RETRYABLE_ERRORS and job["attempts"] < MAX_ATTEMPTS
    status = "delivered" if delivered else "queued" if retry else "failed"
    delay = retry_delay(job["attempts"], result) if retry else 0
    with connection.transaction():
        changed = (
            connection.execute(
                # One clock reading per statement: clock_timestamp() advances between
                # columns, so available_at would fall a microsecond short of the delay.
                "UPDATE space.webhook_deliveries SET status=%s,status_code=%s,error_code=%s,"
                "available_at=statement_timestamp()+make_interval(secs=>%s),lease_id=NULL,"
                "lease_until=NULL,updated_at=statement_timestamp() WHERE organization_id=%s "
                "AND space_id=%s AND id=%s AND lease_id=%s AND cycle=%s AND attempts=%s "
                "AND status='processing' AND lease_until>statement_timestamp()",
                (
                    status,
                    result.status_code,
                    result.error_code,
                    delay,
                    job["organization_id"],
                    job["space_id"],
                    job["id"],
                    job["lease_id"],
                    job["cycle"],
                    job["attempts"],
                ),
            ).rowcount
            == 1
        )
        if changed:
            connection.execute(
                "UPDATE space.webhook_attempts SET finished_at=clock_timestamp(),"
                "status_code=%s,error_code=%s WHERE organization_id=%s AND space_id=%s "
                "AND delivery_id=%s AND cycle=%s AND attempt=%s AND finished_at IS NULL",
                (
                    result.status_code,
                    result.error_code,
                    job["organization_id"],
                    job["space_id"],
                    job["id"],
                    job["cycle"],
                    job["attempts"],
                ),
            )
        return changed


def _owner_scope(workspace, runtime, actor, job, subscription):
    """Parameters: the original human receipt and current subscription scope.
    What it does: requires its creator to remain the current owner of this exact Space.
    Output: the authorized runtime scope, or an access error.
    """
    from space.workspace import CommandError

    scope = workspace._scope(runtime, actor, job["space_id"], write=True, human=True)
    if (
        scope["role"] != "owner"
        or scope["principal_id"] != subscription["created_by"]
        or scope["organization_id"] != job["organization_id"]
        or scope["space_id"] != job["space_id"]
    ):
        raise CommandError("Webhook owner access was removed", 403)
    return scope


def _current_delivery(connection, job):
    """Parameters: the claimed delivery identity, cycle and lease.
    What it does: locks its current state and uses wall time to fence expired leases.
    Output: the current row including its event retention flag, or None.
    """
    return connection.execute(
        "SELECT d.*, (d.created_at<clock_timestamp()-make_interval(days=>%s) OR "
        "e.created_at<clock_timestamp()-make_interval(days=>%s)) AS expired "
        "FROM space.webhook_deliveries d JOIN space.webhook_events e ON "
        "(e.organization_id,e.space_id,e.id)=(d.organization_id,d.space_id,d.event_id) "
        "WHERE d.organization_id=%s AND d.space_id=%s AND d.id=%s AND d.lease_id=%s "
        "AND d.cycle=%s AND d.attempts=%s AND d.status='processing' "
        "AND d.lease_until>clock_timestamp() FOR SHARE OF d",
        (
            RETENTION_DAYS,
            RETENTION_DAYS,
            job["organization_id"],
            job["space_id"],
            job["id"],
            job["lease_id"],
            job["cycle"],
            job["attempts"],
        ),
    ).fetchone()


def process(workspace, connection, job):
    """Parameters: a runtime Workspace, worker queue and claimed delivery.
    What it does: holds subscription and owner locks through one signed network attempt.
    Output: a safe AttemptResult; the caller must publish it with finish().
    """
    from space.workspace import CommandError

    try:
        with connection.transaction():
            subscription = connection.execute(
                "SELECT * FROM space.webhook_subscriptions WHERE organization_id=%s "
                "AND space_id=%s AND id=%s FOR SHARE",
                (job["organization_id"], job["space_id"], job["subscription_id"]),
            ).fetchone()
            current = _current_delivery(connection, job)
            if current is None:
                return AttemptResult(error_code="obsolete_lease")
            if subscription is None or subscription["removed_at"] is not None:
                return AttemptResult(error_code="subscription_removed")
            if current["expired"]:
                return AttemptResult(error_code="expired")
            if subscription["paused"]:
                return AttemptResult(error_code="subscription_paused")
            actor = Identity(
                subscription["issuer"],
                subscription["subject"],
                int(time.time()) + LEASE_SECONDS + 30,
            )
            with workspace.db.transaction(actor) as runtime:
                scope = _owner_scope(workspace, runtime, actor, job, subscription)
                configuration_key = workspace._key_row(runtime, scope, subscription["key_id"])
                delivery_key = workspace._key_row(runtime, scope, current["key_id"])
            keys = {}
            for row in (configuration_key, delivery_key):
                if row is None:
                    return AttemptResult(error_code="invalid_payload")
                if row["id"] not in keys:
                    keys[row["id"]] = workspace.keys.unwrap(
                        scope["organization_id"], scope["space_id"], row
                    )
            try:
                configuration = workspace._unpack(
                    keys[subscription["key_id"]],
                    scope,
                    subscription["id"],
                    subscription["revision"],
                    "webhook_subscription",
                    subscription["encrypted_configuration"],
                )
                payload = workspace._unpack(
                    keys[current["key_id"]],
                    scope,
                    current["id"],
                    1,
                    "webhook_delivery",
                    current["encrypted_payload"],
                )
                if (
                    not isinstance(configuration, dict)
                    or not isinstance(payload, dict)
                    or not isinstance(configuration.get("endpoint_url"), str)
                    or not isinstance(configuration.get("signing_secret"), str)
                    or not configuration["signing_secret"]
                ):
                    return AttemptResult(error_code="invalid_payload")
                from space.workspace import encode

                body = encode(payload)
            except Exception:
                return AttemptResult(error_code="invalid_payload")
            with workspace.db.transaction(actor) as runtime:
                _owner_scope(workspace, runtime, actor, job, subscription)
                current = _current_delivery(connection, job)
                if current is None:
                    return AttemptResult(error_code="obsolete_lease")
                if current["expired"]:
                    return AttemptResult(error_code="expired")
                timestamp = int(time.time())
                headers = {
                    "X-Space-Event-ID": str(current["event_id"]),
                    "X-Space-Delivery-ID": str(current["id"]),
                    "X-Space-Timestamp": str(timestamp),
                    "X-Space-Signature": webhook_http.sign(
                        configuration["signing_secret"], timestamp, body
                    ),
                }
                return _safe_result(webhook_http.send(configuration["endpoint_url"], body, headers))
    except (CommandError, Unauthenticated) as error:
        return AttemptResult(
            error_code="access_revoked"
            if getattr(error, "status", 401) in (401, 403, 404)
            else "temporarily_unavailable"
        )
    except Exception:
        return AttemptResult(error_code="temporarily_unavailable")
