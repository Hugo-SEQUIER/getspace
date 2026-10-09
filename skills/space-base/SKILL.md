---
name: space-base
description: Create and maintain a custom database in Space, including its fields and records, for tables and boards. Use for "create a table", "add a field", "log these rows", "make a board", or "update the record we keep for this client".
---

# Custom databases

A database is a workspace item that holds a typed schema and its records, versioned as one snapshot. Read and change records with `query_database`, which runs SQL on one table and saves a change as a new revision. Change the schema (fields, view, grouping, name, description) by saving the whole snapshot with `save_workspace_item`. This skill does not write documents, tasks or memory.

## Before you start

- Read the connected guide once: the `memory_guide` prompt or the `space://memory/guide` resource, and resolve the Space with `workspace_info`.
- Send the returned `agent_id` on every content call.
- Use the live tool schema for field names and bounds.

## Find the database

List the Space's databases with `list_workspace_items(kind="database")`, following `next_cursor`. Listings return each database's schema and `record_count`, never its records. Find one by content with `search_workspace`, which also matches record names, record values, record notes and field names.

## Read and change records with SQL

`query_database(space_id, database_id, request)` exposes one table named `records`. Its columns are `id`, `name`, `notes`, `created_at`, `updated_at` and one column per field, named after the field. Quote a name that is not a plain identifier, such as `"Next step"`. A field whose name collides with a built-in column, such as a field called `name`, appears as `field_name`. Pass `include_schema=true` to get the exact column map.

- Read with `SELECT`, binding values to `?` placeholders through `params` rather than building SQL strings. Results are capped by `max_rows` (default 100, up to 500) and 20,000 characters; narrow the query or add `LIMIT`/`OFFSET` when a result says `truncated`.
- Write with `INSERT`, `UPDATE` or `DELETE`, passing a new `command_id` and the database's current `revision` as `expected_revision`. Omit `id` on `INSERT` to have one assigned; `INSERT ... RETURNING id` reports it. `id`, `created_at` and `updated_at` cannot be updated; Space sets the timestamps.
- Use `statements` for a small related change, up to 20 statements that run in order, see each other's changes, and succeed or fail together as one revision.
- Checkbox columns hold `0` or `1`. Select columns accept only the field's options. The whole resulting snapshot is validated before it is saved, so a rejected change saves nothing.

Only one table exists. `PRAGMA`, `ATTACH`, `CREATE`, `DROP`, transaction statements and blob generators are refused.

## Read the current snapshot before a schema change

A schema save replaces the stored snapshot, so a write built from memory destroys work. Read first with `get_workspace_item`, and keep every existing `fields[].id` and `records[].id` exactly as returned: the IDs are what the values refer to, and a changed ID orphans the data that used it.

For a new database, generate the item ID yourself: `item_id` is a caller-supplied UUID, and the server does not invent one. Send `expected_revision=0`. For an update, pass the current `revision` as `expected_revision`, because the server rejects a stale write.

## Define fields

Each field needs a stable `id`, a `name` and a `type`. Field IDs must be distinct, and field names must be distinct ignoring case. A field `name` is what the user sees, so keep it close to the user's own wording rather than inventing a scheme.

| Type | Accepts |
|---|---|
| `text` | Text up to 2000 characters |
| `number` | A finite number; a boolean is rejected |
| `select` | One of the field's `options` |
| `date` | An ISO date |
| `email` | A string with `@` and no whitespace |
| `phone` | Text up to 2000 characters |
| `url` | An HTTP or HTTPS URL with a host |
| `checkbox` | A strict boolean |

Only a `select` field takes `options`, and it must have between 1 and 30 of them, each named and at most 80 characters, all distinct. Any other type with options is rejected. A select value that is not one of its options is rejected too.

Adding a field is a schema change: it alters every record's shape, so make it when the user asks rather than to fit one new row.

## Records in a snapshot

Prefer `query_database` for records. When a snapshot save must include records, such as a bulk import, a record has a stable `id`, a `name`, an optional `notes` string and a `values` object keyed by field ID. Record IDs must be distinct, and a value key that is not a field ID is rejected.

Leave a value out, or set it to null, when the user has no value for it. An empty string is accepted and stored verbatim for every string field, and it skips the checks that apply to a non-empty value, so an empty `select`, `date`, `url` or `email` value is stored without being validated. Only `number` and `checkbox` reject an empty string. `updatedAt` is required and must be an ISO 8601 timestamp, so set it when the record's content changes and not when the snapshot is merely re-saved.

Log records the user asked for. Do not invent values, dates or contact details to fill a schema, and do not treat a document or message as a record unless the user asked for it to be stored as one.

