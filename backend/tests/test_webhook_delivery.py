"""Parameters: synthetic encrypted webhooks and opt-in disposable PostgreSQL fixtures.
What it does: tests bounded policies, audited recovery, ownership checks and worker isolation.
Output: unit checks without Docker and database checks without any live receiver or credentials.
"""

import os
import secrets
import signal
import socket
import ssl
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from threading import Event
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.rows import dict_row
from test_database_setup import database as database_fixture
from test_workspace import command_database as command_database_fixture
from test_workspace import commands as commands_fixture

from space import webhook_delivery as delivery
from space import webhook_http, webhook_worker
from space import workspace_items as items
from space.security.auth import Identity
from space.webhook_http import AttemptResult
from space.workspace import encode

database = database_fixture
command_database = command_database_fixture
commands = commands_fixture


def test_interrupted_connections_are_retryable_and_certificate_failures_are_final():
    assert "network_error" in delivery.RETRYABLE_ERRORS
    assert "tls_error" in delivery.SAFE_ERRORS
    assert "tls_error" not in delivery.RETRYABLE_ERRORS


@pytest.mark.parametrize("attempt,base", [(1, 30), (2, 120), (3, 480), (4, 1920), (5, 1920)])
def test_backoff_has_bounded_positive_jitter(monkeypatch, attempt, base):
    monkeypatch.setattr(delivery.secrets, "randbelow", lambda bound: bound - 1)
    delay = delivery.retry_delay(attempt, AttemptResult(error_code="timeout"))
    assert base <= delay <= base * 1.2
    assert delay <= 3600


@pytest.mark.parametrize("status", [429, 503])
@pytest.mark.parametrize("retry_after,expected", [(1800, 1800), (99999, 3600), (0, 30)])
def test_retry_after_is_bounded_and_honored_only_for_eligible_statuses(
    monkeypatch, status, retry_after, expected
):
    monkeypatch.setattr(delivery.secrets, "randbelow", lambda _: 0)
    assert delivery.retry_delay(1, AttemptResult(status, "http_5xx", retry_after)) == expected
    assert delivery.retry_delay(1, AttemptResult(500, "http_5xx", retry_after)) == 30


@pytest.mark.parametrize(
    "result,attempt,status,error",
    [
        (AttemptResult(202), 1, "delivered", None),
        (AttemptResult(400, "http_4xx"), 1, "failed", "http_4xx"),
        (AttemptResult(302, "redirect"), 1, "failed", "redirect"),
        (AttemptResult(error_code="unsafe_address"), 1, "failed", "unsafe_address"),
        (AttemptResult(error_code="tls_error"), 1, "failed", "tls_error"),
        (AttemptResult(error_code="timeout"), 1, "queued", "timeout"),
        (AttemptResult(error_code="timeout"), 5, "failed", "timeout"),
        (AttemptResult(error_code="network_error"), 1, "queued", "network_error"),
        (AttemptResult(error_code="network_error"), 5, "failed", "network_error"),
        (AttemptResult(429, "rate_limited", 3600), 1, "queued", "rate_limited"),
        (AttemptResult(503, "http_5xx", 120), 5, "failed", "http_5xx"),
        (AttemptResult(error_code="access_revoked"), 1, "failed", "access_revoked"),
        (AttemptResult(error_code="subscription_paused"), 1, "queued", "subscription_paused"),
        (AttemptResult(error_code="subscription_paused"), 5, "failed", "subscription_paused"),
        (AttemptResult(error_code="PRIVATE-RAW-ERROR"), 1, "queued", "temporarily_unavailable"),
    ],
)
def test_finish_policy_and_receipt_never_persist_raw_errors(result, attempt, status, error):
    queue = Mock()
    queue.transaction.return_value = nullcontext()
    queue.execute.return_value.rowcount = 1
    job = {
        "organization_id": uuid4(),
        "space_id": uuid4(),
        "id": uuid4(),
        "lease_id": uuid4(),
        "cycle": 0,
        "attempts": attempt,
    }
    assert delivery.finish(queue, job, result)
    updates = queue.execute.call_args_list
    assert len(updates) == 2
    state, receipt = (update.args[1] for update in updates)
    assert state[:3] == (status, result.status_code, error)
    assert receipt[:2] == (result.status_code, error)
    assert 0 <= state[3] <= 3600
    assert "PRIVATE" not in str(updates)


