"""Publish one reviewed merge atomically."""

import hmac
from datetime import datetime
from uuid import UUID, uuid4

from space import (
    brain,
    content_changes,
    database_records,
    memory_link_index,
    memory_routing,
    tasks,
    workspace_items,
)
from space.terms import resource_fingerprints, term_key
from space.workspace import CommandError


def publish(
    workspace,
    identity,
    space_id,
    restore_id,
    scope,
    key,
    entries,
    snapshot,
    source_space,
    fingerprint,
    *,
    task_columns=(),
):
    org = scope["organization_id"]
    restored_columns = tasks.prepare_restore(
        workspace, identity, space_id, entries, list(task_columns)
    )
    lease = uuid4()
    prepared = []
    # All size validation happens before acquiring a lease.
    for entry in entries:
        entity, value = entry["entity"], entry["value"]
        id_, revision = UUID(value["id"]), value["revision"]
        row = {**entry, "id": id_, "revision": revision}
        if entity == "agent":
            content = {"name": value["name"], "harness": value["harness"]}
            row["encrypted"] = workspace._pack(key, scope, id_, revision, "agent", content)
            row["identity_hash"] = hmac.digest(
                key.key.material,
                b"space.agent.v1\0"
                + value["harness"].encode()
                + b"\0"
                + value["name"].casefold().encode(),
                "sha256",
            )
        elif entity == "item":
            content = {
                k: v
                for k, v in value.items()
                if k
                not in {
                    "id",
                    "kind",
                    "revision",
                    "deleted",
                    "updated_at",
                    "author_agent",
                    "records",
                    "record_count",
                }
            }
            if value["kind"] == "database":
                # Each archived revision carries the whole table; the rows go to the row
                # tables below, one revision per record that changed between archived states.
                row["records"] = [
                    workspace_items.DatabaseRecord.model_validate(record).model_dump(mode="json")
                    for record in value.get("records") or []
                ]
            row["encrypted"] = workspace._pack(
                key,
                scope,
                id_,
                revision,
                "workspace_item",
                {"data": content, "author_agent": value.get("author_agent")},
            )
        else:
            metadata = {
                k: value.get(k)
                for k in (
                    "title",
                    "status",
                    "recall",
                    "project",
                    "folder",
                    "priority",
                    "author_agent",
                )
            }
            if value.get("verdict"):
                metadata["verdict"] = value["verdict"]
            content = {**metadata, "body": value["body"]}
            if value.get("_memory"):
                content["memory"] = value["_memory"]
            row["encrypted"] = workspace._pack(
                key,
                scope,
                id_,
                revision,
                "record" if value["kind"] == "task" else "source",
                content,
            )
            row["metadata"] = workspace._pack(key, scope, id_, revision, "metadata", metadata)
            # Restored bodies are stored inline, like every new revision.
            row["object_key"] = None
            if entry["current"]:
                row["terms"] = resource_fingerprints(
                    value["title"],
                    value["recall"]["topics"],
                    value["body"],
                    term_key(key.key.material),
                )
        prepared.append(row)
    result = {
        "resources": sum(e["entity"] == "resource" and e["current"] for e in entries),
        "items": sum(e["entity"] == "item" and e["current"] for e in entries),
        "agents": sum(e["entity"] == "agent" for e in entries),
        "revisions": len(entries),
        "source_space": source_space,
        "request_fingerprint": fingerprint,
    }
    encrypted_result = workspace._pack(key, scope, restore_id, 1, "restore_result", result)
    with workspace.db.transaction(identity) as connection:
        workspace._scope(connection, identity, space_id, human=True)
        connection.execute("SELECT id FROM space.spaces FOR UPDATE")
        current = connection.execute(
            "SELECT state,lease_until>now() AS active FROM space.restore_sessions WHERE "
            "id=%s AND expires_at>now() FOR UPDATE",
            (restore_id,),
        ).fetchone()
        if not current or current["state"] == "committed":
            raise CommandError("Restore already completed or expired. Check its status.", 409)
        if current["state"] == "publishing" and current["active"]:
            raise CommandError("Restore is publishing. Check its status before retrying.", 409)
        connection.execute(
            "UPDATE space.restore_sessions SET state='publishing',lease_id=%s,"
            "lease_until=now()+interval '5 minutes' WHERE id=%s",
            (lease, restore_id),
        )
        for row in prepared:
            if row.get("object_key"):
                connection.execute(
                    "INSERT INTO space.restore_objects(organization_id,space_id,restore_id,"
                    "object_key,lease_id,content_bytes) VALUES(%s,%s,%s,%s,%s,%s)",
                    (org, space_id, restore_id, row["object_key"], lease, len(row["encrypted"])),
                )
    try:
        for row in prepared:
            if row.get("object_key"):
                # Renew only a still-owned lease; a stale process cannot publish after takeover.
                with workspace.db.transaction(identity) as connection:
                    workspace._scope(connection, identity, space_id, human=True)
                    renewed = connection.execute(
                        "UPDATE space.restore_sessions SET lease_until=now()+interval '5 "
                        "minutes' WHERE id=%s AND state='publishing' AND lease_id=%s AND "
                        "lease_until>now() RETURNING id",
                        (restore_id, lease),
                    ).fetchone()
                    if renewed is None:
                        raise CommandError(
                            "Restore lease expired. Review its status before retrying.", 409
                        )
                workspace.objects.put(row["object_key"], row["encrypted"])
        with workspace.db.transaction(identity) as connection:
            workspace._scope(connection, identity, space_id, human=True)
            current_snapshot = connection.execute(
                "SELECT content_revision FROM space.spaces FOR UPDATE"
            ).fetchone()["content_revision"]
            if current_snapshot != snapshot:
                raise CommandError("Space changed since review. Review the merge again.", 409)
            valid = connection.execute(
                "SELECT id FROM space.restore_sessions WHERE id=%s AND state='publishing' "
                "AND lease_id=%s AND lease_until>now() FOR UPDATE",
                (restore_id, lease),
            ).fetchone()
            if not valid:
                raise CommandError("Restore lease expired. Check its status.", 409)
            connection.execute(
                "DELETE FROM space.restore_objects WHERE restore_id=%s AND lease_id=%s",
                (restore_id, lease),
            )
            collection = connection.execute(
                "SELECT id FROM space.collections WHERE system_role='tasks'"
            ).fetchone()["id"]
            if restored_columns:
                collection_id, encrypted_schema = restored_columns
                connection.execute(
                    "UPDATE space.collections SET encrypted_schema=%s WHERE id=%s",
                    (encrypted_schema, collection_id),
                )
            # The databases this restore replaces, as they are before any head moves: their
            # rows are the baseline of the first appended revision and of the change events.
            existing_databases = {}
            for row in prepared:
                value = row["value"]
                if (
                    row["entity"] == "item"
                    and value["kind"] == "database"
                    and row["action"] == "replace"
                    and row["id"] not in existing_databases
                ):
                    existing_databases[row["id"]] = _current_database(
                        workspace, connection, scope, key, row["id"]
                    )
            # Publish all heads before their revisions; scoped foreign keys are deferred.
            for row in prepared:
                if not row["current"]:
                    continue
                value, entity = row["value"], row["entity"]
                if entity == "agent":
                    connection.execute(
                        "INSERT INTO space.agent_identities(organization_id,space_id,id,"
                        "revision,key_id,encrypted_metadata,identity_hash) VALUES(%s,%s,%s,"
                        "%s,%s,%s,%s)",
                        (
                            org,
                            space_id,
                            row["id"],
                            row["revision"],
                            key.key.key_id,
                            row["encrypted"],
                            row["identity_hash"],
                        ),
                    )
                elif entity == "item":
                    if row["action"] == "replace":
                        connection.execute(
                            "UPDATE space.workspace_items SET current_revision=%s,"
                            "deleted=%s WHERE id=%s",
                            (row["revision"], value["deleted"], row["id"]),
                        )
                    else:
                        connection.execute(
                            "INSERT INTO space.workspace_items(organization_id,space_id,id,"
                            "kind,current_revision,deleted) VALUES(%s,%s,%s,%s,%s,%s)",
                            (
                                org,
                                space_id,
                                row["id"],
                                value["kind"],
                                row["revision"],
                                value["deleted"],
                            ),
                        )
                else:
                    memory_kind = (
                        value["recall"]["category"]
                        if value["recall"]["category"] != "source"
                        else None
                    )
                    if row["action"] == "replace":
                        connection.execute(
                            "UPDATE space.resources SET current_revision=%s,memory_kind=%s "
                            "WHERE id=%s",
                            (row["revision"], memory_kind, row["id"]),
                        )
                    else:
                        connection.execute(
                            "INSERT INTO space.resources(organization_id,space_id,id,kind,"
                            "collection_id,current_revision,memory_kind) VALUES(%s,%s,%s,"
                            "%s,%s,%s,%s)",
                            (
                                org,
                                space_id,
                                row["id"],
                                "record" if value["kind"] == "task" else "document",
                                collection if value["kind"] == "task" else None,
                                row["revision"],
                                memory_kind,
                            ),
                        )
                    connection.execute(
                        "UPDATE space.resources SET content_lines=%s WHERE id=%s",
                        (len(value["body"].splitlines()), row["id"]),
                    )
            for row in prepared:
                value, entity = row["value"], row["entity"]
                if entity == "agent":
                    continue
                timestamp = datetime.fromisoformat(value["updated_at"].replace("Z", "+00:00"))
                if entity == "item":
                    connection.execute(
                        "INSERT INTO space.workspace_item_revisions(organization_id,"
                        "space_id,item_id,revision,key_id,encrypted_payload,deleted,"
                        "author_principal_id,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (
                            org,
                            space_id,
                            row["id"],
                            row["revision"],
                            key.key.key_id,
                            row["encrypted"],
                            value["deleted"],
                            scope["principal_id"],
                            timestamp,
                        ),
                    )
                else:
                    connection.execute(
                        "INSERT INTO space.resource_revisions(organization_id,space_id,"
                        "resource_id,revision,key_id,encrypted_metadata,encrypted_record,"
                        "object_key,author_principal_id,created_at,content_bytes) "
                        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (
                            org,
                            space_id,
                            row["id"],
                            row["revision"],
                            key.key.key_id,
                            row["metadata"],
                            None if row["object_key"] else row["encrypted"],
                            row["object_key"],
                            scope["principal_id"],
                            timestamp,
                            len(row["encrypted"]),
                        ),
                    )
            _publish_rows(workspace, connection, scope, key, prepared, existing_databases)
            for row in prepared:
                if row["entity"] != "resource" or not row["current"]:
                    continue
                value = row["value"]
                connection.execute(
                    "DELETE FROM space.resource_terms WHERE resource_id=%s", (row["id"],)
                )
                for field, terms, counts, total in row["terms"]:
                    connection.execute(
                        "INSERT INTO space.resource_terms(organization_id,space_id,"
                        "resource_id,revision,field,terms,occurrences,tokens) VALUES(%s,%s,"
                        "%s,%s,%s,%s,%s,%s)",
                        (org, space_id, row["id"], row["revision"], field, terms, counts, total),
                    )
                if value.get("_memory"):
                    from space.memory_pipeline import index_rows

                    index = value["memory_index"]
                    source = value["recall"]["sources"][0] if value["recall"]["sources"] else None
                    brain.apply(
                        connection,
                        memory_id=row["id"],
                        revision=row["revision"],
                        claims=index_rows(value["_memory"]),
                        model_name=index[0]["model"] if index else None,
                        vectors={c["id"]: c["vector"] for c in index},
                        source_resource_id=source["resource_id"] if source else None,
                        source_revision=source["revision"] if source else None,
                    )
                else:
                    brain.forget(connection, row["id"])
            # Targets may appear later in the archive. Rebuild links only after all claims exist.
            for row in prepared:
                if row["entity"] == "resource" and row["current"]:
                    memory_link_index.replace(
                        connection,
                        row["id"],
                        row["revision"],
                        row["value"].get("_memory", {}).get("claims", []),
                        links=row["value"].get("_memory", {}).get("links", []),
                    )
                    memory_routing.replace(
                        connection,
                        row["id"],
                        row["revision"],
                        row["value"].get("_memory", {}).get("claims", []),
                    )
            content_changes.changed(connection)
            connection.execute(
                "UPDATE space.restore_sessions SET state='committed',encrypted_result=%s,"
                "lease_until=NULL WHERE id=%s AND lease_id=%s",
                (encrypted_result, restore_id, lease),
            )

            connection.execute(
                "DELETE FROM space.restore_chunks WHERE restore_id=%s", (restore_id,)
            )
    except Exception:
        # Durable upload intents remain for cleanup even if the process or object service fails.
        with workspace.db.transaction(identity) as connection:
            workspace._scope(connection, identity, space_id, human=True)
            connection.execute(
                "UPDATE space.restore_sessions SET state='staged',lease_until=NULL WHERE "
                "id=%s AND lease_id=%s AND state='publishing'",
                (restore_id, lease),
            )
        raise
    workspace._recheck(identity, space_id)
    return {"id": str(restore_id), "state": "committed", "result": result}


