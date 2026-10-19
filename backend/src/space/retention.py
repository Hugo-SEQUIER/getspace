"""Retention for the command and event log.

`space.commands` records one row per applied write so a retried command ID is not
applied twice, and `space.events` records the resource event that command produced.
Both are append-only and neither is read after the fact, so they have a retention
window instead of growing forever.

Pruning is an operator decision over every Space at once, which the runtime login cannot
see under row-level security, so this runs with the migration credential. Deleting an
event before its command matters, because `space.events.command_id` references
`space.commands`.
"""

from __future__ import annotations

import argparse
import sys

from space.database_setup import InstallSettings, connect

COMMAND_RETENTION_DAYS = 180
ACTIVITY_RETENTION_DAYS = 90
# Any export finishes well within a day; migration 0036 keeps retired vectors for exports.
RETIRED_VECTOR_RETENTION_DAYS = 1
BATCH_SIZE = 10000

# Events first: they reference the commands being removed.
EVENTS_SQL = """
DELETE FROM space.events
WHERE ctid IN (
    SELECT e.ctid
    FROM space.events e
    JOIN space.commands c
      ON c.organization_id = e.organization_id
     AND c.space_id = e.space_id
     AND c.id = e.command_id
    WHERE c.created_at < now() - make_interval(days => %s)
    LIMIT %s
)
"""

COMMANDS_SQL = """
DELETE FROM space.commands
WHERE ctid IN (
    SELECT ctid FROM space.commands
    WHERE created_at < now() - make_interval(days => %s)
    LIMIT %s
)
"""

ACTIVITY_SQL = """
DELETE FROM space.activity_events
WHERE ctid IN (
    SELECT ctid FROM space.activity_events
    WHERE created_at < now() - make_interval(days => %s)
    LIMIT %s
)
"""

# Recall vectors kept for exports that started before a claim changed.
RETIRED_VECTORS_SQL = """
DELETE FROM space.retired_claim_vectors
WHERE ctid IN (
    SELECT ctid FROM space.retired_claim_vectors
    WHERE retired_at < now() - make_interval(days => %s)
    LIMIT %s
)
"""


def _delete_in_batches(connection, statement: str, days: int, batch_size: int) -> int:
    """Runs one delete statement until it stops finding rows.

    Parameters: an owner-role connection, the delete statement, the retention window in
        days and the batch size.
    What it does: commits each batch on its own, so no single statement holds locks
        across the whole table while a long delete runs.
    Output: the number of rows deleted in total.
    """
    removed = 0
    while True:
        with connection.transaction():
            deleted = connection.execute(statement, (days, batch_size)).rowcount
        removed += deleted
        if deleted < batch_size:
            return removed


def purge(connection, *, days: int = COMMAND_RETENTION_DAYS, batch_size: int = BATCH_SIZE):
    """Deletes commands and their events older than the retention window.

    Parameters: an owner-role connection, the retention window in days and the batch size.
    What it does: removes the events of expired commands, then the commands themselves,
        each in committed batches.
    Output: a dict with the `events` and `commands` counts removed. A command ID older
        than the window is forgotten, so a retry that late is applied again.
    """
    if days < 1:
        raise ValueError("days must be at least 1")
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    events = _delete_in_batches(connection, EVENTS_SQL, days, batch_size)
    commands = _delete_in_batches(connection, COMMANDS_SQL, days, batch_size)
    activity = _delete_in_batches(connection, ACTIVITY_SQL, ACTIVITY_RETENTION_DAYS, batch_size)
    vectors = _delete_in_batches(
        connection, RETIRED_VECTORS_SQL, RETIRED_VECTOR_RETENTION_DAYS, batch_size
    )
    return {
        "events": events,
        "commands": commands,
        "activity_events": activity,
        "retired_claim_vectors": vectors,
    }


def main(argv=None) -> int:
    """Runs the purge from the command line.

    Parameters: optional argv, defaulting to the process arguments.
    What it does: connects with the migration credential and applies the retention policy.
    Output: an exit status. Prints the counts removed.
    """
    parser = argparse.ArgumentParser(description="Delete command and event rows past retention.")
    parser.add_argument("--days", type=int, default=COMMAND_RETENTION_DAYS)
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    args = parser.parse_args(argv)
    settings = InstallSettings()
    if not settings.migration_database_url:
        print("Set SPACE_MIGRATION_DATABASE_URL first", file=sys.stderr)
        return 2
    connection = connect(settings)
    try:
        counts = purge(connection, days=args.days, batch_size=args.batch_size)
    finally:
        connection.close()
    print(
        f"Removed {counts['events']} events and {counts['commands']} commands "
        f"older than {args.days} days."
    )
    print(f"Removed {counts['activity_events']} activity events older than 90 days.")
    print(f"Removed {counts['retired_claim_vectors']} retired recall vectors older than 1 day.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
