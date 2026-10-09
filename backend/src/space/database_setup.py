"""Install the database foundation with separate migration credentials.

The schema and the restricted `space_app` runtime login are created with the migration
(superuser) connection; the API never holds that credential. Errors and credentials
are never printed. Existing unrelated roles or schemas are refused. `space.install`
runs this together with the worker logins for a complete, idempotent installation.
"""

from pathlib import Path
from typing import Literal
from urllib.parse import quote, unquote, urlsplit, urlunsplit

import psycopg
from alembic import command
from alembic.config import Config
from psycopg import sql
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import Connection

from space.brain import DIMENSIONS_SQL
from space.settings import BACKEND_DIR, CONFIG_FILE, database_connection_options

SCHEMA_MARKER = "Space versioned private database foundation"
ROLE_MARKER = "Space restricted application database login"
# Room for the API pool, the job and webhook workers and a second API process.
DEFAULT_CONNECTION_LIMIT = 20


class DatabaseSetupError(Exception):
    """A deliberately non-sensitive setup failure."""


class InstallSettings(BaseSettings):
    """Connections for installation and maintenance commands, never for the API."""

    model_config = SettingsConfigDict(
        env_prefix="SPACE_",
        env_file=CONFIG_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    migration_database_url: SecretStr | None = None
    database_url: SecretStr | None = None
    job_database_url: SecretStr | None = None
    webhook_database_url: SecretStr | None = None
    database_connection_limit: int = Field(default=DEFAULT_CONNECTION_LIMIT, ge=1, le=10000)
    # The embedding model's output width. Set it before the first memory is stored:
    # 1536 for text-embedding-3-small, 1024 or 768 for common local models.
    memory_embedding_dimensions: int = Field(default=1536, ge=16, le=2000)
    database_sslmode: Literal[
        "disable", "allow", "prefer", "require", "verify-ca", "verify-full"
    ] = "prefer"
    database_ca_file: Path | None = None

    @field_validator("*", mode="before")
    @classmethod
    def empty_as_missing(cls, value):
        return None if value == "" else value

    @field_validator(
        "migration_database_url", "database_url", "job_database_url", "webhook_database_url"
    )
    @classmethod
    def postgres_url(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            parsed = urlsplit(value.get_secret_value())
            if parsed.scheme not in ("postgres", "postgresql") or not parsed.hostname:
                raise ValueError("Expected a PostgreSQL connection URL")
        return value


def connect(settings, *, runtime: bool = False) -> psycopg.Connection:
    secret = settings.database_url if runtime else settings.migration_database_url
    if secret is None:
        raise DatabaseSetupError("The required database connection is missing")
    return psycopg.connect(
        secret.get_secret_value(),
        **database_connection_options(settings),
        connect_timeout=8,
        options="-c statement_timeout=15000 -c lock_timeout=5000",
    )


def runtime_url(migration_url: str, password: str) -> str:
    parsed = urlsplit(migration_url)
    host = parsed.hostname
    if not host or not parsed.username or parsed.path in ("", "/"):
        raise DatabaseSetupError("Migration connection must identify a host, user and database")
    address = f"[{host}]" if ":" in host else host
    if parsed.port:
        address += f":{parsed.port}"
    authority = f"space_app:{quote(password, safe='')}@{address}"
    # Do not carry migration URL query options that could override the runtime identity.
    return urlunsplit(("postgresql", authority, parsed.path, "", ""))


def login_password(secret: SecretStr, login: str) -> str:
    """The password inside one login's URL, which must name that login."""
    parsed = urlsplit(secret.get_secret_value())
    if unquote(parsed.username or "") != login or not parsed.password:
        raise DatabaseSetupError(f"The {login} connection URL must name {login} with a password")
    return unquote(parsed.password)


def set_password(connection: psycopg.Connection, role: str, password: str) -> None:
    """Make the role's password match the configuration; libpq hashes it locally."""
    verifier = connection.pgconn.encrypt_password(
        password.encode(), role.encode(), b"scram-sha-256"
    ).decode()
    connection.execute(
        sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier(role), sql.Literal(verifier))
    )


def apply_foundation(
    connection: Connection, password: str, *, connection_limit: int | None = None
) -> None:
    """Apply inside the caller's transaction; a failure rolls back role and schema changes."""
    if connection_limit is not None and (
        type(connection_limit) is not int or not 1 <= connection_limit <= 10000
    ):
        raise DatabaseSetupError("Connection allowance must be between 1 and 10000")
    connection.exec_driver_sql("SELECT pg_advisory_xact_lock(831672, 1)")
    schema = connection.exec_driver_sql(
        "SELECT obj_description(oid, 'pg_namespace') FROM pg_namespace WHERE nspname = 'space'"
    ).fetchone()
    role = connection.exec_driver_sql(
        "SELECT shobj_description(oid, 'pg_authid'), rolsuper, rolbypassrls, rolcreaterole, "
        "rolcreatedb, rolreplication, rolinherit FROM pg_roles WHERE rolname = 'space_app'"
    ).fetchone()
    if schema is not None and schema[0] != SCHEMA_MARKER:
        raise DatabaseSetupError("An unrelated space schema already exists; no changes made")
    if role is not None and (role[0] != ROLE_MARKER or any(role[1:])):
        raise DatabaseSetupError(
            "An unrelated or privileged space_app role exists; no changes made"
        )
    if role is not None:
        memberships = connection.exec_driver_sql(
            "SELECT 1 FROM pg_auth_members WHERE member = "
            "(SELECT oid FROM pg_roles WHERE rolname = 'space_app') LIMIT 1"
        ).fetchone()
        if memberships:
            raise DatabaseSetupError("space_app has unexpected role memberships; no changes made")
    if role is None:
        driver = connection.connection.driver_connection
        # libpq hashes locally, so the CREATE ROLE statement does not carry a plaintext password.
        verifier = driver.pgconn.encrypt_password(
            password.encode(), b"space_app", b"scram-sha-256"
        ).decode()
        statement = sql.SQL(
            "CREATE ROLE space_app LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE "
            "NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 5 PASSWORD {}"
        ).format(sql.Literal(verifier))
        connection.exec_driver_sql(statement.as_string(driver))
        connection.exec_driver_sql(
            sql.SQL("COMMENT ON ROLE space_app IS {}").format(sql.Literal(ROLE_MARKER)).as_string()
        )
    if schema is None:
        connection.exec_driver_sql("CREATE SCHEMA space")
        connection.exec_driver_sql(
            sql.SQL("COMMENT ON SCHEMA space IS {}").format(sql.Literal(SCHEMA_MARKER)).as_string()
        )
    if connection_limit is not None:
        connection.exec_driver_sql(
            sql.SQL("ALTER ROLE space_app CONNECTION LIMIT {}")
            .format(sql.Literal(connection_limit))
            .as_string()
        )
    connection.exec_driver_sql("REVOKE ALL ON SCHEMA space FROM PUBLIC")
    connection.exec_driver_sql("GRANT USAGE ON SCHEMA space TO space_app")
    connection.exec_driver_sql(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA space REVOKE ALL ON TABLES FROM PUBLIC"
    )
    connection.exec_driver_sql(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA space REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC"
    )
    configuration = Config()
    configuration.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    configuration.attributes["connection"] = connection
    command.upgrade(configuration, "head")
    database = connection.exec_driver_sql("SELECT current_database()").scalar_one()
    connection.exec_driver_sql(
        sql.SQL("GRANT CONNECT ON DATABASE {} TO space_app")
        .format(sql.Identifier(database))
        .as_string()
    )
    connection.exec_driver_sql("ALTER ROLE space_app SET search_path = pg_catalog, space")
    connection.exec_driver_sql("ALTER ROLE space_app SET statement_timeout = '15s'")
    connection.exec_driver_sql("ALTER ROLE space_app SET lock_timeout = '5s'")
    connection.exec_driver_sql(
        "ALTER ROLE space_app SET idle_in_transaction_session_timeout = '30s'"
    )


def ensure_embedding_width(connection: Connection, dimensions: int) -> None:
    """Resize the claim index to the configured embedding width while it is empty.

    An index that already holds embeddings keeps its width: vectors of another size
    cannot be compared, so changing models later means re-embedding every claim.
    """
    if type(dimensions) is not int or not 16 <= dimensions <= 2000:
        raise DatabaseSetupError("Embedding dimensions must be between 16 and 2000")
    current = connection.exec_driver_sql(DIMENSIONS_SQL).scalar_one()
    if current == dimensions:
        return
    # Count every row; a login still subject to row-level security fails here instead.
    connection.exec_driver_sql("SET LOCAL row_security = off")
    if connection.exec_driver_sql("SELECT EXISTS (SELECT 1 FROM space.memory_claims)").scalar_one():
        raise DatabaseSetupError(
            f"Stored memories use {current}-dimension embeddings. Set "
            f"SPACE_MEMORY_EMBEDDING_DIMENSIONS={current}; another width needs an empty "
            "memory index."
        )
    connection.exec_driver_sql("DROP INDEX space.memory_claims_embedding_idx")
    connection.exec_driver_sql(
        f"ALTER TABLE space.memory_claims ALTER COLUMN embedding TYPE space.vector({dimensions})"
    )
    connection.exec_driver_sql(
        "CREATE INDEX memory_claims_embedding_idx ON space.memory_claims "
        "USING hnsw (embedding space.vector_cosine_ops)"
    )


def database_failure_detail(error: psycopg.Error) -> str:
    """Classify locally; never return any fragment of a driver error or DSN."""
    message = str(error).lower()
    if "password authentication failed" in message:
        return (
            "Database password rejected. Check the password in the connection URL and its "
            "URL encoding."
        )
    if "certificate verify failed" in message or "certificate verification failed" in message:
        return (
            "TLS certificate verification failed. Configure SPACE_DATABASE_CA_FILE with the "
            "database's CA, or choose another SPACE_DATABASE_SSLMODE on a private network."
        )
    if any(
        text in message
        for text in ("failed to resolve", "could not translate host name", "name resolution")
    ):
        return "Database hostname did not resolve. Check the host in the connection URL."
    if any(text in message for text in ("timeout", "network is unreachable", "connection refused")):
        return "Database network connection failed. Check host, port and that it is running."
    return "Database connection failed; check configuration. Driver error details withheld."
