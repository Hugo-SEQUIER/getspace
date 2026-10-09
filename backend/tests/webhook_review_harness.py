"""Parameters: synthetic canonical tasks, Dev rows, revision history and controlled callbacks.
What it does: proves signed wake-hint consumption without Hermes, production writes or a daemon.
Output: test-only state and receipts; one shared process lock is NOT a distributed runtime lock.
"""

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from threading import RLock
from uuid import UUID, uuid4

from space.webhook_http import verify_signature

SPEC_SECTIONS = ("User story", "Context", "Objective", "Suggested approach", "Tests")
EVENT_FIELDS = {
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


def spec_digest(body: str) -> str:
    """Parameters: canonical task Markdown with the five named level-two sections.
    What it does: hashes their ordered text, ignoring Workflow and outer blank lines.
    Output: a SHA-256 digest; missing or duplicate canonical sections raise ValueError.
    """
    sections, current = {}, None
    for line in body.replace("\r\n", "\n").splitlines():
        heading = re.fullmatch(r"##\s+(.+?)\s*", line)
        if heading:
            current = heading[1].strip("*")
            if current in SPEC_SECTIONS:
                if current in sections:
                    raise ValueError("Duplicate canonical section")
                sections[current] = []
        elif current in SPEC_SECTIONS:
            sections[current].append(line)
    if set(sections) != set(SPEC_SECTIONS):
        raise ValueError("Missing canonical section")
    content = [[name, "\n".join(sections[name]).strip()] for name in SPEC_SECTIONS]
    return hashlib.sha256(json.dumps(content, ensure_ascii=False).encode()).hexdigest()


@dataclass(frozen=True)
class Task:
    id: str
    body: str
    revision: int = 1
    status: str = "in_progress"


@dataclass(frozen=True)
class ReviewKey:
    task_id: str
    task_revision: int
    repository: str
    base: str
    head: str
    spec_digest: str


@dataclass(frozen=True)
class ReviewRequest:
    submission_id: str
    entry_revision: int
    key: ReviewKey
    required_checks: tuple[str, ...]


@dataclass(frozen=True)
class DevRow:
    id: str
    task_id: str
    stage: str
    repository: str
    base: str
    head: str
    request: ReviewRequest | None = None
    stored_result: str = "Pass"
    blocker: str | None = None


@dataclass(frozen=True)
class DatabaseVersion:
    revision: int
    row: DevRow


@dataclass(frozen=True)
class GitEvidence:
    available: bool
    repository: str | None = None
    base: str | None = None
    head: str | None = None


@dataclass(frozen=True)
class ReviewResult:
    key: ReviewKey
    outcome: str
    checks: tuple[str, ...] = ()
    detail: str = ""


@dataclass(frozen=True)
class Receipt:
    status_code: int
    outcome: str


def review_key(task: Task, row: DevRow) -> ReviewKey:
    """Parameters: a reread canonical task and current Dev row.
    What it does: binds the task identity and revision, repository, Git versions and spec.
    Output: an exact immutable key, never a stored Pass value.
    """
    if row.task_id != task.id:
        raise ValueError("Dev row points to a different task")
    return ReviewKey(
        task.id, task.revision, row.repository, row.base, row.head, spec_digest(task.body)
    )


class ControlledStore:
    def __init__(self, task, row, *, space_id, database_id, stage_field_id, revision=1):
        """Parameters: synthetic state, scope, Stage field ID and initial database revision.
        What it does: keeps canonical snapshots and claims under one shared RLock.
        Output: process-local proof storage, without durability or cross-process coordination.
        """
        self.lock = RLock()
        self.task, self.row = task, row
        self.space_id, self.database_id = str(space_id), str(database_id)
        self.stage_field_id = stage_field_id
        self.history = [DatabaseVersion(revision, row)]
        self.claimed_events, self.claimed_submissions, self.claimed_reviews = set(), set(), set()
        self.dispatches = []
        self.columns = ("todo", "in_progress", "done")

    def update_row(self, **changes):
        """Parameters: synthetic Dev row changes only.
        What it does: atomically saves a new authoritative database snapshot.
        Output: the new row; it never changes canonical task status or global columns.
        """
        with self.lock:
            self.row = replace(self.row, **changes)
            self.history.append(DatabaseVersion(self.history[-1].revision + 1, self.row))
            return self.row

    def update_task(self, **changes):
        """Parameters: synthetic canonical task changes.
        What it does: increments its revision under the same proof lock.
        Output: the new canonical task.
        """
        with self.lock:
            self.task = replace(self.task, revision=self.task.revision + 1, **changes)
            return self.task

    def submit(self, *, required_checks=("unit", "security"), submission_id=None):
        """Parameters: controlled required checks and optional stable submission ID.
        What it does: persists an exact review request together with genuine entry into Review.
        Output: a minimal event hint for that authoritative revision.
        """
        with self.lock:
            if self.row.stage == "Review":
                raise ValueError("Submission requires a genuine new entry into Review")
            request = ReviewRequest(
                submission_id or str(uuid4()),
                self.history[-1].revision + 1,
                review_key(self.task, self.row),
                tuple(required_checks),
            )
            self.update_row(stage="Review", request=request, blocker=None)
            return self.event()

    def event(self, *, revision=None):
        """Parameters: an optional authoritative database revision.
        What it does: derives only changed-field hints and the real Stage transition.
        Output: the proposed v1 wire payload without task, record or review metadata.
        """
        with self.lock:
            version = next(
                v
                for v in self.history
                if v.revision == (revision if revision is not None else self.history[-1].revision)
            )
            previous = next((v for v in self.history if v.revision == version.revision - 1), None)
            before = previous.row.stage if previous else None
            transition = None
            if before != version.row.stage:
                transition = {
                    "field_id": self.stage_field_id,
                    "before": before,
                    "after": version.row.stage,
                }
            return {
                "version": 1,
                "id": str(uuid4()),
                "type": "database.record.changed",
                "occurred_at": datetime.now(UTC).isoformat(),
                "space_id": self.space_id,
                "database_id": self.database_id,
                "record_id": version.row.id,
                "previous_revision": version.revision - 1,
                "revision": version.revision,
                "operation": "updated" if previous else "created",
                "changed_fields": [self.stage_field_id] if transition else ["review_metadata"],
                "changed_properties": [],
                "transition": transition,
            }

    def latest_entry(self):
        """Parameters: authoritative synthetic database history under the store lock.
        What it does: finds the last actual non-Review to Review transition, not a hint.
        Output: the matching database snapshot or None.
        """
        latest, before = None, None
        for version in self.history:
            if version.row.stage == "Review" and before != "Review":
                latest = version
            before = version.row.stage
        return latest


def _event(body):
    """Parameters: verified raw notification bytes.
    What it does: rejects unexpected metadata, malformed scopes, versions and transitions.
    Output: the minimal validated event dictionary or ValueError.
    """
    event = json.loads(body)
    if not isinstance(event, dict) or set(event) != EVENT_FIELDS:
        raise ValueError("Unexpected notification fields")
    if type(event["version"]) is not int or event["version"] != 1:
        raise ValueError("Unsupported version")
    for field in ("id", "space_id", "database_id"):
        UUID(event[field])
    if event["type"] != "database.record.changed" or not isinstance(event["record_id"], str):
        raise ValueError("Wrong notification type")
    occurred = datetime.fromisoformat(event["occurred_at"])
    if occurred.tzinfo is None:
        raise ValueError("Missing timezone")
    for field in ("previous_revision", "revision"):
        if type(event[field]) is not int or event[field] < 0:
            raise ValueError("Invalid revision")
    if event["revision"] != event["previous_revision"] + 1:
        raise ValueError("Invalid revision pair")
    if event["operation"] not in ("created", "updated", "deleted"):
        raise ValueError("Invalid operation")
    for field in ("changed_fields", "changed_properties"):
        if not isinstance(event[field], list) or any(type(v) is not str for v in event[field]):
            raise ValueError("Invalid changed-field hint")
    transition = event["transition"]
    if transition is not None:
        if not isinstance(transition, dict) or set(transition) != {"field_id", "before", "after"}:
            raise ValueError("Invalid transition")
        if type(transition["field_id"]) is not str or any(
            v is not None and type(v) is not str
            for v in (transition["before"], transition["after"])
        ):
            raise ValueError("Invalid transition value")
    return event


class ReviewReceiver:
    def __init__(self, store, *, secret, access, review, now=None):
        """Parameters: shared ControlledStore, signing secret and explicit evidence callbacks.
        What it does: binds a test-only receiver; access(key) returns GitEvidence and
        review(key, required_checks) returns a controlled ReviewResult.
        Output: a receiver with accept(raw_body, headers); no listener or runtime process.
        """
        self.store, self.secret = store, secret
        self.access: Callable[[ReviewKey], GitEvidence] = access
        self.review: Callable[[ReviewKey, tuple[str, ...]], ReviewResult] = review
        self.now = now

    def accept(self, body: bytes, headers: Mapping[str, str]) -> Receipt:
        """Parameters: exact raw POST bytes and timestamp, signature and event-ID headers.
        What it does: authenticates a wake hint, rereads canonical history, claims before
        dispatch, then rechecks all current versions before applying a controlled result.
        Output: a receipt and at most one synthetic Stage/blocker update; no approval or merge.
        """
        headers = {k.lower(): v for k, v in headers.items()}
        try:
            timestamp = int(headers.get("x-space-timestamp", ""))
        except ValueError:
            return Receipt(401, "unauthenticated")
        now = self.now() if self.now else None
        if not verify_signature(
            self.secret, timestamp, body, headers.get("x-space-signature", ""), now=now
        ):
            return Receipt(401, "unauthenticated")
        try:
            event = _event(body)
        except (ValueError, TypeError, AttributeError):
            return Receipt(400, "invalid")
        if headers.get("x-space-event-id") != event["id"]:
            return Receipt(400, "invalid")
        store = self.store
        if (event["space_id"], event["database_id"], event["record_id"]) != (
            store.space_id,
            store.database_id,
            store.row.id,
        ):
            return Receipt(403, "wrong_scope")
        transition = event["transition"]
        if not transition or transition["field_id"] != store.stage_field_id:
            return Receipt(202, "ignored")
        with store.lock:
            task, row, entry = store.task, store.row, store.latest_entry()
            if row.stage != "Review" or entry is None or event["revision"] != entry.revision:
                return Receipt(202, "ignored")
            previous = next((v for v in store.history if v.revision == entry.revision - 1), None)
            genuine = {
                "field_id": store.stage_field_id,
                "before": previous.row.stage if previous else None,
                "after": "Review",
            }
            if (
                transition != genuine
                or event["operation"] != ("updated" if previous else "created")
                or store.stage_field_id not in event["changed_fields"]
            ):
                return Receipt(202, "ignored")
            request = row.request
            try:
                key = review_key(task, row)
            except ValueError:
                key = None
            if (
                request is None
                or request.key != key
                or request.entry_revision != entry.revision
                or entry.row.request != request
                or task.status != "in_progress"
            ):
                store.update_row(blocker="Persisted review request does not match current versions")
                return Receipt(202, "blocked")
            submission = (row.id, request.submission_id)
            exact = (submission, key)
            if (
                event["id"] in store.claimed_events
                or submission in store.claimed_submissions
                or exact in store.claimed_reviews
            ):
                return Receipt(202, "duplicate")
            store.claimed_events.add(event["id"])
            store.claimed_submissions.add(submission)
            store.claimed_reviews.add(exact)
            store.dispatches.append(key)
        try:
            evidence = self.access(key)
            if not evidence.available or (evidence.repository, evidence.base, evidence.head) != (
                key.repository,
                key.base,
                key.head,
            ):
                result = ReviewResult(
                    key,
                    "unavailable",
                    detail="Git access/version evidence unavailable",
                )
            else:
                result = self.review(key, request.required_checks)
        except Exception:
            result = ReviewResult(
                key,
                "unavailable",
                detail="Controlled evidence callback unavailable",
            )
        with store.lock:
            current_entry = store.latest_entry()
            try:
                current_key = review_key(store.task, store.row)
            except ValueError:
                current_key = None
            if (
                store.row.stage != "Review"
                or store.row.request != request
                or current_key != key
                or store.task.status != "in_progress"
                or current_entry != entry
            ):
                return Receipt(202, "stale")
            if result.key != key or result.outcome == "unavailable":
                store.update_row(
                    blocker="Git/review evidence unavailable for the exact requested versions",
                )
                return Receipt(202, "blocked")
            if result.outcome == "defect" and result.detail:
                store.update_row(stage="In progress", stored_result="Defect", blocker=None)
                return Receipt(202, "defect")
            if (
                result.outcome != "pass"
                or not request.required_checks
                or not set(request.required_checks).issubset(result.checks)
            ):
                store.update_row(blocker="Required review checks are missing or inconclusive")
                return Receipt(202, "blocked")
            store.update_row(stage="Security Check", stored_result="Pass", blocker=None)
            return Receipt(202, "passed")


def synthetic_fixture(
    *,
    space_id=None,
    database_id=None,
    record_id="synthetic-dev-row",
    stage_field_id="stage",
    previous_revision=1,
):
    """Parameters: optional event scope, record/Stage IDs and preceding database revision.
    What it does: seeds a five-section canonical task, Dev row, history and exact submission.
    Output: (ControlledStore, minimal_event); callers must supply evidence callbacks explicitly.
    """
    body = "\n\n".join(f"## {name}\nSynthetic {name.lower()}." for name in SPEC_SECTIONS)
    body += "\n\n## Workflow\nSynthetic workflow, outside the specification fingerprint."
    task = Task(str(uuid4()), body)
    row = DevRow(record_id, task.id, "In progress", "synthetic/repository", "a" * 40, "b" * 40)
    store = ControlledStore(
        task,
        row,
        space_id=space_id or uuid4(),
        database_id=database_id or uuid4(),
        stage_field_id=stage_field_id,
        revision=previous_revision,
    )
    return store, store.submit()
