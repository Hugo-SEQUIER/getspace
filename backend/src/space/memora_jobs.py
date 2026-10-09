"""Authenticated Memora previews and durable, encrypted imports for the app."""

import hmac
from uuid import UUID

from pydantic import Field

from space import jobs
from space.memora_import import ImportProblem, parse_export, plan_import
from space.memora_receipts import checked_request, resolve_request, source_request
from space.memory import Input
from space.workspace import CommandError, encode

MAX_IMPORT_BYTES = 800_000
RECORDS_PER_TURN = 4


class Preview(Input):
    source_id: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=MAX_IMPORT_BYTES)
    namespaces: list[str] | None = Field(default=None, min_length=1, max_length=100)


class Start(Preview):
    command_id: UUID
    namespaces: list[str] = Field(min_length=1, max_length=100)


class Retry(Input):
    command_id: UUID


def _plan(request, space_id):
    try:
        raw = request.text.encode("utf-8")
        if len(raw) > MAX_IMPORT_BYTES:
            raise ImportProblem("Memory metadata exceeds 800 KB; split this export")
        export = parse_export(raw)
        plan = plan_import(export, request.source_id, space_id=space_id)
        if request.namespaces:
            selected = set(request.namespaces)
            if selected - {record.namespace for record in plan.records}:
                raise ImportProblem("A selected namespace is absent or invalid")
            if plan.errors:
                raise ImportProblem("Resolve the record errors before importing")
            rows = [record.provenance for record in plan.records if record.namespace in selected]
            rows.sort(key=lambda row: (row["namespace"], row["id"]))
            plan = plan_import(
                {"count": len(rows), "records": rows}, request.source_id, space_id=space_id
            )
        return plan
    except (ImportProblem, UnicodeError) as error:
        raise CommandError(
            str(error) if isinstance(error, ImportProblem) else "Invalid Unicode", 400
        ) from None


def _source_request(item):
    # Compatibility for retained import receipts; this request is never written.
    return source_request(item)


def _existing(workspace, identity, space_id, plan):
    scope, key = workspace._key(identity, space_id, write=True)
    ids = [item.source.command_id for item in plan.writes]
    ids += [item.memory_command_id for item in plan.writes]
    with workspace.db.transaction(identity) as connection:
        scope = workspace._scope(connection, identity, space_id, write=True, human=True)
        rows = connection.execute(
            "SELECT * FROM space.commands WHERE id=ANY(%s)",
            (ids,),
        ).fetchall()
    previous = {row["id"]: row for row in rows}
    conflicts, imported = [], 0
    for item in plan.writes:
        try:
            checked_request(workspace, identity, scope, key, item, previous)
        except CommandError as error:
            if error.status != 409:
                raise
            conflicts.append(item.record.row)
        if item.memory_command_id in previous:
            imported += 1
    return {"previously_imported": imported, "conflict_rows": conflicts}


def preview(workspace, identity, space_id, request: Preview):
    identity.check(human=True)
    plan = _plan(request, space_id)
    summary = plan.summary()
    summary["notes"][0] = "Authenticated preview: destination access and retained imports checked."
    return summary | _existing(workspace, identity, space_id, plan)


def start(workspace, identity, space_id, request: Start):
    identity.check(human=True)
    plan = _plan(request, space_id)
    if not plan.ready:
        raise CommandError("Resolve all record errors before importing", 400)
    if _existing(workspace, identity, space_id, plan)["conflict_rows"]:
        raise CommandError("Previously imported records differ or belong to another writer", 409)
    payload = {
        "source_id": request.source_id,
        "records": [record.provenance for record in plan.records],
    }
    return _enqueue(workspace, identity, space_id, request.command_id, payload)


def _enqueue(workspace, identity, space_id, job_id, payload, *, completed=0, previous_result=None):
    if len(encode(payload)) > MAX_IMPORT_BYTES:
        raise CommandError("Memory metadata exceeds 800 KB; split this export", 400)
    scope, key = workspace._key(identity, space_id, write=True)
    fingerprint = hmac.digest(
        key.key.material, b"space.memora-job.v1\0" + encode(payload), "sha256"
    )
    encrypted = workspace._pack(key, scope, job_id, 1, "memory_job", payload)
    with workspace.db.transaction(identity) as connection:
        scope = workspace._scope(connection, identity, space_id, write=True, human=True)
        connection.execute("SELECT id FROM space.spaces FOR UPDATE")
        previous = connection.execute(
            "SELECT * FROM space.memory_jobs WHERE id=%s", (job_id,)
        ).fetchone()
        if previous:
            if (
                previous["principal_id"] != scope["principal_id"]
                or previous["request_kind"] != "memora_import"
                or not hmac.compare_digest(bytes(previous["fingerprint"]), fingerprint)
            ):
                raise CommandError("This import ID belongs to another request", 409)
            return jobs.public_job(previous)
        if not workspace.memory_models.enabled:
            raise CommandError("Configure a memory provider before importing", 409)
        pending = connection.execute(
            "SELECT count(*) AS n FROM space.memory_jobs WHERE status IN ('queued','processing')"
        ).fetchone()["n"]
        if pending >= jobs.MAX_PENDING_PER_SPACE:
            raise CommandError("This Space's memory queue is full; try after jobs finish", 503)
        row = connection.execute(
            "INSERT INTO space.memory_jobs (organization_id,space_id,id,principal_id,"
            "issuer,subject,"
            "request_kind,key_id,encrypted_request,fingerprint,provider_fingerprint,import_total,"
            "import_completed,result_resource_id,result_revision) "
            "VALUES (%s,%s,%s,%s,%s,%s,'memora_import',%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
            (
                scope["organization_id"],
                space_id,
                job_id,
                scope["principal_id"],
                identity.issuer,
                identity.subject,
                UUID(key.key.key_id),
                encrypted,
                fingerprint,
                workspace.memory_models.chat_fingerprint,
                len(payload["records"]),
                completed,
                previous_result[0] if previous_result else None,
                previous_result[1] if previous_result else None,
            ),
        ).fetchone()
    return jobs.public_job(row)


