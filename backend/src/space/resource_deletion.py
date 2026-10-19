"""Delete document or task revisions atomically, retaining independent memory records."""

from uuid import uuid4

from space import content_changes


def delete_resource(workspace, identity, space_id, resource_id, expected_revision):
    from space.workspace import CommandError

    with workspace.db.transaction(identity) as connection:
        # Removal policy: the owner's website session or API key with write access may remove a
        # source document or a task. A delegated agent grant with write access may
        # remove a task but never a document, and a generated memory record has its
        # own command.
        workspace._scope(connection, identity, space_id, write=True)
        # Serialize with document saves and memory builds, which use the same lock.
        connection.execute("SELECT id FROM space.spaces FOR UPDATE")
        row = connection.execute(
            "SELECT r.kind,r.memory_kind,r.current_revision,c.system_role "
            "FROM space.resources r LEFT JOIN space.collections c ON "
            "(r.organization_id,r.space_id,r.collection_id)=(c.organization_id,c.space_id,c.id) "
            "WHERE r.id=%s",
            (resource_id,),
        ).fetchone()
        if row is None:
            return {"deleted": True}
        if row["memory_kind"] == "memory":
            raise CommandError("Generated memory is changed with change_memory", 400)
        if row["kind"] not in {"document", "record"}:
            raise CommandError("Only documents and tasks can be deleted here", 400)
        task = (
            row["kind"] == "record" and row["system_role"] == "tasks" and row["memory_kind"] is None
        )
        if not task and identity.client_id is not None:
            raise CommandError(
                "Agents can delete tasks only. Delete documents on the website.", 403
            )
        if row["current_revision"] != expected_revision:
            raise CommandError("This resource changed. Reload before deleting.", 409)
        # The existing orphan sweeper retries object removal after this commits.
        # Queue exact immutable keys, never a prefix that could include later writes.
        cleanup_id = uuid4()
        connection.execute("SELECT set_config('space.quota_deletion','on',true)")
        connection.execute(
            "INSERT INTO space.restore_objects(organization_id,space_id,restore_id,"
            "object_key,lease_id,content_bytes) "
            "SELECT organization_id,space_id,%s,object_key,%s,content_bytes "
            "FROM space.resource_revisions WHERE resource_id=%s AND object_key IS NOT NULL "
            "ON CONFLICT DO NOTHING",
            (cleanup_id, cleanup_id, resource_id),
        )
        connection.execute(
            "DELETE FROM space.events WHERE command_id IN "
            "(SELECT id FROM space.commands WHERE resource_id=%s)",
            (resource_id,),
        )
        connection.execute("DELETE FROM space.commands WHERE resource_id=%s", (resource_id,))
        connection.execute(
            "DELETE FROM space.resource_revisions WHERE resource_id=%s", (resource_id,)
        )
        connection.execute("DELETE FROM space.resources WHERE id=%s", (resource_id,))
        content_changes.changed(
            connection, content_changes.resource_kinds(row["kind"], row["memory_kind"])
        )
    return {"deleted": True}


def delete_memory(workspace, identity, space_id, resource_id, expected_revision):
    """Delete a generated memory record with its claims.

    Parameters: the workspace, the caller identity, the Space, the memory resource and
        the revision the caller last read.
    What it does: when a deletion journal is configured, writes one forget event per
        claim first, so a restored backup cannot resurrect them; then removes the index
        rows, the command history, the revisions and the resource itself. Any identity
        with write access may call it, which matches the per-claim forget it replaces.
    Output: {"deleted": True}. A resource that is already absent is a success.
    """
    from space import brain
    from space.workspace import CommandError

    try:
        resource = workspace.get_resource(identity, space_id, resource_id, _internal=True)
    except CommandError as error:
        if error.status == 404:
            return {"deleted": True}
        raise
    record = resource.get("_memory")
    if not record:
        raise CommandError("Only a generated memory record can be deleted here", 400)
    if resource["revision"] != expected_revision:
        raise CommandError("This resource changed. Reload before deleting.", 409)
    claim_ids = [claim["id"] for claim in record["claims"]]

    with workspace.db.transaction(identity) as connection:
        scope = workspace._scope(connection, identity, space_id, write=True)
    if workspace.forget_journal is not None:
        try:
            for claim_id in claim_ids:
                workspace.forget_journal.append_forget(
                    organization_id=scope["organization_id"],
                    space_id=space_id,
                    resource_id=resource_id,
                    claim_id=claim_id,
                )
        except Exception:
            raise CommandError(
                "Deletion journal unavailable. Retry deleting shortly.", 503
            ) from None

    with workspace.db.transaction(identity) as connection:
        workspace._scope(connection, identity, space_id, write=True)
        # Serialize with memory writes and document deletion, which use the same lock.
        connection.execute("SELECT id FROM space.spaces FOR UPDATE")
        row = connection.execute(
            "SELECT memory_kind,current_revision FROM space.resources WHERE id=%s",
            (resource_id,),
        ).fetchone()
        if row is None:
            return {"deleted": True}
        if row["memory_kind"] != "memory":
            raise CommandError("Only a generated memory record can be deleted here", 400)
        if row["current_revision"] != expected_revision:
            raise CommandError("This resource changed. Reload before deleting.", 409)
        # The existing orphan sweeper retries object removal after this commits.
        cleanup_id = uuid4()
        connection.execute("SELECT set_config('space.quota_deletion','on',true)")
        connection.execute(
            "INSERT INTO space.restore_objects(organization_id,space_id,restore_id,"
            "object_key,lease_id,content_bytes) "
            "SELECT organization_id,space_id,%s,object_key,%s,content_bytes "
            "FROM space.resource_revisions WHERE resource_id=%s AND object_key IS NOT NULL "
            "ON CONFLICT DO NOTHING",
            (cleanup_id, cleanup_id, resource_id),
        )
        brain.forget(connection, resource_id)
        connection.execute(
            "DELETE FROM space.retired_claim_vectors WHERE memory_id=%s", (resource_id,)
        )
        connection.execute(
            "DELETE FROM space.events WHERE command_id IN "
            "(SELECT id FROM space.commands WHERE resource_id=%s)",
            (resource_id,),
        )
        connection.execute("DELETE FROM space.commands WHERE resource_id=%s", (resource_id,))
        connection.execute(
            "DELETE FROM space.resource_revisions WHERE resource_id=%s", (resource_id,)
        )
        connection.execute("DELETE FROM space.resources WHERE id=%s", (resource_id,))
        content_changes.changed(connection, ("memory",))
    return {"deleted": True}
