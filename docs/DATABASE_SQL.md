# SQL access to database records

Design recorded on 2 October 2026 and implemented the same day. It is the companion to [Space map](SPACE_MAP.md). Code: `backend/src/space/database_sql.py`; tests: `backend/tests/test_database_sql.py`. On 22 October 2026 the storage underneath changed: each record is its own row in `space.database_records` and `space.database_record_revisions` (`backend/src/space/database_records.py`), so a write stores the rows it changed rather than a copy of the table. The sections below describe the current behaviour; the design decisions keep their dates.

## Decisions

- `list_workspace_items(kind="database")` stops returning records.
- Agents read and change records with SQL, one database per call.
- The first version supports reads and writes: `SELECT`, `INSERT`, `UPDATE` and `DELETE`.
- One call can run a short batch of statements that succeed or fail together and produce one revision. The user was tentative about batches ("I don't know if it's good, yes"), so the batch is kept small and simple.

On 20 October 2026 the owner decided to split the MCP tool in two: `read_database` reads and is marked read-only, and `query_database` only changes records. Three problems led to it. A connector directory rejects a tool that both reads and changes data. Because `query_database` was marked destructive, Claude asked for confirmation before every database read. And a ChatGPT Pro connection, which allows only read-only tools, could not read a database at all. The REST endpoint still takes both reads and writes.

## Purpose

Let agents work with database rows the way they already know how, without loading or resending a whole database. When this was designed, a database was one packed snapshot of up to 1,000 records and 900 KiB, every write replaced it, and an agent had to read all records, change one and send everything back; one listing of the owner's databases returned about 277,000 characters. Since 22 October 2026 each record is stored on its own: a database holds up to 10,000 records, and a write stores only the rows it changed.

## Why not Postgres

Records are rows of their own, but a row's values live inside one packed JSON payload per revision rather than in a column per field, so agent SQL cannot read them in PostgreSQL. Each call unpacks the database's current rows in process and loads them into a throwaway in-memory SQLite database, runs the statements, and then discards the SQLite database. At 10,000 rows or fewer this is cheap, it needs no second copy of the records, and agent SQL never reaches the real database. Python's standard `sqlite3` module is enough, with no new dependency.

## Interface

### MCP `read_database` and `query_database`

```
read_database(
    space_id, database_id,
    request={
        "sql": None, "params": [],
        "statements": None,
        "max_rows": 100, "include_schema": False,
    },
    agent_id=None,
) -> QueryResult

query_database(
    space_id, database_id,
    request={
        "sql": None, "params": [],
        "statements": None,
        "expected_revision": …, "command_id": …,
        "max_rows": 100, "include_schema": False,
    },
    agent_id=None,
) -> QueryResult
```

The query fields travel together in one `request` object: `database_sql.Read` for `read_database` and `database_sql.Change` for `query_database`. `max_rows` accepts 1 to 500.

- Pass either `sql` with `params` for one statement, or `statements: [{"sql": "…", "params": […]}, …]` for a batch. Passing both, or neither, is rejected.
- `read_database` needs the read grant and reads the current revision. Its request has no `command_id` or `expected_revision`, and its authorizer refuses `INSERT`, `UPDATE` and `DELETE`. It is marked read-only, so clients can run it without asking for confirmation.
- `query_database` requires `command_id` and `expected_revision`. It runs in write mode and needs the write grant. A request without them, or with no `INSERT`, `UPDATE` or `DELETE`, is refused, and the error points to `read_database`, so an agent that still reads through `query_database` finds the new tool. A write that matches no row is still accepted and saves nothing. A stale revision returns 409, like every other revision-checked write.
- A retry with the same `command_id` and the same request returns the committed revision with `replayed: true`, empty `results` and no `changes`; it does not replay `RETURNING` rows. The caller's command ID is bound to an HMAC of the exact request, so reusing it for different SQL is treated as a new write and fails on the now-stale revision.
- `params` binds `?` placeholders. Agents should bind values rather than build SQL strings, and the served guide says so.
- `max_rows` ranges from 1 to 500 per statement. The whole result is also capped at 20,000 serialized characters. A larger result sets `truncated` and reports the full row count, and the agent narrows the query or adds `LIMIT`/`OFFSET`.

```json
{
  "database": {"id": "…", "revision": 12},
  "results": [
    {"columns": ["id", "name", "Company", "Status"],
     "rows": [["rec_1", "Acme", "Acme Ltd", "Contacted"]],
     "row_count": 1, "truncated": false}
  ],
  "changes": null
}
```

`results` has one entry per statement, in order. A write returns `database.revision` as the new revision, and `changes` holds the batch totals `{"inserted": n, "updated": n, "deleted": n}`. Rows from a `RETURNING` clause appear in that statement's `rows`.

