"""Operator reset of one Space's content.

A reset preserves the Space, its key and its Tasks collection and removes everything
else in one transaction. It does not touch backups. purge_space(account=True) also
removes those records; the command line does not offer it, because a self-hosted
installation has one fixed owner, whom erasure would lock out for good.
"""

from __future__ import annotations

import argparse
import sys
from uuid import UUID

from psycopg.pq import TransactionStatus

from space import content_changes

# Referential order: children first, one entry per Space-owned table. Every statement is
# written out rather than built: this module deletes rows, and a constructed query string
# has no place where data is destroyed.
STEPS: tuple[tuple[str, str, str], ...] = (
    (
        "webhook_commands",
        "DELETE FROM space.webhook_commands WHERE organization_id=%s AND space_id=%s",
        "SELECT count(*) FROM space.webhook_commands WHERE organization_id=%s AND space_id=%s",
    ),
    (
        "webhook_attempts",
        "DELETE FROM space.webhook_attempts WHERE organization_id=%s AND space_id=%s",
        "SELECT count(*) FROM space.webhook_attempts WHERE organization_id=%s AND space_id=%s",
    ),
    (
        "webhook_deliveries",
        "DELETE FROM space.webhook_deliveries WHERE organization_id=%s AND space_id=%s",
        "SELECT count(*) FROM space.webhook_deliveries WHERE organization_id=%s AND space_id=%s",
    ),
    (
        "webhook_events",
        "DELETE FROM space.webhook_events WHERE organization_id=%s AND space_id=%s",
        "SELECT count(*) FROM space.webhook_events WHERE organization_id=%s AND space_id=%s",
    ),
    (
        "webhook_subscriptions",
        "DELETE FROM space.webhook_subscriptions WHERE organization_id=%s AND space_id=%s",
        "SELECT count(*) FROM space.webhook_subscriptions WHERE organization_id=%s AND space_id=%s",
    ),
    (
        "activity_events",
        "DELETE FROM space.activity_events WHERE organization_id=%s AND space_id=%s",
        "SELECT count(*) FROM space.activity_events WHERE organization_id=%s AND space_id=%s",
    ),
    (
        "restore_chunks",
        "DELETE FROM space.restore_chunks WHERE organization_id=%s AND space_id=%s",
        "SELECT count(*) FROM space.restore_chunks WHERE organization_id=%s AND space_id=%s",
    ),
    (
        "restore_sessions",
        "DELETE FROM space.restore_sessions WHERE organization_id=%s AND space_id=%s",
        "SELECT count(*) FROM space.restore_sessions WHERE organization_id=%s AND space_id=%s",
    ),
    (
        "restore_objects",
        "DELETE FROM space.restore_objects WHERE organization_id=%s AND space_id=%s",
        "SELECT count(*) FROM space.restore_objects WHERE organization_id=%s AND space_id=%s",
    ),
    (
        "workspace_item_commands",
        "DELETE FROM space.workspace_item_commands WHERE organization_id=%s AND space_id=%s",
        "SELECT count(*) FROM space.workspace_item_commands "
        "WHERE organization_id=%s AND space_id=%s",
    ),
    (
        "workspace_item_vectors",
        "DELETE FROM space.workspace_item_vectors WHERE organization_id=%s AND space_id=%s",
        "SELECT count(*) FROM space.workspace_item_vectors "
        "WHERE organization_id=%s AND space_id=%s",
    ),
    (
        "workspace_item_revisions",
        "DELETE FROM space.workspace_item_revisions WHERE organization_id=%s AND space_id=%s",
        "SELECT count(*) FROM space.workspace_item_revisions "
        "WHERE organization_id=%s AND space_id=%s",
    ),
    (
        "workspace_items",
        "DELETE FROM space.workspace_items WHERE organization_id=%s AND space_id=%s",
        "SELECT count(*) FROM space.workspace_items WHERE organization_id=%s AND space_id=%s",
    ),
    (
        "memory_jobs",
        "DELETE FROM space.memory_jobs WHERE organization_id=%s AND space_id=%s",
        "SELECT count(*) FROM space.memory_jobs WHERE organization_id=%s AND space_id=%s",
    ),
    (
        "events",
        "DELETE FROM space.events WHERE organization_id=%s AND space_id=%s",
        "SELECT count(*) AS rows FROM space.events WHERE organization_id=%s AND space_id=%s",
    ),
    (
        "commands",
        "DELETE FROM space.commands WHERE organization_id=%s AND space_id=%s",
        "SELECT count(*) AS rows FROM space.commands WHERE organization_id=%s AND space_id=%s",
    ),
    (
        "memory_routes",
        "DELETE FROM space.memory_routes WHERE organization_id=%s AND space_id=%s",
        "SELECT count(*) AS rows FROM space.memory_routes WHERE organization_id=%s AND space_id=%s",
    ),
    (
        "memory_node_links",
        "DELETE FROM space.memory_node_links WHERE organization_id=%s AND space_id=%s",
        "SELECT count(*) AS rows FROM space.memory_node_links "
        "WHERE organization_id=%s AND space_id=%s",
    ),
    (
        "memory_links",
        "DELETE FROM space.memory_links WHERE organization_id=%s AND space_id=%s",
        "SELECT count(*) AS rows FROM space.memory_links WHERE organization_id=%s AND space_id=%s",
    ),
    (
        "memory_claims",
        "DELETE FROM space.memory_claims WHERE organization_id=%s AND space_id=%s",
        "SELECT count(*) AS rows FROM space.memory_claims WHERE organization_id=%s AND space_id=%s",
    ),
    # After memory_claims: deleting a claim keeps its vector here.
    (
        "retired_claim_vectors",
        "DELETE FROM space.retired_claim_vectors WHERE organization_id=%s AND space_id=%s",
        "SELECT count(*) AS rows FROM space.retired_claim_vectors "
        "WHERE organization_id=%s AND space_id=%s",
    ),
    (
        "resource_terms",
        "DELETE FROM space.resource_terms WHERE organization_id=%s AND space_id=%s",
        "SELECT count(*) AS rows FROM space.resource_terms "
        "WHERE organization_id=%s AND space_id=%s",
    ),
    (
        "resource_revisions",
        "DELETE FROM space.resource_revisions WHERE organization_id=%s AND space_id=%s",
        "SELECT count(*) AS rows FROM space.resource_revisions "
        "WHERE organization_id=%s AND space_id=%s",
    ),
    (
        "resources",
        "DELETE FROM space.resources WHERE organization_id=%s AND space_id=%s",
        "SELECT count(*) AS rows FROM space.resources WHERE organization_id=%s AND space_id=%s",
    ),
    (
        "collections",
        "DELETE FROM space.collections WHERE organization_id=%s AND space_id=%s",
        "SELECT count(*) AS rows FROM space.collections WHERE organization_id=%s AND space_id=%s",
    ),
    (
        "api_keys",
        "DELETE FROM space.api_keys WHERE organization_id=%s AND space_id=%s",
        "SELECT count(*) FROM space.api_keys WHERE organization_id=%s AND space_id=%s",
    ),
    (
        "oauth_grants",
        "DELETE FROM space.oauth_grants WHERE organization_id=%s AND space_id=%s",
        "SELECT count(*) AS rows FROM space.oauth_grants WHERE organization_id=%s AND space_id=%s",
    ),
    (
        "agent_identities",
        "DELETE FROM space.agent_identities WHERE organization_id=%s AND space_id=%s",
        "SELECT count(*) FROM space.agent_identities WHERE organization_id=%s AND space_id=%s",
    ),
    (
        "usage_limits",
        "DELETE FROM space.usage_limits WHERE organization_id=%s AND space_id=%s",
        "SELECT count(*) FROM space.usage_limits WHERE organization_id=%s AND space_id=%s",
    ),
)

