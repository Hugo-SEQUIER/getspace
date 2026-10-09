"""Explicit node connections are user intent, not model judgments."""

from uuid import UUID


def validate(workspace, identity, space_id, memory_id, links, snapshot=None):
    """Parameters: scoped writer, node ID, explicit targets and optional write snapshot.

    What it does: validates exact existing memory IDs without model calls. Names alone
    never choose a target. Pins current targets before enrichment when a snapshot exists.
    Output: canonical explicit links or a command error; no persistence.
    """
    from space.memory_snapshot import IncomingMemorySnapshot
    from space.workspace import CommandError

    if not links:
        return []
    ids = [link.resource_id for link in links]
    if UUID(str(memory_id)) in ids or len(set(ids)) != len(ids):
        raise CommandError("Explicit memory links must be unique and cannot point to self")
    with workspace.db.transaction(identity) as connection:
        workspace._scope(connection, identity, space_id, write=True)
        rows = connection.execute(
            "SELECT id,current_revision FROM space.resources "
            "WHERE id=ANY(%s::uuid[]) AND memory_kind='memory'",
            (ids,),
        ).fetchall()
        if {row["id"] for row in rows} != set(ids):
            raise CommandError("Link targets must be existing memory nodes in this Space", 404)
        if isinstance(snapshot, IncomingMemorySnapshot):
            for row in rows:
                snapshot.related.setdefault(str(row["id"]), row["current_revision"])
            workspace._check_manifest(connection, snapshot)
    return [link.model_dump(mode="json") for link in links]