def recent(workspace, identity, space_id):
    with workspace.db.transaction(identity) as connection:
        workspace._scope(connection, identity, space_id, human=True)
        rows = connection.execute(
            "SELECT id,status,attempts,result_resource_id,result_revision,error_code,"
            "request_kind,import_total,import_completed,created_at "
            "FROM space.memory_jobs WHERE request_kind='memora_import' "
            "ORDER BY (status IN ('queued','processing')) DESC, created_at DESC LIMIT 30"
        ).fetchall()
    return {"items": [jobs.public_job(row) for row in rows]}


def retry(workspace, identity, space_id, job_id, request: Retry):
    with workspace.db.transaction(identity) as connection:
        scope = workspace._scope(connection, identity, space_id, write=True, human=True)
        row = connection.execute(
            "SELECT * FROM space.memory_jobs WHERE id=%s AND request_kind='memora_import'",
            (job_id,),
        ).fetchone()
        if not row or row["principal_id"] != scope["principal_id"]:
            raise CommandError("Import not found for this account", 404)
        if row["status"] != "failed":
            raise CommandError("Only a stopped import can be resumed", 409)
        key_row = workspace._key_row(connection, scope, row["key_id"])
    key = workspace.keys.unwrap(scope["organization_id"], space_id, key_row)
    payload = workspace._unpack(key, scope, job_id, 1, "memory_job", row["encrypted_request"])
    return _enqueue(
        workspace,
        identity,
        space_id,
        request.command_id,
        payload,
        completed=row["import_completed"],
        previous_result=(row["result_resource_id"], row["result_revision"]),
    )


def process_batch(workspace, connection, job, actor, payload):
    """Checkpoint each record and yield after a bounded turn for fair scheduling."""
    plan = plan_import(
        {"count": len(payload["records"]), "records": payload["records"]},
        payload["source_id"],
        space_id=job["space_id"],
    )
    if not plan.ready or len(plan.writes) != job["import_total"]:
        raise CommandError("Invalid import payload", 400)
    completed = job["import_completed"]
    result = None
    if completed == len(plan.writes):
        result = workspace.get_resource(
            actor, job["space_id"], job["result_resource_id"], job["result_revision"]
        )
    for item in plan.writes[completed : completed + RECORDS_PER_TURN]:
        request = item.memory()
        recovery_only = job["attempts"] > jobs.MAX_ATTEMPTS
        provider_changed = job["provider_fingerprint"] != workspace.memory_models.chat_fingerprint
        if recovery_only or provider_changed:
            request = resolve_request(workspace, actor, job["space_id"], request)
            _, result = workspace._memory_retry(actor, job["space_id"], "remember_claims", request)
            if result is None:
                raise CommandError(
                    "Import attempts exhausted"
                    if recovery_only
                    else "Memory provider changed; resume the import",
                    400,
                )
        else:
            # remember_claims owns the idempotent lookup; do not repeat it for every row.
            result = workspace.remember_claims(actor, job["space_id"], request)
        completed += 1
        with connection.transaction():
            changed = connection.execute(
                "UPDATE space.memory_jobs SET import_completed=%s,result_resource_id=%s,"
                "result_revision=%s,updated_at=now() WHERE organization_id=%s AND space_id=%s "
                "AND id=%s AND lease_id=%s AND status='processing' AND lease_until>now()",
                (
                    completed,
                    result["id"],
                    result["revision"],
                    job["organization_id"],
                    job["space_id"],
                    job["id"],
                    job["lease_id"],
                ),
            ).rowcount
        if not changed:
            raise CommandError("Import lease expired", 409)
    if completed < len(plan.writes):
        with connection.transaction():
            connection.execute(
                "UPDATE space.memory_jobs SET status='queued',attempts=0,error_code=NULL,"
                "available_at=now(),lease_id=NULL,lease_until=NULL,updated_at=now() "
                "WHERE organization_id=%s AND space_id=%s AND id=%s AND lease_id=%s "
                "AND status='processing' AND lease_until>now()",
                (job["organization_id"], job["space_id"], job["id"], job["lease_id"]),
            )
        return None, True
    return result, False
