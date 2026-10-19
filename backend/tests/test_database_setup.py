"""Real PostgreSQL tests in an opt-in disposable local container, never the .env database."""

import os
import secrets
import select
import shutil
import subprocess
import time
from dataclasses import dataclass
from uuid import uuid4

import psycopg
import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from space.database_setup import (
    SCHEMA_MARKER,
    DatabaseSetupError,
    apply_foundation,
    runtime_url,
)
from space.retention import purge


def test_runtime_url_drops_migration_identity_overrides_and_encodes_password():
    value = runtime_url(
        "postgresql://postgres:old@db:5432/space?user=postgres&password=old&sslmode=disable",
        "fixture@password[]",
    )
    assert value == "postgresql://space_app:fixture%40password%5B%5D@db:5432/space"


@dataclass
class Identity:
    subject: str
    principal_id: str
    organization_id: str
    space_id: str
    key_id: str


def set_scope(connection, identity, *, subject=None, organization=None, space=None):
    values = {
        "space.identity_issuer": "https://identity.invalid",
        "space.identity_subject": subject or identity.subject,
        "space.organization_id": organization or identity.organization_id,
        "space.space_id": space or identity.space_id,
    }
    for name, value in values.items():
        connection.execute("SELECT set_config(%s, %s, true)", (name, value))


def seed(connection):
    identity = Identity(*(str(uuid4()) for _ in range(5)))
    set_scope(connection, identity)
    connection.execute(
        "INSERT INTO space.principals(id,issuer,subject) VALUES(%s,%s,%s)",
        (identity.principal_id, "https://identity.invalid", identity.subject),
    )
    connection.execute(
        "INSERT INTO space.organizations(id,owner_principal_id,default_space_id) VALUES(%s,%s,%s)",
        (identity.organization_id, identity.principal_id, identity.space_id),
    )
    connection.execute(
        "INSERT INTO space.spaces(organization_id,id,metadata_key_id,encrypted_metadata) "
        "VALUES(%s,%s,%s,%s)",
        (identity.organization_id, identity.space_id, identity.key_id, secrets.token_bytes(64)),
    )
    connection.execute(
        "INSERT INTO space.space_keys(organization_id,space_id,id,wrapping_provider,"
        "wrapping_key_reference,wrapped_key) VALUES(%s,%s,%s,'test','test-key',%s)",
        (identity.organization_id, identity.space_id, identity.key_id, secrets.token_bytes(128)),
    )
    return identity


