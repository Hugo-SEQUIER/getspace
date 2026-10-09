import secrets
from urllib.parse import urlsplit

import psycopg
import pytest
from psycopg.rows import dict_row
from test_database_setup import database as database_fixture

from space import webhook_delivery, webhook_worker
from space.database_setup import DatabaseSetupError
from space.job_worker import restrict_connection
from space.jobs import claim
from space.worker_setup import JOBS, LOGIN, QUEUES, provision, supported_versions, worker_url

WEBHOOKS = QUEUES["webhooks"]

database = database_fixture


def test_worker_url_preserves_only_the_target_and_encodes_credentials():
    value = worker_url(
        "postgresql://postgres:old@db:5432/space?user=postgres&sslmode=disable",
        "test@secret[]",
    )
    assert value == "postgresql://space_worker:test%40secret%5B%5D@db:5432/space"
    assert (
        urlsplit(worker_url("postgresql://postgres:old@[::1]:5432/db", "secret")).username == LOGIN
    )


def test_provisioned_login_can_claim_but_cannot_read_content_or_change_inputs(database):
    connect_as = database[0]
    password = secrets.token_urlsafe(32)
    with connect_as() as setup:
        provision(setup, password, 1)
        connection_args = {
            "host": setup.info.host,
            "port": setup.info.port,
            "dbname": setup.info.dbname,
            "user": LOGIN,
            "password": password,
            "sslmode": "disable",
            "row_factory": dict_row,
        }
    # Idempotent setup may change the budget but must never replace the password.
    with connect_as() as setup:
        provision(setup, "different-not-used", 2)
    with psycopg.connect(**connection_args) as worker:
        restrict_connection(worker)
        assert claim(worker) is None
        worker.execute("SELECT id FROM memory_jobs LIMIT 0")
        worker.execute("UPDATE memory_jobs SET status='queued' WHERE false")
        worker.commit()
        for statement in (
            "SELECT * FROM space.space_keys",
            "SELECT * FROM space.resource_revisions",
            "UPDATE memory_jobs SET subject='forged' WHERE false",
            "SET ROLE space_app",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with worker.transaction():
                    worker.execute(statement)
    with connect_as(runtime=True) as api:
        assert not api.execute(
            "SELECT pg_has_role(current_user,'space_job_worker','MEMBER')"
        ).fetchone()[0]


def test_worker_setup_refuses_unrelated_role_and_rolls_back(database):
    with database[0]() as setup:
        provision(setup, secrets.token_urlsafe(32), 1)
        setup.execute("COMMENT ON ROLE space_worker IS 'unrelated'")
        with pytest.raises(DatabaseSetupError, match="unrelated"):
            provision(setup, "unused", 1)
        setup.rollback()


@pytest.mark.parametrize("revision", ["0007_purge_cleanup", "9999_unknown"])
def test_worker_setup_rejects_unsupported_schema_versions(database, revision):
    with database[0]() as setup:
        setup.execute("UPDATE space.alembic_version SET version_num=%s", (revision,))
        with pytest.raises(DatabaseSetupError, match="supported foundation migrations"):
            provision(setup, "unused", 1)
        setup.rollback()


def test_worker_setup_still_accepts_the_original_jobs_migration(database):
    with database[0]() as setup:
        setup.execute("UPDATE space.alembic_version SET version_num='0008_memory_jobs'")
        provision(setup, secrets.token_urlsafe(32), 1)
        setup.rollback()


def test_supported_versions_follow_the_migration_chain(tmp_path):
    from space.worker_setup import supported_versions

    versions = supported_versions()
    assert {"0008_memory_jobs", "0024_node_links", "0029_memory_routes"} <= versions
    assert "0007_extraction_receipts" not in versions and "0001_private_space" not in versions
    for name, parent in (
        ("0008_memory_jobs", None),
        ("0009_next", "0008_memory_jobs"),
        ("0010_future", "0009_next"),
        ("0005_old", None),
    ):
        down = f'"{parent}"' if parent else "None"
        (tmp_path / f"{name}.py").write_text(f'revision = "{name}"\ndown_revision = {down}\n')
    assert supported_versions(tmp_path) == {"0008_memory_jobs", "0009_next", "0010_future"}


def test_webhook_queue_uses_its_own_login_and_starts_at_the_webhooks_migration():
    assert {JOBS.login, WEBHOOKS.login, "space_app"} == {
        "space_worker",
        "space_webhook",
        "space_app",
    }
    assert JOBS.config_key == "SPACE_JOB_DATABASE_URL"
    assert WEBHOOKS.config_key == "SPACE_WEBHOOK_DATABASE_URL"
    assert (
        urlsplit(worker_url("postgresql://postgres:old@db:5432/space", "x", WEBHOOKS)).username
        == "space_webhook"
    )
    versions = supported_versions(first=WEBHOOKS.migration)
    assert {"0027_webhooks", "0029_memory_routes"} <= versions
    assert "0026_document_folders" not in versions


def test_provisioned_webhook_login_can_claim_deliveries_but_not_jobs_or_content(database):
    connect_as = database[0]
    password = secrets.token_urlsafe(32)
    with connect_as() as setup:
        provision(setup, password, 2, WEBHOOKS)
        connection_args = {
            "host": setup.info.host,
            "port": setup.info.port,
            "dbname": setup.info.dbname,
            "user": WEBHOOKS.login,
            "password": password,
            "sslmode": "disable",
            "row_factory": dict_row,
        }
        limit, config = setup.execute(
            "SELECT rolconnlimit, rolconfig FROM pg_roles WHERE rolname=%s", (WEBHOOKS.login,)
        ).fetchone()
    assert limit == 2
    assert "idle_in_transaction_session_timeout=45s" in config
    with connect_as() as setup:
        provision(setup, "different-not-used", 2, WEBHOOKS)
    with psycopg.connect(**connection_args) as worker:
        webhook_worker.restrict_connection(worker)
        assert webhook_delivery.claim(worker) is None
        worker.execute(WEBHOOKS.probe)
        worker.commit()
        for statement in (
            "SELECT * FROM space.space_keys",
            "SELECT * FROM space.resource_revisions",
            "SELECT id FROM memory_jobs LIMIT 0",
            "UPDATE space.webhook_subscriptions SET paused=true WHERE false",
            "SET ROLE space_job_worker",
            "SET ROLE space_app",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with worker.transaction():
                    worker.execute(statement)
    with psycopg.connect(**connection_args) as worker:
        with pytest.raises(RuntimeError):
            restrict_connection(worker)


def test_queue_logins_stay_separate_from_each_other_and_the_api(database):
    with database[0]() as setup:
        provision(setup, secrets.token_urlsafe(32), 1, JOBS)
        provision(setup, secrets.token_urlsafe(32), 1, WEBHOOKS)
        setup.execute("GRANT space_webhook_worker TO space_worker")
        with pytest.raises(DatabaseSetupError, match="Another worker login"):
            provision(setup, "unused", 1, WEBHOOKS)
        setup.rollback()
    with database[0]() as setup:
        provision(setup, secrets.token_urlsafe(32), 1, WEBHOOKS)
        setup.execute("GRANT space_job_worker TO space_webhook")
        with pytest.raises(DatabaseSetupError, match="unexpected role memberships"):
            provision(setup, "unused", 1, WEBHOOKS)
        setup.rollback()
    with database[0]() as setup:
        setup.execute("GRANT space_webhook_worker TO space_app")
        with pytest.raises(DatabaseSetupError, match="API login"):
            provision(setup, "unused", 1, WEBHOOKS)
        setup.rollback()