def test_obsolete_finish_does_not_touch_attempt_receipt():
    queue = Mock()
    queue.transaction.return_value = nullcontext()
    queue.execute.return_value.rowcount = 0
    job = {
        "organization_id": uuid4(),
        "space_id": uuid4(),
        "id": uuid4(),
        "lease_id": uuid4(),
        "cycle": 0,
        "attempts": 1,
    }
    assert not delivery.finish(queue, job, AttemptResult(204))
    queue.execute.assert_called_once()


@pytest.fixture
def queue(commands):
    with commands[3]() as maintenance:
        maintenance.execute("DELETE FROM space.webhook_attempts")
        maintenance.execute("DELETE FROM space.webhook_deliveries")
        maintenance.execute("DELETE FROM space.webhook_events")
        maintenance.execute("DELETE FROM space.webhook_commands")
        maintenance.execute("DELETE FROM space.webhook_subscriptions")
    with commands[3](admin=True) as worker:
        worker.row_factory = dict_row
        worker.execute("SET ROLE space_webhook_worker")
        worker.commit()
        yield worker


def enqueue_fixture(commands):
    service, actor, space, _ = commands
    database_id, subscription_id, event_id, delivery_id = (uuid4() for _ in range(4))
    items.save(
        service,
        actor,
        space,
        items.SaveItem(
            command_id=uuid4(),
            item_id=database_id,
            expected_revision=0,
            kind="database",
            data={"name": "Synthetic review", "fields": [], "records": []},
        ),
    )
    scope, key = service._key(actor, space, write=True)
    configuration = {
        "endpoint_url": "https://receiver.example.com/hooks",
        "transition": "entered",
        "signing_secret": "fixture-secret-no-live-value",
    }
    payload = {
        "event_id": str(event_id),
        "database_id": str(database_id),
        "record_id": "record-1",
        "previous_revision": 0,
        "revision": 1,
        "operation": "created",
        "record": {"name": "PRIVATE-SYNTHETIC-PROJECTION", "values": {"status": "In Review"}},
    }
    encrypted_configuration = service._pack(
        key, scope, subscription_id, 1, "webhook_subscription", configuration
    )
    encrypted_filter = service._pack(
        key,
        scope,
        subscription_id,
        1,
        "webhook_filter",
        {"field_id": "status", "value": "In Review"},
    )
    encrypted_event = service._pack(key, scope, event_id, 1, "webhook_event", payload)
    encrypted_delivery = service._pack(key, scope, delivery_id, 1, "webhook_delivery", payload)
    with service.db.transaction(actor) as runtime:
        scope = service._scope(runtime, actor, space, write=True, human=True)
        runtime.execute(
            "INSERT INTO space.webhook_subscriptions (organization_id,space_id,id,database_id,"
            "revision,key_id,encrypted_configuration,encrypted_filter,created_by,issuer,subject) "
            "VALUES (%s,%s,%s,%s,1,%s,%s,%s,%s,%s,%s)",
            (
                scope["organization_id"],
                space,
                subscription_id,
                database_id,
                UUID(key.key.key_id),
                encrypted_configuration,
                encrypted_filter,
                scope["principal_id"],
                actor.issuer,
                actor.subject,
            ),
        )
        runtime.execute(
            "INSERT INTO space.webhook_events (organization_id,space_id,id,database_id,record_id,"
            "previous_revision,revision,operation,key_id,encrypted_payload) "
            "VALUES (%s,%s,%s,%s,'record-1',0,1,'created',%s,%s)",
            (
                scope["organization_id"],
                space,
                event_id,
                database_id,
                UUID(key.key.key_id),
                encrypted_event,
            ),
        )
        runtime.execute(
            "INSERT INTO space.webhook_deliveries (organization_id,space_id,id,subscription_id,"
            "event_id,key_id,encrypted_payload) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (
                scope["organization_id"],
                space,
                delivery_id,
                subscription_id,
                event_id,
                UUID(key.key.key_id),
                encrypted_delivery,
            ),
        )
    return SimpleNamespace(
        id=delivery_id,
        event_id=event_id,
        subscription_id=subscription_id,
        database_id=database_id,
        payload=payload,
        configuration=configuration,
        key=key,
        scope=scope,
    )