@pytest.fixture(scope="module")
def database():
    if os.getenv("SPACE_RUN_DATABASE_TESTS") != "1":
        pytest.skip("Set SPACE_RUN_DATABASE_TESTS=1 to run isolated local PostgreSQL tests")
    name = "space-database-test-" + uuid4().hex[:12]
    docker = shutil.which("docker")
    if docker is None:
        pytest.fail("Docker is required for the opted-in database tests")
    admin_password = secrets.token_urlsafe(32)
    migration_password = secrets.token_urlsafe(32)
    app_password = secrets.token_urlsafe(32)
    subprocess.run(  # noqa: S603 - fixed local disposable Docker command, no shell
        [
            docker,
            "run",
            "--rm",
            "--detach",
            "--pull",
            "never",
            "--name",
            name,
            "--publish",
            "127.0.0.1::5432",
            "--tmpfs",
            "/var/lib/postgresql/data:rw,nosuid,size=256m",
            "--env",
            "POSTGRES_PASSWORD=" + admin_password,
            # Migration 0003 needs the pgvector extension beside stock PostgreSQL.
            "pgvector/pgvector:pg17",
        ],
        check=True,
        capture_output=True,
    )
    engine = None
    try:
        address = subprocess.check_output(  # noqa: S603
            [docker, "port", name, "5432/tcp"], text=True
        ).strip()
        port = int(address.rsplit(":", 1)[1])

        def connect_as(*, runtime=False, admin=False):
            # Only this disposable loopback test database uses sslmode=disable.
            return psycopg.connect(
                host="127.0.0.1",
                port=port,
                dbname="postgres",
                sslmode="disable",
                user="space_app" if runtime else ("postgres" if admin else "space_migrator"),
                password=app_password
                if runtime
                else (admin_password if admin else migration_password),
                connect_timeout=2,
            )

        deadline = time.monotonic() + 30
        while True:
            try:
                with connect_as(admin=True):
                    break
            except psycopg.OperationalError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Disposable PostgreSQL did not become ready") from None
                time.sleep(0.25)
        # The migrations must also work for a migration login that is not a superuser.
        with connect_as(admin=True) as connection:
            connection.execute(
                psycopg.sql.SQL(
                    "CREATE ROLE space_migrator LOGIN NOSUPERUSER NOBYPASSRLS CREATEROLE "
                    "PASSWORD {}"
                ).format(psycopg.sql.Literal(migration_password))
            )
            connection.execute(
                "GRANT CONNECT, CREATE ON DATABASE postgres TO space_migrator WITH GRANT OPTION"
            )
        with connect_as(admin=True) as connection:
            # A non-superuser migration login needs a superuser to
            # install pgvector. Prepare the marked schema, then migrate as the
            # non-superuser migration login exactly as before.
            connection.execute("CREATE SCHEMA space AUTHORIZATION space_migrator")
            connection.execute(
                psycopg.sql.SQL("COMMENT ON SCHEMA space IS {}").format(
                    psycopg.sql.Literal(SCHEMA_MARKER)
                )
            )
            connection.execute("CREATE EXTENSION vector SCHEMA space")
        engine = create_engine(
            "postgresql+psycopg://", creator=connect_as, poolclass=NullPool, hide_parameters=True
        )
        with engine.begin() as connection:
            apply_foundation(connection, app_password)
        with connect_as(runtime=True) as connection:
            alice = seed(connection)
        with connect_as(runtime=True) as connection:
            bob = seed(connection)
        yield connect_as, engine, alice, bob, app_password
    finally:
        if engine:
            engine.dispose()
        subprocess.run(  # noqa: S603 - only remove the test container created above
            [docker, "rm", "--force", name], check=False, capture_output=True
        )


def test_private_owner_can_read_own_space_and_wrapped_key(database):
    connect_as, _, alice, _, _ = database
    with connect_as(runtime=True) as connection:
        set_scope(connection, alice)
        assert connection.execute("SELECT count(*) FROM space.principals").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM space.spaces").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM space.space_keys").fetchone()[0] == 1


def test_runtime_pool_replaces_a_connection_terminated_while_idle(database):
    from pydantic import SecretStr

    from space.settings import Settings
    from space.workspace import Database

    connect_as, _, _, _, _ = database

    class LocalConnection(psycopg.Connection):
        @classmethod
        def connect(cls, *args, **kwargs):
            connection = connect_as(runtime=True)
            connection.row_factory = kwargs["row_factory"]
            return connection

    settings = Settings(environment="test", database_pool_size=1).model_copy(
        update={"database_url": SecretStr("postgresql://fixture.invalid/unused")}
    )
    runtime = Database(settings)
    # Only the connection factory changes: runtime checkout, checks and role validation
    # run normally against the disposable fixture, never the configured live database.
    runtime.pool.connection_class = LocalConnection
    runtime.open()
    try:
        with runtime.pool.connection() as connection:
            old_pid = connection.info.backend_pid
            pooled = connection
        with connect_as(admin=True) as administrator:
            administrator.execute("SELECT pg_terminate_backend(%s)", (old_pid,))
        # Termination returns before the backend writes its notice and closes. The pool
        # trusts a recently used connection until that notice makes its socket readable.
        readable, _, _ = select.select([pooled.fileno()], [], [], 10)
        assert readable, "the terminated backend's notice reached the pooled connection"
        with runtime.pool.connection() as connection:
            assert connection.info.backend_pid != old_pid
            assert connection.execute("SELECT 1 AS alive").fetchone() == {"alive": 1}
    finally:
        runtime.close()


def test_no_identity_or_scope_discloses_no_rows(database):
    connect_as, _, _, _, _ = database
    with connect_as(runtime=True) as connection:
        for table in ("principals", "organizations", "spaces", "space_keys"):
            assert (
                connection.execute(
                    psycopg.sql.SQL("SELECT count(*) FROM space.{}").format(
                        psycopg.sql.Identifier(table)
                    )
                ).fetchone()[0]
                == 0
            )


