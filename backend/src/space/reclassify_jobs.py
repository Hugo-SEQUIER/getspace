"""One automatic catch-up per Space for claims the old catalog limit left unassigned.

Space has no background authority of its own. The catch-up is queued by the next memory
write and runs under that writer's grant, through the ordinary job queue and quotas.
"""

import hmac
import logging
from functools import wraps
from uuid import UUID, uuid4

from space import jobs, memory_classification
from space.job_context import active_job

logger = logging.getLogger(__name__)

LIMIT_REASONS = frozenset({"catalog_limit", "input_limit"})
MEMORIES_PER_TURN = 5
# A failed catch-up may be queued again by a later write, at most once a day.
EXISTING = (
    "SELECT count(*) AS n FROM space.memory_jobs WHERE request_kind='reclassify' "
    "AND (status<>'failed' OR updated_at>now()-interval '1 day')"
)


def limited(claim):
    classification = claim.get("classification") or {}
    return classification.get("method") != "manual" and classification.get("reason") in (
        LIMIT_REASONS
    )


def after_memory_write(method):
    """Queue the catch-up once a memory write has committed; never fail that write."""

    @wraps(method)
    def wrapper(workspace, identity, space_id, *args, **kwargs):
        result = method(workspace, identity, space_id, *args, **kwargs)
        maybe_enqueue(workspace, identity, space_id)
        return result

    return wrapper


def maybe_enqueue(workspace, identity, space_id):
    if active_job.get() or not memory_classification.enabled(workspace):
        return None
    try:
        return enqueue(workspace, identity, space_id)
    except Exception:
        # Content-free: the write already succeeded and the next write can try again.
        logger.warning("Memory reclassification catch-up was not queued")
        return None


def enqueue(workspace, identity, space_id):
    """One transaction: a cheap existence check on every write, the insert only once."""
    from space.workspace import encode

    with workspace.db.transaction(identity) as connection:
        scope = workspace._scope(connection, identity, space_id, write=True)
        if connection.execute(EXISTING).fetchone()["n"]:
            return None
        # Serializes concurrent writers, so a Space gets one catch-up at a time.
        connection.execute("SELECT id FROM space.spaces FOR UPDATE")
        if connection.execute(EXISTING).fetchone()["n"]:
            return None
        pending = connection.execute(
            "SELECT count(*) AS n FROM space.memory_jobs WHERE status IN ('queued','processing')"
        ).fetchone()["n"]
        if pending >= jobs.MAX_PENDING_PER_SPACE:
            return None
        ids = [
            str(row["id"])
            for row in connection.execute(
                "SELECT id FROM space.resources WHERE memory_kind='memory' ORDER BY id"
            ).fetchall()
        ]
        if not ids:
            return None
        # Runs inside the write's key operation, so the unwrapped key is reused.
        key = workspace._unwrap_key(
            scope["organization_id"], scope["space_id"], workspace._key_row(connection, scope)
        )
        job_id = uuid4()
        payload = {"version": memory_classification.VERSION, "memory_ids": ids}
        row = connection.execute(
            "INSERT INTO space.memory_jobs (organization_id,space_id,id,principal_id,"
            "issuer,subject,client_id,grant_id,agent_id,request_kind,key_id,encrypted_request,"
            "fingerprint,provider_fingerprint,import_total) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'reclassify',%s,%s,%s,%s,%s) RETURNING *",
            (
                scope["organization_id"],
                space_id,
                job_id,
                scope["principal_id"],
                identity.issuer,
                identity.subject,
                identity.client_id,
                identity.grant_id,
                identity.agent_id,
                UUID(key.key.key_id),
                workspace._pack(key, scope, job_id, 1, "memory_job", payload),
                hmac.digest(
                    key.key.material, b"space.reclassify-job.v1\0" + encode(payload), "sha256"
                ),
                workspace.memory_models.chat_fingerprint,
                len(ids),
            ),
        ).fetchone()
    return jobs.public_job(row)


def process_batch(workspace, connection, job, actor, payload):
    """Reclassify a bounded turn of memories, checkpoint, then yield for fair scheduling."""
    from space.workspace import CommandError

    ids = payload["memory_ids"]
    if len(ids) != job["import_total"]:
        raise CommandError("Invalid reclassification payload", 400)
    completed = job["import_completed"]
    for memory_id in ids[completed : completed + MEMORIES_PER_TURN]:
        workspace.reclassify_limited(actor, job["space_id"], UUID(memory_id), job["id"])
        completed += 1
        with connection.transaction():
            changed = connection.execute(
                "UPDATE space.memory_jobs SET import_completed=%s,updated_at=now() "
                "WHERE organization_id=%s AND space_id=%s AND id=%s AND lease_id=%s "
                "AND status='processing' AND lease_until>now()",
                (completed, job["organization_id"], job["space_id"], job["id"], job["lease_id"]),
            ).rowcount
        if not changed:
            raise CommandError("Reclassification lease expired", 409)
    if completed < len(ids):
        with connection.transaction():
            connection.execute(
                "UPDATE space.memory_jobs SET status='queued',attempts=0,error_code=NULL,"
                "available_at=now(),lease_id=NULL,lease_until=NULL,updated_at=now() "
                "WHERE organization_id=%s AND space_id=%s AND id=%s AND lease_id=%s "
                "AND status='processing' AND lease_until>now()",
                (job["organization_id"], job["space_id"], job["id"], job["lease_id"]),
            )
        return None, True
    return {}, False