def state(queue, delivery_id):
    with queue.transaction():
        row = queue.execute(
            "SELECT * FROM space.webhook_deliveries WHERE id=%s", (delivery_id,)
        ).fetchone()
        receipts = queue.execute(
            "SELECT * FROM space.webhook_attempts WHERE delivery_id=%s ORDER BY cycle,attempt",
            (delivery_id,),
        ).fetchall()
    return row, receipts


def make_available(commands, delivery_id):
    with commands[3]() as maintenance:
        maintenance.execute(
            "UPDATE space.webhook_deliveries SET available_at=clock_timestamp() WHERE id=%s",
            (delivery_id,),
        )


def expire_lease(commands, delivery_id):
    with commands[3]() as maintenance:
        maintenance.execute(
            "UPDATE space.webhook_deliveries SET lease_until=clock_timestamp()-interval '1 second' "
            "WHERE id=%s",
            (delivery_id,),
        )


def test_signed_retry_keeps_body_event_delivery_and_audited_receipts(commands, queue, monkeypatch):
    fixture = enqueue_fixture(commands)
    calls = []
    outcomes = iter([AttemptResult(503, "http_5xx", 1800), AttemptResult(202)])

    def send(url, body, headers):
        calls.append((url, body, headers))
        return next(outcomes)

    monkeypatch.setattr(webhook_http, "send", send)
    first = delivery.claim(queue)
    assert first["id"] == fixture.id and first["attempts"] == 1
    assert first["event_id"] == fixture.event_id and first["cycle"] == 0
    assert b"PRIVATE" in bytes(first["encrypted_payload"])  # the queued body is readable
    assert delivery.finish(queue, first, delivery.process(commands[0], queue, first))
    queued, receipts = state(queue, fixture.id)
    assert queued["status"] == "queued" and queued["attempts"] == 1
    assert 1799 <= (queued["available_at"] - queued["updated_at"]).total_seconds() <= 1801
    assert receipts[0]["status_code"] == 503 and receipts[0]["error_code"] == "http_5xx"
    make_available(commands, fixture.id)
    second = delivery.claim(queue)
    assert second["id"] == first["id"] and second["event_id"] == first["event_id"]
    assert second["encrypted_payload"] == first["encrypted_payload"]
    assert second["lease_id"] != first["lease_id"] and second["attempts"] == 2
    assert delivery.finish(queue, second, delivery.process(commands[0], queue, second))
    completed, receipts = state(queue, fixture.id)
    assert completed["status"] == "delivered" and delivery.claim(queue) is None
    assert [receipt["attempt"] for receipt in receipts] == [1, 2]
    assert all(receipt["finished_at"] >= receipt["started_at"] for receipt in receipts)
    assert receipts[1]["status_code"] == 202 and receipts[1]["error_code"] is None
    assert calls[0][1] == calls[1][1] == encode(fixture.payload)
    for _, body, headers in calls:
        assert headers["X-Space-Event-ID"] == str(fixture.event_id)
        assert headers["X-Space-Delivery-ID"] == str(fixture.id)
        assert webhook_http.verify_signature(
            fixture.configuration["signing_secret"],
            int(headers["X-Space-Timestamp"]),
            body,
            headers["X-Space-Signature"],
        )
    assert "PRIVATE" not in str(receipts)