def test_selecting_another_owners_valid_scope_does_not_authorize_access(database):
    connect_as, _, alice, bob, _ = database
    with connect_as(runtime=True) as connection:
        set_scope(connection, alice, organization=bob.organization_id, space=bob.space_id)
        assert connection.execute("SELECT count(*) FROM space.spaces").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM space.space_keys").fetchone()[0] == 0
        assert (
            connection.execute(
                "UPDATE space.spaces SET encrypted_metadata=%s", (secrets.token_bytes(64),)
            ).rowcount
            == 0
        )


def test_cross_owner_insert_is_rejected(database):
    connect_as, _, alice, bob, _ = database
    with (
        pytest.raises(psycopg.errors.InsufficientPrivilege),
        connect_as(runtime=True) as connection,
    ):
        set_scope(connection, alice, organization=bob.organization_id, space=bob.space_id)
        connection.execute(
            "INSERT INTO space.space_keys(organization_id,space_id,id,wrapping_provider,"
            "wrapping_key_reference,wrapped_key) VALUES(%s,%s,%s,'test','test-key',%s)",
            (bob.organization_id, bob.space_id, str(uuid4()), secrets.token_bytes(128)),
        )


def test_identity_issuer_is_part_of_ownership(database):
    connect_as, _, alice, _, _ = database
    with connect_as(runtime=True) as connection:
        set_scope(connection, alice)
        connection.execute(
            "SELECT set_config('space.identity_issuer','https://other.invalid',true)"
        )
        assert connection.execute("SELECT count(*) FROM space.organizations").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM space.spaces").fetchone()[0] == 0


def test_transaction_context_does_not_leak_to_next_transaction(database):
    connect_as, _, alice, _, _ = database
    with connect_as(runtime=True) as connection:
        with connection.transaction():
            set_scope(connection, alice)
            assert connection.execute("SELECT count(*) FROM space.spaces").fetchone()[0] == 1
        with connection.transaction():
            assert connection.execute("SELECT count(*) FROM space.spaces").fetchone()[0] == 0


@pytest.mark.parametrize(
    "statement",
    [
        "ALTER TABLE space.spaces DISABLE ROW LEVEL SECURITY",
        "DROP TABLE space.spaces",
        "CREATE TABLE space.unexpected(id int)",
        "SET ROLE postgres",
        "UPDATE space.principals SET subject='changed'",
        "UPDATE space.organizations SET owner_principal_id=gen_random_uuid()",
        "UPDATE space.space_keys SET wrapped_key='changed'::bytea",
    ],
)
def test_runtime_cannot_administer_schema_or_rewrite_identity_and_key_history(database, statement):
    connect_as, _, alice, _, _ = database
    with (
        pytest.raises(psycopg.errors.InsufficientPrivilege),
        connect_as(runtime=True) as connection,
    ):
        set_scope(connection, alice)
        connection.execute(statement)


def test_incomplete_personal_bootstrap_rolls_back(database):
    connect_as, _, _, _, _ = database
    identity = Identity(*(str(uuid4()) for _ in range(5)))
    with pytest.raises(psycopg.errors.ForeignKeyViolation), connect_as(runtime=True) as connection:
        set_scope(connection, identity)
        connection.execute(
            "INSERT INTO space.principals(id,issuer,subject) VALUES(%s,%s,%s)",
            (identity.principal_id, "https://identity.invalid", identity.subject),
        )
        connection.execute(
            "INSERT INTO space.organizations(id,owner_principal_id,default_space_id) "
            "VALUES(%s,%s,%s)",
            (identity.organization_id, identity.principal_id, identity.space_id),
        )
    with connect_as(runtime=True) as connection:
        set_scope(connection, identity)
        assert connection.execute("SELECT count(*) FROM space.principals").fetchone()[0] == 0


def test_space_cannot_reference_a_key_from_a_different_space(database):
    connect_as, _, alice, _, _ = database
    with pytest.raises(psycopg.errors.ForeignKeyViolation), connect_as(runtime=True) as connection:
        another_space = str(uuid4())
        set_scope(connection, alice, space=another_space)
        connection.execute(
            "INSERT INTO space.spaces(organization_id,id,metadata_key_id,encrypted_metadata) "
            "VALUES(%s,%s,%s,%s)",
            (alice.organization_id, another_space, alice.key_id, secrets.token_bytes(64)),
        )


