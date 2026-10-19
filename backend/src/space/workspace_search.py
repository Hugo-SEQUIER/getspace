"""Bounded full-content search, with explicit continuation and snapshot consistency.

Content is encrypted, so matching happens after decryption. Each page is read in one
transaction and decrypted together, then authorization and the snapshot are rechecked
once: a page costs a fixed handful of statements however many items it holds.
"""

from contextvars import copy_context
from time import perf_counter
from uuid import UUID

from pydantic import Field, model_validator

from space.memory import Input, document_text
from space.workspace import CommandError
from space.workspace_items import ROW

SEARCH_PAGE = 25
INDEX_PAGE = 100
# Browsers match index text locally; a bound keeps one oversized document from
# dominating the payload. Search pages still match the full text.
INDEX_TEXT = 100_000

CORPUS = (
    "SELECT * FROM (SELECT 'r:'||id::text AS cursor,id,current_revision AS revision "
    "FROM space.resources UNION ALL SELECT 'i:'||id::text,id,current_revision "
    "FROM space.workspace_items WHERE NOT deleted) corpus "
    "WHERE (%s::text IS NULL OR cursor>%s) ORDER BY cursor LIMIT %s"
)
# Revisions are immutable, so pinning the listed ones keeps the page consistent.
RESOURCES = (
    "SELECT v.*, r.kind FROM space.resources r JOIN space.resource_revisions v "
    "ON (r.organization_id,r.space_id,r.id)=(v.organization_id,v.space_id,v.resource_id) "
    "WHERE (v.resource_id,v.revision) IN (SELECT * FROM unnest(%s::uuid[],%s::integer[]))"
)
ITEMS = ROW + (  # noqa: S608 - static SQL; all values are parameters
    "WHERE (i.id,v.revision) IN (SELECT * FROM unnest(%s::uuid[],%s::integer[]))"
)


class Search(Input):
    query: str = Field(default="", max_length=300)
    after: str | None = Field(default=None, pattern=r"^[ir]:[0-9a-f-]{36}$")
    snapshot: int | None = Field(default=None, ge=0)
    project: UUID | None = None

    @model_validator(mode="after")
    def cursor(self):
        if self.after:
            UUID(self.after[2:])
            if self.snapshot is None:
                raise ValueError("Continuation requires the first page's snapshot")
        return self


def _revision(connection):
    return connection.execute("SELECT content_revision FROM space.spaces").fetchone()[
        "content_revision"
    ]


def revision(workspace, identity, space_id):
    with workspace.db.transaction(identity) as connection:
        workspace._scope(connection, identity, space_id)
        return _revision(connection)


def require_snapshot(workspace, identity, space_id, expected):
    current = revision(workspace, identity, space_id)
    if expected is not None and current != expected:
        raise CommandError("Space changed. Restart to include the latest changes.", 409)
    return current


def read_memory(workspace, identity, space_id, resource_id):
    value = workspace.get_resource(identity, space_id, resource_id)
    if not value.get("memory"):
        raise CommandError("Memory not found", 404)
    sources = value["recall"]["sources"]
    current = workspace._current_revisions(identity, space_id, {r["resource_id"] for r in sources})
    return {
        **{k: v for k, v in value.items() if k not in {"memory", "body"}},
        "stale": workspace._stale(sources, current),
        "claims": [
            {**claim, "source": sources[0]["resource_id"] if sources else "agent"}
            for claim in value["memory"]["claims"]
        ],
    }


def _pinned(rows):
    return [row["id"] for row in rows], [row["revision"] for row in rows]