def test_handshake_closed_by_peer_is_retried_then_delivered(commands, queue, monkeypatch):
    fixture = enqueue_fixture(commands)

    class ClosedDuringHandshake:
        def __init__(self, host, address):
            self.closed = False

        def request(self, method, path, *, body, headers):
            raise ssl.SSLEOFError("PRIVATE handshake closed by peer")

        def close(self):
            self.closed = True

    class Accepted(ClosedDuringHandshake):
        def request(self, method, path, *, body, headers):
            self.sent = body

        def getresponse(self):
            return SimpleNamespace(status=200, getheader=lambda name: None, close=lambda: None)

    transports = iter([ClosedDuringHandshake, Accepted])
    monkeypatch.setattr(
        webhook_http.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))],
    )
    monkeypatch.setattr(
        webhook_http,
        "_PinnedHTTPSConnection",
        lambda host, address: next(transports)(host, address),
    )
    first = delivery.claim(queue)
    assert delivery.finish(queue, first, delivery.process(commands[0], queue, first))
    queued, receipts = state(queue, fixture.id)
    assert queued["status"] == "queued" and queued["attempts"] == 1
    assert 30 <= (queued["available_at"] - queued["updated_at"]).total_seconds() <= 37
    assert receipts[0]["status_code"] is None and receipts[0]["error_code"] == "network_error"
    make_available(commands, fixture.id)
    second = delivery.claim(queue)
    assert second["id"] == first["id"] and second["attempts"] == 2
    assert delivery.finish(queue, second, delivery.process(commands[0], queue, second))
    completed, receipts = state(queue, fixture.id)
    assert completed["status"] == "delivered" and delivery.claim(queue) is None
    assert [receipt["error_code"] for receipt in receipts] == ["network_error", None]
    assert receipts[1]["status_code"] == 200
    # Receipts never keep request or response content.
    assert "PRIVATE" not in str(receipts)
    assert "PRIVATE" not in str({k: v for k, v in completed.items() if k != "encrypted_payload"})


def test_timeout_attempts_exhaust_at_five_and_never_persist_response_content(
    commands, queue, monkeypatch
):
    fixture = enqueue_fixture(commands)
    send = Mock(return_value=AttemptResult(error_code="timeout"))
    monkeypatch.setattr(webhook_http, "send", send)
    for attempt in range(1, delivery.MAX_ATTEMPTS + 1):
        make_available(commands, fixture.id)
        claimed = delivery.claim(queue)
        assert claimed["attempts"] == attempt and claimed["id"] == fixture.id
        assert delivery.finish(queue, claimed, delivery.process(commands[0], queue, claimed))
        row, receipts = state(queue, fixture.id)
        assert row["status"] == ("failed" if attempt == 5 else "queued")
        assert row["error_code"] == "timeout" and len(receipts) == attempt
    assert send.call_count == 5 and delivery.claim(queue) is None
    assert all(receipt["error_code"] == "timeout" for receipt in receipts)
    assert "PRIVATE" not in str(receipts)


def test_worker_lost_receipt_and_obsolete_lease_cas_preserve_recovered_attempt(commands, queue):
    fixture = enqueue_fixture(commands)
    first = delivery.claim(queue)
    expire_lease(commands, fixture.id)
    assert not delivery.finish(queue, first, AttemptResult(200))
    second = delivery.claim(queue)
    assert second["attempts"] == 2 and second["lease_id"] != first["lease_id"]
    assert second["id"] == first["id"] and second["event_id"] == first["event_id"]
    assert not delivery.finish(queue, first, AttemptResult(204))
    row, receipts = state(queue, fixture.id)
    assert row["status"] == "processing" and row["lease_id"] == second["lease_id"]
    assert receipts[0]["error_code"] == "worker_lost" and receipts[0]["finished_at"] is not None
    assert receipts[1]["finished_at"] is None
    assert delivery.finish(queue, second, AttemptResult(200))


def test_fifth_crash_exhausts_without_sending_and_audits_each_lost_attempt(commands, queue):
    fixture = enqueue_fixture(commands)
    for attempt in range(1, delivery.MAX_ATTEMPTS + 1):
        claimed = delivery.claim(queue)
        assert claimed["attempts"] == attempt and claimed["id"] == fixture.id
        expire_lease(commands, fixture.id)
    assert delivery.claim(queue) is None
    row, receipts = state(queue, fixture.id)
    assert row["status"] == "failed" and row["error_code"] == "attempts_exhausted"
    assert row["attempts"] == 5 and row["lease_id"] is None
    assert len(receipts) == 5
    assert all(receipt["error_code"] == "worker_lost" for receipt in receipts)


def test_claimers_never_duplicate_an_active_lease(commands, queue):
    fixture = enqueue_fixture(commands)
    with queue.transaction():
        first = delivery.claim(queue)
        with commands[3](admin=True) as other:
            other.row_factory = dict_row
            other.execute("SET ROLE space_webhook_worker")
            other.commit()
            assert delivery.claim(other) is None
    assert first["id"] == fixture.id
    assert delivery.claim(queue) is None
    assert len(state(queue, fixture.id)[1]) == 1