def test_reapplying_migration_preserves_login_and_existing_rows(database):
    connect_as, engine, alice, _, password = database
    with engine.begin() as connection:
        apply_foundation(connection, password + "must-not-replace-existing-password")
    with connect_as(runtime=True) as connection:
        set_scope(connection, alice)
        assert connection.execute("SELECT count(*) FROM space.spaces").fetchone()[0] == 1


def test_explicit_connection_allowance_is_preserved_and_runtime_checks_actual_role(database):
    from psycopg.rows import dict_row

    from space.workspace import Database

    connect_as, engine, _, _, password = database
    try:
        with engine.begin() as connection:
            apply_foundation(connection, password, connection_limit=8)
        with engine.begin() as connection:
            apply_foundation(connection, password)
        with connect_as(runtime=True) as connection:
            connection.row_factory = dict_row
            runtime = object.__new__(Database)
            runtime.required_connections = 9
            with pytest.raises(RuntimeError, match="allows 8.*requires 9"):
                runtime._configure_budget(connection)
            runtime.required_connections = 8
            runtime._configure_budget(connection)
    finally:
        with engine.begin() as connection:
            apply_foundation(connection, password, connection_limit=5)


def test_setup_refuses_an_unrelated_role_without_adopting_it(database):
    _, engine, _, _, password = database
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.exec_driver_sql("COMMENT ON ROLE space_app IS 'Unrelated role'")
            with pytest.raises(DatabaseSetupError, match="unrelated"):
                apply_foundation(connection, password)
        finally:
            transaction.rollback()


def test_command_retention_removes_only_rows_past_the_window(database):
    """Retention deletes expired events and their commands, and keeps the rest.

    The rows are seeded directly because a command needs a resource revision, and the
    assertions name only this test's two command IDs so rows from other tests cannot
    change the result.
    """
    connect_as, _, alice, _, _ = database
    resource_id, expired, recent = uuid4(), uuid4(), uuid4()
    with connect_as(admin=True) as connection:
        connection.execute(
            "INSERT INTO space.resources(organization_id,space_id,id,kind,current_revision) "
            "VALUES(%s,%s,%s,'document',1)",
            (alice.organization_id, alice.space_id, resource_id),
        )
        connection.execute(
            "INSERT INTO space.resource_revisions(organization_id,space_id,resource_id,revision,"
            "key_id,encrypted_metadata,object_key,author_principal_id) "
            "VALUES(%s,%s,%s,1,%s,%s,'object',%s)",
            (
                alice.organization_id,
                alice.space_id,
                resource_id,
                alice.key_id,
                secrets.token_bytes(32),
                alice.principal_id,
            ),
        )
        for command_id, age in ((expired, "200 days"), (recent, "1 day")):
            connection.execute(
                "INSERT INTO space.commands(organization_id,space_id,id,principal_id,fingerprint,"
                "resource_id,revision,created_at) VALUES(%s,%s,%s,%s,%s,%s,1,now() - %s::interval)",
                (
                    alice.organization_id,
                    alice.space_id,
                    command_id,
                    alice.principal_id,
                    secrets.token_bytes(32),
                    resource_id,
                    age,
                ),
            )
            connection.execute(
                "INSERT INTO space.events(organization_id,space_id,id,command_id,event_type,"
                "created_at) VALUES(%s,%s,%s,%s,'resource.created',now() - %s::interval)",
                (alice.organization_id, alice.space_id, uuid4(), command_id, age),
            )
    with connect_as(admin=True) as connection:
        assert purge(connection, days=180, batch_size=100) == {
            "events": 1,
            "commands": 1,
            "activity_events": 0,
            "retired_claim_vectors": 0,
        }
    with connect_as(admin=True) as connection:
        commands = connection.execute(
            "SELECT id FROM space.commands WHERE id = ANY(%s::uuid[])", ([expired, recent],)
        ).fetchall()
        events = connection.execute(
            "SELECT command_id FROM space.events WHERE command_id = ANY(%s::uuid[])",
            ([expired, recent],),
        ).fetchall()
    assert {str(row[0]) for row in commands} == {str(recent)}
    assert {str(row[0]) for row in events} == {str(recent)}
