"""What the published reference says about each public operation.

One table, kept beside the response models, documents every route an API key can call:
its response schema, a description, and the errors it can return. Public routes are built
as DocumentedRoute, which applies the table and refuses to construct a route missing from
it, so the reference cannot silently fall behind the routes.
"""

import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from fastapi.routing import APIRoute

from space.api import schemas as s

ERRORS = {
    400: "The request is well formed but cannot be carried out, such as an unknown status.",
    401: "The API key is missing, unknown, expired or revoked.",
    402: "An active subscription is required to add content. Reading, export and deletion "
    "still work.",
    403: "Not allowed: a read-only key, a view-only role, or an operation keys cannot do.",
    404: "Not found. Other Spaces also look absent to a key.",
    409: "Conflict: a stale expected_revision, a reused command_id, or a changed snapshot. "
    "Re-read and retry.",
    422: "Invalid fields, revision or payload.",
    429: "A Space quota is exhausted (storage, items or monthly model requests).",
    503: "Temporarily unavailable or busy. Retry with backoff, honouring Retry-After.",
}
# Real requests and responses captured by tests/test_api_contract.py with
# SPACE_API_EXAMPLES=write, shortened, and validated against the schemas by that suite.
EXAMPLES = Path(__file__).with_name("examples.json")
# Every /v1 route validates its path and query, so each can answer 422.
READ = (401, 403, 404, 422, 503)
WRITE = (401, 402, 403, 404, 409, 422, 429, 503)
MODEL = (401, 403, 404, 422, 429, 503)


@dataclass(frozen=True)
class Operation:
    model: type
    description: str
    errors: tuple[int, ...] = READ
    status: int = 200