# The account path. The Space goes first: its organization reference is an immediate
# constraint, while the two constraints that point back at the Space (a Space's metadata key
# and an organization's default Space) are deferred, so they settle at commit. The owner
# principal goes last, after the organization that names it.
ORGANIZATIONS_SQL = "DELETE FROM space.organizations WHERE id=%s AND default_space_id=%s"
SPACES_SQL = "DELETE FROM space.spaces WHERE organization_id=%s AND id=%s"
KEYS_SQL = "DELETE FROM space.space_keys WHERE organization_id=%s AND space_id=%s"
OWNER_SQL = "SELECT owner_principal_id AS owner FROM space.organizations WHERE id=%s"
PRINCIPALS_SQL = "DELETE FROM space.principals WHERE id=%s"


def _scalar(row) -> object:
    """Read one value from a row, whether the connection yields tuples or mappings.

    The application connects with named rows and the operator's setup connection yields
    tuples, so this module must not assume either.
    """
    if row is None:
        return None
    if isinstance(row, dict):
        return next(iter(row.values()))
    return row[0]


def object_prefix(organization_id: UUID, space_id: UUID) -> str:
    """The key prefix every object of one Space lives under, where objects exist."""
    return f"spaces/{organization_id}/{space_id}/"


def cleanup_objects(connection, organization_id, space_id, objects):
    """Resume only persisted cleanup; never infer reset targets from current content."""
    deleted = 0
    while True:
        with connection.transaction():
            rows = connection.execute(
                "SELECT object_key, is_prefix FROM space.purge_cleanup "
                "WHERE organization_id=%s AND space_id=%s ORDER BY object_key LIMIT 1000",
                (organization_id, space_id),
            ).fetchall()
        if not rows:
            return deleted
        entries = [
            (row["object_key"], row["is_prefix"]) if isinstance(row, dict) else row for row in rows
        ]
        keys = [key for key, prefix in entries if not prefix]
        if keys:
            deleted += objects.delete_keys(keys)
        for key, prefix in entries:
            if prefix:
                deleted += objects.delete_prefix(key)
        # A failed cloud call leaves the entire batch available for an idempotent retry.
        with connection.transaction():
            connection.execute(
                "DELETE FROM space.purge_cleanup WHERE organization_id=%s "
                "AND space_id=%s AND object_key=ANY(%s)",
                (organization_id, space_id, [key for key, _ in entries]),
            )


