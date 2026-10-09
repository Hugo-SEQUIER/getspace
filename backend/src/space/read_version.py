"""Small, authorized change token for browser content reads."""

from space.workspace import CommandError


def snapshot(workspace, identity, space_id):
    with workspace.db.transaction(identity) as connection:
        # _scope also holds the team access lock and rechecks the caller's role.
        scope = workspace._scope(connection, identity, space_id)
        row = connection.execute(
            "SELECT content_revision,visibility FROM space.spaces "
            "WHERE organization_id=%s AND id=%s",
            (scope["organization_id"], scope["space_id"]),
        ).fetchone()
        if row is None:
            raise CommandError("Space not found or access was removed.", 404)
        # Keep the bigint as an opaque string so JavaScript cannot round it.
        version = f"1:{row['content_revision']}:{scope['role']}:{row['visibility']}"
    return {"version": version}
