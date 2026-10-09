"""Validate both generations of import receipts without creating source documents."""

import hmac

from space.memora_import import ImportProblem, plan_import
from space.memory import SourceReference
from space.workspace import CommandError, SaveResource, encode


def source_request(item):
    # Old CLI payloads included default fields; keep that exact fingerprint contract.
    return SaveResource.model_validate(item.source.model_dump(mode="json"))


def checked_request(workspace, identity, scope, key, item, previous):
    """Choose a legacy request only when its original memory already committed."""
    request = item.memory()
    saved_source = previous.get(item.source.command_id)
    legacy = None
    if saved_source:
        fingerprint = hmac.digest(
            key.key.material,
            b"space.command.v1\0" + encode(source_request(item).model_dump(mode="json")),
            "sha256",
        )
        workspace._validate_previous(identity, scope, saved_source, fingerprint)
        legacy = item.memory(
            SourceReference(
                resource_id=saved_source["resource_id"], revision=saved_source["revision"]
            )
        )
    saved_memory = previous.get(item.memory_command_id)
    fingerprint = workspace._memory_fingerprint(identity, key, "remember_claims", request)
    if (
        saved_memory
        and legacy
        and not hmac.compare_digest(bytes(saved_memory["fingerprint"]), fingerprint)
    ):
        fingerprint = workspace._memory_fingerprint(identity, key, "remember_claims", legacy)
        workspace._validate_previous(identity, scope, saved_memory, fingerprint)
        return legacy
    workspace._validate_previous(identity, scope, saved_memory, fingerprint)
    return request


def resolve_request(workspace, identity, space_id, request):
    """Verify provenance and retry identity before any model call or write."""
    identity.check(human=True)
    evidence = request.provenance
    try:
        plan = plan_import(
            {"count": 1, "records": [evidence.record]}, evidence.source_id, space_id=space_id
        )
    except ImportProblem:
        raise CommandError("Invalid import evidence", 400) from None
    if not plan.ready or plan.writes[0].memory() != request:
        raise CommandError("Import evidence must match the memory and its stable command ID", 400)
    item = plan.writes[0]
    scope, key = workspace._key(identity, space_id, write=True)
    with workspace.db.transaction(identity) as connection:
        scope = workspace._scope(connection, identity, space_id, write=True, human=True)
        rows = connection.execute(
            "SELECT * FROM space.commands WHERE id=ANY(%s)",
            ([item.source.command_id, item.memory_command_id],),
        ).fetchall()
    return checked_request(workspace, identity, scope, key, item, {row["id"]: row for row in rows})
