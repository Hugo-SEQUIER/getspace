import secrets
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from test_database_setup import database as database_fixture

from space.database_setup import apply_foundation

database = database_fixture
OWNER_ONLY_TABLES = ("alembic_version", "erased_subjects", "deletion_receipts")
INTERNAL_TABLES = (*OWNER_ONLY_TABLES, "environment_control")
MIGRATION_SQL = Path(__file__).parents[1] / "migrations/versions/0023_internal_table_rls.sql"


@pytest.fixture
def internal_rows(database):
    connect, _, _, _, _ = database
    environment_id, subject_hash, receipt_id = uuid4(), secrets.token_bytes(32), uuid4()
    with connect() as connection:
        connection.execute(
            "INSERT INTO space.environment_control(environment_id,name,serving) "
            "VALUES(%s,'test',true)",
            (environment_id,),
        )
        connection.execute(
            "INSERT INTO space.erased_subjects(subject_hash) VALUES(%s)", (subject_hash,)
        )
        connection.execute("INSERT INTO space.deletion_receipts(id) VALUES(%s)", (receipt_id,))
    try:
        yield environment_id, subject_hash, receipt_id
    finally:
        with connect() as connection:
            connection.execute(
                "DELETE FROM space.environment_control WHERE environment_id=%s", (environment_id,)
            )
            connection.execute(
                "DELETE FROM space.erased_subjects WHERE subject_hash=%s", (subject_hash,)
            )
            connection.execute("DELETE FROM space.deletion_receipts WHERE id=%s", (receipt_id,))


def test_every_space_table_has_rls_including_latest_memory_links(database):
    connect, _, _, _, _ = database
    with connect() as connection:
        tables = connection.execute(
            "SELECT relname,relrowsecurity,relforcerowsecurity FROM pg_class "
            "WHERE relnamespace='space'::regnamespace AND relkind IN ('r','p')"
        ).fetchall()
    assert set(INTERNAL_TABLES) | {"memory_links"} <= {row[0] for row in tables}
    assert [name for name, enabled, _ in tables if not enabled] == []
    assert [name for name, _, forced in tables if name in INTERNAL_TABLES and forced] == []


def test_trusted_owner_can_maintain_internal_tables(database, internal_rows):
    connect, _, _, _, _ = database
    environment_id, subject_hash, receipt_id = internal_rows
    sentinel = "rls_probe_" + uuid4().hex[:12]
    with connect() as connection:
        try:
            assert connection.execute(
                "SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user"
            ).fetchone() == (False, False)
            assert connection.execute(
                "SELECT environment_id FROM space.environment_control WHERE environment_id=%s",
                (environment_id,),
            ).fetchone() == (environment_id,)
            assert (
                connection.execute(
                    "UPDATE space.environment_control SET serving=false WHERE environment_id=%s",
                    (environment_id,),
                ).rowcount
                == 1
            )
            assert (
                connection.execute(
                    "UPDATE space.erased_subjects SET erased_at=now() WHERE subject_hash=%s",
                    (subject_hash,),
                ).rowcount
                == 1
            )
            assert (
                connection.execute(
                    "UPDATE space.deletion_receipts SET applied_at=now() WHERE id=%s", (receipt_id,)
                ).rowcount
                == 1
            )
            connection.execute(
                "INSERT INTO space.alembic_version(version_num) VALUES(%s)", (sentinel,)
            )
            assert connection.execute(
                "SELECT version_num FROM space.alembic_version WHERE version_num=%s", (sentinel,)
            ).fetchone() == (sentinel,)
            assert (
                connection.execute(
                    "UPDATE space.alembic_version SET version_num=%s WHERE version_num=%s",
                    (sentinel + "_u", sentinel),
                ).rowcount
                == 1
            )
            for table, key, value in (
                ("environment_control", "environment_id", environment_id),
                ("erased_subjects", "subject_hash", subject_hash),
                ("deletion_receipts", "id", receipt_id),
                ("alembic_version", "version_num", sentinel + "_u"),
            ):
                assert (
                    connection.execute(
                        sql.SQL("DELETE FROM space.{} WHERE {}=%s").format(
                            sql.Identifier(table), sql.Identifier(key)
                        ),
                        (value,),
                    ).rowcount
                    == 1
                )
        finally:
            connection.rollback()


