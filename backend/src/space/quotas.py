"""Shared durable budgets; charges survive process restarts and concurrent workers."""

# Migration 0035 stores each column's maximum to mean "no limit".
UNLIMITED = {
    "storage_limit_bytes": 9223372036854775807,
    "item_limit": 2147483647,
    "model_request_limit": 2147483647,
}


def consume_model_request(workspace, identity, space_id):
    with workspace.db.transaction(identity) as connection:
        workspace._scope(connection, identity, space_id)
        connection.execute("SELECT space.consume_model_request()").fetchone()


def usage(workspace, identity, space_id):
    """Output: the Space's usage counters, with null for a limit that is not set."""
    with workspace.db.transaction(identity) as connection:
        workspace._scope(connection, identity, space_id)
        row = connection.execute(
            "SELECT retained_bytes,items,storage_limit_bytes,item_limit,model_request_limit,"
            "CASE WHEN month=date_trunc('month',now() AT TIME ZONE 'UTC')::date "
            "THEN model_requests ELSE 0 END AS model_requests,"
            "(date_trunc('month',now() AT TIME ZONE 'UTC')+interval '1 month') AS resets_at "
            "FROM space.usage_limits"
        ).fetchone()
    row = (
        dict(row)
        if row
        else {
            "retained_bytes": 0,
            "items": 0,
            "model_requests": 0,
            "resets_at": None,
            **UNLIMITED,
        }
    )
    for name, value in UNLIMITED.items():
        if row[name] >= value:
            row[name] = None
    return {**row, "plan": None}