### Batches

A batch is for one small, related change, such as "move these three rows to Won and add the follow-up row", without a revision and a round of webhooks for each step.

- At most 20 statements, with 20,000 characters of SQL and 200 parameters in total.
- Statements run in order on the same in-memory table inside one SQLite transaction, so a later statement sees earlier changes. A `SELECT` placed after a write reads the batch's own uncommitted state.
- The batch is validated and saved once, as a single revision that stores only the rows the batch changed. If any statement fails, or a changed row fails validation, nothing is saved, and the error names the statement index.
- The instruction budget applies to the batch as a whole, not to each statement.
- Webhooks compare each changed row with its stored version, so a row inserted and then deleted in the same batch produces no event.

A batch is not a script. Each entry is still exactly one statement, under the same authorizer. If batches turn out to cause confusing partial-intent errors in practice, the single-statement form remains and batches can be removed without breaking it.

The REST endpoint `POST /v1/spaces/{space_id}/databases/{database_id}/query` runs the same command with one request shape, `database_sql.Query`: a read without `command_id` and `expected_revision`, a write with both. The split into two tools applies to MCP only.

### Table shape

Each call exposes exactly one table, `records`:

| Column | SQLite type | Source |
| --- | --- | --- |
| `id` | TEXT | record ID; assigned inside SQLite on insert when omitted, derived from the command so a retry assigns the same ID, and reported by `RETURNING id`; read-only after insert |
| `name` | TEXT | record name; required |
| `notes` | TEXT | record notes |
| `created_at` | TEXT | `createdAt`; set on insert, read-only |
| `updated_at` | TEXT | `updatedAt`; set by Space on every changed row, read-only |
| one column per field | see below | `values[field.id]`, named by `field.name` |

Field names are already distinct when case is ignored. Agents quote names that are not plain identifiers, for example `"Next step"`. A field whose name collides with a built-in column (for example a field called `name`) is exposed as `field_<name>`, and the schema lists the mapping.

| Field type | SQLite type | Stored as |
| --- | --- | --- |
| text, select, date, email, phone, url | TEXT | string; empty string and NULL both mean no value |
| number | REAL | finite number |
| checkbox | INTEGER | 0 or 1, converted to `false`/`true` |

`PRAGMA table_info` is not available. The schema comes from `get_space_map` or `get_workspace_item`. When the call passes `include_schema=true`, the result also includes a `schema` list of `{column, field_id, type, options}`.

## Write path

1. Read the database's current rows and unpack them; a write whose `expected_revision` is no longer current is refused with 409 before any SQL runs.
2. Run the statement, or each statement of the batch in order, under the authorizer and limits below.
3. Read the table back and compare it with what was loaded (`Sandbox.changes`): inserted rows, rows whose content changed and the IDs that went away. Rows keep their stored order and inserted rows go at the end. Space sets `updated_at` on changed rows, and `id` and `created_at` on inserted rows.
4. Validate each changed row against the schema: select options, dates, URLs, emails, finite numbers, and the 10,000-record limit. Any failure rejects the whole statement, nothing is saved, and the error names the problem.
5. Save through `workspace_items.change_rows` as a new revision with the caller's agent attribution: one packed record revision per changed row and a tombstone per deleted row, beside a schema-only item revision. The shared write path emits `database.record.changed` webhooks for each changed record, so SQL writes need no separate notification code.

A statement that changes nothing still returns the current revision and creates no new revision.

## Safety limits

The input is agent-supplied SQL, so the sandbox is deny-by-default:

- One statement per `sql` string, including each batch entry. A second statement in the same string is rejected (`sqlite3` already refuses multiple statements in `execute`), and so are an empty statement and a trailing statement after a semicolon.
- `set_authorizer` allows only `SELECT`, `READ`, `INSERT`, `UPDATE` and `DELETE` on `records`, plus an allow-list of functions: string, math, date/time, `json_*`, aggregates and window functions. It denies `ATTACH`, `DETACH`, `PRAGMA`, `CREATE`, `DROP`, `ALTER`, transaction control, `VACUUM`, `ANALYZE` and every other action.
- In read mode the authorizer also refuses `INSERT`, `UPDATE` and `DELETE`. In write mode it refuses updates to `id`, `created_at` and `updated_at`.
- Extension loading stays disabled. The connection is `:memory:`, with no file or URI access.
- A progress handler stops a call after 5 million virtual machine steps or 0.5 seconds, whichever comes first, and returns "query too expensive; narrow it or add LIMIT". `SQLITE_LIMIT_LENGTH` caps any string or blob at 1 MB.
- `SQLITE_DBCONFIG_DEFENSIVE` is on, and triggers and views are disabled. Trusted schema stays on, because the only schema is Space's own table, whose ID default calls Space's ID function.
- A single statement is limited to 10,000 characters and 100 parameters. A batch is limited as described under Batches.
- `RETURNING` needs SQLite 3.35 or newer. The pinned runtime image (`python:3.13-slim-bookworm@sha256:ed86c822…`) reports SQLite 3.40.1, checked on 2 October 2026, and the local backend virtualenv reports 3.53.1. On an older SQLite every call returns 503 instead of running.
- Errors return the SQLite message for syntax and constraint problems. They never include values from other rows.

