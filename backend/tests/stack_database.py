"""Disposable PostgreSQL for the real-stack browser tests.

Parameters: `start --state <file>` or `stop --state <file>`.
What it does: `start` mints a local certificate authority and a server certificate for
127.0.0.1, runs pgvector/pgvector:pg17 with TLS on a free loopback port, prepares the
`space` schema exactly as the backend test fixture does (non-superuser migration login,
marked schema, vector extension, `apply_foundation`), and writes the runtime connection
details to the state file. `stop` removes the container and the state file.
Output: on `start`, one JSON line with the runtime database URL, the CA file and the
container name. Credentials are random per run and never reach the repository.
"""

import argparse
import datetime as dt
import json
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from ipaddress import ip_address
from pathlib import Path
from uuid import uuid4

import psycopg
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from psycopg import sql
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from space.database_setup import SCHEMA_MARKER, apply_foundation, runtime_url

IMAGE = "pgvector/pgvector:pg17"


def _certificate(directory: Path):
    """A throwaway CA and a server certificate valid for 127.0.0.1, written as PEM."""
    now = dt.datetime.now(dt.UTC)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "space-stack-test-ca")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    server_key = ec.generate_private_key(ec.SECP256R1())
    server = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")]))
        .issuer_name(ca_name)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ip_address("127.0.0.1"))]),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    (directory / "ca.pem").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    (directory / "server.crt").write_bytes(server.public_bytes(serialization.Encoding.PEM))
    (directory / "server.key").write_bytes(
        server_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )


def start(state: Path):
    docker = shutil.which("docker")
    if docker is None:
        raise SystemExit("Docker is required for the real-stack tests")
    name = "space-stack-test-" + uuid4().hex[:12]
    certs = Path(tempfile.mkdtemp(prefix="space-stack-certs-"))
    _certificate(certs)
    admin_password = secrets.token_urlsafe(32)
    migration_password = secrets.token_urlsafe(32)
    app_password = secrets.token_urlsafe(32)
    # The key must belong to the postgres user with owner-only permissions, which a bind
    # mount from macOS cannot guarantee, so the container copies the files first.
    command = (
        "cp /certs-src/server.crt /certs-src/server.key /var/lib/postgresql/ && "
        "chown postgres:postgres /var/lib/postgresql/server.* && "
        "chmod 600 /var/lib/postgresql/server.key && "
        "exec docker-entrypoint.sh postgres -c ssl=on "
        "-c ssl_cert_file=/var/lib/postgresql/server.crt "
        "-c ssl_key_file=/var/lib/postgresql/server.key"
    )
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
            "--volume",
            f"{certs}:/certs-src:ro",
            "--env",
            "POSTGRES_PASSWORD=" + admin_password,
            "--entrypoint",
            "sh",
            IMAGE,
            "-c",
            command,
        ],
        check=True,
        capture_output=True,
    )
    try:
        address = subprocess.check_output(  # noqa: S603
            [docker, "port", name, "5432/tcp"], text=True
        ).strip()
        port = int(address.rsplit(":", 1)[1])

        def connect_as(*, runtime=False, admin=False):
            return psycopg.connect(
                host="127.0.0.1",
                port=port,
                dbname="postgres",
                sslmode="verify-full",
                sslrootcert=str(certs / "ca.pem"),
                user="space_app" if runtime else ("postgres" if admin else "space_migrator"),
                password=app_password
                if runtime
                else (admin_password if admin else migration_password),
                connect_timeout=2,
            )

        deadline = time.monotonic() + 60
        while True:
            try:
                with connect_as(admin=True):
                    break
            except psycopg.OperationalError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Disposable PostgreSQL did not become ready") from None
                time.sleep(0.25)
        with connect_as(admin=True) as connection:
            connection.execute(
                sql.SQL(
                    "CREATE ROLE space_migrator LOGIN NOSUPERUSER NOBYPASSRLS CREATEROLE "
                    "PASSWORD {}"
                ).format(sql.Literal(migration_password))
            )
            connection.execute(
                "GRANT CONNECT, CREATE ON DATABASE postgres TO space_migrator WITH GRANT OPTION"
            )
            connection.execute("CREATE SCHEMA space AUTHORIZATION space_migrator")
            connection.execute(
                sql.SQL("COMMENT ON SCHEMA space IS {}").format(sql.Literal(SCHEMA_MARKER))
            )
            connection.execute("CREATE EXTENSION vector SCHEMA space")
        engine = create_engine(
            "postgresql+psycopg://", creator=connect_as, poolclass=NullPool, hide_parameters=True
        )
        try:
            with engine.begin() as connection:
                # The API's default pools need more than the five-connection default.
                apply_foundation(connection, app_password, connection_limit=40)
        finally:
            engine.dispose()
        with connect_as(runtime=True) as connection:
            assert connection.execute("SELECT current_user").fetchone()[0] == "space_app"
        migration = f"postgresql://space_migrator:{migration_password}@127.0.0.1:{port}/postgres"
        record = {
            "container": name,
            "certs": str(certs),
            "ca_file": str(certs / "ca.pem"),
            "database_url": runtime_url(migration, app_password),
        }
        state.write_text(json.dumps(record))
        # The runtime credential stays in the state file; the log only names the container.
        print(json.dumps({"container": name, "ca_file": record["ca_file"], "state": str(state)}))
    except BaseException:
        subprocess.run([docker, "rm", "--force", name], check=False, capture_output=True)  # noqa: S603
        shutil.rmtree(certs, ignore_errors=True)
        raise


def stop(state: Path):
    if not state.exists():
        return
    record = json.loads(state.read_text())
    docker = shutil.which("docker")
    if docker:
        subprocess.run(  # noqa: S603
            [docker, "rm", "--force", record["container"]], check=False, capture_output=True
        )
    shutil.rmtree(record["certs"], ignore_errors=True)
    state.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("start", "stop"))
    parser.add_argument("--state", type=Path, required=True)
    arguments = parser.parse_args(argv)
    if arguments.command == "start":
        start(arguments.state)
    else:
        stop(arguments.state)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:  # Provider details stay out of the log.
        print("stack database failed:", type(error).__name__, str(error)[:200], file=sys.stderr)
        sys.exit(1)
