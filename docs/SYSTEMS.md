# Systems

A **system** is a recurring activity, such as a posting routine, an outreach campaign, an SEO routine or a development lane. Its **lab** holds the hypotheses and lessons it tests. Agents and people can try things freely in the lab, because a lab entry is never recalled. Only **verdicts** reach recall: entries promoted to Validated (it works, a rule or tactic) or Invalidated (it does not work, an anti-pattern). The code is `backend/src/space/systems.py`; the website area is `/app/systems`.

## Model

All three are packed workspace items (`space.workspace_items`, migrations `0043_systems` and `0046_system_readings`), so each change is a revision with its author, and the history of an entry is the list of its revisions.

| Item | Fields |
|---|---|
| `system` | `name`, `description`, `status` (`healthy`, `needs_attention`, `broken`, `not_measured`, `paused`), `goal_metric`, `goal_target`, `current_value`, `counter_metric`, `source_of_truth`, `review_cadence` (`weekly`, `per_cohort`, `monthly`, `quarterly`), `last_review`, `next_decision` and `playbook_id`, the ID of a document in the Space |
| `lab_entry` | `name` (the hypothesis or lesson in one sentence, at most 280 characters), `system_id`, `domain`, `status` (`testing`, `validated`, `invalidated`, `retired`), `evidence` (`untested`, `suggestive`, `measured`, `owner_call`), `why`, `apply`, `applies_to`, `source`, `captured`, `note` (what this revision changed), `uses` (`used_in`, `at`, `by`), `verdict` and `retries` |
| `system_readings` | `system_id`, `month` (`YYYY-MM`) and `readings`, oldest first and at most one per date: `date`, `value` (a number), `unit`, `note`, `source` (where the number came from) and `recorded_by` (the agent, or null for the person) |

The domains are fixed: Copy & hooks, Social, Outreach & sales, SEO/GEO, Product & positioning, Dev & engineering, Agents & automation, Video and Ops & business. A new entry starts in `testing`. Its status, uses and verdict change only through promotion, demotion and recorded uses; `save_workspace_item` does not accept any of the three kinds, and the Space map leaves them out. Full-text search finds systems and entries, not readings. A Space holds at most 5,000 active systems and entries together, and an entry keeps at most 500 uses.

## Readings

`current_value` is one sentence with no history. A **reading** is a dated number a sensor script measured, such as clicks from Search Console or replies from Gmail, so a review can compare week over week and a sensor that stopped reporting shows. `record_system_reading` takes `command_id`, `date` (at most tomorrow), `value` (a JSON number; strings, booleans, NaN and infinity are refused), an optional `unit` (40 characters) and `note` (500), and `source` (required, 200), and credits the agent that called it. A system keeps one reading per date: a second write for that date replaces it, and the month's earlier revision keeps the old number. A retry with the same command ID answers with what it wrote, even after later readings; the ID reused for another system, date, number or operation is refused. An archived system takes no new reading, and its readings stay listable.

Each system keeps one `system_readings` item per calendar month, whose ID derives from the system and the month. One item per reading would make every listing unpack every reading in the Space; one list on the system would add a system revision per reading, so a sensor write would make a person's open edit stale, and every revision would copy the whole history. A month item keeps a revision under 31 readings, and reading the Systems area unpacks one item per system and month. `list_system_readings` returns `{system_id, readings, more}` newest first, from an optional `since` date, at most `limit` (1 to 1,000, default 100); `more` says the limit left older ones out. `list_systems` and `list_verdicts` give each system `latest_reading` (`{date, value, unit}`, the latest by date) and `reading_age_days` (days since that date, never negative), both null when the system has no reading.

## Promotion and demotion

Anyone with write access, the person or their agents, can promote or demote; no model judges an entry. Promotion is refused unless the entry is in testing (or holds the other verdict), the evidence is Measured or Owner's call, the source is set and the system is active, and the refusal names what is missing.

Promotion writes a memory through the ordinary memory write (`Workspace.remember`): titled `Verdict: <sentence>`, with the label, system, domain, evidence, why, how to apply and where it applies, and topics naming the system and domain. The memory's packed metadata carries a `verdict` (label, status, evidence, system, domain, entry ID, promotion time), which stays on the memory through later revisions. The entry then records the memory ID, the label, the time and who promoted it. A retried promotion lands on the same memory, because its ID comes from the command. If the entry cannot be written, the new memory is taken back out of recall, so a refused promotion leaves nothing recallable. A promotion that changes the label replaces the memory.

Demotion moves an entry to `testing` (a retry, which adds one to `retries`) or `retired`. It first takes the verdict's memory out of recall with `Workspace.set_memory_recall`, which writes one revision with `recall.excluded=true` and keeps the claims, then writes the entry. The memory and both histories stay; `list_memories` shows the memory as excluded. A verdict cannot be edited or archived: move it back to testing first. A command ID reused for another entry, revision or move is refused.

## Recall

Recall reads only memory, so a testing or retired entry is never returned by `get_context`, and lab entries are not extracted into memory. A verdict is returned like any memory, with a `verdict` object: `label` (`works` or `does_not_work`), `status`, `evidence`, `system`, `domain`, `entry_id`, `promoted_at` and `url`, the link to `/app/systems/{system_id}?entry={entry_id}`. `RecallVerdict` in `backend/src/space/api/schemas.py` documents it.

## Usage and reuse

`record_verdict_use` appends where a live verdict was applied, with the date and the agent that recorded it; `last_used` is the latest use. A verdict counts as reused when a use falls within 30 days after its promotion. Each system's `stats` gives `entries`, `testing`, `works`, `does_not_work`, `retired`, `verdicts`, `reused_30d` and `reuse_share`, the success measure, with a 30% target.

## Interfaces

MCP: `list_systems`, `list_verdicts` (by `system_id` or `domain`), `create_system`, `update_system`, `add_lab_entry`, `update_lab_entry`, `promote_lab_entry`, `demote_lab_entry`, `record_verdict_use`, `record_system_reading` and `list_system_readings`. `update_system` and `update_lab_entry` replace every field, and `record_system_reading` replaces a reading on the same date, so they are marked destructive. The [guide](../backend/src/space/guide.py) tells agents to read verdicts before recurring work, record their use, put untested ideas in the lab rather than in memory, and promote only on evidence.

The website calls browser-session routes that are not in the published API: `GET /v1/spaces/{space_id}/systems`, `POST /v1/spaces/{space_id}/systems`, `POST /v1/spaces/{space_id}/systems/entries`, `GET /v1/spaces/{space_id}/systems/activity` (the 30 latest lab changes, each with the revision it replaced, optionally for one `system_id`), under `/v1/spaces/{space_id}/systems/entries/{entry_id}` `promote`, `demote`, `uses` and `history`, and `POST` and `GET /v1/spaces/{space_id}/systems/{system_id}/readings` (the same request and `since` and `limit` query parameters). Archiving uses the item visibility route; a month of readings is not archived on its own, it follows its system.

## Limits

- A verdict memory is a copy of its entry at promotion. Changing the lesson means moving it back to testing, editing it and promoting it again.
- A system with live verdicts cannot be archived: move them back to testing or retire them first.
- Export and restore keep systems, lab entries, verdicts and readings; a month whose system is not in the archive, or whose reading is dated outside the month, fails validation. A restored copy points at the copied system, playbook, verdict memory and entry when they were copied too, and keeps the original IDs otherwise.
