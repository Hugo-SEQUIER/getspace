"""PostgreSQL job admission and a separate, least-privilege worker queue.

The queue records a verified actor receipt, not a JWT. Work may outlive logout;
Workspace checks the original grant and the lease before each access and commit.
"""

import hmac
import time
from uuid import UUID, uuid4

from space.job_context import active_job
from space.memory import RememberMemory
from space.memory_pipeline import MAX_BUILD_CHARS, BuildMemory
from space.security.auth import Identity, Unauthenticated

MAX_PENDING_PER_SPACE = 100
MAX_ATTEMPTS = 3
# One further lease may recover a committed command, but must never call a model.
MAX_RECOVERY_ATTEMPTS = 1
LEASE_SECONDS = 300


def public_job(row):
    result = {
        "id": str(row["id"]),
        "status": row["status"],
        "attempts": row["attempts"],
        "source_saved": True,
        "recall_ready": row["status"] == "ready",
        "result": {
            "resource_id": str(row["result_resource_id"]),
            "revision": row["result_revision"],
        }
        if row["result_resource_id"]
        else None,
        "error_code": row["error_code"],
    }
    if row.get("request_kind") == "memora_import":
        result.update(
            kind="memora_import",
            total=row["import_total"],
            completed=row["import_completed"],
            created_at=row["created_at"].isoformat(),
        )
    return result


def enqueue(workspace, identity, space_id, request: RememberMemory | BuildMemory):
    from space.workspace import CommandError, encode

    identity.check()
    kind = "remember" if isinstance(request, RememberMemory) else "build"
    payload = request.model_dump(mode="json")
    if not payload.get("links"):
        payload.pop("links", None)
    if kind == "remember" and len(request.title) + len(request.text) + 1 > MAX_BUILD_CHARS:
        raise CommandError("Extraction supports 12,000 characters including the title", 400)
    scope, key = workspace._key(identity, space_id, write=True)
    fingerprint = hmac.digest(
        key.key.material, b"space.job.v1\0" + kind.encode() + encode(payload), "sha256"
    )
    encrypted = workspace._pack(key, scope, request.command_id, 1, "memory_job", payload)
    with workspace.db.transaction(identity) as connection:
        scope = workspace._scope(connection, identity, space_id, write=True)
        # Serializes enqueue limits and reset/account deletion with ordinary writes.
        connection.execute("SELECT id FROM space.spaces FOR UPDATE")
        previous = connection.execute(
            "SELECT * FROM space.memory_jobs WHERE id=%s", (request.command_id,)
        ).fetchone()
        if previous:
            if (
                str(previous["client_id"] or "") != (identity.client_id or "")
                or str(previous["grant_id"] or "") != (identity.grant_id or "")
                or str(previous["agent_id"] or "") != (identity.agent_id or "")
                or not hmac.compare_digest(bytes(previous["fingerprint"]), fingerprint)
            ):
                raise CommandError("This job ID was already used for another request or grant", 409)
            return public_job(previous)
        if not workspace.memory_models.enabled:
            raise CommandError("Configure an extraction provider before queueing memory", 409)
        pending = connection.execute(
            "SELECT count(*) AS n FROM space.memory_jobs WHERE status IN ('queued','processing')"
        ).fetchone()["n"]
        if pending >= MAX_PENDING_PER_SPACE:
            raise CommandError(
                "This Space's extraction queue is full; retry after jobs finish", 503
            )
        if kind == "build" or request.source:
            source = request.source
            row = connection.execute(
                "SELECT current_revision FROM space.resources WHERE id=%s", (source.resource_id,)
            ).fetchone()
            if row is None or row["current_revision"] != source.revision:
                raise CommandError("Extraction requires a current source revision", 409)
        row = connection.execute(
            "INSERT INTO space.memory_jobs (organization_id,space_id,id,principal_id,"
            "issuer,subject,"
            "client_id,grant_id,agent_id,request_kind,key_id,encrypted_request,fingerprint,"
            "provider_fingerprint) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
            (
                scope["organization_id"],
                space_id,
                request.command_id,
                scope["principal_id"],
                identity.issuer,
                identity.subject,
                identity.client_id,
                identity.grant_id,
                identity.agent_id,
                kind,
                UUID(key.key.key_id),
                encrypted,
                fingerprint,
                workspace.memory_models.chat_fingerprint,
            ),
        ).fetchone()
        return public_job(row)