def test_wall_clock_expiry_blocks_finish_inside_an_old_transaction(commands, queue):
    fixture = enqueue_fixture(commands)
    claimed = delivery.claim(queue)
    with queue.transaction():
        queue.execute(
            "UPDATE space.webhook_deliveries SET lease_until=clock_timestamp()+interval "
            "'100 milliseconds' WHERE id=%s",
            (fixture.id,),
        )
        queue.execute("SELECT pg_sleep(0.2)")
        assert not delivery.finish(queue, claimed, AttemptResult(200))
    assert state(queue, fixture.id)[1][0]["finished_at"] is None


@pytest.mark.parametrize("aging", ["event", "delivery"])
def test_old_pending_work_expires_even_when_subscription_paused(commands, queue, aging):
    fixture = enqueue_fixture(commands)
    with commands[3]() as maintenance:
        maintenance.execute(
            "UPDATE space.webhook_subscriptions SET paused=true WHERE id=%s",
            (fixture.subscription_id,),
        )
        statement = (
            "UPDATE space.webhook_events SET "
            "created_at=clock_timestamp()-interval '31 days' WHERE id=%s"
            if aging == "event"
            else "UPDATE space.webhook_deliveries SET "
            "created_at=clock_timestamp()-interval '31 days' WHERE id=%s"
        )
        maintenance.execute(statement, (fixture.event_id if aging == "event" else fixture.id,))
    assert delivery.claim(queue) is None
    row, receipts = state(queue, fixture.id)
    assert row["status"] == "failed" and row["error_code"] == "expired" and row["attempts"] == 0
    assert receipts == []


def test_pause_excludes_claim_and_cancels_leased_unsent_attempt_then_remove_fails(
    commands, queue, monkeypatch
):
    fixture = enqueue_fixture(commands)
    send = Mock(return_value=AttemptResult(200))
    monkeypatch.setattr(webhook_http, "send", send)
    first = delivery.claim(queue)
    with commands[3]() as maintenance:
        maintenance.execute(
            "UPDATE space.webhook_subscriptions SET paused=true WHERE id=%s",
            (fixture.subscription_id,),
        )
    assert delivery.finish(queue, first, delivery.process(commands[0], queue, first))
    row, receipts = state(queue, fixture.id)
    assert row["status"] == "queued" and receipts[0]["error_code"] == "subscription_paused"
    make_available(commands, fixture.id)
    assert delivery.claim(queue) is None
    with commands[3]() as maintenance:
        maintenance.execute(
            "UPDATE space.webhook_subscriptions SET paused=false WHERE id=%s",
            (fixture.subscription_id,),
        )
    second = delivery.claim(queue)
    with commands[3]() as maintenance:
        maintenance.execute(
            "UPDATE space.webhook_subscriptions SET removed_at=clock_timestamp() WHERE id=%s",
            (fixture.subscription_id,),
        )
    assert delivery.finish(queue, second, delivery.process(commands[0], queue, second))
    assert state(queue, fixture.id)[0]["error_code"] == "subscription_removed"
    assert state(queue, fixture.id)[0]["status"] == "failed"
    send.assert_not_called()


def test_removed_subscription_fails_unclaimed_work_visibly(commands, queue):
    fixture = enqueue_fixture(commands)
    with commands[3]() as maintenance:
        maintenance.execute(
            "UPDATE space.webhook_subscriptions SET removed_at=clock_timestamp() WHERE id=%s",
            (fixture.subscription_id,),
        )
    assert delivery.claim(queue) is None
    row, receipts = state(queue, fixture.id)
    assert row["status"] == "failed" and row["error_code"] == "subscription_removed"
    assert receipts == []


