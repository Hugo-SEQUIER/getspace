"""The 10,000 pending OAuth and device requests count only those still open, and the owner's
direct connection is not held by the device cap. Opt-in PostgreSQL tests."""

import hashlib
import secrets
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb
from test_database_setup import database as database_fixture

database = database_fixture
CAP = 10000
CHALLENGE = "A" * 43


def device_begin(connection, *, capped):
    digest = hashlib.sha256(secrets.token_bytes(32)).digest()
    code = f"{secrets.randbelow(10**6):06d}"
    connection.execute(
        "SELECT space.device_begin(%s,%s,%s,%s,interval '10 minutes',%s)",
        (digest, code, "Agent", "other", capped),
    )


def fill_device(connection, start, *, expires):
    # Six-digit codes from `start`: one pending row per value.
    connection.execute(
        "INSERT INTO space.device_authorizations"
        "(device_code_hash, user_code, client_name, harness, expires_at) "
        "SELECT sha256(convert_to('fill' || n, 'UTF8')), lpad(n::text, 6, '0'), 'Flood', "
        "'other', now() + %s::interval FROM generate_series(%s, %s) AS n",
        (expires, start, start + CAP - 1),
    )


def test_device_cap_counts_open_codes_and_spares_the_owner(database):
    connect_as, _, *_ = database
    with connect_as(admin=True) as admin:
        # Expired half an hour ago: the sweep keeps them another half hour.
        fill_device(admin, 100000, expires="-30 minutes")
    with connect_as(runtime=True) as app:
        device_begin(app, capped=True)
    with connect_as(admin=True) as admin:
        fill_device(admin, 200000, expires="10 minutes")
    with connect_as(runtime=True) as app:
        with pytest.raises(psycopg.errors.RaiseException, match="space.device_busy"):
            device_begin(app, capped=True)
    with connect_as(runtime=True) as app:
        device_begin(app, capped=False)


def test_oauth_cap_counts_open_requests_only(database):
    connect_as, _, *_ = database
    client = uuid4()
    with connect_as(runtime=True) as app:
        app.execute(
            "SELECT space.oauth_register(%s, NULL, %s)",
            (client, Jsonb({"redirect_uris": ["https://client.example/cb"]})),
        )

    def fill(expires):
        with connect_as(admin=True) as admin:
            admin.execute(
                "INSERT INTO space.oauth_requests(client_id, redirect_uri, "
                "redirect_uri_explicit, code_challenge, expires_at) "
                "SELECT %s, 'https://client.example/cb', true, %s, now() + %s::interval "
                "FROM generate_series(1, %s)",
                (client, CHALLENGE, expires, CAP),
            )

    def begin(app):
        return app.execute(
            "SELECT space.oauth_begin(%s,'https://client.example/cb',true,%s,NULL,'',NULL,"
            "interval '10 minutes')",
            (client, CHALLENGE),
        ).fetchone()[0]

    fill("-30 minutes")
    with connect_as(runtime=True) as app:
        assert begin(app)
    fill("10 minutes")
    with connect_as(runtime=True) as app:
        with pytest.raises(psycopg.errors.RaiseException, match="space.oauth_busy"):
            begin(app)
