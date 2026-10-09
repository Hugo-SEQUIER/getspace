"""Parameters: a trusted operator connection and terminal webhook receipts.
What it does: removes completed delivery history after a fixed recovery window.
Output: bounded cleanup counts; pending work is retained for the worker's visible expiry.
"""

import argparse

RETENTION_DAYS = 30


def prune(connection, *, batch_size=1000):
    """Parameters: maintenance connection and batch size from 1 to 10000.
    What it does: removes old terminal receipts, unused events and removed subscriptions.
    Output: row counts; active secrets and recent recovery evidence remain stored.
    """
    if not 1 <= batch_size <= 10000:
        raise ValueError("Batch size must be between 1 and 10000")
    with connection.transaction():
        deliveries = connection.execute(
            "DELETE FROM space.webhook_deliveries WHERE (organization_id,space_id,id) IN "
            "(SELECT organization_id,space_id,id FROM space.webhook_deliveries WHERE "
            "status IN ('delivered','failed') AND created_at<now()-interval '30 days' "
            "ORDER BY created_at LIMIT %s)",
            (batch_size,),
        ).rowcount
        events = connection.execute(
            "DELETE FROM space.webhook_events e WHERE (organization_id,space_id,id) IN "
            "(SELECT organization_id,space_id,id FROM space.webhook_events WHERE "
            "created_at<now()-interval '30 days' ORDER BY created_at LIMIT %s) AND NOT EXISTS "
            "(SELECT 1 FROM space.webhook_deliveries d WHERE "
            "(d.organization_id,d.space_id,d.event_id)="
            "(e.organization_id,e.space_id,e.id))",
            (batch_size,),
        ).rowcount
        commands = connection.execute(
            "DELETE FROM space.webhook_commands WHERE (organization_id,space_id,id) IN "
            "(SELECT organization_id,space_id,id FROM space.webhook_commands WHERE "
            "created_at<now()-interval '30 days' ORDER BY created_at LIMIT %s)",
            (batch_size,),
        ).rowcount
        subscriptions = connection.execute(
            "DELETE FROM space.webhook_subscriptions s WHERE (organization_id,space_id,id) IN "
            "(SELECT organization_id,space_id,id FROM space.webhook_subscriptions WHERE "
            "removed_at<now()-interval '30 days' ORDER BY removed_at LIMIT %s) AND NOT EXISTS "
            "(SELECT 1 FROM space.webhook_deliveries d WHERE "
            "(d.organization_id,d.space_id,d.subscription_id)="
            "(s.organization_id,s.space_id,s.id)) AND NOT EXISTS "
            "(SELECT 1 FROM space.webhook_commands c "
            "WHERE (c.organization_id,c.space_id,c.subscription_id)="
            "(s.organization_id,s.space_id,s.id))",
            (batch_size,),
        ).rowcount
    return {
        "deliveries": deliveries,
        "events": events,
        "commands": commands,
        "subscriptions": subscriptions,
    }


def main(argv=None):
    """Parameters: explicit operator CLI arguments.
    What it does: runs one bounded maintenance batch, never installs a recurring job.
    Output: cleanup counts without payloads or credentials.
    """
    from space.database_setup import InstallSettings, connect

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-size", type=int, default=1000)
    args = parser.parse_args(argv)
    with connect(InstallSettings()) as connection:
        print(prune(connection, batch_size=args.batch_size))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
