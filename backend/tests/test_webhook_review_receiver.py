"""Parameters: controlled synthetic state, shared signing helpers and one loopback HTTP listener.
What it does: proves wake-hint authentication, canonical rereads, claims and stale-result guards.
Output: consumer proof only, not a live Hermes review, production write or distributed lock test.
"""

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from secrets import token_hex
from threading import Event, Thread
from uuid import uuid4

import pytest
from webhook_review_harness import (
    SPEC_SECTIONS,
    GitEvidence,
    ReviewReceiver,
    ReviewResult,
    spec_digest,
    synthetic_fixture,
)

from space.webhook_http import sign

SECRET = token_hex(32)
NOW = 1_800_000_000


@pytest.fixture
def state():
    """Parameters: none.
    What it does: creates isolated synthetic canonical state and a persisted Review submission.
    Output: a shared proof store and its minimal signed-event candidate.
    """
    return synthetic_fixture()


def signed(event, *, secret=SECRET, timestamp=NOW):
    """Parameters: a minimal notification and controlled signing inputs.
    What it does: uses the shared production signing helper over the exact JSON bytes.
    Output: raw bytes and the three authenticated notification headers.
    """
    body = json.dumps(event, separators=(",", ":")).encode()
    return body, {
        "X-Space-Timestamp": str(timestamp),
        "X-Space-Signature": sign(secret, timestamp, body),
        "X-Space-Event-ID": event["id"],
    }


def accessible(key):
    """Parameters: the exact selected synthetic review key.
    What it does: returns controlled access and version evidence, not live Git evidence.
    Output: GitEvidence bound to repository, base and head.
    """
    return GitEvidence(True, key.repository, key.base, key.head)


def controlled_pass(key, required_checks):
    """Parameters: the selected synthetic key and persisted required checks.
    What it does: provides an explicit controlled passing review result.
    Output: ReviewResult with completed checks, never a stored Pass assertion.
    """
    return ReviewResult(key, "pass", required_checks, "Synthetic controlled pass")


def receiver(store, *, access=accessible, review=controlled_pass):
    """Parameters: shared proof storage and optional explicit evidence callbacks.
    What it does: constructs a test receiver with a fixed signature clock.
    Output: ReviewReceiver; it does not start any runtime process.
    """
    return ReviewReceiver(store, secret=SECRET, access=access, review=review, now=lambda: NOW)


def test_signed_notification_rereads_canonical_state_and_preserves_broad_task_status(state):
    store, event = state
    original_task, columns = store.task, store.columns
    assert set(event) == {
        "version",
        "id",
        "type",
        "occurred_at",
        "space_id",
        "database_id",
        "record_id",
        "previous_revision",
        "revision",
        "operation",
        "changed_fields",
        "changed_properties",
        "transition",
    }
    assert receiver(store).accept(*signed(event)).outcome == "passed"
    assert store.row.stage == "Security Check"
    assert store.task == original_task and store.task.status == "in_progress"
    assert store.columns == columns == ("todo", "in_progress", "done")
    assert len(store.dispatches) == 1


@pytest.mark.parametrize("change", ["tamper", "wrong_secret", "bad_signature", "expired", "future"])
def test_bad_signature_or_timestamp_never_dispatches_or_writes(state, change):
    store, event = state
    before = tuple(store.history)
    body, headers = signed(event)
    if change == "tamper":
        body = body.replace(b'"updated"', b'"deleted"')
    elif change == "wrong_secret":
        body, headers = signed(event, secret=token_hex(32))
    elif change == "bad_signature":
        headers["X-Space-Signature"] = "v1=" + "0" * 64
    else:
        body, headers = signed(event, timestamp=NOW + (301 if change == "future" else -301))
    assert receiver(store).accept(body, headers).status_code == 401
    assert not store.dispatches and tuple(store.history) == before


def test_event_id_header_must_equal_authenticated_body(state):
    store, event = state
    body, headers = signed(event)
    headers["X-Space-Event-ID"] = str(uuid4())
    assert receiver(store).accept(body, headers).status_code == 400
    assert not store.dispatches and store.row.stage == "Review"


@pytest.mark.parametrize(
    "field,value",
    [
        ("version", 2),
        ("version", True),
        ("type", "task.changed"),
        ("revision", True),
        ("previous_revision", 3),
        ("review_metadata", {"result": "Pass"}),
    ],
)
def test_signed_invalid_payload_cannot_supply_extra_review_evidence(state, field, value):
    store, event = state
    event = {**event, field: value}
    assert receiver(store).accept(*signed(event)).status_code == 400
    assert not store.dispatches and store.row.stage == "Review"


@pytest.mark.parametrize("field", ["space_id", "database_id", "record_id"])
def test_signed_wrong_scope_cannot_wake_review(state, field):
    store, event = state
    event = {**event, field: str(uuid4())}
    assert receiver(store).accept(*signed(event)).status_code == 403
    assert not store.dispatches