@pytest.mark.parametrize("revocation", ["creator", "owner", "during_unwrap"])
def test_original_human_must_still_own_space_and_match_subscription_creator(
    commands, queue, monkeypatch, revocation
):
    service, owner, _, connect_as = commands
    fixture = enqueue_fixture(commands)
    other = Identity(owner.issuer, str(uuid4()), int(time.time()) + 600)
    other_space = UUID(service.bootstrap(other)["id"])
    with service.db.transaction(other) as runtime:
        other_scope = service._scope(runtime, other, other_space)
    send = Mock(return_value=AttemptResult(200))
    monkeypatch.setattr(webhook_http, "send", send)

    def revoke():
        with connect_as() as maintenance:
            if revocation == "creator":
                maintenance.execute(
                    "UPDATE space.webhook_subscriptions SET created_by=%s WHERE id=%s",
                    (other_scope["principal_id"], fixture.subscription_id),
                )
            else:
                maintenance.execute(
                    "UPDATE space.spaces SET owner_principal_id=%s WHERE id=%s",
                    (other_scope["principal_id"], fixture.scope["space_id"]),
                )

    if revocation == "during_unwrap":
        original_unwrap = service.keys.unwrap

        def unwrap(*args):
            key = original_unwrap(*args)
            revoke()
            return key

        monkeypatch.setattr(service.keys, "unwrap", unwrap)
    else:
        revoke()
    claimed = delivery.claim(queue)
    result = delivery.process(service, queue, claimed)
    assert result.error_code == "access_revoked"
    assert delivery.finish(queue, claimed, result)
    row, receipts = state(queue, fixture.id)
    assert row["status"] == "failed" and receipts[0]["error_code"] == "access_revoked"
    send.assert_not_called()


def test_current_secret_rotation_signs_the_unchanged_delivery_projection(
    commands, queue, monkeypatch
):
    service, _, _, connect_as = commands
    fixture = enqueue_fixture(commands)
    configuration = {**fixture.configuration, "signing_secret": "rotated-synthetic-secret"}
    encrypted = service._pack(
        fixture.key,
        fixture.scope,
        fixture.subscription_id,
        2,
        "webhook_subscription",
        configuration,
    )
    with connect_as() as maintenance:
        maintenance.execute(
            "UPDATE space.webhook_subscriptions SET revision=2,encrypted_configuration=%s "
            "WHERE id=%s",
            (encrypted, fixture.subscription_id),
        )
    send = Mock(return_value=AttemptResult(204))
    monkeypatch.setattr(webhook_http, "send", send)
    claimed = delivery.claim(queue)
    assert delivery.process(service, queue, claimed) == AttemptResult(204)
    _, body, headers = send.call_args.args
    timestamp = int(headers["X-Space-Timestamp"])
    assert body == encode(fixture.payload)
    assert webhook_http.verify_signature(
        configuration["signing_secret"], timestamp, body, headers["X-Space-Signature"]
    )
    assert not webhook_http.verify_signature(
        fixture.configuration["signing_secret"], timestamp, body, headers["X-Space-Signature"]
    )


def test_pause_update_waits_for_network_and_blocks_further_unsent_io(commands, queue, monkeypatch):
    fixture = enqueue_fixture(commands)
    claimed = delivery.claim(queue)
    entered, release, updating = Event(), Event(), Event()
    calls = []

    def send(*args):
        calls.append(args)
        entered.set()
        assert release.wait(3)
        return AttemptResult(200)

    def pause():
        with commands[3]() as maintenance:
            updating.set()
            maintenance.execute(
                "UPDATE space.webhook_subscriptions SET paused=true WHERE id=%s",
                (fixture.subscription_id,),
            )

    monkeypatch.setattr(webhook_http, "send", send)
    with ThreadPoolExecutor(max_workers=2) as pool:
        sending = pool.submit(delivery.process, commands[0], queue, claimed)
        assert entered.wait(3)
        pausing = pool.submit(pause)
        assert updating.wait(3)
        try:
            with pytest.raises(TimeoutError):
                pausing.result(timeout=0.1)
        finally:
            release.set()
        assert sending.result(timeout=3) == AttemptResult(200)
        pausing.result(timeout=3)
    assert delivery.process(commands[0], queue, claimed).error_code == "subscription_paused"
    assert len(calls) == 1