SQL text and parameters are never logged or sent to telemetry. Telemetry records statement kind, duration, row counts and outcome only.

## Changes to existing tools

- `list_workspace_items(kind="database")` returns each database's metadata, fields, `record_count`, view and group-by field, without `records`. This was a breaking change for MCP clients on 2 October 2026, announced in the served guide and the `space-base` skill. Other kinds are unchanged.
- Since 22 October 2026 `get_workspace_item` returns a database the same way, with its schema and `record_count` and no records: with up to 10,000 rows a table no longer fits a tool result, and rows are read with `read_database`. REST's `GET /items/{item_id}` still carries the records, because the website edits against them.
- `save_workspace_item` changes the schema (fields, view, grouping, name, description, project or section) and keeps every stored row when `records` is omitted. With `records`, the list is the whole table: rows it omits are deleted, and only rows that differ are stored. That path stays for bulk imports.
- The website sends row-level changes through `POST /spaces/{space_id}/databases/{database_id}/changes` (`workspace_items.DatabaseChange`: the schema when it changed, the rows to write and the IDs to delete, as one revision) and loads a database's rows when a page opens it.
- `search_workspace` still matches record names, values and notes.

## Storage

Each database item revision holds the schema and metadata. `space.database_records` has one head row per record with its current revision and its insertion position; `space.database_record_revisions` holds one packed record per version, keyed by the database revision that wrote it, and a payload-less tombstone when a record is deleted. A read at an earlier revision takes each record's last version at or before that revision, so history is complete without storing the table again. Record revisions count toward retained storage like every other stored revision; a tombstone costs nothing. A value a later schema cannot represent (a removed field, a dropped select option, a changed type) reads as empty until the row is next written, and the stored revision keeps it. Databases written before migration `0042_database_records` keep their records inside their item payloads; every read of those revisions still finds them there, and the database's next write copies its current records into the row tables.

## Context map

Expected touch points; confirm during implementation.

- `backend/src/space/database_sql.py` — rows to SQLite, authorizer, limits, read-back and the changed-row diff.
- `backend/src/space/database_records.py` — the row tables: reads at the current or an earlier revision, the diff a write plans, and the row revisions it stores.
- `backend/src/space/workspace_items.py` — listing without records, `record_count`, `DatabaseChange` and the shared write path every item revision takes.
- `backend/src/space/api/mcp.py`, `api/rest.py` — tool and route.
- `backend/src/space/guide.py` — agent guidance.
- `skills/space-base/SKILL.md` — rows through SQL, snapshot saves only for schema changes.

## Test plan

- Reads: filters, ordering, aggregates, `LIMIT`/`OFFSET`, quoted field names, the built-in name collision, checkbox and number conversion, empty and null values, `max_rows` and character truncation.
- Writes: insert with and without `id`, update, delete, `RETURNING`, read-only columns rejected, invalid select option, bad date, URL and email, the 10,001st record, no-op statement, stale revision 409, retry with the same `command_id`.
- Batches: later statements see earlier changes, one revision per batch, a failure at any index saves nothing and names the index, a final-snapshot validation failure saves nothing, 21 statements rejected, combined size limits enforced, `sql` and `statements` together rejected.
- Webhooks: one event per changed record, none for a no-op, none for a row inserted and deleted in the same batch, matching the existing snapshot comparison.
- Sandbox: `ATTACH`, `PRAGMA`, `CREATE`, `DROP`, transaction control, multiple statements, recursive CTE runaway, a huge `zeroblob` or string, extension loading — each rejected with no partial save.
- Authorization: read grant cannot write, revoked grant denied, cross-Space database ID rejected, archived database rejected for writes.
- Listing: database listings contain no records and report the correct `record_count`.
- MCP end to end: a real client adds a row with `INSERT … RETURNING id`, updates it by ID, and the website shows the new revision.

## Open questions

1. Resolved on 22 October 2026: the website's row editing moved to a row-level change endpoint that shares the write path with SQL writes and snapshot saves. Agents keep SQL.
2. Databases written before migration `0042` keep every earlier whole-table revision. Dropping those superseded copies would reclaim their storage but deletes history, so it is an operator decision not taken here.
