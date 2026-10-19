"""Small, authorized change token for browser content reads."""

from space.content_changes import KINDS
from space.workspace import CommandError


def snapshot(workspace, identity, space_id):
    with workspace.db.transaction(identity) as connection:
        # _scope also holds the team access lock and rechecks the caller's role.
        scope = workspace._scope(connection, identity, space_id)
        row = connection.execute(
            "SELECT content_revision,change_revisions,visibility FROM space.spaces "
            "WHERE organization_id=%s AND id=%s",
            (scope["organization_id"], scope["space_id"]),
        ).fetchone()
        if row is None:
            raise CommandError("Space not found or access was removed.", 404)
    access = f"{scope['role']}:{row['visibility']}"
    changes = row["change_revisions"] or {}
    # Keep the bigints as opaque strings so JavaScript cannot round them. `version`
    # changes with any content; each entry of `versions` only with that kind.
    return {
        "version": f"1:{row['content_revision']}:{access}",
        "versions": {kind: f"2:{kind}:{changes.get(kind, 0)}:{access}" for kind in KINDS},
    }