@pytest.mark.parametrize("section", SPEC_SECTIONS)
def test_each_canonical_section_changes_digest_but_workflow_does_not(state, section):
    store, _ = state
    original = spec_digest(store.task.body)
    changed = store.task.body.replace(f"Synthetic {section.lower()}.", "Changed specification.")
    assert spec_digest(changed) != original
    workflow_changed = store.task.body.replace("Synthetic workflow", "Changed workflow")
    assert spec_digest(workflow_changed) == original
    assert spec_digest(store.task.body.replace("## User story", "## **User story**")) == original


def test_missing_canonical_section_blocks_without_trusting_stored_pass(state):
    store, event = state
    store.update_task(body=store.task.body.replace("## Tests", "## Removed tests"))
    assert store.row.stored_result == "Pass"
    assert receiver(store).accept(*signed(event)).outcome == "blocked"
    assert not store.dispatches and store.row.stage == "Review" and store.row.blocker


@pytest.mark.parametrize("changed", ["head", "spec", "base", "repository", "task_link"])
def test_stale_persisted_request_blocks_before_review(state, changed):
    store, event = state
    if changed == "spec":
        store.update_task(body=store.task.body.replace("Synthetic tests.", "New required tests."))
    elif changed == "task_link":
        store.update_row(task_id=str(uuid4()))
    else:
        store.update_row(**{changed: "different-version-or-repository"})
    assert receiver(store).accept(*signed(event)).outcome == "blocked"
    assert not store.dispatches and store.row.stage == "Review" and store.row.blocker


def test_old_hint_cannot_claim_later_genuine_review_entry_with_unchanged_code(state):
    store, old_event = state
    original = store.row.request.key
    store.update_row(stage="In progress")
    latest_event = store.submit()
    assert store.row.request.key == original
    handler = receiver(store)
    assert handler.accept(*signed(old_event)).outcome == "ignored"
    assert not store.dispatches
    assert handler.accept(*signed(latest_event)).outcome == "passed"
    assert len(store.dispatches) == 1


def test_hint_cannot_invent_a_genuine_review_transition(state):
    store, _ = state
    store.update_row(blocker="Unrelated reviewer metadata")
    invented = store.event()
    invented["transition"] = {
        "field_id": store.stage_field_id,
        "before": "In progress",
        "after": "Review",
    }
    invented["changed_fields"] = [store.stage_field_id]
    assert receiver(store).accept(*signed(invented)).outcome == "ignored"
    assert not store.dispatches


def test_submission_metadata_replaced_without_stage_entry_cannot_claim_old_event(state):
    store, event = state
    store.update_row(request=replace(store.row.request, submission_id=str(uuid4())))
    assert receiver(store).accept(*signed(event)).outcome == "blocked"
    assert not store.dispatches


def test_reviewer_metadata_only_notifications_do_not_retrigger(state):
    store, event = state
    handler = receiver(store)
    assert handler.accept(*signed(event)).outcome == "passed"
    store.update_row(stored_result="Reviewer metadata updated")
    notification = store.event()
    assert notification["transition"] is None
    assert handler.accept(*signed(notification)).outcome == "ignored"
    assert len(store.dispatches) == 1


def test_duplicate_event_and_new_event_id_for_same_submission_dispatch_once(state):
    store, event = state
    handler = receiver(store, access=lambda key: GitEvidence(False))
    assert handler.accept(*signed(event)).outcome == "blocked"
    assert handler.accept(*signed(event)).outcome == "duplicate"
    assert handler.accept(*signed({**event, "id": str(uuid4())})).outcome == "duplicate"
    assert len(store.dispatches) == 1 and len(store.claimed_reviews) == 1


def test_stable_submission_id_cannot_be_dispatched_twice_after_reentry(state):
    store, event = state
    submission_id = store.row.request.submission_id
    handler = receiver(store, access=lambda key: GitEvidence(False))
    assert handler.accept(*signed(event)).outcome == "blocked"
    store.update_row(stage="In progress")
    latest_event = store.submit(submission_id=submission_id)
    assert handler.accept(*signed(latest_event)).outcome == "duplicate"
    assert len(store.dispatches) == 1


def test_two_receiver_instances_atomically_claim_before_concurrent_dispatch(state):
    store, event = state
    entered, release = Event(), Event()
    review_calls = []

    def waiting_access(key):
        """Parameters: an exact synthetic key.
        What it does: holds evidence collection while a concurrent duplicate is delivered.
        Output: controlled matching evidence after the test releases the gate.
        """
        entered.set()
        assert release.wait(5), "Concurrent proof did not release its gate"
        return accessible(key)

    def counted_review(key, checks):
        """Parameters: an exact key and required checks.
        What it does: records controlled dispatch count without reviewing real code.
        Output: a controlled passing result.
        """
        review_calls.append(key)
        return controlled_pass(key, checks)

    first = receiver(store, access=waiting_access, review=counted_review)
    second = receiver(store, review=counted_review)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(first.accept, *signed(event))
        try:
            assert entered.wait(5), "Evidence collection did not start"
            duplicate = second.accept(*signed({**event, "id": str(uuid4())}))
            assert duplicate.outcome == "duplicate"
        finally:
            release.set()
        assert pending.result(timeout=5).outcome == "passed"
    assert len(review_calls) == len(store.dispatches) == 1


