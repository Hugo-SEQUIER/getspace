# Space map

Design recorded 2 October 2026. All three delivery phases were implemented the same day, with migrations `0028_classification_scale` and `0029_memory_routes`. The evaluation corpora, reports and benchmark harnesses named below are not part of this repository; the figures are copied from them.

Implementation notes where the build differs from the design below:

- `change_memory` gained a separate `route` action (`routes_to`, where an empty list clears and pins routing) instead of extending `classify`.
- A Space-wide rule is stored with a NULL `target_item_id` rather than the Space ID, so the target keeps a foreign key to `workspace_items`.
- Explicit `routes_to` always names containers; Space-wide rules come only from automatic detection of a global-scope rule.
- The placement-rule question is asked inside the claim's existing classification request, so a claim that is not a rule costs no extra request. A detected rule costs one more request over at most 8 candidates: up to 4 named or nearest containers first, then the folders and databases of the claim's classified projects, then the remaining nearest. Folders and databases get up to 4 semantic slots each.
- Automatic routing was evaluated live on a frozen corpus; see [Routing evaluation](#routing-evaluation-2-october-2026).
- The website does not yet show the map or edit placement. Its saves now carry `project_id` and `section_id` through unchanged, so editing a placed folder or database no longer clears its placement.

## Decisions


- The map has two layers: a generated tree of the Space's containers and routing rules.
- Agents get it from a new read-only MCP tool, `get_space_map`. `workspace_info` only points to it.
- Routing is learned by the memory engine, not managed by the user. Users never edit routing notes or open a routing page in the app. Agents state placement rules through the normal memory path, and the engine attaches them to the containers they govern.
- Databases gain an optional project or section, and folders gain an optional project, so the tree can nest them.
- Projects do not get a dedicated start-here pointer, because a fixed field would be restrictive. A routing rule can say "start with document X" instead.
- Database listings stop returning records. Agents read and change rows through a SQL tool; see [DATABASE_SQL.md](DATABASE_SQL.md).
- Memory organization must stay automatic however large the Space grows. The current 32-item catalog limit is replaced by a shortlist stage; see [Classification at any catalog size](#classification-at-any-catalog-size).

## Purpose

Give every connected agent one cheap call that answers "where does this go, and where do I look first?" for the selected Space. The map routes an agent to the right project, folder or database before it reads or writes, so agents stop guessing placement, creating parallel databases or logging rows in the wrong table.

It is called a map rather than an index because "index" already names the pgvector claim index and the resource term index.

## Problem

- Every project, section, folder, database and category already carries a name and description, and owners already use descriptions as routing rules ("keep contacts in Clients & Outreach, not here"). Agents have no compact way to see them together.
- Orientation today takes one `list_workspace_items` call per kind, paged at 25 items. The `database` listing returns full snapshots with every record: in the owner's Space one call returned about 277,000 characters, mostly from one 111-row queue.
- `guide.operating_instructions` is generated per installation, not per Space, so it cannot carry a Space's layout or conventions.
- Recall answers "what do we know about X", not "which container should hold a new X". Placement rules stored as ordinary memory are found only when a query happens to match them.

## Layer 1: generated tree

Built on every read from current, non-archived workspace items. Nothing is stored, so it cannot drift.

- Sections, ordered by name. Each holds its projects, plus the folders and databases assigned directly to it.
- Projects: ID, name, status, description, and the folders and databases assigned to the project.
- Folders: ID, name, description.
- Databases: ID, name, description, record count, field names and types, view and group-by field. Records are never included.
- Containers without a section are listed last under `unsectioned`.
- Categories: ID, name, description.
- Task statuses from `task_columns`.

Document and task counts per project or folder are not in v1. Those assignments live inside each resource's JSON metadata, so counting them means reading every current revision in the Space.

### Folder and database placement

`DatabaseData` gains two optional fields, `project_id` and `section_id`. `FolderData` keeps its `section_id` and gains an optional `project_id`. The rules are the same for both kinds:

- When a project is set, the container appears under that project. Its section is derived from the project, and a stored `section_id` is ignored.
- When only a section is set, it appears directly under that section.
- The target must be an active item of the right kind in the same Space, validated the same way as project `section_id`.
- Archiving the parent leaves the reference in place. The map then lists the container under `unsectioned` with an `archived_parent` marker.
- Existing databases have neither field and appear under `unsectioned` until an agent or the user assigns them. Existing folders keep their section.

A folder's project describes where the folder belongs. It does not move or reassign the documents inside it: a document's own `project` and `folder` stay independent, as they are today. The website's project view can list the project's folders, but that change is separate.

`space-base` and `space-organize` assign a container when they create it or when the user names its project, and `space-audit` reports unassigned databases and folders.

## Layer 2: routing learned from memory

A routing rule is an ordinary memory claim that the engine has linked to one or more containers. Examples:

- "Outreach contacts go in Clients & Outreach; Outreach Experiments holds cohorts, never contacts."
- "For the infrastructure project, start with the 00 Overview document."
- "Personal Brand channels are sub-areas of one project, not separate projects."

Routing claims follow the memory lifecycle: they are live when stored, versioned, recallable, marked stale when their source changes, and removable with `change_memory` or `delete_memory`. There is no routing document, no routing editor and no new app page. A routing claim still appears in the existing memory browser like any other claim, but nobody has to manage it there.

### How a claim becomes a routing rule

Two paths, both written into the claim's `routing` assignment:

1. **Explicit.** `remember` and `remember_claims` accept `routes_to: ["<item UUID>", …]`, up to 8 containers per claim. Targets must be active projects, sections, folders or databases in the same Space. They are checked before any provider work, with the same exact-ID rules as explicit memory links: no name-based guessing. This path needs no model and works when classification is disabled. The served guide tells agents to use it whenever they save a placement rule, which is the common case because the agent saving the rule knows the target.
2. **Automatic.** When classification is enabled, each new or edited claim gets one extra Noul question, asked in the same request as its classification questions: "Does this memory state where a kind of content should be stored, looked up or kept out?" Below 0.9 the claim is not a routing rule. At 0.9 or above, the engine takes up to 8 containers from the [shared shortlist](#shortlist), restricted to projects, sections, folders and databases, plus the folders and databases under any project the claim was classified to. Each candidate gets an independent Noul question: "Does this rule govern what belongs in this container?" A candidate at 0.9 or above is attached. If none passes, the claim stays an unattached routing rule. It is shown under `global` when its classification scope is global, and omitted otherwise.

Routing never asks about the whole catalog, so its cost does not grow with the size of the Space.

A manual or explicit `routes_to` is pinned like a manual classification. Automatic edits and rebuilds keep it, and `change_memory` with `action="classify"` can replace or clear it. When an explicit target is passed, the automatic routing questions are skipped for that claim.

### Lookup index

The map must find routing claims without reading the whole memory corpus. A table of opaque identifiers indexes them:

```sql
CREATE TABLE space.memory_routes (
    organization_id uuid NOT NULL,
    space_id uuid NOT NULL,
    memory_id uuid NOT NULL,
    claim_id uuid NOT NULL,
    target_item_id uuid NOT NULL,
    origin text NOT NULL CHECK (origin IN ('explicit', 'inferred')),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id, space_id, claim_id, target_item_id)
);
```

It has the same row-level security, cascading memory foreign keys and maintenance pattern as `space.memory_node_links`. Editing, forgetting or deleting a claim rewrites or removes its rows in the same transaction. A global routing claim with no container gets one row whose `target_item_id` is the Space ID. The table reveals that a claim governs a container, but not what either says.

### What the map shows

For each container, the map shows up to 5 routing claims, ordered by explicit before inferred, then non-stale before stale, then newest first. A `global` list shows up to 10 Space-wide rules. Each rule carries its claim text, `entry_id`, memory resource ID and revision, origin and stale flag. When two current rules on the same container contradict each other, the map does not try to resolve it. It shows both, and the agent asks the user or replaces the outdated one with `change_memory`.

## Classification at any catalog size

Automatic organization is a core promise of Space, so it must not switch off when a Space grows. Today [memory classification](MEMORY_ORGANIZATION.md) sends every claim with the complete catalog of categories, projects and sections. It gives up with `catalog_limit` above 32 items, or with `input_limit` above 12,000 serialized characters. The owner's Space has 33 items (13 projects, 14 sections, 6 categories), and several project descriptions run past 1,000 characters, so new memories there are likely stored unassigned.

### Small catalogs keep today's behavior

When the complete catalog fits both limits, classification sends it unchanged. The evaluated behavior and thresholds stay exactly as they are for these Spaces.

### Shortlist

When the catalog does not fit, a shortlist stage runs first and the existing per-item Noul questions run on the shortlist only. The same shortlist supplies routing candidates, so it ranks folders and databases too. Routing always uses the shortlist, even in small Spaces, because folders and databases are never part of the classification catalog.

1. **Name mentions.** Items whose name appears in the claim text, matched case-insensitively on whole words, longest name first so "Atlas Mobile" is preferred over "Atlas".
2. **Semantic neighbors.** The items whose embedding is closest to the claim's embedding by cosine similarity, counted per kind: up to 6 categories, 3 sections and 10 projects. Per-kind slots were added after the first live evaluation, where 66 specific projects crowded broad categories such as Dev out of a single top-12 list.
3. **Parent closure.** When a project is selected, its active section is added, matching how classification already derives sections from projects.

The union is capped at 24 items, keeping all name mentions first. The Noul state still has to fit in 12,000 characters, so each shortlisted item's description is trimmed evenly to fit. Only the copy sent to the provider is trimmed; stored descriptions are untouched. Project disambiguation still works because every shortlisted project keeps its full name and the rubric is unchanged. Thresholds stay at 0.85 for categories and sections and 0.90 for projects and global scope.

### Item embeddings

Shortlisting needs an embedding for each catalog item. The text embedded is `<kind>: <name>\n<description>`.

- The vector is stored as JSON in a table keyed by item ID, item revision and embedding model. Unlike claim vectors, these are never ranked in SQL: the catalog is small enough, even at several hundred items, to load the vectors and rank them in process.
- The vector is computed the first time a large catalog needs it, in the same model session as the claim embedding, and cached against the item revision. Editing an item creates a new revision, so its vector is recomputed on next use. Item saves never call a model. An embedding failure or exhausted request budget fails the memory write, the same as a claim embedding failure.
- Embedding item text sends container names and descriptions to the configured embedding provider. The served provider disclosure and [Data and privacy](PRIVACY.md) say so.

### Pipeline order

`memory_classification.apply` currently runs before `_index_vectors` in the build, remember and edit paths in `workspace.py`. Embedding text is `index + value` only and does not depend on classification, so the order can be reversed without changing any stored vector. The pipeline becomes: embed the claims, shortlist from those vectors, classify and route, then publish. Relationship inference already runs after embedding and keeps doing so.

### Catching up existing memories

Claims stored unassigned with reason `catalog_limit` or `input_limit` failed because of the old limit, not because their content was uncertain. Space has no background authority of its own: every job runs under a verified user or agent grant, and the worker rechecks that grant before each step. The catch-up runs under the grant of the next memory write.

- After any committed `remember`, `remember_claims`, `build_memory` or `change_memory` with classification enabled, Space queues one `reclassify` job for the Space, unless one is queued, running or finished, or one failed within the last day. Queuing never fails or retries the write that triggered it.
- The job lists the Space's memory resources from readable IDs at queue time. It processes 5 memories per worker turn, checkpoints progress in `import_completed`, and yields to other Spaces between turns, the same way Memora imports do.
- For each memory, it re-reads the current revision and reclassifies only claims whose reason is `catalog_limit` or `input_limit` and that are not manually pinned. Text, other claims and relationships are unchanged. A concurrent edit wins: a revision conflict re-reads once, then leaves that memory for a later catch-up.
- Requests count toward the Space's model request counter. A provider outage, or a model request limit the operator set, fails the job after the usual retries; a later write can queue a new one after a day, and already-reclassified claims are skipped.
- `memory_job` listings for agents show only `remember` and `build` jobs, so the catch-up does not appear among an agent's own jobs.

This is a narrow exception to the rule that existing memories are not silently backfilled. Claims left unassigned for other reasons (`uncertain`, `disabled`, `unavailable`) stay as they are and can still be classified with **Classify now**.

### Evaluation before release

The earlier evaluation used an eight-item catalog and says nothing about large catalogs. Before release:

- Freeze a synthetic catalog of at least 100 items with overlapping names, long descriptions and folders and databases under projects, plus a labelled claim set written before testing.
- Measure shortlist recall (the share of expected labels that reach the shortlist), complete-assignment matches, latency and cost per 1,000 claims.
- Run the existing eight-item corpus again to confirm small catalogs are unchanged.

The frozen corpus, `classification-large-catalog-v1.json`, has 100 synthetic items and 30 labelled cases, written before any live call. Its serialized catalog is about 22,600 characters, so every case takes the shortlist path. The report adds per-field matches and shortlist recall. A live run sends synthetic text only, to TypeSafe and to the embedding provider.

Release targets are proposed, not measured: shortlist recall of at least 0.98, and complete-assignment matches no worse than the small-catalog fresh-case run (9/10). If shortlist recall falls short, raise the semantic neighbor count before changing thresholds.

#### First live run, 2 October 2026

Report (`classification-large-catalog-report-v1.json`), run with a single top-12 neighbor list before per-kind slots were added: 11/30 complete matches, shortlist recall 0.88, median 375 ms, p95 447 ms, about $0.21 per 1,000 claims in TypeSafe input tokens. It does not meet the targets. The failures have two separate causes:

- All 12 recall misses were broad categories (Dev 7 times, Marketing, Hardware, Music) pushed out of the top 12 by specific projects. Per-kind slots address this; they have not yet been measured live.
- 8 cases had the correct project in the shortlist but below the 0.90 project threshold, because the claim described the project ("this week's long run for the April marathon") without naming it or its codebase. That is the existing project rubric's behavior, not a shortlist effect, and it would apply to a small catalog too.

Two labels in the frozen corpus are questionable: "I do not eat cilantro" was labelled unassigned, although the rubric treats a lasting personal preference as global, and a dental appointment classified as Health is defensible. The corpus stays frozen. After the per-kind change, the next measurement must use fresh cases written before the run, because this corpus has now informed a design change.

#### Fresh-case run with per-kind slots, 2 October 2026

Precision comes before recall: the project rubric stays strict, so a project is assigned only when the claim names it or its codebase. Fresh cases (`classification-large-catalog-holdout-v2.json`) were written on the same 100-item catalog after the per-kind change and before the run, including two traps ("an atlas of European rivers", "supernova").

Report (`classification-large-catalog-holdout-report-v2.json`): 23/30 complete matches, **shortlist recall 1.0**, median 394 ms, p95 502 ms, about $0.27 per 1,000 claims in TypeSafe input tokens.

| Labels | Assigned | Correct | Precision | Recall |
| --- | ---: | ---: | ---: | ---: |
| Projects | 22 | 22 | 1.00 | 0.88 |
| Categories | 20 | 19 | 0.95 | 0.95 |
| Sections | 26 | 23 | 0.88 | 0.96 |

Both traps stayed unassigned. Every expected label reached the shortlist, so none of the 7 mismatches is a shortlist miss:

- 3 cases added the Engineering section ("Internal software engineering projects") to coding claims about Products or Community projects. Sections may match by subject, not only as a project's parent; that is the existing section rubric, and the Engineering item would be present in a whole catalog too.
- 2 cases left a named project unassigned (Nova Business; atlas-web and atlas-ios together). These are recall misses; no wrong project was assigned.
- 1 case added Family to a wedding budget, and 1 missed Sales for a course price.

The shortlist recall target is met. Complete matches (77%) are below the small-catalog fresh-case run (9/10), but the gap comes from the existing rubric rather than shortlisting, and a 10-case run is too small to compare closely. Whether sections should stop matching by subject, to raise section precision, was decided the same day: yes.

#### Sections only through projects, 2 October 2026

Sections are no longer asked about; a claim gets a section only as its matched project's parent. Re-run on the same fresh cases (report `classification-large-catalog-holdout-report-v3.json`): 26/30 complete matches, shortlist recall 1.0, project precision 1.00 (recall 0.88), category precision 0.95 (recall 0.95), **section precision 1.00** (recall 0.92), median 381 ms, about $0.20 per 1,000 claims. The remaining section misses follow the two named projects left unassigned. These cases had already been seen once, so this run confirms the change rather than serving as a fresh holdout.

## Routing evaluation, 2 October 2026

Corpus (`routing-v1.json`), written before any live routing call: the 100-item classification catalog plus 12 databases and 10 folders with overlapping names (Outreach Experiments, Course Students, Signal Notes Ideas and Issues, Atlas Specs and Runbooks). 40 cases: 18 rules naming containers, 2 describing them, 2 global rules, 10 lookalike facts that mention a container, 4 traps that name one container and exclude another, 2 rules naming containers that do not exist, and 2 more named rules.

Report (`routing-report-v1.json`), thresholds 0.9 for detection and 0.9 per target:

| Measure | Result |
| --- | ---: |
| Complete matches | 36/40 |
| Rule detection precision / recall | 1.00 / 0.90 |
| Target precision / recall | 1.00 (27/27) / 0.87 |
| Facts given a target | 0 of 10 |
| Rules naming a missing container given a target | 0 of 2 |
| Global rules | 2 of 2, no false global |
| Median / p95 latency per claim | 580 ms / 685 ms |
| TypeSafe input tokens | about $0.27 per 1,000 claims |

Every miss is a recall miss. Three rules scored 0.81-0.89 for detection, below the 0.9 bar, and one rule that described its folder ("the billing app's runbooks folder") had the right candidate judged below 0.9. The highest detection score on a fact was 0.45, so a lower detection bar might recover rules, but choosing it from this corpus would tune on the evaluation; any change needs fresh cases. The thresholds stay at 0.9 because precision comes first.

## Threshold review, 2 October 2026

The user asked to recover the recall the 0.9 thresholds were costing without giving up precision. Two fresh corpora were written before any call on them: `routing-v2-fresh.json` (30 cases) and `classification-large-catalog-holdout-v4.json` (30 cases, including name traps such as "Orion's constellation" and "bossa nova"). The harnesses record raw probabilities, so lower thresholds were replayed offline on these and on every earlier report.

At the previous thresholds the fresh runs had precision 1.00 for every label kind (routing: 26/30, detection recall 0.82; classification: 26/30). Margins between the highest-scoring wrong label and the lowest-scoring correct one decided each threshold:

| Threshold | Highest wrong score seen | Correct scores missed at 0.9 | Decision |
| --- | ---: | --- | --- |
| Placement-rule detection | 0.45 (a fact) | 0.81, 0.88, 0.89 | 0.9 → **0.85** |
| Container target | none above 0.9 changed | none | stays 0.9 |
| Project | 0.51 | 0.86, 0.89 | 0.9 → **0.85** |
| Category | 0.91 and 0.80 | 0.80, 0.82 | stays 0.85 |
| Global scope | not measured separately | n/a | stays 0.9 |

Replayed with the new thresholds, precision stays 1.00 for projects, sections and routing on all four runs; project recall rises to 0.96 on the earlier cases (from 0.88) and routing detection recall to 0.97 and 1.00. Category precision is unchanged at 0.95 on the earlier cases, from the Family label that already cleared 0.85. These corpora are small; the margins, not the 1.00 figures, justify the change.

## Interface

### MCP `get_space_map`

```
get_space_map(space_id, agent_id=None, max_chars=12000) -> SpaceMap
```

It is read-only and idempotent, makes no model calls, and is authorized like `list_workspace_items`. `max_chars` ranges from 4,000 to 20,000; the ceiling matches `get_context`.

```json
{
  "space": {"id": "…", "name": "Private Space"},
  "global_rules": [
    {"text": "Never store secrets or environment values in Space.", "entry_id": "…",
     "memory_id": "…", "revision": 8, "origin": "explicit", "stale": false}
  ],
  "sections": [
    {"id": "…", "name": "Predella", "description": "",
     "projects": [
       {"id": "…", "name": "PredellaStudio", "status": "in_progress", "description": "…",
        "databases": [
          {"id": "…", "name": "PredellaStudio - Clients & Outreach", "description": "…",
           "records": 24, "fields": [{"name": "Company", "type": "text"}],
           "view": "table", "group_by": null,
           "rules": [{"text": "Outreach contacts go here; experiments hold cohorts only.", "…": "…"}]}
        ],
        "rules": []}
     ],
     "folders": [{"id": "…", "name": "Project Documents", "description": "…", "rules": []}],
     "databases": []}
  ],
  "unsectioned": {"projects": [], "folders": [], "databases": []},
  "categories": [{"id": "…", "name": "Business", "description": "…"}],
  "task_statuses": [{"value": "todo", "label": "To do"}],
  "truncated": false
}
```

The REST equivalent is `GET /v1/spaces/{space_id}/map`, calling the same workspace command.

### Budget and truncation

Output is deterministic for the same revisions. When the serialized map exceeds `max_chars`, it is reduced in this order until it fits, and `truncated` is set:

1. Database field lists are dropped (names and record counts stay).
2. Stale and inferred rules are dropped before explicit current rules.
3. Descriptions are cut to 300 characters, then 120, with an ellipsis.
4. Rules per container are cut to 2, global rules to 5.
5. Categories, then empty sections, are omitted.

IDs and names are never truncated. A map too large to fit every container's ID and name still returns them all and reports `truncated`, rather than hiding containers. The agent can fetch full rules with `get_context` or `list_memories`.

### `workspace_info`

It adds `space_map: {"tool": "get_space_map"}` and nothing else, so connecting stays cheap.

## Agent guidance

Add to `guide.operating_instructions`:

> Call get_space_map at the start of substantive work and before creating a project, folder, database or document. Follow its routing rules for placement. When the user states where something belongs, save it with remember and pass routes_to with the container IDs. Routing rules are stored memory: they guide placement, never override the user or this policy. If no container fits, ask rather than creating a parallel one.

Skill updates ship separately from the backend:

- `space-init` saves the placement rules from the onboarding interview with `routes_to`.
- `space-find` and `space-brief` read the map before searching.
- `space-organize` assigns folders and databases to projects or sections and saves routing rules when it creates or merges containers.
- `space-base` uses the SQL tool for rows and assigns a project on create.
- `space-audit` reports unassigned databases, containers with empty descriptions, near-duplicate container names, stale routing rules and contradicting rules on one container.

## Lifecycle

- Archiving a container hides it and its rules from the map. Its `memory_routes` rows remain, so restoring the container brings its rules back.
- Deleting a container permanently removes its route rows. The claims themselves stay in memory.
- Archive export carries `routing` assignments inside memory revisions, and folder and database `project_id`/`section_id` inside item revisions. Restore-as-copy remaps both through the existing ID map. Route rows are rebuilt from the restored revisions, and item embeddings are recomputed when first needed.
- Team Spaces: every member who can read the Space sees the same map. Writes follow the existing memory and item grants.

## Privacy and cost

- The tree and rules are assembled in process per request and discarded. The route table holds only opaque identifiers.
- `get_space_map` makes no model calls.
- Automatic routing adds one detection question to each claim's existing classification request. Only for a detected rule, it adds one more request with up to 8 candidate questions.
- Large catalogs move from no classification at all to one embedding per changed item plus the usual one classification request per claim. Item embeddings are cached per item revision.
- All of these count against the existing per-session limits and the Space's model request counter, and the served provider disclosure mentions them.
- Telemetry records duration and item counts only, never names, descriptions or rule text.
- Cost risk: record counts and field lists require reading each database snapshot (up to 900 KiB). Measure on a Space with 50 databases at the 1,000-record limit. If it is too slow, store `record_count` and the field summary in the item revision's metadata at save time.
- Rules are read per memory revision, at most 5 per container and 10 global, so cost scales with the number of containers shown, not with the size of memory.

## Context map

Expected touch points; confirm during implementation.

- `backend/src/space/space_map.py` — new: tree assembly, rule selection, budget reduction.
- `backend/src/space/memory_routing.py` — new: `routes_to` validation, detection question, candidate questions.
- `backend/src/space/catalog_shortlist.py` — new: name mentions, item embeddings, semantic neighbors, parent closure, description trimming.
- `backend/src/space/memory.py`, `memory_pipeline.py`, `memory_classification.py` — `routes_to` input, `routing` assignment, pinning, shortlist path for large catalogs.
- `backend/src/space/workspace_items.py` — folder `project_id`, database `project_id`/`section_id`, single-transaction read of all current items, item embedding after save.
- `backend/src/space/workspace.py` — `info` pointer, embed-before-classify order, route-row maintenance in memory writes and deletes.
- `backend/src/space/jobs.py`, `job_worker.py` — reclassification job for `catalog_limit` and `input_limit` claims.
- `backend/src/space/api/mcp.py`, `api/rest.py` — tool and route.
- `backend/src/space/guide.py` — agent guidance and provider disclosure.
- `backend/src/space/archive_schema.py`, `restore_merge.py`, `restore_publish.py` — ID remapping and route rebuild.
- `backend/migrations/versions/0028_classification_scale.sql` and `0029_memory_routes.sql` — the item-embedding cache, the reclassification job kind, `memory_routes` and their policies.
- `skills/space-*` — workflow updates above.

## Test plan

- Tree: ordering, project/section nesting for folders and databases, unsectioned and archived-parent cases, archived items hidden, empty Space, records never present.
- Folder and database placement: valid and invalid targets, project overrides section, archive and restore of the parent, documents in a folder keep their own project.
- Shortlist: small catalogs bypass it and produce the same requests as today; name mentions always kept; semantic neighbors ranked from cached vectors; parent sections added; 24-item cap; descriptions trimmed only in the provider copy; missing or outdated item vectors recomputed; embedding failure on item save does not fail the save.
- Pipeline order: vectors are identical before and after moving classification behind embedding.
- Catch-up job: only `catalog_limit` and `input_limit` claims are reclassified, pinned claims are skipped, a concurrent edit wins, quota exhaustion pauses and resumes.
- Explicit routing: valid targets, cross-Space and wrong-kind targets rejected before provider work, more than 8 rejected, pin survives rebuild, `change_memory` replaces and clears it.
- Automatic routing: detection threshold, candidates from the shortlist and project children, 8-candidate cap, provider failure leaves the claim unrouted without failing the write, disabled classification makes no calls.
- Large-catalog evaluation: the frozen 100-item corpus meets the release targets, and the eight-item corpus is unchanged.
- Route index: rows match stored assignments after create, edit, forget, delete and restore; RLS denies other Spaces.
- Map rules: ordering, per-container and global caps, stale flag, contradicting rules both shown.
- Budget: each reduction step triggers in order; IDs and names survive; repeated reads return identical output.
- Authorization: read grant for the map, revoked grant denied, cross-Space IDs rejected.
- Performance: 1,000 items, 50 full databases and 10,000 claims within an agreed latency budget.
- MCP end to end: a real client calls `workspace_info`, then `get_space_map`, saves a placement rule with `routes_to`, and a fresh session places a new row in the database that rule names.

## Delivery plan

Agreed on 2 October 2026: each phase ships and is verified on its own before the next starts.

1. **Classification at any catalog size.** Item embeddings, the shortlist, embed-before-classify order, the catch-up job and the large-catalog evaluation. Needs no new tools, and restores automatic organization for Spaces past 32 items.
2. **SQL for databases.** `query_database`, record-free database listings and the `space-base` skill update; see [DATABASE_SQL.md](DATABASE_SQL.md).
3. **Space map.** Folder and database placement, `get_space_map`, `routes_to`, automatic routing on the phase 1 shortlist, the route table, guidance and skill updates.

Each phase got its own migration and its own verification.