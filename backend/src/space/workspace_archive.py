"""Portable owner archives: all retained revisions, independent of browser caches."""

import json
from dataclasses import replace
from datetime import UTC, datetime

from pydantic import Field, model_validator

from space import agents, tasks, workspace_items
from space.memory import Input
from space.workspace_search import require_snapshot

FORMAT = "space.archive.v1"


class ExportPage(Input):
    after: str | None = Field(default=None, max_length=100)
    snapshot: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def continuation(self):
        if self.after and self.snapshot is None:
            raise ValueError("Continuation requires the first page's snapshot")
        return self


def export_page(workspace, identity, space_id, request: ExportPage):
    identity = replace(identity, selected_space_id=space_id)
    # An owner export includes history and internal memory state, never access grants.
    with workspace.db.transaction(identity) as connection:
        workspace._scope(connection, identity, space_id, human=True, api_key=True)
    snapshot = require_snapshot(workspace, identity, space_id, request.snapshot)
    with workspace.db.transaction(identity) as connection:
        workspace._scope(connection, identity, space_id, human=True, api_key=True)
        rows = connection.execute(
            "SELECT * FROM ("
            "SELECT 'r:'||v.resource_id::text||':'||lpad(v.revision::text,10,'0') AS cursor, "
            "v.resource_id AS id,v.revision,r.current_revision=v.revision AS current "
            "FROM space.resource_revisions v JOIN space.resources r ON r.id=v.resource_id "
            "AND (r.organization_id,r.space_id)=(v.organization_id,v.space_id) UNION ALL "
            "SELECT 'i:'||v.item_id::text||':'||lpad(v.revision::text,10,'0'),v.item_id,"
            "v.revision,i.current_revision=v.revision FROM space.workspace_item_revisions v "
            "JOIN space.workspace_items i ON (i.organization_id,i.space_id,i.id)="
            "(v.organization_id,v.space_id,v.item_id) UNION ALL "
            "SELECT 'a:'||id::text,id,revision,true FROM space.agent_identities "
            "WHERE deleted_at IS NULL) corpus "
            "WHERE (%s::text IS NULL OR cursor>%s) ORDER BY cursor LIMIT 3",
            (request.after, request.after),
        ).fetchall()
    # Two full revisions keep even maximum-sized documents under gateway response limits.
    entries = []
    profiles = None
    for row in rows[:2]:
        kind = row["cursor"][0]
        if kind == "a":
            profiles = profiles or agents.list_profiles(workspace, identity)["items"]
            value = next(profile for profile in profiles if profile["id"] == str(row["id"]))
            entity = "agent"
        elif kind == "i":
            value = workspace_items.get(workspace, identity, space_id, row["id"], row["revision"])
            entity = "item"
        else:
            value = workspace.get_resource(
                identity, space_id, row["id"], row["revision"], _internal=True
            )
            value.pop("memory", None)  # _memory retains edited and forgotten state plus cue graph.
            entity = "resource"
            if row["current"] and value.get("_memory"):
                with workspace.db.transaction(identity) as connection:
                    workspace._scope(connection, identity, space_id, human=True, api_key=True)
                    index = connection.execute(
                        "SELECT claim_id,embedding::text,embedding_model "
                        "FROM space.memory_claims WHERE memory_id=%s ORDER BY claim_id",
                        (row["id"],),
                    ).fetchall()
                value["memory_index"] = [
                    {
                        "id": str(claim["claim_id"]),
                        "model": claim["embedding_model"],
                        "vector": json.loads(claim["embedding"]),
                    }
                    for claim in index
                ]
        entries.append({"entity": entity, "current": row["current"], "value": value})
    task_columns = (
        tasks.columns(workspace, identity, space_id)["columns"] if not request.after else None
    )
    require_snapshot(workspace, identity, space_id, snapshot)
    return {
        **({"task_columns": task_columns} if task_columns is not None else {}),
        "format": FORMAT,
        "source_space": str(space_id),
        "snapshot": snapshot,
        "exported_at": datetime.now(UTC).isoformat(),
        "entries": entries,
        "next_cursor": rows[1]["cursor"] if len(rows) > 2 else None,
    }