def _current_database(workspace, connection, scope, key, database_id):
    """The unpacked current payload data and revision of a database about to be replaced."""
    row = connection.execute(
        workspace_items.ROW + "WHERE i.id=%s AND v.revision=i.current_revision", (database_id,)
    ).fetchone()
    row_key = (
        key
        if str(row["key_id"]) == key.key.key_id
        else workspace._unwrap_key(
            scope["organization_id"], scope["space_id"], workspace._key_rows([row])[row["key_id"]]
        )
    )
    data = workspace._unpack(
        row_key, scope, database_id, row["revision"], "workspace_item", row["encrypted_payload"]
    )["data"]
    return data, row["revision"]


def _publish_rows(workspace, connection, scope, key, prepared, existing_databases):
    """Parameters: the publishing transaction, the prepared entries whose item revisions are
    inserted, and the pre-restore state of each replaced database.
    What it does: stores the rows of every archived database revision, one record revision per
    row that differs from the previous archived state, then queues one change event per record
    whose content differs between the database before the restore and its restored head.
    Output: nothing; a rolled-back transaction leaves no row and no event.
    """
    from space.webhook_events import enqueue, snapshot_changes

    versions = {}
    for row in prepared:
        if row["entity"] == "item" and row["value"]["kind"] == "database":
            versions.setdefault(row["id"], []).append(row)
    for database_id, rows in versions.items():
        existing_data, existing_revision = existing_databases.get(database_id, (None, 0))
        before, heads, legacy, _ = database_records.baseline(
            workspace, connection, scope, database_id, existing_data
        )
        pre_restore = list(before.values())
        for row in sorted(rows, key=lambda row: row["revision"]):
            upserts, deletes = database_records.snapshot_changes(before, row["records"])
            writes, _, _ = database_records.plan(
                before, upserts, deletes, legacy=legacy, total=len(before)
            )
            database_records.write(
                workspace, connection, scope, key, database_id, row["revision"], writes, heads
            )
            heads |= {record_id for record_id, record in writes if record is not None}
            before = {record["id"]: record for record in row["records"]}
            legacy = False
        head = max(rows, key=lambda row: row["revision"])
        if head["current"] and not head["value"]["deleted"]:
            enqueue(
                workspace,
                connection,
                scope,
                key,
                database_id,
                existing_revision,
                head["revision"],
                snapshot_changes(pre_restore, head["records"]),
                head["value"].get("fields", []),
            )
