"""Parameters: disposable Space storage, a test-local TLS receiver and controlled review evidence.
What it does: sends a committed Review delivery over HTTPS and consumes duplicate/stale hints.
Output: signed transport and one eligible review dispatch, never live Hermes or merge evidence.
"""

import json
import ssl
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from uuid import UUID, uuid4

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from psycopg.rows import dict_row
from test_database_setup import database as database_fixture
from test_webhooks import next_save, saved_database, subscribe
from test_workspace import command_database as command_database_fixture
from test_workspace import commands as commands_fixture
from webhook_review_harness import (
    ControlledStore,
    DevRow,
    GitEvidence,
    ReviewReceiver,
    ReviewResult,
    Task,
    spec_digest,
)

from space import tasks, webhook_delivery, webhook_http, webhooks, workspace_items
from space.workspace import SaveResource

commands = commands_fixture
command_database = command_database_fixture
database = database_fixture


@pytest.fixture
def tls_receiver(tmp_path, monkeypatch):
    """Parameters: a private temporary directory and test-only monkeypatches.
    What it does: trusts a synthetic certificate and maps the approved test IP to local TLS.
    Output: a HTTPS test server; production URL policy and connector code have no bypass.
    """
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "receiver.example.com")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("receiver.example.com")]), False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), True)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "receiver.pem", tmp_path / "receiver-key.pem"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    notifications, outcomes = [], []
    handler = {"accept": None}

    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            """Parameters: a test-local signed HTTP request after real TLS verification.
            What it does: authenticates the unchanged bytes and records only controlled outcomes.
            Output: a content-free receipt status, without logging payloads or secrets.
            """
            body = self.rfile.read(int(self.headers["Content-Length"]))
            headers = dict(self.headers)
            notifications.append((body, headers))
            receipt = handler["accept"](body, headers)
            outcomes.append(receipt)
            self.send_response(receipt.status_code)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            """Parameters: HTTP logger arguments. What it does: discards them. Output: none."""

    server = HTTPServer(("127.0.0.1", 0), Receiver)
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(cert_path, key_path)
    server.socket = server_context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    original_socket = webhook_http.socket.socket
    original_context = ssl.create_default_context

    class TestSocket(original_socket):
        def connect(self, destination):
            """Parameters: the pinned production connector destination.
            What it does: permits only this synthetic public IP and routes it to the test listener.
            Output: a loopback test connection; every unexpected destination fails closed.
            """
            assert destination == ("8.8.8.8", 443)
            return super().connect(server.server_address)

    def trust_test_certificate():
        """Parameters: none.
        What it does: trusts only the synthetic receiver CA.
        Output: a verified TLS context.
        """
        return original_context(cafile=cert_path)

    monkeypatch.setattr(webhook_http.socket, "socket", TestSocket)
    monkeypatch.setattr(webhook_http.ssl, "create_default_context", trust_test_certificate)
    monkeypatch.setattr(webhook_http, "resolve_endpoint", lambda _: ("8.8.8.8",))
    try:
        yield handler, notifications, outcomes
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_committed_review_delivery_verifies_https_signature_and_rereads_exact_space_state(
    commands,
    monkeypatch,
    tls_receiver,
):
    service, owner, space, connect = commands
    initial_columns = tasks.columns(service, owner, space)
    board = saved_database(service, owner, space)
    canonical_body = "\n\n".join(
        f"## {section}\nControlled {section.lower()}."
        for section in ("User story", "Context", "Objective", "Suggested approach", "Tests")
    )
    canonical = service.save(
        owner,
        space,
        SaveResource(
            command_id=uuid4(),
            expected_revision=0,
            kind="task",
            title="Controlled review proof",
            body=canonical_body,
            status="in_progress",
        ),
    )
    task = Task(canonical["id"], canonical["body"], canonical["revision"], canonical["status"])
    metadata = {
        "task_id": task.id,
        "repository_url": "controlled/repository",
        "base_sha": "a" * 40,
        "head_sha": "b" * 40,
        "spec_digest": spec_digest(task.body),
    }
    enriched = next_save(board)
    enriched.data["fields"].extend(
        {"id": name, "name": name, "type": "text", "options": []} for name in metadata
    )
    enriched.data["records"][0]["values"].update(metadata)
    enriched.data["records"][0]["values"]["stage"] = "In progress"
    workspace_items.save(service, owner, space, enriched)
    created, _ = subscribe(service, owner, space, board.item_id, monkeypatch)
    subscription_id = UUID(created["subscription"]["id"])
    store = ControlledStore(
        task,
        DevRow(
            "selected",
            task.id,
            "In progress",
            metadata["repository_url"],
            metadata["base_sha"],
            metadata["head_sha"],
        ),
        space_id=space,
        database_id=board.item_id,
        stage_field_id="stage",
        revision=2,
    )
    store.submit(required_checks=("controlled-unit",))
    review = next_save(enriched, stage="Review")
    workspace_items.save(service, owner, space, review)

    def exact_space_evidence(key):
        """Parameters: the controlled receiver's requested exact review key.
        What it does: rereads the actual canonical task and Dev row from disposable Space.
        Output: explicit synthetic Git evidence only when all current inputs still match.
        """
        current_task = service.get_resource(owner, space, UUID(key.task_id))
        current_database = workspace_items.get(service, owner, space, board.item_id)
        row = next(record for record in current_database["records"] if record["id"] == "selected")
        values = row["values"]
        valid = (
            current_task["revision"] == key.task_revision
            and spec_digest(current_task["body"]) == key.spec_digest
            and values["task_id"] == key.task_id
            and values["stage"] == "Review"
            and values["spec_digest"] == key.spec_digest
            and (values["repository_url"], values["base_sha"], values["head_sha"])
            == (key.repository, key.base, key.head)
        )
        return GitEvidence(valid, key.repository, key.base, key.head)

    def controlled_review(key, checks):
        """Parameters: a verified exact key and required controlled checks.
        What it does: returns explicit test-only evidence, not a claim of live code review.
        Output: a synthetic pass bound to the unchanged key.
        """
        return ReviewResult(key, "pass", checks, "Controlled harness evidence only")

    receiver = ReviewReceiver(
        store,
        secret=created["signing_secret"],
        access=exact_space_evidence,
        review=controlled_review,
    )
    handler, notifications, outcomes = tls_receiver
    handler["accept"] = receiver.accept
    with connect(admin=True) as queue:
        queue.row_factory = dict_row
        queue.execute("SET ROLE space_webhook_worker")
        queue.commit()
        job = webhook_delivery.claim(queue)
        assert job is not None
        result = webhook_delivery.process(service, queue, job)
        assert result.status_code == 202 and result.error_code is None
        assert webhook_delivery.finish(queue, job, result)
    assert len(store.dispatches) == 1 and store.row.stage == "Security Check"
    assert outcomes[0].outcome == "passed"
    raw_body, headers = notifications[0]
    event = json.loads(raw_body)
    assert event["revision"] == 3 and event["previous_revision"] == 2
    assert event["transition"] == {"field_id": "stage", "before": "In progress", "after": "Review"}
    assert headers["X-Space-Event-ID"] == event["id"]
    assert b"PRIVATE" not in raw_body and b"controlled/repository" not in raw_body
    assert created["signing_secret"].encode() not in raw_body
    recorded = webhooks.deliveries(service, owner, space, subscription_id)["items"][0]
    assert recorded["status"] == "delivered" and recorded["receipts"][0]["status_code"] == 202
    duplicate = webhook_http.send(created["subscription"]["endpoint_url"], raw_body, headers)
    assert duplicate.status_code == 202 and duplicate.error_code is None
    assert outcomes[-1].outcome in {"ignored", "stale", "duplicate"}
    assert len(store.dispatches) == 1

    # Parameters: test-only reviewer outcome mirrored into the disposable Dev row.
    # What it does: confirms reviewer metadata writes do not retrigger a submission.
    # Output: no second eligible delivery, no canonical/global task-status change.
    metadata_edit = next_save(review, blocker="Controlled review receipt")
    workspace_items.save(service, owner, space, metadata_edit)
    assert len(webhooks.deliveries(service, owner, space, subscription_id)["items"]) == 1
    progressed = next_save(metadata_edit, stage="Security Check")
    workspace_items.save(service, owner, space, progressed)
    stale = webhook_http.send(created["subscription"]["endpoint_url"], raw_body, headers)
    assert stale.status_code == 202 and stale.error_code is None
    assert outcomes[-1].outcome in {"ignored", "stale", "duplicate"}
    assert len(store.dispatches) == 1
    assert service.get_resource(owner, space, UUID(task.id))["status"] == "in_progress"
    assert tasks.columns(service, owner, space) == initial_columns
