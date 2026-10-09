# Outbound database webhooks

## Purpose

Space can persist signed notifications for committed changes to records in a selected database. A notification is a wake-up hint, not permission to review, approve, modify code or merge. The `webhooks` service of the compose stack delivers them; see [Architecture](ARCHITECTURE.md#background-work).

## When to use

Use Settings → Webhooks to send minimal database change notifications to a receiver the Space owner controls. For Dev Review, select the Dev database, its stable Stage field ID and the existing Review option. Review is a database select value; webhook setup never adds general Tasks columns.

## Context map

- `backend/src/space/webhooks.py` — human owner management, expected revisions, command deduplication and receipts.
- `backend/src/space/webhook_events.py` — stable-record snapshot comparisons and transactional outbox production.
- `backend/src/space/workspace_items.py` and `backend/src/space/restore_publish.py` — ordinary REST/MCP saves and committed archive-restore heads.
- `backend/src/space/webhook_http.py` — signature verification, public HTTPS validation and pinned TLS connections.
- `backend/src/space/webhook_delivery.py` and `backend/src/space/webhook_worker.py` — durable attempts, lease recovery and bounded worker execution.
- `backend/migrations/versions/0027_webhooks.sql` — scoped storage, RLS and the non-login worker capability.
- `apps/web/app/components/space/webhook-settings.tsx` — existing Settings management surface.
- `backend/tests/webhook_review_harness.py` — controlled consumer proof, not a production listener or distributed ownership lock.

## Workflow / behavior

### Subscription management

Only the Space's owner may manage subscriptions or read receipts, from the website or with the access key. Agent tokens and personal API keys cannot configure a destination or read its signing secret. A subscription names an active database in that exact Space. The optional `transition` filter names one existing select field and one of its current options; field IDs, not display labels, are authoritative.

| Method | `/v1/spaces/{space_id}` route | Request / response |
|---|---|---|
| GET | `/webhooks` | `{items: [subscription]}`; excludes removed subscriptions and all secrets |
| POST | `/webhooks` | `{command_id, database_id, endpoint_url, transition}` → `{subscription, signing_secret}` |
| POST | `/webhooks/{subscription_id}` | `{command_id, expected_revision, action}`; action is `pause`, `resume`, `remove` or `rotate` |
| GET | `/webhooks/{subscription_id}/deliveries` | Latest 100 deliveries and their attempt receipts; manual Refresh in Settings |
| POST | `/webhooks/{subscription_id}/deliveries/{delivery_id}/redeliver` | `{command_id}`; requeues only a terminal delivery for an active, unpaused subscription |

Creation and rotation return a random 256-bit signing secret. Copy it to the controlled receiver immediately; it is not in list or receipt responses and is not stored in browser persistence. A retry with the same command ID can recover the same secret only while the subscription still has that command's resulting revision. A later configuration change never returns an old secret. Command IDs cannot be reused for different requests. Retried commands return the current subscription state without applying another change.

Pause stops new event admission for that subscription and new network attempts. Already queued deliveries remain stored and can become eligible on resume; pause does not backfill changes made while paused. Remove clears the live configuration and blocks further attempts. An attempt already sending may finish. The worker holds a subscription shared lock through network I/O, so pause/remove returns only after any already-started send releases that lock. Rotate replaces the active key immediately, including for pending retries; there is no implicit old-key grace period. Coordinate rotation with the receiver, preferably while paused.

### Commit-only notifications

Ordinary browser REST saves, `save_workspace_item` MCP writes and final active database heads published by archive restore share the same outbox producer. Each snapshot compares records by stable record ID with its locked predecessor. Only record additions, deletions or actual `name`, `notes`, `createdAt` or field-value differences create events. Row order, database/view/schema metadata alone and `updatedAt`-only edits do not report unchanged rows. Missing and null field values are equivalent. Database archive/restore visibility toggles do not submit unchanged records for review.

Event and delivery inserts commit atomically with the database revision. Failed validation, revision conflicts, rolled-back writes and retries of an already committed command create no additional events. An enabled subscription is required for event production; creating a subscription does not report older changes. A full outbox rejects the save rather than silently losing notifications.

Events have one UUID per changed record and committed database revision. The same event ID is shared across matching subscriptions, while each delivery has its own stable ID. `previous_revision` and `revision` refer to database snapshots, not per-record counters. An archive merge reports only the final changed row, not every imported historical revision.

The v1 payload shape is:

```json
{
  "version": 1,
  "id": "00000000-0000-4000-8000-000000000001",
  "type": "database.record.changed",
  "occurred_at": "2026-01-01T00:00:00+00:00",
  "space_id": "00000000-0000-4000-8000-000000000002",
  "database_id": "00000000-0000-4000-8000-000000000003",
  "record_id": "controlled-row",
  "previous_revision": 4,
  "revision": 5,
  "operation": "updated",
  "changed_fields": ["stage"],
  "changed_properties": [],
  "transition": {"field_id": "stage", "before": "In progress", "after": "Review"}
}
```

`operation` is `created`, `updated` or `deleted`. Unfiltered subscriptions receive `transition: null` and only changed field/property IDs, never values. A filtered subscription explicitly discloses before/after values for its one selected field. It receives a notification only when the value actually changes into the configured target. A newly created row can enter the target from null. Review → Review metadata edits do not match; Review → In progress → Review creates a fresh event even when head/spec are unchanged. Removing or changing the field away from a compatible select disables matching until the schema is compatible again.

Bodies never contain complete records, names, notes, canonical task bodies, Git metadata, subscription secrets or unrelated content. Configuration, filters, events and delivery bodies are stored as readable JSON, like every other payload. Record writers can read only filters, through a scoped function, never subscription destinations or secrets. Receipts contain IDs, revision numbers, timestamps, numeric HTTP status and fixed diagnostic codes. No receiver response body, raw exception or provider message is stored or logged.

### Authentication and outbound safety

Space POSTs UTF-8 JSON with `Content-Type: application/json` and these headers:

- `X-Space-Event-ID`: the payload's stable event UUID.
- `X-Space-Delivery-ID`: the stable subscription/event delivery UUID, unchanged on retries/redelivery.
- `X-Space-Timestamp`: a fresh integer Unix timestamp for this network attempt.
- `X-Space-Signature`: `v1=` followed by lowercase HMAC-SHA256 hexadecimal.

The signed bytes are `ASCII(timestamp) + b"." + raw_request_body`. The HMAC key is the literal UTF-8 signing-secret string, not base64-decoded material. Verify the unchanged body before parsing, compare signatures in constant time, reject timestamps outside a bounded replay window (the helper defaults to ±300 seconds), and require the event-ID header to match the signed body. Never treat an unauthenticated delivery-ID header as authority. Retries preserve the event/body identity but refresh the timestamp/signature.

Endpoints must use HTTPS and port 443. Userinfo, query strings, fragments, whitespace/control characters, local/special-use hosts and private/reserved/multicast addresses are rejected. Configuration checks DNS, and every send resolves afresh and rejects the entire answer set if any address is unsafe, including mapped IPv6 addresses. The connection pins one approved public IP while preserving the original Host, TLS hostname and SNI. Environment proxies, redirects, alternate-address retries and response-body reads are disabled. There is no runtime private-receiver override. Supporting intentionally private receivers requires a separately reviewed design and approval.

### Delivery, limits and recovery

The delivery queue uses durable PostgreSQL leases and immutable bodies. A new attempt and its receipt are recorded before network I/O. A restarted worker closes an expired unfinished receipt as `worker_lost`, then can claim the same delivery/event again. Lease compare-and-swap prevents an obsolete worker from publishing a result. Network delivery is at-least-once, not exactly-once: a receiver may have accepted an attempt whose receipt was lost.

| Bound | Value |
|---|---|
| Active or paused subscriptions per Space | 20 |
| Pending deliveries per Space | 10,000 |
| Attempts per automatic delivery cycle | 5 |
| Socket timeout / supervisor wall deadline / lease | 10 / 30 / 60 seconds |
| Retry base delays | 30, 120, 480, 1,920 seconds, plus bounded jitter |
| Retry-After for 429/503 | Decimal seconds or HTTP date, bounded to 1–3,600 seconds |
| Manual recovery cycles | At most 20, audited with the original IDs/body |
| Recovery / receipt-command retention window | 30 days |

2xx succeeds. Timeouts, DNS/network errors, 429 and 5xx retry within the bound. A connection the receiver closes during the TLS handshake counts as a network error and is retried, with a full verified handshake on every attempt. Unsafe destinations, redirects, certificate or hostname verification failures, other TLS protocol errors and other 4xx fail without automatic retry. Exhaustion is visible as terminal `failed` plus its last fixed error and attempt receipts. Pending work older than 30 days becomes visibly failed as `expired` when the worker runs. Manual redelivery resets the attempt count, increments the audited cycle and keeps prior receipts; it cannot change the payload, bypass pause/removal, redeliver active work or recover expired deliveries.

The `webhooks` service runs `python -m space.webhook_worker --loop`. While idle it checks for due deliveries every 3 seconds (`--poll-interval`) with one cheap query, and drains them whenever any are due, including retries that come due later. Each attempt runs in a fresh child process with a 30-second deadline. For manual use the worker also supports `--once` and `--drain --max-attempts 20`; a drain stops when nothing is due or the bound is reached. Nothing starts from imports or API startup. `SPACE_WEBHOOK_DATABASE_URL` names the `space_webhook` login, which the installer creates with a single membership, the `space_webhook_worker` capability role from migration `0027`, and a limit of 2 connections; it must not be the migration login, the runtime login or the memory worker's login. Each worker process also holds one `space_app` connection, for the owner's authorization and for reading the subscription; `SPACE_DATABASE_WEBHOOK_PROCESSES` declares it in the API's connection budget.

`python -m space.webhook_retention --batch-size 1000` runs one explicit maintenance batch: it removes old terminal deliveries and their receipts, unused events, command receipts and unreferenced removed subscriptions. It installs no recurring job and never discards pending work; [Operations](OPERATIONS.md#retention) shows how to schedule it. Resetting or deleting a Space includes all webhook tables. Restoring a database backup brings subscriptions and their queued deliveries back as they were when it was taken, so deliveries pending at that moment are sent again; receivers must deduplicate by event ID, as they must for any retry. The owner coordinates key rotation, resume and any redelivery. Portable Space archives intentionally omit webhook destinations and secrets.

## Decisions and constraints

- Reuse existing authorization, revision transactions and worker patterns, but do not repurpose memory-extraction jobs or document/task audit events for outbound delivery.
- Space authenticates and delivers metadata. It does not run review skills, approve gates, fix code, change Dev stages or authorize merges.
- A real Dev consumer must reread the canonical task, current Dev row and revision history; verify the latest genuine Review submission and exact repository/base/head/spec; atomically claim ownership; and deduplicate the submission plus exact review key. Recheck those inputs before writing a result. Missing access/evidence remains Review with a blocker; an evidenced code-review pass can move to Security Check and a confirmed defect to In progress, never directly to Done or merge.
- A later submission with unchanged code is distinct. Deduplicating only by head/spec loses genuine resubmissions. Conversely, an old event from an earlier Review entry cannot authorize a later submission with the same code.
- Hermes compatibility requires an approved adapter that verifies this signature before invoking its authenticated trigger and existing review skill. This implementation does not connect a gateway, expand credentials or install that adapter.
- The controlled receiver uses explicit synthetic Git/review evidence and one process-local shared lock. It proves the guards and one dispatch in that environment, not distributed locking, live Git/provider checks, user acceptance or unattended runtime isolation.

## Verification / maintenance

Use the repository's locked backend environment. Disposable database tests require Docker and the existing `pgvector/pgvector:pg17` image; they never use the database configured in `backend/.env`.

```sh
uv run --directory backend --locked pytest tests/test_webhook_http.py tests/test_webhook_review_receiver.py
SPACE_RUN_DATABASE_TESTS=1 uv run --directory backend --locked pytest tests/test_webhooks.py tests/test_webhook_delivery.py tests/test_webhook_end_to_end.py
uv run --directory backend --locked ruff check src tests migrations scripts
uv run --directory backend --locked ruff format --check src tests migrations scripts
npm --prefix apps/web test
npm --prefix apps/web run typecheck
```

`test_webhook_end_to_end.py` commits a real disposable Space Review row, claims and signs its stored delivery, uses the actual TLS-pinned connector against a synthetic trusted HTTPS listener through test-only socket mapping, rereads the canonical task/Dev row and verifies one controlled dispatch. It also posts duplicate/stale signed notifications and preserves general task columns. No production URL override exists. Consumer tests separately cover concurrency, unchanged-code resubmission, invalid signatures, stale versions and controlled pass/defect/blocked results.

Update this document when the payload version, endpoint policy, management contract, retry bounds, retention or worker activation model changes. Browser interaction evidence is separate from static checks; a blocked full-app build must not be reported as a passed application flow.
