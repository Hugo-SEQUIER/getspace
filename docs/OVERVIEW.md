# Space overview

The Overview page is at `/app/overview`. It loads `GET /v1/spaces/{space_id}/overview?days=90&all_periods=true` once; supported periods are 7, 30 and 90 calendar days including today, in UTC. Changing the period selects and filters the loaded snapshot in the browser without another request or loading screen. The response includes exact summary, agent and strategy aggregates for each period, because percentiles and distinct memory counts cannot be summed from daily totals. Single-period API reads remain supported. Totals show current content, while the chart, activity table, retrieval metrics and recent events use the selected period. The page refreshes every minute while visible and has a manual refresh button.

## Measures

| Measure | Definition |
|---|---|
| Documents | Current document resources, excluding generated memory and handoffs. |
| Memories | Current individual claims in the memory index, including claims whose parent memory is excluded from recall. This is not a count of memory containers. |
| Documents created | New document resources saved during the tracked portion of the period. |
| Memories added | Claim IDs added to a memory's index during a committed save. Edits that keep the same claim ID do not count as additions. |
| Document saves | Committed document revisions, including creation and updates. Idempotent retries do not add activity. |
| Retrievals | Executed context requests from REST or MCP, including resume requests, no-result responses and execution failures. Admission rejections and unauthorized requests are excluded. Workspace text search and direct resource reads are separate operations and are not counted. |
| With results | Completed context requests with at least one ranked result, divided by completed requests. Failed requests are excluded from the percentage and shown separately. |
| Typical retrieval | Median server execution duration of completed context requests, including model calls and authorization during execution. Queueing, network delivery, telemetry persistence and failures are excluded. The 95th percentile uses the same population. Strategy detail separates requested auto, lexical, semantic, prompt and resume modes. |
| Memories retrieved | Distinct claim IDs returned among ranked results in the period. Related suggestions and source documents are excluded. Retrieval does not establish that an agent used a claim in its answer. Historical reuse can include memories deleted since. |
| Agent activity | Saves and context requests attributed to the stable agent identity at execution time. Website actions are separate. Last activity is restricted to the selected period and these tracked operation types. |
| Document lines | Python `str.splitlines()` count of the current document body, captured during saving. Empty bodies have zero lines; a trailing newline does not add a line. Documents saved before measurement began stay unknown, and the page shows coverage. |
| Capacity | The Space's retained bytes and this calendar month's model requests, independent of the activity period. Storage includes revision history, and several provider requests may belong to one retrieval or extraction. A self-hosted Space has no limits unless the operator sets one, so the response reports the limits as `null`; see [Operations](OPERATIONS.md#usage-limits). |

## Storage and access

Migration `0019_overview` adds the tracking start timestamp to Spaces, nullable current line counts to resources, and a content-free `space.activity_events` table. Saves record their measurement in the same transaction as the content, before index replacement. Background memory jobs use the same save path. Archive restores do not contribute save events or invent historical activity; current resource and index totals and line counts still include restored content. Line counts for older documents appear on their next normal save.

Retrieval tracking is best effort, in a separate transaction with bounded statement and lock timeouts. A tracking failure logs a content-free warning and never turns a successful retrieval into a failed one, so activity counts can undercount while tracking fails. The time the Space was created, or the migration applied, is the earliest coverage boundary. No historical activity is fabricated, and earlier chart days are null rather than zero. The current day and the first tracked day may be partial.

The table keeps timestamps, opaque Space, agent, resource and returned-claim IDs, actor and operation types, counts, outcomes, strategy and duration. It contains no queries, excerpts, names, document contents or provider responses. These readable measurements still reveal usage and relationships; see [Data and privacy](PRIVACY.md). The endpoint answers the owner and personal API keys, never agents, and applies the same Space authorization and row-level isolation as content reads. Agent names come from the agent identity API.

`python -m space.retention` deletes activity older than 90 days, independently of its command-log window; schedule it as [Operations](OPERATIONS.md#retention) describes. Resetting or deleting a Space also removes its activity. Activity is not included in Space exports.

## Verification

`backend/tests/test_overview.py` exercises counts, line coverage, write retries, memory edits, period filtering, unique reuse, timing, failures, private and team Space isolation, viewer reads, the REST endpoint and MCP tracking against a disposable PostgreSQL. `apps/web/tests/overview.test.mjs` checks result-rate denominators, timing formatting and history gaps.
