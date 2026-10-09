"""Parameters: the owner, a Space and the documents or tasks they opened.
What it does: records the revision of each resource the person last looked at, and
lists those marks so the website can show what agents changed since then.
Output: per-person seen revisions. Identifiers and numbers only, never content.
"""

from typing import Annotated
from uuid import UUID

from pydantic import Field

from space.memory import Input

MAX_ITEMS = 500


class SeenItem(Input):
    resource_id: UUID
    revision: int = Field(ge=1, strict=True)


class MarkSeen(Input):
    items: Annotated[list[SeenItem], Field(min_length=1, max_length=MAX_ITEMS)]


def _rows(rows):
    return {
        "items": [
            {
                "resource_id": str(row["resource_id"]),
                "seen_revision": row["seen_revision"],
                "seen_at": row["seen_at"].isoformat(),
            }
            for row in rows
        ]
    }


def listing(workspace, identity, space_id: UUID):
    """Every resource the owner has opened in this Space, with the revision seen."""
    with workspace.db.transaction(identity) as connection:
        # Seen state belongs to a person on the website; agents and API keys never hold it.
        workspace._scope(connection, identity, space_id, human=True)
        rows = connection.execute(
            "SELECT resource_id,seen_revision,seen_at FROM space.resource_views "
            "ORDER BY resource_id"
        ).fetchall()
    return _rows(rows)


def mark(workspace, identity, space_id: UUID, change: MarkSeen):
    """Records each resource as seen at the given revision. A mark never moves backwards,
    never exceeds the current revision, and silently skips a resource that no longer
    exists. Viewers may mark what they read; this is their own state, not content."""
    latest = {}
    for item in change.items:
        latest[item.resource_id] = max(latest.get(item.resource_id, 0), item.revision)
    with workspace.db.transaction(identity) as connection:
        workspace._scope(connection, identity, space_id, human=True)
        rows = []
        for resource_id, revision in latest.items():
            row = connection.execute(
                "INSERT INTO space.resource_views(organization_id,space_id,principal_id,"
                "resource_id,seen_revision) "
                "SELECT r.organization_id,r.space_id,space.current_principal_id(),r.id,"
                "least(%s,r.current_revision) FROM space.resources r WHERE r.id=%s "
                "ON CONFLICT (organization_id,space_id,principal_id,resource_id) DO UPDATE "
                "SET seen_revision=greatest(space.resource_views.seen_revision,"
                "EXCLUDED.seen_revision),seen_at=now() "
                "RETURNING resource_id,seen_revision,seen_at",
                (revision, resource_id),
            ).fetchone()
            if row is not None:
                rows.append(row)
    return _rows(rows)