def get(workspace, identity, space_id, job_id):
    from space.workspace import CommandError

    with workspace.db.transaction(identity) as connection:
        workspace._scope(connection, identity, space_id)
        # Poll progress without transferring encrypted inputs or worker authorization receipts.
        row = connection.execute(
            "SELECT id,status,attempts,result_resource_id,result_revision,error_code,"
            "request_kind,import_total,import_completed,created_at "
            "FROM space.memory_jobs WHERE id=%s",
            (job_id,),
        ).fetchone()
        if not row:
            raise CommandError("Memory job not found", 404)
        return public_job(row)


def recent(workspace, identity, space_id):
    with workspace.db.transaction(identity) as connection:
        workspace._scope(connection, identity, space_id)
        rows = connection.execute(
            "SELECT id,status,attempts,result_resource_id,result_revision,error_code "
            "FROM space.memory_jobs WHERE request_kind IN ('remember','build') ORDER BY "
            "(status IN ('queued','processing')) DESC, created_at DESC LIMIT %s",
            (MAX_PENDING_PER_SPACE + 20,),
        ).fetchall()
        return {"items": [public_job(row) for row in rows]}


def claim(connection):
    """Rotate eligible Spaces, preferring those without a live job, then claim FIFO."""
    with connection.transaction():
        # A repeatedly killed recovery attempt must not cycle forever before it can
        # reach process()'s attempt check (for example, when key unwrapping hangs).
        connection.execute(
            "WITH exhausted AS (SELECT organization_id,space_id,id FROM space.memory_jobs "
            "WHERE status='processing' AND lease_until<now() AND attempts>=%s "
            "FOR UPDATE SKIP LOCKED LIMIT 100) "
            "UPDATE space.memory_jobs j SET status='failed',error_code='recovery_failed',"
            "lease_id=NULL,lease_until=NULL,updated_at=now() FROM exhausted e "
            "WHERE (j.organization_id,j.space_id,j.id)=(e.organization_id,e.space_id,e.id)",
            (MAX_ATTEMPTS + MAX_RECOVERY_ATTEMPTS,),
        )
        selected = connection.execute(
            "SELECT s.organization_id,s.space_id FROM space.memory_job_schedule s "
            "WHERE EXISTS (SELECT 1 FROM space.memory_jobs j "
            "WHERE (j.organization_id,j.space_id)=(s.organization_id,s.space_id) AND "
            "((j.status='queued' AND j.available_at<=now()) "
            "OR (j.status='processing' AND j.lease_until<now() AND j.attempts<%s))) "
            "ORDER BY EXISTS (SELECT 1 FROM space.memory_jobs j "
            "WHERE (j.organization_id,j.space_id)=(s.organization_id,s.space_id) "
            "AND j.status='processing' AND j.lease_until>now()), "
            "s.last_claimed_at NULLS FIRST,s.organization_id,s.space_id "
            "FOR UPDATE OF s SKIP LOCKED LIMIT 1",
            (MAX_ATTEMPTS + MAX_RECOVERY_ATTEMPTS,),
        ).fetchone()
        if selected is None:
            return None
        job = connection.execute(
            "WITH next_job AS (SELECT organization_id,space_id,id FROM space.memory_jobs "
            "WHERE organization_id=%s AND space_id=%s AND "
            "((status='queued' AND available_at<=now()) "
            "OR (status='processing' AND lease_until<now() AND attempts<%s)) "
            "ORDER BY available_at,created_at,id FOR UPDATE SKIP LOCKED LIMIT 1) "
            "UPDATE space.memory_jobs j SET status='processing',attempts=j.attempts+1,"
            "lease_id=%s,lease_until=now()+make_interval(secs=>%s),updated_at=now() "
            "FROM next_job n WHERE (j.organization_id,j.space_id,j.id)="
            "(n.organization_id,n.space_id,n.id) RETURNING j.*",
            (
                selected["organization_id"],
                selected["space_id"],
                MAX_ATTEMPTS + MAX_RECOVERY_ATTEMPTS,
                uuid4(),
                LEASE_SECONDS,
            ),
        ).fetchone()
        if job:
            connection.execute(
                "UPDATE space.memory_job_schedule SET last_claimed_at=clock_timestamp() "
                "WHERE organization_id=%s AND space_id=%s",
                (selected["organization_id"], selected["space_id"]),
            )
        return job