def test_worker_role_cannot_read_key_material_or_change_approved_endpoint(commands, queue):
    enqueue_fixture(commands)
    for statement in (
        "SELECT * FROM space.space_keys",
        "SELECT * FROM space.resource_revisions",
        "UPDATE space.webhook_subscriptions SET paused=false",
        "UPDATE space.webhook_subscriptions SET encrypted_configuration='forged'::bytea",
        "UPDATE space.webhook_deliveries SET encrypted_payload='forged'::bytea",
    ):
        with pytest.raises(psycopg.errors.InsufficientPrivilege), queue.transaction():
            queue.execute(statement)
    with commands[3]() as privileged:
        privileged.row_factory = dict_row
        with pytest.raises(RuntimeError, match="non-privileged"):
            webhook_worker.restrict_connection(privileged)
    login = "test_webhook_login_" + uuid4().hex[:12]
    password = secrets.token_urlsafe(32)
    with commands[3](admin=True) as administrator:
        administrator.execute(
            psycopg.sql.SQL("CREATE ROLE {} LOGIN NOINHERIT PASSWORD {}").format(
                psycopg.sql.Identifier(login), psycopg.sql.Literal(password)
            )
        )
        administrator.execute(
            psycopg.sql.SQL("GRANT space_webhook_worker TO {}").format(
                psycopg.sql.Identifier(login)
            )
        )
        args = {
            "host": administrator.info.host,
            "port": administrator.info.port,
            "dbname": administrator.info.dbname,
            "user": login,
            "password": password,
            "sslmode": "disable",
            "row_factory": dict_row,
        }
    with psycopg.connect(**args) as worker:
        webhook_worker.restrict_connection(worker)
        role = worker.execute("SELECT current_user AS role").fetchone()["role"]
        assert role == "space_webhook_worker"


@pytest.fixture
def runtime(monkeypatch):
    settings = SimpleNamespace(
        webhook_database_url=SimpleNamespace(get_secret_value=lambda: "synthetic-fixture"),
        database_url=True,
        database_sslmode="disable",
        database_ca_file=None,
        mcp_resource_url="https://fixture.invalid/mcp/",
        web_url="https://fixture.invalid",
    )
    database, keys, workspace, queue = (Mock() for _ in range(4))
    queue.transaction.return_value = nullcontext()
    queue.execute.return_value.fetchone.return_value = {"ready": True}
    workspace.aclose = AsyncMock()
    monkeypatch.setattr(webhook_worker, "Settings", lambda **_: settings)
    monkeypatch.setattr(webhook_worker, "Database", lambda *_, **__: database)
    monkeypatch.setattr(webhook_worker, "LocalKeys", lambda *_: keys)
    workspace_constructor = Mock(return_value=workspace)
    monkeypatch.setattr(webhook_worker, "Workspace", workspace_constructor)
    monkeypatch.setattr(webhook_worker.psycopg, "connect", lambda *_, **__: nullcontext(queue))
    monkeypatch.setattr(webhook_worker, "restrict_connection", Mock())
    claim, process, finish = Mock(), Mock(return_value=AttemptResult(200)), Mock(return_value=True)
    monkeypatch.setattr(webhook_worker, "claim", claim)
    monkeypatch.setattr(webhook_worker, "process", process)
    monkeypatch.setattr(webhook_worker, "finish", finish)
    return SimpleNamespace(
        database=database,
        keys=keys,
        workspace=workspace,
        queue=queue,
        claim=claim,
        process=process,
        finish=finish,
        workspace_constructor=workspace_constructor,
    )


@pytest.mark.parametrize("job", [None, {"id": "synthetic"}])
def test_child_claims_once_closes_connections_and_constructs_no_models(runtime, job):
    runtime.claim.return_value = job
    assert webhook_worker.run_attempt() == (0 if job else webhook_worker.EXIT_IDLE)
    runtime.claim.assert_called_once()
    assert runtime.process.call_count == runtime.finish.call_count == bool(job)
    runtime.workspace.aclose.assert_awaited_once()
    runtime.database.close.assert_called_once()
    runtime.keys.close.assert_called_once()
    assert runtime.workspace_constructor.call_args.args[2] is None
    assert len(runtime.workspace_constructor.call_args.args) == 5


def test_environment_fence_prevents_claims_and_still_closes_runtime(runtime):
    runtime.queue.execute.return_value.fetchone.return_value = {"ready": False}
    assert webhook_worker.run_attempt() == webhook_worker.EXIT_IDLE
    runtime.claim.assert_not_called()
    runtime.workspace.aclose.assert_awaited_once()
    runtime.database.close.assert_called_once()
    runtime.keys.close.assert_called_once()


@pytest.fixture
def supervisor(monkeypatch):
    handlers = {}
    monkeypatch.setattr(
        webhook_worker.signal,
        "signal",
        lambda signum, handler: handlers.update({signum: handler}),
    )
    run = Mock()
    monkeypatch.setattr(webhook_worker, "supervise", run)
    return SimpleNamespace(run=run, handlers=handlers)