## Views and boards

`view` is `table` or `board`. `groupBy` is optional, and when it is set it must name a field that exists and is of type `select`; any other value is rejected. The server does not require `groupBy` for a board, so do not claim that a board without one is invalid: set it when the user asks for a board and a select field can group it, and leave it unset otherwise.

## Task databases

A database with a `tasks` setting is a task list: its open rows can be planned for a day and appear in the user's Today view under Tasks. `tasks.status` is the ID of a select field, `tasks.done` lists the options of that field that close a row, and `tasks.planned` is the ID of a date field holding the day a row is planned for. `get_space_map` shows the setting with field names instead of IDs, so you can query the rows directly.

- To plan a row for a day, set its planned field to that date with `query_database`. To take it off the plan, set the field to null. To close it, set the status to one of the `done` options.
- To find the user's work for a day, select the rows whose planned field is on or before that day and whose status is not a done option, across every task database in the map.
- Add the setting only when the user asks for a database to be tracked as tasks. Create a missing select or date field in the same snapshot save as the setting, because the server rejects a setting that names a field that does not exist or has the wrong type.
- A website save keeps the setting. Keep it in every snapshot save you make, or the database stops being a task list.

## Report the result

Return the database's item ID, its new revision, and what changed: the `changes` counts from `query_database`, or the number of fields and records after a snapshot save. Report a rejected value with the field it belongs to, and never describe a partial write as complete: a snapshot either saves as a whole or not at all.

## Call shapes

Every tool below is an MCP tool. A client that surfaces MCP tools calls them as written. A client that reaches MCP through a code runtime, such as Prime Agent, discovers them with `mcp.list_tools("<server>")` and calls `mcp.call_tool("<server>", "<tool>", {…})`.

`save_workspace_item(space_id, change)` with `kind="database"`:

| Field | Type | Notes |
|---|---|---|
| `command_id` | UUID | New per change, reused unchanged on retry |
| `item_id` | UUID | Required on creation too; generate it yourself |
| `expected_revision` | int >= 0 | `0` on creation, otherwise the revision just read |
| `kind` | `database` | Cannot change after creation |
| `data` | object | The full snapshot, described below |

`data` for a database:

| Field | Type | Notes |
|---|---|---|
| `name` | string, 1-200 | Must not be blank |
| `description` | string, up to 10000 | Defaults to empty |
| `fields` | list, up to 30 | Each field is described below |
| `records` | list, up to 1000 | Each record is described below |
| `view` | `table` or `board` | Defaults to `table` |
| `groupBy` | string, up to 100 | Optional; when set, a select field's ID |
| `tasks` | object | Optional; `{status, done, planned}` as described under Task databases |

Each field: `id` (string, 1-100), `name` (string, 1-80), `type` (one of the eight types above), `options` (list, up to 30, select fields only).

Each record: `id` (string, 1-100), `name` (string, 1-200), `notes` (string, up to 20000), `values` (object, up to 30 keys, keyed by field ID), `updatedAt` (ISO 8601 string, up to 64 characters). A value is a string, a number, a boolean or null, and any string value is at most 2000 characters, including a `date`, `url`, `email` or `select` value.

The whole snapshot must stay within 900 KiB.

`query_database(space_id, database_id, request)`: `request` holds either `sql` with `params`, or `statements` (a list of `{sql, params}`, up to 20). Writes add `command_id` and `expected_revision`. Optional `max_rows` (1-500) and `include_schema`. The result has `results` (one `{columns, rows, row_count, truncated}` per statement), `database.revision` and `changes` (`inserted`, `updated`, `deleted`, or null when nothing changed).

`get_workspace_item(space_id, item_id, revision)` returns the stored snapshot, which is the input to the next schema save. `list_workspace_items(space_id, kind="database", after, deleted)` returns at most 25 databases per page, with `record_count` and no records. `set_workspace_item_deleted` archives or restores the database with its history; an archived database refuses SQL writes until restored.

## Boundaries

- Do not write a snapshot you did not read first.
- Do not regenerate field or record IDs, and do not change a field's type to fit one value.
- Do not invent values, dates or personal data to complete a row.
- Do not add a field or a record the user did not ask for.
- Do not describe a rejected snapshot as partially saved.
- Do not archive a database to hide a data problem.

## Failures and retries

Reuse the same `command_id` and payload after an uncertain write; a changed payload needs a fresh command ID. A retried `query_database` write returns the committed revision with `replayed: true` and no rows; run a `SELECT` to see the result. On a revision conflict, read the current item and rebuild the snapshot from it. A rejected value names the rule it broke, such as an unknown select option or an unknown field key: report it and ask rather than dropping the value silently.