OPERATIONS = {
    ("get", "/health/live"): Operation(
        s.Live, "Process liveness. It does not check storage; use `/health/ready` for that.", ()
    ),
    ("get", "/health/ready"): Operation(
        s.Ready, "Returns ready once the database answers, otherwise 503.", (503,)
    ),
    ("get", "/v1/workspace"): Operation(
        s.SpaceInfo,
        "The Space this key was created for, with your role in it. Start here to learn the "
        "`space_id` every other route takes.",
        (401, 404, 422, 503),
    ),
    ("get", "/v1/spaces/{space_id}/read-version"): Operation(
        s.ReadVersion,
        "A cheap opaque version of the Space. Poll it and refetch only when it changes.",
    ),
    ("get", "/v1/spaces/{space_id}/usage"): Operation(
        s.Usage, "Storage, item and monthly model-request usage against the Space's limits."
    ),
    ("get", "/v1/spaces/{space_id}/overview"): Operation(
        s.Overview,
        "Activity over the last 7, 30 or 90 days: totals, a daily series, per-agent and "
        "per-strategy measures and the most recent events. Pass `all_periods=true` to get "
        "every window at once.",
        (*READ, 400),
    ),
    ("get", "/v1/spaces/{space_id}/map"): Operation(
        s.SpaceMap,
        "The Space's structure: sections, projects, folders, databases and categories, with "
        "the routing rules that say what belongs where. No document text or records. "
        "`max_chars` bounds the size; `truncated` says whether it was shortened.",
    ),
    ("get", "/v1/spaces/{space_id}/resources"): Operation(
        s.ResourcePage,
        "Documents or tasks, 50 per page in ID order, without their bodies. Memory records "
        "and handoffs are not listed here.",
        (*READ, 422),
    ),
    ("post", "/v1/spaces/{space_id}/resources"): Operation(
        s.Resource,
        "Creates a document or task with `expected_revision: 0`, or replaces one by sending "
        "its `resource_id` and current revision. Every save is a new immutable revision.",
        WRITE,
    ),
    ("get", "/v1/spaces/{space_id}/resources/{resource_id}"): Operation(
        s.Resource,
        "One document or task with its body. Pass `revision` to read an earlier revision.",
        (*READ, 422),
    ),
    ("delete", "/v1/spaces/{space_id}/resources/{resource_id}"): Operation(
        s.Deleted,
        "Permanently deletes a document or task and its history. `expected_revision` must be "
        "the current revision.",
        WRITE,
    ),
    ("get", "/v1/spaces/{space_id}/tasks/columns"): Operation(
        s.TaskColumns, "The task board's columns, in order. A task's `status` is a column value."
    ),
    ("post", "/v1/spaces/{space_id}/tasks/columns"): Operation(
        s.TaskColumns,
        "Adds a column to the task board. `expected_revision` is the columns' revision.",
        WRITE,
    ),
    ("post", "/v1/spaces/{space_id}/tasks/{resource_id}/status"): Operation(
        s.Resource, "Moves a task to another column without rewriting its content.", WRITE
    ),
    ("post", "/v1/spaces/{space_id}/handoffs"): Operation(
        s.Resource,
        "Saves a session handoff: a summary with decisions, progress, open questions and next "
        "actions. Handoffs are recalled with `mode: resume` on the context route.",
        WRITE,
    ),
    ("post", "/v1/spaces/{space_id}/export"): Operation(
        s.ExportPage,
        "Exports everything in the Space, including history, one page at a time. Send the "
        "first page's `snapshot` with `after` on the next pages; a 409 means the Space "
        "changed, so start again.",
        (*READ, 409, 422),
    ),
    ("post", "/v1/spaces/{space_id}/search"): Operation(
        s.SearchPage,
        "Full-text search over current documents, tasks, memory, projects and database "
        "records. Each call scans at most 25 items: keep following `next_cursor` with the "
        "same `snapshot` until it is null, even when a page has no matches.",
        (*READ, 409, 422),
    ),
    ("post", "/v1/spaces/{space_id}/context"): Operation(
        s.RecallResult,
        "Recalls cited evidence for a question (`mode: search`) or recent handoffs (`mode: "
        "resume`). `strategy: lexical` makes no model calls; `auto` also uses embeddings and "
        "an evidence check, which count toward the monthly model requests.",
        MODEL,
    ),
    ("get", "/v1/spaces/{space_id}/items"): Operation(
        s.ItemPage,
        "Sections, projects, folders, categories or databases of one `kind`. Pass "
        "`deleted=true` to list deleted ones.",
        (*READ, 422),
    ),
    ("post", "/v1/spaces/{space_id}/items"): Operation(
        s.WorkspaceItem,
        "Creates an item with a new `item_id` and `expected_revision: 0`, or replaces its "
        "`data` at the current revision. `data` depends on `kind`; databases carry `fields` "
        "and `records`.",
        WRITE,
    ),
    ("get", "/v1/spaces/{space_id}/items/{item_id}"): Operation(
        s.WorkspaceItem, "One item. Pass `revision` to read an earlier revision.", (*READ, 422)
    ),
    ("post", "/v1/spaces/{space_id}/databases/{database_id}/query"): Operation(
        s.QueryResult,
        "Runs SQLite-dialect SQL against one database's `records` table. Reads need nothing "
        "else; writes (INSERT, UPDATE, DELETE) need a read-write key plus `command_id` and "
        "the database's current `expected_revision`. `include_schema=true` maps field names "
        "to SQL columns.",
        WRITE,
    ),
    ("get", "/v1/spaces/{space_id}/memory/provider"): Operation(
        s.ProviderInfo, "Which model provider processes memory, and the state of the index."
    ),
    ("get", "/v1/spaces/{space_id}/memories"): Operation(
        s.MemoryPage,
        "Memory records with their claims, newest first. `query` filters by words.",
        (*READ, 422),
    ),
    ("get", "/v1/spaces/{space_id}/memories/{resource_id}"): Operation(
        s.MemorySummary, "One memory record with its claims and whether its sources changed."
    ),
    ("delete", "/v1/spaces/{space_id}/memories/{resource_id}"): Operation(
        s.Deleted,
        "Permanently deletes a memory record with all its claims. To remove one claim, use "
        "the change route with `action: forget`.",
        WRITE,
    ),
    ("put", "/v1/spaces/{space_id}/memories/{resource_id}/links"): Operation(
        s.MemoryResource,
        "Replaces the memory's explicit links to other memories. An empty list clears them.",
        WRITE,
    ),
    ("get", "/v1/spaces/{space_id}/memories/{resource_id}/links"): Operation(
        s.MemoryLinks, "Outgoing links and backlinks, explicit and inferred.", (*READ, 422)
    ),
    ("post", "/v1/spaces/{space_id}/memory/build"): Operation(
        s.MemoryResource,
        "Extracts memory from a saved source right away, using the configured text model. "
        "Prefer the jobs route for anything long.",
        (*WRITE, 503),
    ),
    ("post", "/v1/spaces/{space_id}/memory/claims"): Operation(
        s.MemoryResource,
        "Saves claims you already wrote, verbatim: each is classified and indexed, with no "
        "text generation. Recallable as soon as this returns.",
        WRITE,
    ),
    ("post", "/v1/spaces/{space_id}/memory/jobs"): Operation(
        s.Job,
        "Queues extraction from text or a saved source. Poll the job until `recall_ready`.",
        WRITE,
        202,
    ),
    ("get", "/v1/spaces/{space_id}/memory/jobs"): Operation(
        s.JobList, "Recent memory jobs in this Space."
    ),
    ("get", "/v1/spaces/{space_id}/memory/jobs/{job_id}"): Operation(
        s.Job, "One memory job's progress and result."
    ),
    ("post", "/v1/spaces/{space_id}/memory/remember"): Operation(
        s.MemoryResource,
        "Stores one authored memory exactly as written: organized, embedded and indexed "
        "without extraction. Recallable as soon as this returns.",
        WRITE,
    ),
    ("post", "/v1/spaces/{space_id}/memory/change"): Operation(
        s.MemoryResource,
        "Edits, forgets, classifies or routes one claim. `forget` removes it from recall "
        "immediately and permanently.",
        WRITE,
    ),
    ("post", "/v1/spaces/{space_id}/memory/creator"): Operation(
        s.MemoryResource,
        "Corrects who created selected claims: an agent identity, or null for you.",
        WRITE,
    ),
}