@pytest.mark.parametrize("outcomes,expected", [([0, 0], 2), ([0, 10], 2), ([10], 1)])
def test_drain_is_bounded_and_exits_without_idle_polling(supervisor, outcomes, expected):
    supervisor.run.side_effect = outcomes
    assert webhook_worker.main(["--drain", "--max-attempts", "2"]) == 0
    assert supervisor.run.call_count == expected
    for call in supervisor.run.call_args_list:
        command, deadline = call.args
        assert deadline == 30 and command[1:3] == ["-m", "space.webhook_worker"]


@pytest.mark.parametrize("outcome", [0, webhook_worker.EXIT_IDLE, webhook_worker.EXIT_TIMEOUT])
def test_once_starts_at_most_one_supervised_child(supervisor, outcome):
    supervisor.run.return_value = outcome
    assert webhook_worker.main(["--once"]) == int(outcome == webhook_worker.EXIT_TIMEOUT)
    supervisor.run.assert_called_once()


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["--drain", "--max-attempts", "0"],
        ["--drain", "--max-attempts", "101"],
        ["--once", "--drain"],
    ],
)
def test_worker_requires_explicit_execution_and_valid_bound(supervisor, args):
    with pytest.raises(SystemExit):
        webhook_worker.main(args)
    supervisor.run.assert_not_called()


def test_loop_drains_only_when_the_queue_has_due_deliveries(supervisor, monkeypatch):
    answers, waits = [False, True], []

    class Watch:
        closed = False

        def __init__(self, *_):
            pass

        def pending(self):
            return answers.pop(0) if answers else False

        def close(self):
            Watch.closed = True

    def wait(seconds, stopping):
        waits.append(seconds)
        if len(waits) == 2:
            supervisor.handlers[signal.SIGTERM]()

    monkeypatch.setattr(webhook_worker, "QueueWatch", Watch)
    monkeypatch.setattr(webhook_worker, "wait", wait)
    monkeypatch.setattr(
        webhook_worker,
        "worker_settings",
        lambda *_: SimpleNamespace(
            webhook_database_url=SimpleNamespace(get_secret_value=lambda: "fixture"),
            database_sslmode="disable",
            database_ca_file=None,
        ),
    )
    supervisor.run.side_effect = [0, webhook_worker.EXIT_IDLE]
    assert webhook_worker.main(["--loop", "--poll-interval", "5"]) == 0
    assert supervisor.run.call_count == 2
    assert waits == [5, 5]
    assert Watch.closed


def test_child_exception_is_safe_and_closes_resources(runtime, capsys):
    runtime.claim.return_value = {"id": "synthetic"}
    runtime.process.side_effect = RuntimeError("PRIVATE-CONNECTION-SECRET")
    assert webhook_worker.main(["--attempt"]) == 1
    assert "PRIVATE" not in str(capsys.readouterr())
    runtime.database.close.assert_called_once()
    runtime.keys.close.assert_called_once()
    runtime.workspace.aclose.assert_awaited_once()


def test_shutdown_after_current_child_never_starts_another(supervisor):
    def completed(*_):
        supervisor.handlers[signal.SIGTERM]()
        return 0

    supervisor.run.side_effect = completed
    assert webhook_worker.main(["--drain"]) == 0
    supervisor.run.assert_called_once()


def test_real_supervisor_discards_streams_and_kills_unresponsive_child(tmp_path, capfd):
    assert (
        webhook_worker.supervise(
            [sys.executable, "-c", "print('PRIVATE-CHILD-CONTENT'); raise SystemExit(10)"], 2
        )
        == webhook_worker.EXIT_IDLE
    )
    assert "PRIVATE" not in str(capfd.readouterr())
    pid_file = tmp_path / "webhook-child.pid"
    script = (
        "import os, signal, sys\nfrom pathlib import Path\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "Path(sys.argv[1]).write_text(str(os.getpid()))\nwhile True: pass\n"
    )
    started = time.monotonic()
    assert webhook_worker.supervise([sys.executable, "-c", script, str(pid_file)], 1) == (
        webhook_worker.EXIT_TIMEOUT
    )
    assert time.monotonic() - started < 5
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)
