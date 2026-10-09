"""Provision the restricted queue logins the background workers use.

`space.install` calls `provision` for each queue with the password from that worker's
connection URL. A worker login can assume only its own capability role.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from psycopg import sql

from space import job_worker, webhook_worker
from space.database_setup import DatabaseSetupError, runtime_url
from space.settings import BACKEND_DIR


@dataclass(frozen=True)
class Queue:
    """One worker's login, the capability role it may assume and its connection setting."""

    name: str
    login: str
    capability: str
    capability_marker: str
    migration: str
    marker: str
    config_key: str
    probe: str
    restrict: Callable
    # Longer than the worker's own attempt deadline, so PostgreSQL never ends a
    # transaction that the worker's supervisor would still let finish.
    idle_in_transaction: str


QUEUES = {
    "jobs": Queue(
        name="jobs",
        login="space_worker",
        capability="space_job_worker",
        capability_marker="space.memory_jobs.worker.v1",
        migration="0008_memory_jobs",
        marker="Space restricted extraction queue login v1",
        config_key="SPACE_JOB_DATABASE_URL",
        probe="SELECT id FROM space.memory_jobs LIMIT 0",
        restrict=job_worker.restrict_connection,
        idle_in_transaction="30s",
    ),
    # The delivery worker holds a subscription share lock through network I/O,
    # bounded by its 30-second attempt deadline.
    "webhooks": Queue(
        name="webhooks",
        login="space_webhook",
        capability="space_webhook_worker",
        capability_marker="space.webhooks.worker.v1",
        migration="0027_webhooks",
        marker="Space restricted webhook delivery login v1",
        config_key="SPACE_WEBHOOK_DATABASE_URL",
        probe="SELECT id FROM space.webhook_deliveries LIMIT 0",
        restrict=webhook_worker.restrict_connection,
        idle_in_transaction="45s",
    ),
}
JOBS = QUEUES["jobs"]
LOGIN = JOBS.login


def worker_url(migration_url: str, password: str, queue: Queue = JOBS) -> str:
    parsed = urlsplit(runtime_url(migration_url, password))
    username = unquote(parsed.username).replace("space_app", queue.login, 1)
    return urlunsplit(
        parsed._replace(netloc=quote(username, safe="") + parsed.netloc[parsed.netloc.index(":") :])
    )


def supported_versions(
    directory=BACKEND_DIR / "migrations/versions", first: str = JOBS.migration
) -> set[str]:
    """Every migration from the one that created the worker role onwards.

    Read from the migration chain itself, so adding a migration cannot silently stop
    worker setup the way a hand-kept list did after 0024.
    """
    parents = {}
    for path in directory.glob("*.py"):
        text = path.read_text()
        revision = re.search(r'^revision = "([^"]+)"', text, re.MULTILINE)
        parent = re.search(r'^down_revision = "([^"]+)"', text, re.MULTILINE)
        if revision:
            parents[revision.group(1)] = parent.group(1) if parent else None
    supported = set()
    for revision in parents:
        current = revision
        while current and current not in supported:
            if current == first:
                break
            current = parents.get(current)
        if current == first or current in supported:
            supported.add(revision)
    return supported


def provision(connection, password: str, connection_limit: int, queue: Queue = JOBS) -> None:
    """Caller owns the transaction; unrelated or broadened roles fail closed."""
    if type(connection_limit) is not int or not 1 <= connection_limit <= 10000:
        raise DatabaseSetupError("Worker connection allowance must be between 1 and 10000")
    # One lock for every queue, because each setup checks the other logins' memberships.
    connection.execute("SELECT pg_advisory_xact_lock(831672, 1)")
    version = connection.execute("SELECT version_num FROM space.alembic_version").fetchone()
    supported = supported_versions(first=queue.migration)
    if not supported:
        raise DatabaseSetupError(
            "Run worker setup from a checkout that includes backend/migrations"
        )
    if version is None or version[0] not in supported:
        raise DatabaseSetupError("Apply the supported foundation migrations before worker setup")
    capability = connection.execute(
        "SELECT shobj_description(oid,'pg_authid'),rolcanlogin,rolsuper,rolbypassrls,"
        "rolcreaterole,rolcreatedb,rolreplication,rolinherit FROM pg_roles WHERE rolname=%s",
        (queue.capability,),
    ).fetchone()
    if not capability or capability[0] != queue.capability_marker or any(capability[1:]):
        raise DatabaseSetupError("Worker capability role is missing or privileged")
    # The API and every other worker keep separate logins, so no one credential can
    # both serve requests and drain a queue, or drain two queues.
    for other in ["space_app", *(q.login for q in QUEUES.values() if q.login != queue.login)]:
        if connection.execute(
            "SELECT pg_has_role(%s,%s,'MEMBER') FROM pg_roles WHERE rolname=%s",
            (other, queue.capability, other),
        ).fetchone() == (True,):
            raise DatabaseSetupError(
                "The API login must not have worker membership"
                if other == "space_app"
                else "Another worker login must not have this worker's membership"
            )
    existing = connection.execute(
        "SELECT shobj_description(oid,'pg_authid'),rolcanlogin,rolsuper,rolbypassrls,"
        "rolcreaterole,rolcreatedb,rolreplication,rolinherit FROM pg_roles WHERE rolname=%s",
        (queue.login,),
    ).fetchone()
    if existing and (existing[0] != queue.marker or not existing[1] or any(existing[2:])):
        raise DatabaseSetupError("An unrelated or privileged worker login exists")
    if existing:
        memberships = connection.execute(
            "SELECT r.rolname FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.roleid "
            "JOIN pg_roles member ON member.oid=m.member WHERE member.rolname=%s",
            (queue.login,),
        ).fetchall()
        if memberships != [(queue.capability,)]:
            raise DatabaseSetupError("Worker login has unexpected role memberships")
    else:
        verifier = connection.pgconn.encrypt_password(
            password.encode(), queue.login.encode(), b"scram-sha-256"
        ).decode()
        connection.execute(
            sql.SQL(
                "CREATE ROLE {} LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE "
                "NOREPLICATION NOBYPASSRLS PASSWORD {}"
            ).format(sql.Identifier(queue.login), sql.Literal(verifier))
        )
        connection.execute(
            sql.SQL("COMMENT ON ROLE {} IS {}").format(
                sql.Identifier(queue.login), sql.Literal(queue.marker)
            )
        )
        connection.execute(
            sql.SQL("GRANT {} TO {}").format(
                sql.Identifier(queue.capability), sql.Identifier(queue.login)
            )
        )
    connection.execute(
        sql.SQL("ALTER ROLE {} CONNECTION LIMIT {}").format(
            sql.Identifier(queue.login), sql.Literal(connection_limit)
        )
    )
    database = connection.execute("SELECT current_database()").fetchone()[0]
    connection.execute(
        sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
            sql.Identifier(database), sql.Identifier(queue.login)
        )
    )
    connection.execute(
        sql.SQL("ALTER ROLE {} SET search_path = pg_catalog, space").format(
            sql.Identifier(queue.login)
        )
    )
    for setting, value in (
        ("statement_timeout", "15s"),
        ("lock_timeout", "5s"),
        ("idle_in_transaction_session_timeout", queue.idle_in_transaction),
    ):
        connection.execute(
            sql.SQL("ALTER ROLE {} SET {} = {}").format(
                sql.Identifier(queue.login), sql.Identifier(setting), sql.Literal(value)
            )
        )