def route_options(method: str, path: str) -> dict:
    """Parameters: an HTTP method and full path. Output: the description and responses to
    construct that route with. Raises for a public route missing from the table."""
    entry = OPERATIONS.get((method.lower(), path))
    if entry is None:
        raise RuntimeError(f"{method.upper()} {path} is public but undocumented")
    return {
        "description": entry.description,
        "responses": {
            entry.status: {"model": entry.model, "description": "Success"},
            **{
                code: {"model": s.Error, "description": ERRORS[code]}
                for code in sorted(set(entry.errors))
            },
        },
    }


def add_examples(schema: dict) -> dict:
    """Parameters: a generated OpenAPI document. What it does: attaches each operation's
    captured request and response examples. Done after generation because FastAPI drops
    null values, and real responses contain them. Output: the same document."""
    examples = _examples()
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            example = examples.get(f"{method} {path}")
            if not example:
                continue
            if example["response"] is not None:
                status = str(OPERATIONS[(method, path)].status)
                media = operation["responses"][status]["content"]["application/json"]
                media["example"] = example["response"]
            if example["request"] is not None and "requestBody" in operation:
                body = operation["requestBody"]["content"]["application/json"]
                body["example"] = example["request"]
    return schema


@cache
def _examples() -> dict:
    return json.loads(EXAMPLES.read_text()) if EXAMPLES.exists() else {}


class DocumentedRoute(APIRoute):
    """A public route, constructed with its entry from the reference table."""

    def __init__(self, path: str, endpoint, **kwargs):
        (method,) = kwargs.get("methods") or ["GET"]
        super().__init__(path, endpoint, **(kwargs | route_options(method, path)))
