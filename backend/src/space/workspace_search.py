"""Bounded full-content search, with explicit continuation and snapshot consistency."""

from uuid import UUID

from pydantic import Field, model_validator

from space import workspace_items
from space.memory import Input, document_text
from space.workspace import CommandError


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


def revision(workspace, identity, space_id):
    with workspace.db.transaction(identity) as connection:
        workspace._scope(connection, identity, space_id)
        return connection.execute("SELECT content_revision FROM space.spaces").fetchone()[
            "content_revision"
        ]


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


def search(workspace, identity, space_id, request: Search):
    snapshot = require_snapshot(workspace, identity, space_id, request.snapshot)
    with workspace.db.transaction(identity) as connection:
        workspace._scope(connection, identity, space_id)
        rows = connection.execute(
            "SELECT * FROM (SELECT 'r:'||id::text AS cursor,id,current_revision AS revision "
            "FROM space.resources UNION ALL SELECT 'i:'||id::text,id,current_revision "
            "FROM space.workspace_items WHERE NOT deleted) corpus "
            "WHERE (%s::text IS NULL OR cursor>%s) ORDER BY cursor LIMIT 26",
            (request.after, request.after),
        ).fetchall()
    needle = request.query.strip().casefold()
    results = []

    def match(value):
        return not needle or needle in value.casefold()

    def result(id_, kind, title, content, **extra):
        if match(title + "\n" + content):
            start = max(0, content.casefold().find(needle) - 50) if needle else 0
            results.append(
                {
                    "id": id_,
                    "kind": kind,
                    "title": title,
                    "description": content[start : start + 180],
                    **extra,
                }
            )

    for row in rows[:25]:
        if row["cursor"].startswith("i:"):
            if request.project:
                continue
            item = workspace_items.get(workspace, identity, space_id, row["id"], row["revision"])
            text = item["description"]
            if item["kind"] == "database":
                text += "\n" + "\n".join(
                    " ".join(
                        str(value)
                        for value in [record["name"], record["notes"], *record["values"].values()]
                        if value is not None
                    )
                    for record in item["records"]
                )
                text += "\n" + " ".join(field["name"] for field in item["fields"])
            result(item["id"], item["kind"], item["name"], text)
        else:
            resource = workspace.get_resource(identity, space_id, row["id"], row["revision"])
            if request.project and resource.get("project") != str(request.project):
                continue
            if resource["recall"]["category"] == "memory":
                for claim in resource.get("memory", {}).get("claims", []):
                    result(
                        resource["id"] + ":" + claim["id"],
                        "memory",
                        claim["value"],
                        resource["title"],
                        resource_id=resource["id"],
                    )
            else:
                result(
                    resource["id"],
                    resource["kind"],
                    resource["title"],
                    document_text(resource["body"]),
                    status=resource["status"],
                    updated_at=resource["updated_at"],
                )
    require_snapshot(workspace, identity, space_id, snapshot)
    return {
        "items": results,
        "snapshot": snapshot,
        "scanned": min(25, len(rows)),
        "next_cursor": rows[24]["cursor"] if len(rows) > 25 else None,
    }
