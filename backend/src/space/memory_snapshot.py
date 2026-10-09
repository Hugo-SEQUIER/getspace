"""Revision dependencies for incoming memory, without a whole-workspace write barrier."""

from dataclasses import dataclass, field
from uuid import UUID


def catalog_digest(connection):
    """Track exactly the active labels/projects the automatic classifier can read."""
    return connection.execute(
        "SELECT coalesce(md5(string_agg(id::text || ':' || current_revision::text, "
        "',' ORDER BY id)), '') AS fingerprint FROM space.workspace_items "
        "WHERE kind IN ('category','project','section') AND NOT deleted"
    ).fetchone()["fingerprint"]


@dataclass(frozen=True)
class IncomingMemorySnapshot:
    source_id: UUID | None
    source_revision: int | None
    catalog: str | None
    related: dict[str, int] = field(default_factory=dict)

    @classmethod
    def capture(cls, connection, source, *, classify):
        return cls(
            source.resource_id if source else None,
            source.revision if source else None,
            catalog_digest(connection) if classify else None,
        )

    def matches(self, connection):
        if self.source_id is not None:
            current = connection.execute(
                "SELECT current_revision FROM space.resources WHERE id=%s", (self.source_id,)
            ).fetchone()
            if current is None or current["current_revision"] != self.source_revision:
                return False
        if self.related:
            rows = connection.execute(
                "SELECT id,current_revision FROM space.resources WHERE id=ANY(%s::uuid[])",
                (list(self.related),),
            ).fetchall()
            if {str(row["id"]): row["current_revision"] for row in rows} != self.related:
                return False
        return self.catalog is None or catalog_digest(connection) == self.catalog