@pytest.mark.parametrize(
    "changed",
    [
        "head",
        "base",
        "repository",
        "spec",
        "task_revision",
        "task_link",
        "submission",
        "reenter",
    ],
)
def test_versions_changed_during_review_prevent_old_result_from_progressing(state, changed):
    store, event = state

    def changed_during_review(key, checks):
        """Parameters: the previously selected exact key and checks.
        What it does: changes authoritative state while a controlled result is in flight.
        Output: the old passing result, which must not move the newer submission.
        """
        if changed == "spec":
            body = store.task.body.replace("Synthetic objective.", "New objective.")
            store.update_task(body=body)
        elif changed == "task_revision":
            store.update_task(body=store.task.body)
        elif changed == "task_link":
            store.update_row(task_id=str(uuid4()))
        elif changed == "submission":
            store.update_row(request=replace(store.row.request, submission_id=str(uuid4())))
        elif changed == "reenter":
            store.update_row(stage="In progress")
            store.submit()
        else:
            store.update_row(**{changed: "new-version-or-repository"})
        return controlled_pass(key, checks)

    assert receiver(store, review=changed_during_review).accept(*signed(event)).outcome == "stale"
    assert store.row.stage == "Review" and store.task.status == "in_progress"
    assert store.row.blocker is None


@pytest.mark.parametrize("evidence", ["no_access", "wrong_head", "wrong_base", "wrong_repository"])
def test_unavailable_or_inexact_git_evidence_leaves_review_with_blocker(state, evidence):
    store, event = state
    calls = []

    def controlled_access(key):
        """Parameters: the selected exact key.
        What it does: supplies intentionally unavailable or mismatched controlled Git evidence.
        Output: evidence that must never authorize a review result.
        """
        exact = accessible(key)
        if evidence == "no_access":
            return replace(exact, available=False)
        return replace(exact, **{evidence.removeprefix("wrong_"): "different"})

    def forbidden_review(key, checks):
        """Parameters: an unused key and checks.
        What it does: records any unsafe review dispatch after failed access evidence.
        Output: an unavailable controlled result.
        """
        calls.append(key)
        return ReviewResult(key, "unavailable")

    handler = receiver(store, access=controlled_access, review=forbidden_review)
    assert handler.accept(*signed(event)).outcome == "blocked"
    assert not calls and store.row.stage == "Review" and store.row.blocker
    assert store.task.status == "in_progress"


@pytest.mark.parametrize(
    "outcome,checks,detail,stage",
    [
        ("pass", ("unit", "security"), "Controlled pass", "Security Check"),
        ("defect", (), "Controlled confirmed defect", "In progress"),
        ("pass", ("unit",), "Missing security check", "Review"),
        ("defect", (), "", "Review"),
        ("unavailable", (), "Review access unavailable", "Review"),
    ],
)
def test_only_controlled_results_and_complete_checks_allow_safe_stage_change(
    state,
    outcome,
    checks,
    detail,
    stage,
):
    store, event = state
    assert store.row.stored_result == "Pass"
    handler = receiver(
        store,
        review=lambda key, required: ReviewResult(key, outcome, checks, detail),
    )
    assert handler.accept(*signed(event)).status_code == 202
    assert store.row.stage == stage
    assert bool(store.row.blocker) == (stage == "Review")
    assert store.task.status == "in_progress"


def test_result_for_different_exact_key_cannot_progress_review(state):
    store, event = state
    handler = receiver(
        store,
        review=lambda key, checks: ReviewResult(replace(key, head="other"), "pass", checks),
    )
    assert handler.accept(*signed(event)).outcome == "blocked"
    assert store.row.stage == "Review" and store.row.blocker


def test_signed_notification_through_actual_loopback_http_listener(state):
    store, event = state
    handler = receiver(store)
    receipts = []

    class Listener(BaseHTTPRequestHandler):
        def do_POST(self):
            """Parameters: one test-only local HTTP POST.
            What it does: passes exact received bytes and headers to the controlled receiver.
            Output: the receiver receipt; production outbound transport is neither patched nor used.
            """
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            receipt = handler.accept(raw, self.headers)
            receipts.append(receipt)
            self.send_response(receipt.status_code)
            self.end_headers()
            self.wfile.write(receipt.outcome.encode())

        def log_message(self, *_args):
            """Parameters: ignored stdlib request-log arguments.
            What it does: suppresses noise for this local controlled fixture.
            Output: none.
            """

    server = ThreadingHTTPServer(("127.0.0.1", 0), Listener)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    try:
        body, headers = signed(event)
        connection.request("POST", "/synthetic-review", body=body, headers=headers)
        response = connection.getresponse()
        assert response.status == 202 and response.read() == b"passed"
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert not thread.is_alive()
    assert len(receipts) == len(store.dispatches) == 1
    assert store.row.stage == "Security Check" and store.task.status == "in_progress"
