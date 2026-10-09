"""Install or upgrade a self-hosted database. Safe to run before every start.

Parameters: environment settings (see the repository's .env.example). The migration URL
is a PostgreSQL superuser; each other URL names one restricted login and its password:
SPACE_DATABASE_URL (space_app, the API), SPACE_JOB_DATABASE_URL (space_worker) and
SPACE_WEBHOOK_DATABASE_URL (space_webhook).
What it does: waits for PostgreSQL, creates the restricted logins or updates their
passwords to the configured ones, applies every migration, sizes the empty memory index
to SPACE_MEMORY_EMBEDDING_DIMENSIONS, then logs in as each login to verify it.
Output: exit status 0 and one PASS line. Credentials and driver errors are never printed.
"""

import argparse
import time

import psycopg
from psycopg.rows import dict_row
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from space.database_setup import (
    DatabaseSetupError,
    InstallSettings,
    apply_foundation,
    connect,
    database_failure_detail,
    ensure_embedding_width,
    login_password,
    set_password,
)
from space.settings import database_connection_options
from space.worker_setup import QUEUES, provision

WORKER_CONNECTION_LIMIT = 2


def wait_for_database(settings: InstallSettings, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            with connect(settings):
                return
        except psycopg.OperationalError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(1)


def install(settings: InstallSettings) -> None:
    if not settings.migration_database_url or not settings.database_url:
        raise DatabaseSetupError("Set SPACE_MIGRATION_DATABASE_URL and SPACE_DATABASE_URL")
    app_password = login_password(settings.database_url, "space_app")
    workers = [
        (queue, url)
        for queue, url in (
            (QUEUES["jobs"], settings.job_database_url),
            (QUEUES["webhooks"], settings.webhook_database_url),
        )
        if url is not None
    ]
    engine = create_engine(
        "postgresql+psycopg://",
        creator=lambda: connect(settings),
        poolclass=NullPool,
        hide_parameters=True,
    )
    try:
        with engine.begin() as connection:
            apply_foundation(
                connection, app_password, connection_limit=settings.database_connection_limit
            )
            ensure_embedding_width(connection, settings.memory_embedding_dimensions)
            set_password(connection.connection.driver_connection, "space_app", app_password)
    finally:
        engine.dispose()
    with connect(settings) as maintenance:
        for queue, url in workers:
            password = login_password(url, queue.login)
            with maintenance.transaction():
                provision(maintenance, password, WORKER_CONNECTION_LIMIT, queue)
                set_password(maintenance, queue.login, password)
    with connect(settings, runtime=True) as runtime:
        if runtime.execute("SELECT current_user").fetchone()[0] != "space_app":
            raise DatabaseSetupError("Runtime connection resolved to an unexpected identity")
    for queue, url in workers:
        with psycopg.connect(
            url.get_secret_value(),
            **database_connection_options(settings),
            connect_timeout=8,
            row_factory=dict_row,
        ) as worker:
            queue.restrict(worker)
            worker.execute(queue.probe)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--wait", type=float, default=60, help="seconds to wait for PostgreSQL (default: 60)"
    )
    args = parser.parse_args(argv)
    try:
        settings = InstallSettings()
        wait_for_database(settings, args.wait)
        install(settings)
        print("PASS Database installed and migrated; restricted logins verified.")
        return 0
    except DatabaseSetupError as error:
        print("FAILED " + str(error))
    except psycopg.Error as error:
        print("FAILED " + database_failure_detail(error))
    except Exception:
        print("FAILED Installation did not complete; driver details withheld.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
