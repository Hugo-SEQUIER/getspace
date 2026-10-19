"""Portable owner archives: all retained revisions, independent of browser caches.

An export spans many requests while agents keep writing. Each revision records the Space
content revision it committed under, so every page reads the revisions that existed at
the first page and the archive is the Space as it was when the export started.
"""

import json
from dataclasses import replace
from datetime import UTC, datetime
from time import monotonic

from pydantic import Field, model_validator

from space import agents, brain, tasks, workspace_items
from space.memory import Input
from space.workspace import CommandError
from space.workspace_search import revision as content_revision

FORMAT = "space.archive.v1"
PAGE_ROWS = 50
# Keeps pages under gateway response limits: a page stops before the entry that would
# take it past this size, and always holds at least one entry.
PAGE_BYTES = 3_000_000
# Each entry is read and decoded on its own, which can take a while on a slow server,
# while the website gives a request 45 seconds. A page stops taking entries after
# this long; the browser continues from its cursor.
PAGE_SECONDS = 20
# Revisions stamped after the snapshot did not exist yet. Each "current" flag marks the
# latest revision at the snapshot, not the latest one now.
CORPUS = (
    "SELECT * FROM ("
    "SELECT 'r:'||resource_id::text||':'||lpad(revision::text,10,'0') AS cursor,"
    "resource_id AS id,revision,revision=max(revision) OVER (PARTITION BY resource_id) AS current "
    "FROM space.resource_revisions WHERE space_revision IS NULL OR space_revision<=%s "
    "UNION ALL "
    "SELECT 'i:'||item_id::text||':'||lpad(revision::text,10,'0'),item_id,revision,"
    "revision=max(revision) OVER (PARTITION BY item_id) "
    "FROM space.workspace_item_revisions WHERE space_revision IS NULL OR space_revision<=%s"
    ") corpus WHERE (%s::text IS NULL OR cursor>%s) ORDER BY cursor LIMIT %s"
)


class ExportPage(Input):
    after: str | None = Field(default=None, max_length=100)
    snapshot: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def continuation(self):
        if self.after and self.snapshot is None:
            raise ValueError("Continuation requires the first page's snapshot")
        return self


def _vector(row):
    return {
        "id": str(row["claim_id"]),
        "model": row["embedding_model"],
        "vector": json.loads(row["embedding"]),
    }


def _memory_index(workspace, identity, space_id, memory_id, revision, claims):
    """Recall vectors of the exported claims, as they were at the snapshot."""
    with workspace.db.transaction(identity) as connection:
        workspace._scope(connection, identity, space_id, human=True, api_key=True)
        live = connection.execute(
            "SELECT current_revision FROM space.resources WHERE id=%s", (memory_id,)
        ).fetchone()
        index = connection.execute(
            "SELECT claim_id,text_hash,embedding::text,embedding_model "
            "FROM space.memory_claims WHERE memory_id=%s ORDER BY claim_id",
            (memory_id,),
        ).fetchall()
        if live is not None and live["current_revision"] == revision:
            return [_vector(row) for row in index]
        # The memory changed after the snapshot. A vector depends only on the claim text,
        # so each claim takes the live or retired vector of the text it had then.
        retired = connection.execute(
            "SELECT claim_id,text_hash,embedding::text,embedding_model "
            "FROM space.retired_claim_vectors WHERE memory_id=%s ORDER BY retired_at DESC",
            (memory_id,),
        ).fetchall()
    vectors = {}
    for row in [*index, *retired]:
        vectors.setdefault((str(row["claim_id"]), bytes(row["text_hash"])), row)
    found = (vectors.get((claim["id"], brain.text_hash(claim["value"]))) for claim in claims)
    return [_vector(row) for row in found if row is not None]


def _entry(workspace, identity, space_id, row):
    if row["cursor"][0] == "i":
        value = workspace_items.get(workspace, identity, space_id, row["id"], row["revision"])
        return {"entity": "item", "current": row["current"], "value": value}
    try:
        value = workspace.get_resource(
            identity, space_id, row["id"], row["revision"], _internal=True
        )
    except CommandError as error:
        if error.status != 404:
            raise
        return None  # Permanently deleted while the export ran.
    value.pop("memory", None)  # _memory retains edited and forgotten state plus cue graph.
    if row["current"] and value.get("_memory"):
        value["memory_index"] = _memory_index(
            workspace, identity, space_id, row["id"], row["revision"], value["_memory"]["claims"]
        )
    return {"entity": "resource", "current": row["current"], "value": value}


def export_page(workspace, identity, space_id, request: ExportPage):
    started = monotonic()
    identity = replace(identity, selected_space_id=space_id)
    # An owner export includes history and internal memory state, never access grants.
    with workspace.db.transaction(identity) as connection:
        workspace._scope(connection, identity, space_id, human=True, api_key=True)
        current = connection.execute("SELECT content_revision FROM space.spaces").fetchone()[
            "content_revision"
        ]
        snapshot = current if request.snapshot is None else request.snapshot
        if snapshot > current:
            raise CommandError("Unknown snapshot. Start the export again.", 422)
        rows = connection.execute(
            CORPUS, (snapshot, snapshot, request.after, request.after, PAGE_ROWS + 1)
        ).fetchall()
    entries = []
    task_columns = None
    if not request.after:
        task_columns = tasks.columns(workspace, identity, space_id)["columns"]
        entries = [
            {"entity": "agent", "current": True, "value": profile}
            for profile in sorted(
                agents.list_profiles(workspace, identity)["items"], key=lambda p: p["id"]
            )
        ]
    size = len(json.dumps(entries, default=str))
    taken = 0
    for row in rows[:PAGE_ROWS]:
        if taken and monotonic() - started > PAGE_SECONDS:
            break
        entry = _entry(workspace, identity, space_id, row)
        cost = len(json.dumps(entry, default=str)) if entry else 0
        if taken and size + cost > PAGE_BYTES:
            break
        size += cost
        taken += 1
        if entry:
            entries.append(entry)
    content_revision(workspace, identity, space_id)  # Rechecks access after reading.
    return {
        **({"task_columns": task_columns} if task_columns is not None else {}),
        "format": FORMAT,
        "source_space": str(space_id),
        "snapshot": snapshot,
        "exported_at": datetime.now(UTC).isoformat(),
        "entries": entries,
        "next_cursor": rows[taken - 1]["cursor"] if taken < len(rows) else None,
    }