def _page(workspace, identity, space_id, after, size, expected=None):
    """One page of searchable entries, read in one transaction and rechecked once."""
    with workspace.db.transaction(identity) as connection:
        scope = workspace._scope(connection, identity, space_id)
        snapshot = _revision(connection)
        if expected is not None and snapshot != expected:
            raise CommandError("Space changed. Restart to include the latest changes.", 409)
        corpus = connection.execute(CORPUS, (after, after, size + 1)).fetchall()
        page = corpus[:size]
        resource_rows = [row for row in page if row["cursor"].startswith("r:")]
        item_rows = [row for row in page if row["cursor"].startswith("i:")]
        resources = (
            connection.execute(RESOURCES, _pinned(resource_rows)).fetchall()
            if resource_rows
            else []
        )
        items = connection.execute(ITEMS, _pinned(item_rows)).fetchall() if item_rows else []
        key_rows = {
            key_id: workspace._key_row(connection, scope, key_id)
            for key_id in {row["key_id"] for row in [*resources, *items]}
        }
    keys = {
        key_id: workspace._unwrap_key(scope["organization_id"], scope["space_id"], row)
        for key_id, row in key_rows.items()
    }
    # Object bodies download concurrently, like the single-resource read.
    pending = {
        row["resource_id"]: workspace._object_reads.submit(
            copy_context().run, workspace._read_resource_object, row["object_key"], perf_counter()
        )
        for row in resources
        if row["object_key"]
    }
    try:
        payloads = {
            row["resource_id"]: pending[row["resource_id"]].result()
            if row["resource_id"] in pending
            else row["encrypted_record"]
            for row in resources
        }
    finally:
        # On failure, cancel queued reads and drain active ones before returning.
        for future in pending.values():
            if not future.cancel():
                future.exception()
    entries = {}
    for row in items:
        data = workspace._unpack(
            keys[row["key_id"]],
            scope,
            row["id"],
            row["revision"],
            "workspace_item",
            row["encrypted_payload"],
        )["data"]
        text = data.get("description", "")
        if row["kind"] == "database":
            text += "\n" + "\n".join(
                " ".join(
                    str(value)
                    for value in [record["name"], record["notes"], *record["values"].values()]
                    if value is not None
                )
                for record in data["records"]
            )
            text += "\n" + " ".join(field["name"] for field in data["fields"])
        entries[row["id"]] = [
            {"id": str(row["id"]), "kind": row["kind"], "title": data["name"], "text": text}
        ]
    for row in resources:
        content = workspace._unpack(
            keys[row["key_id"]],
            scope,
            row["resource_id"],
            row["revision"],
            "source" if row["kind"] == "document" else "record",
            payloads[row["resource_id"]],
        )
        summary = workspace._summary(row, content, scope["principal_id"])
        if summary["recall"]["category"] == "memory":
            entries[row["resource_id"]] = [
                {
                    "id": summary["id"] + ":" + claim["id"],
                    "kind": "memory",
                    "title": claim["value"],
                    "text": summary["title"],
                    "resource_id": summary["id"],
                    "project": summary["project"],
                }
                for claim in (content.get("memory") or {}).get("claims", [])
            ]
        else:
            entries[row["resource_id"]] = [
                {
                    "id": summary["id"],
                    "kind": summary["kind"],
                    "title": summary["title"],
                    "text": document_text(content["body"]),
                    "status": summary["status"],
                    "updated_at": summary["updated_at"],
                    "project": summary["project"],
                }
            ]
    # Rechecks access after decryption and refuses a page that spans a change.
    require_snapshot(workspace, identity, space_id, snapshot)
    return {
        "entries": [entry for row in page for entry in entries.get(row["id"], [])],
        "snapshot": snapshot,
        "scanned": len(page),
        "next_cursor": page[-1]["cursor"] if len(corpus) > size else None,
    }


def search(workspace, identity, space_id, request: Search):
    page = _page(workspace, identity, space_id, request.after, SEARCH_PAGE, request.snapshot)
    needle = request.query.strip().casefold()
    results = []
    for entry in page["entries"]:
        if request.project and ("project" not in entry or entry["project"] != str(request.project)):
            continue
        text = entry.pop("text")
        entry.pop("project", None)
        if needle and needle not in (entry["title"] + "\n" + text).casefold():
            continue
        start = max(0, text.casefold().find(needle) - 50) if needle else 0
        results.append({**entry, "description": text[start : start + 180]})
    return {
        "items": results,
        "snapshot": page["snapshot"],
        "scanned": page["scanned"],
        "next_cursor": page["next_cursor"],
    }


def index(workspace, identity, space_id, after: str | None = None):
    """Every searchable entry with its plain text, so browsers can match as people type."""
    page = _page(workspace, identity, space_id, after, INDEX_PAGE)
    return {
        "items": [{**entry, "text": entry["text"][:INDEX_TEXT]} for entry in page["entries"]],
        "next_cursor": page["next_cursor"],
    }