def purge_space(
    connection,
    organization_id: UUID,
    space_id: UUID,
    *,
    objects=None,
    account: bool = False,
    dry_run: bool = False,
    cleanup_only: bool = False,
    journal=None,
    replay_event=None,
) -> dict:
    """Reset content or delete an account, then execute its saved cleanup work.

    Keys disappear with the account's database records, before object cleanup. The
    operator must drain running writers before final account cleanup: an upload already
    in flight can otherwise arrive after the last prefix listing. A reset deletes only
    objects captured from old revisions, so subsequent writes keep their own objects.
    """
    if connection.info.transaction_status != TransactionStatus.IDLE:
        raise ValueError("Purge requires an idle connection so cleanup follows a real commit")
    counts = {}
    parameters = (organization_id, space_id)
    with connection.transaction():
        connection.execute("SET CONSTRAINTS ALL DEFERRED")
        owner = _scalar(
            connection.execute(
                "SELECT o.owner_principal_id FROM space.organizations o JOIN space.spaces s "
                "ON s.organization_id=o.id AND s.id=o.default_space_id "
                "WHERE o.id=%s AND s.id=%s FOR UPDATE OF s, o",
                parameters,
            ).fetchone()
        )
        if account and owner and not cleanup_only:
            team_bound = _scalar(
                connection.execute(
                    "SELECT EXISTS(SELECT 1 FROM space.organizations "
                    "WHERE owner_principal_id=%s AND kind='team') OR "
                    "EXISTS(SELECT 1 FROM space.organization_members WHERE principal_id=%s)",
                    (owner, owner),
                ).fetchone()
            )
            if team_bound:
                # Check before appending an irreversible external deletion event.
                # The personal-account command must never erase a team's owner,
                # shared authorship, or another member's private Space.
                raise ValueError(
                    "Account erasure with team history requires an organization-aware "
                    "ownership and retention plan. This personal-account command cannot do it."
                )
        if not cleanup_only:
            if not dry_run and owner:
                from space.environment import subject_hash

                principal = connection.execute(
                    "SELECT issuer,subject FROM space.principals WHERE id=%s", (owner,)
                ).fetchone()
                issuer, subject = (
                    (principal["issuer"], principal["subject"])
                    if isinstance(principal, dict)
                    else principal
                )
                event = replay_event
                if journal is not None:
                    event = journal.append(
                        organization_id=organization_id,
                        space_id=space_id,
                        account=account,
                        issuer=issuer,
                        subject=subject,
                    )
                if event is not None:
                    connection.execute(
                        "INSERT INTO space.deletion_receipts(id) VALUES(%s) ON CONFLICT DO NOTHING",
                        (event.id,),
                    )
                if account:
                    connection.execute(
                        "INSERT INTO space.erased_subjects(subject_hash) VALUES(%s) "
                        "ON CONFLICT DO NOTHING",
                        (subject_hash(issuer, subject),),
                    )
                # Object cleanup is recorded only when an object store holds bodies;
                # a self-hosted installation keeps every body inline in PostgreSQL.
                if objects is not None and account:
                    connection.execute(
                        "INSERT INTO space.purge_cleanup "
                        "(organization_id,space_id,object_key,is_prefix) VALUES(%s,%s,%s,true) "
                        "ON CONFLICT DO NOTHING",
                        (*parameters, object_prefix(*parameters)),
                    )
                elif objects is not None:
                    connection.execute(
                        "INSERT INTO space.purge_cleanup (organization_id,space_id,object_key) "
                        "SELECT DISTINCT organization_id,space_id,object_key "
                        "FROM space.resource_revisions WHERE organization_id=%s "
                        "AND space_id=%s AND object_key IS NOT NULL ON CONFLICT DO NOTHING",
                        parameters,
                    )
                    connection.execute(
                        "INSERT INTO space.purge_cleanup (organization_id,space_id,object_key) "
                        "SELECT organization_id,space_id,object_key FROM space.restore_objects "
                        "WHERE organization_id=%s AND space_id=%s ON CONFLICT DO NOTHING",
                        parameters,
                    )
                if not account:
                    connection.execute(
                        "UPDATE space.spaces SET content_revision=content_revision+1,"
                        "change_revisions=change_revisions||(SELECT "
                        "jsonb_object_agg(kind,content_revision+1) FROM unnest(%s::text[]) kind) "
                        "WHERE organization_id=%s AND id=%s",
                        (list(content_changes.KINDS), *parameters),
                    )
            for table, delete, count in STEPS:
                if table == "collections" and not account:
                    continue
                counts[table] = int(_scalar(connection.execute(count, parameters).fetchone()))
                if not dry_run:
                    connection.execute(delete, parameters)
            if account:
                for table, count, delete, args in (
                    (
                        "spaces",
                        "SELECT count(*) FROM space.spaces WHERE organization_id=%s AND id=%s",
                        SPACES_SQL,
                        parameters,
                    ),
                    (
                        "space_keys",
                        "SELECT count(*) FROM space.space_keys WHERE organization_id=%s "
                        "AND space_id=%s",
                        KEYS_SQL,
                        parameters,
                    ),
                    (
                        "organizations",
                        "SELECT count(*) FROM space.organizations WHERE id=%s "
                        "AND default_space_id=%s",
                        ORGANIZATIONS_SQL,
                        parameters,
                    ),
                    (
                        "principals",
                        "SELECT count(*) FROM space.principals WHERE id=%s",
                        PRINCIPALS_SQL,
                        (owner,),
                    ),
                ):
                    counts[table] = int(_scalar(connection.execute(count, args).fetchone()))
                    if not dry_run:
                        connection.execute(delete, args)
        counts["pending_cleanup"] = int(
            _scalar(
                connection.execute(
                    "SELECT count(*) FROM space.purge_cleanup WHERE organization_id=%s "
                    "AND space_id=%s",
                    parameters,
                ).fetchone()
            )
        )
    if dry_run:
        return counts | {"dry_run": True}
    if objects is not None:
        counts["objects"] = cleanup_objects(connection, *parameters, objects)
        counts["pending_cleanup"] = 0
    return counts


def main(argv=None) -> int:
    """Operator entry point: connect with the migration login and purge one Space."""
    from space.database_setup import InstallSettings, connect

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("organization_id", type=UUID)
    parser.add_argument("space_id", type=UUID)
    parser.add_argument("--dry-run", action="store_true", help="report what would go")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    # Only a content reset: deleting the account would erase the installation's single
    # owner for good, and the API could not start again. To remove everything, delete
    # the database volume instead (docker compose down -v).
    with connect(InstallSettings()) as connection:
        counts = purge_space(connection, args.organization_id, args.space_id, dry_run=args.dry_run)
    for key in sorted(counts):
        print(f"{key}={counts[key]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