def finish(connection, job, *, result=None, error_code=None, retry=False):
    """Only the worker still holding the lease may publish a state transition."""
    # A catch-up job completes with an empty result: it produces no single resource.
    status = "ready" if result is not None else ("queued" if retry else "failed")
    with connection.transaction():
        return (
            connection.execute(
                "UPDATE space.memory_jobs SET status=%s,result_resource_id=COALESCE(%s,"
                "CASE WHEN request_kind='memora_import' THEN result_resource_id END),"
                "result_revision=COALESCE(%s,CASE WHEN request_kind='memora_import' "
                "THEN result_revision END),"
                "error_code=%s,available_at=now()+make_interval(secs=>%s),lease_id=NULL,"
                "lease_until=NULL,updated_at=now() WHERE organization_id=%s AND space_id=%s "
                "AND id=%s AND lease_id=%s AND status='processing' AND lease_until>now()",
                (
                    status,
                    result.get("id") if result else None,
                    result.get("revision") if result else None,
                    error_code,
                    min(60, 2 ** job["attempts"]),
                    job["organization_id"],
                    job["space_id"],
                    job["id"],
                    job["lease_id"],
                ),
            ).rowcount
            == 1
        )


def process(workspace, connection, job):
    """Use the queued actor's scope; never infer fresh authority from a new grant."""
    from space.workspace import CommandError, SnapshotChanged

    actor = Identity(
        job["issuer"],
        job["subject"],
        int(time.time()) + LEASE_SECONDS + 60,
        str(job["client_id"]) if job["client_id"] else None,
        str(job["grant_id"]) if job["grant_id"] else None,
        str(job["agent_id"]) if job.get("agent_id") else None,
    )
    receipt = active_job.set((job["id"], job["lease_id"]))
    result = None
    code, retry = None, False
    try:
        with workspace.db.transaction(actor) as runtime:
            scope = workspace._scope(runtime, actor, job["space_id"], write=True)
            key_row = workspace._key_row(runtime, scope, job["key_id"])
        key = workspace.keys.unwrap(scope["organization_id"], scope["space_id"], key_row)
        workspace._recheck(actor, job["space_id"])
        payload = workspace._unpack(
            key, scope, job["id"], 1, "memory_job", job["encrypted_request"]
        )
        if job["request_kind"] in {"memora_import", "reclassify"}:
            if job["request_kind"] == "memora_import":
                from space.memora_jobs import process_batch
            else:
                from space.reclassify_jobs import process_batch

            result, yielded = process_batch(workspace, connection, job, actor, payload)
            if yielded:
                return None
        else:
            request = (
                RememberMemory if job["request_kind"] == "remember" else BuildMemory
            ).model_validate(payload)
            recovery_only = job["attempts"] > MAX_ATTEMPTS
            provider_changed = (
                job["provider_fingerprint"] != workspace.memory_models.chat_fingerprint
            )
            if recovery_only or provider_changed:
                # Recovery is still allowed after a provider change, without model calls.
                _, result = workspace._memory_retry(
                    actor, job["space_id"], job["request_kind"], request
                )
                if result is None:
                    raise CommandError(
                        "Extraction attempts exhausted"
                        if recovery_only
                        else "Extraction provider changed; submit a new job",
                        400,
                    )
            else:
                # The operation checks its own command receipt before generating anything.
                operation = (
                    workspace.remember
                    if job["request_kind"] == "remember"
                    else workspace.build_memory
                )
                result = operation(
                    actor,
                    job["space_id"],
                    request,
                    **({"extract": True} if job["request_kind"] == "remember" else {}),
                )
    except (CommandError, Unauthenticated) as error:
        status = getattr(error, "status", 401)
        code = (
            "access_revoked"
            if status in {401, 403, 404}
            else "source_or_revision_changed"
            if status == 409
            else "extraction_failed"
        )
        retry = (status == 503 or isinstance(error, SnapshotChanged)) and job[
            "attempts"
        ] < MAX_ATTEMPTS
        if isinstance(error, SnapshotChanged):
            code = "context_changed"
    except Exception:
        # Never persist provider text, source text, credentials, or raw exception messages.
        code, retry = "temporarily_unavailable", job["attempts"] < MAX_ATTEMPTS
    finally:
        active_job.reset(receipt)
    if result is None and code != "access_revoked":
        # A provider/library may wrap the original authorization error. Never retry
        # revoked work merely because the wrapper made it look like a transient failure.
        try:
            with workspace.db.transaction(actor) as runtime:
                workspace._scope(runtime, actor, job["space_id"], write=True)
        except (CommandError, Unauthenticated) as error:
            if getattr(error, "status", 401) in {401, 403, 404}:
                code, retry = "access_revoked", False
        except Exception:
            code, retry = "temporarily_unavailable", job["attempts"] < MAX_ATTEMPTS
    finish(connection, job, result=result, error_code=code, retry=retry)
    return result
