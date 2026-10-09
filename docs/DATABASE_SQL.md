# SQL access to database records

Design recorded on 2 October 2026 and implemented the same day. It is the companion to [Space map](SPACE_MAP.md). Code: `backend/src/space/database_sql.py`; tests: `backend/tests/test_database_sql.py`.

## Decisions


- `list_workspace_items(kind="database")` stops returning records.
- Agents read and change records with SQL, one database per call.
- The first version supports reads and writes: `SELECT`, `INSERT`, `UPDATE` and `DELETE`.
- One call can run a short batch of statements that succeed or fail together and produce one revision. The user was tentative about batches ("I don't know if it's good, yes"), so the batch is kept small and simple.

## Purpose

Let agents work with database rows the way they already know how, without loading or resending a whole database. A database snapshot holds up to 1,000 records and 900 KiB. Today every write replaces the snapshot, so an agent must read all records, change one, and send everything back. One listing of the owner's databases returned about 277,000 characters.

## Why not Postgres

Records are not table rows in PostgreSQL: each database revision stores all its records as one JSON snapshot. Each call reads one snapshot in process and loads it into a throwaway in-memory SQLite database, runs the statements, and then discards the SQLite database. At 1,000 rows or fewer this is cheap, it needs no second copy of the records, and agent SQL never reaches the real database. Python's standard `sqlite3` module is enough, with no new dependency.

## Interface

### MCP `query_database`

```
query_database(
    space_id, database_id,
    sql=None, params=[],
    statements=None,
    expected_revision=None, command_id=None,
    max_rows=100, include_schema=False, agent_id=None,
) -> QueryResult
```

- Pass either `sql` with `params` for one statement, or `statements: [{"sql": "…", "params": […]}, …]` for a batch. Passing both, or neither, is rejected.
- A call made only of `SELECT` statements needs the read grant. It reads the current revision, or `expected_revision` when given.
- A call that passes `command_id` and `expected_revision` runs in write mode and needs the write grant. Without them, the authorizer refuses `INSERT`, `UPDATE` and `DELETE`. A stale revision returns 409, like every other revision-checked write.
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
- The batch is validated and saved once, as a single revision. If any statement fails, or the final snapshot fails validation, nothing is saved, and the error names the statement index.
- The instruction budget applies to the batch as a whole, not to each statement.
- Webhooks compare the final snapshot with its predecessor, so a row inserted and then deleted in the same batch produces no event.

A batch is not a script. Each entry is still exactly one statement, under the same authorizer. If batches turn out to cause confusing partial-intent errors in practice, the single-statement form remains and batches can be removed without breaking it.

The REST equivalent is `POST /v1/spaces/{space_id}/databases/{database_id}/query`, used by the same workspace command.

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

1. Read the snapshot at `expected_revision` and load it into SQLite.
2. Run the statement, or each statement of the batch in order, under the authorizer and limits below.
3. Read the table back and rebuild the records list. Rows keep their original order, inserted rows go at the end, and deleted rows are removed. Space sets `updated_at` on changed rows, and `id` and `created_at` on inserted rows.
4. Validate the rebuilt snapshot with `DatabaseData`: select options, dates, URLs, emails, finite numbers, the 1,000-record limit and the 900 KiB limit. Any failure rejects the whole statement, nothing is saved, and the error names the column and row.
5. Save through `workspace_items.save` as a new revision with the caller's agent attribution. This existing path already emits `database.record.changed` webhooks for each changed record, so SQL writes need no separate notification code.

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

- `list_workspace_items(kind="database")` returns each database's metadata, fields, `record_count`, view and group-by field, without `records`. This is a breaking change for MCP clients, announced in the served guide and the `space-base` skill. Other kinds are unchanged.
- `get_workspace_item` keeps returning the full snapshot. Schema edits (fields, view, grouping, name, description, project or section) still save the whole snapshot through `save_workspace_item`, because they are rare and small. A later option is an `include_records=false` flag.
- `save_workspace_item` with records keeps working for bulk imports and for the website.
- The website keeps using its REST snapshot endpoints. Moving its row editing to the query endpoint is a later, separate change.
- `search_workspace` still matches record names, values and notes.

## Context map

Expected touch points; confirm during implementation.

- `backend/src/space/database_sql.py` — new: snapshot to SQLite, authorizer, limits, read-back and rebuild.
- `backend/src/space/workspace_items.py` — listing without records, `record_count`, save reuse.
- `backend/src/space/api/mcp.py`, `api/rest.py` — tool and route.
- `backend/src/space/guide.py` — agent guidance.
- `skills/space-base/SKILL.md` — rows through SQL, snapshot saves only for schema changes.

## Test plan

- Reads: filters, ordering, aggregates, `LIMIT`/`OFFSET`, quoted field names, the built-in name collision, checkbox and number conversion, empty and null values, `max_rows` and character truncation.
- Writes: insert with and without `id`, update, delete, `RETURNING`, read-only columns rejected, invalid select option, bad date, URL and email, the 1,001st record, the 900 KiB limit, no-op statement, stale revision 409, retry with the same `command_id`.
- Batches: later statements see earlier changes, one revision per batch, a failure at any index saves nothing and names the index, a final-snapshot validation failure saves nothing, 21 statements rejected, combined size limits enforced, `sql` and `statements` together rejected.
- Webhooks: one event per changed record, none for a no-op, none for a row inserted and deleted in the same batch, matching the existing snapshot comparison.
- Sandbox: `ATTACH`, `PRAGMA`, `CREATE`, `DROP`, transaction control, multiple statements, recursive CTE runaway, a huge `zeroblob` or string, extension loading — each rejected with no partial save.
- Authorization: read grant cannot write, revoked grant denied, cross-Space database ID rejected, archived database rejected for writes.
- Listing: database listings contain no records and report the correct `record_count`.
- MCP end to end: a real client adds a row with `INSERT … RETURNING id`, updates it by ID, and the website shows the new revision.

## Open questions

1. Should the website's row editing move to the query endpoint, so the website and agents share one row-level write path?