def test_reapplying_rls_migration_and_setup_preserves_internal_rows(database, internal_rows):
    connect, engine, _, _, password = database
    environment_id, subject_hash, receipt_id = internal_rows
    migration = MIGRATION_SQL.read_text()
    for _ in range(2):
        with engine.begin() as connection:
            connection.exec_driver_sql(migration, execution_options={"no_parameters": True})
            apply_foundation(connection, password)
    with connect() as connection:
        assert connection.execute("SELECT version_num FROM space.alembic_version").fetchall() == [
            ("0040_webhook_inbox",)
        ]
        assert connection.execute(
            "SELECT subject_hash FROM space.erased_subjects WHERE subject_hash=%s", (subject_hash,)
        ).fetchone() == (subject_hash,)
        assert connection.execute(
            "SELECT id FROM space.deletion_receipts WHERE id=%s", (receipt_id,)
        ).fetchone() == (receipt_id,)
    with connect(runtime=True) as connection:
        assert connection.execute(
            "SELECT environment_id,serving FROM space.environment_control WHERE environment_id=%s",
            (environment_id,),
        ).fetchone() == (environment_id, True)


@pytest.mark.parametrize("table", OWNER_ONLY_TABLES)
def test_runtime_cannot_directly_read_owner_only_tables(database, table):
    connect, _, _, _, _ = database
    with pytest.raises(psycopg.errors.InsufficientPrivilege), connect(runtime=True) as connection:
        connection.execute(sql.SQL("SELECT * FROM space.{}").format(sql.Identifier(table)))


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO space.environment_control(environment_id,name) VALUES(%s,'test')",
        "UPDATE space.environment_control SET serving=false WHERE environment_id=%s",
        "DELETE FROM space.environment_control WHERE environment_id=%s",
    ],
)
def test_runtime_cannot_mutate_environment_status(database, internal_rows, statement):
    connect, _, _, _, _ = database
    environment_id, _, _ = internal_rows
    with pytest.raises(psycopg.errors.InsufficientPrivilege), connect(runtime=True) as connection:
        connection.execute(statement, (environment_id,))


def test_security_definer_guards_still_see_internal_rows(database, internal_rows):
    connect, _, _, _, _ = database
    environment_id, subject_hash, _ = internal_rows
    with connect(runtime=True) as connection:
        assert connection.execute(
            "SELECT space.identity_erased(%s),space.identity_erased(%s)",
            (subject_hash, secrets.token_bytes(32)),
        ).fetchone() == (True, False)
        assert connection.execute(
            "SELECT space.check_environment(%s),space.check_environment(%s),"
            "space.check_environment(NULL)",
            (environment_id, uuid4()),
        ).fetchone() == (True, False, False)
    with connect() as connection:
        connection.execute(
            "UPDATE space.environment_control SET serving=false WHERE environment_id=%s",
            (environment_id,),
        )
    with connect(runtime=True) as connection:
        assert connection.execute(
            "SELECT space.check_environment(%s)", (environment_id,)
        ).fetchone() == (False,)


def test_security_definer_environment_guard_handles_unconfigured_database(database):
    connect, _, _, _, _ = database
    with connect(runtime=True) as connection:
        assert connection.execute(
            "SELECT space.check_environment(NULL),space.check_environment(%s)", (uuid4(),)
        ).fetchone() == (True, False)


def test_unprivileged_select_grants_do_not_bypass_default_deny(database, internal_rows):
    connect, _, _, _, _ = database
    environment_id, subject_hash, receipt_id = internal_rows
    probe = sql.Identifier("rls_probe_" + uuid4().hex)
    with connect(admin=True) as connection:
        try:
            connection.execute(sql.SQL("CREATE ROLE {} NOLOGIN NOBYPASSRLS").format(probe))
            connection.execute(sql.SQL("GRANT USAGE ON SCHEMA space TO {}").format(probe))
            for table, key, value in (
                ("alembic_version", "version_num", "0040_webhook_inbox"),
                ("erased_subjects", "subject_hash", subject_hash),
                ("deletion_receipts", "id", receipt_id),
                ("environment_control", "environment_id", environment_id),
            ):
                assert connection.execute(
                    sql.SQL("SELECT count(*) FROM space.{} WHERE {}=%s").format(
                        sql.Identifier(table), sql.Identifier(key)
                    ),
                    (value,),
                ).fetchone() == (1,)
                connection.execute(
                    sql.SQL("GRANT SELECT ON space.{} TO {}").format(sql.Identifier(table), probe)
                )
            connection.execute(sql.SQL("SET LOCAL ROLE {}").format(probe))
            assert connection.execute(
                "SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user"
            ).fetchone() == (False, False)
            for table in INTERNAL_TABLES:
                assert connection.execute(
                    sql.SQL("SELECT count(*) FROM space.{}").format(sql.Identifier(table))
                ).fetchone() == (0,)
        finally:
            connection.rollback()
