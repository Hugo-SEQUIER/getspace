# Memory organization with a decision model

Space can organize memories using categories and sections defined by the user, plus existing projects. This is a separate write-time operation from the decision model's recall evidence assessment. The classifier never changes a claim's text, factual/procedural type, source or citation, and organizational labels do not grant access or restrict which memories can be recalled.

## User workflow

1. Open **Memory → Categories & sections**. Create categories such as Dev, Accounting or Food, and describe what belongs in each. Descriptions can include examples and exclusions. These are shared within the current authorized Space.
2. Create sections in the same dialog. Existing browser-local sidebar sections can be copied using **Sections → Use existing sidebar sections → Use in memory**. This creates a durable section and links its selected projects; syncing later adds or moves the currently selected projects without removing other memberships. Sidebar shortcuts remain browser-local. Section and project saves use separate revision-checked commands, so a failed sync may need retrying after a refresh.
3. Create or edit a project and choose its section. New classifications include a matched project's active parent section automatically.
4. With classification enabled, new claims and text edits are classified against the current catalog. A claim can have multiple categories, projects and sections. No classification call runs during recall.
5. Open a memory's details to inspect its assignments. **Edit organization → Save assignment** pins the complete assignment. Automatic edits and rebuilds preserve this assignment for the same stable claim ID. An empty manual assignment is also a deliberate correction. **Replace with automatic classification** explicitly releases the pin; a provider failure or size limit preserves the existing assignment and reports an error.
6. Existing memories are not silently backfilled, with one exception: claims left unassigned only because the catalog was too large are reclassified once in the background. **Classify now** classifies an existing unpinned claim. Category, project and section filters narrow loaded memories or current search results. **Load older memory** remains available even when filters match nothing. Text search continues to search the Space through the existing server query.

`global` means a clearly expressed lasting preference or rule that applies across projects. `project` requires at least one project assignment. `unassigned` is a scope value for insufficient context; it can coexist with useful categories or sections. **Uncategorized** means no active user category is assigned. Missing matches never default to global, and unassigned memories remain discoverable.

Renaming a label keeps its ID and updates its displayed name. Archiving removes it from future automatic choices and the active filters; it preserves history and existing assignments, which remain visible as archived or unavailable. Restoring a label makes it available again. Changing a description, archiving an item or moving a project does not rewrite existing memories. Stored section assignments describe the relationship at classification time; use explicit reclassification or a manual correction to revise them.

## Configuration and storage

Migration `0017_memory_organization` extends the `workspace_items.kind` check to allow `category` and `section`. Projects store an optional `section_id`. Categories and sections use the existing revision history, authorization, trash and restore, and idempotent commands.

```dotenv
SPACE_MEMORY_CLASSIFICATION_PROVIDER=decision
SPACE_MEMORY_DECISION_MODEL=perplexity/pplx-decider-v1.1-27b
```

Classification defaults to `disabled` and requires an enabled memory provider and a decision model on OpenRouter's Decisions API, reached with `SPACE_MEMORY_API_KEY` or `SPACE_MEMORY_DECISION_API_KEY`. It is independent of `SPACE_MEMORY_RELEVANCE_PROVIDER`. Configure it on both the API and the extraction worker: `compose.yaml` passes `SPACE_MEMORY_CLASSIFICATION_PROVIDER` and the decision settings to both. [Data and privacy](PRIVACY.md#what-leaves-the-server) lists what the decision model receives. Manual organization works without an enabled model provider. Enabling classification does not change the embedding fingerprint or require re-embedding existing memories.

REST and MCP share the same operations:

- `/items` / `save_workspace_item`: `kind=category|section`, with `data={name, description}`; project data also accepts `section_id`.
- `/memory/change` / `change_memory`: `action=classify` and a `classification` object pins a manual assignment. The object contains `scope`, `category_ids`, `project_ids` and `section_ids`. Omitting `classification` explicitly requests automatic classification by the decision model. All changes require the memory resource, entry ID, expected revision and idempotent command ID.
- Memory listing, resource reading and recall expose `classification` separately from `memory_type`. It records assignment IDs, `method` (`manual`, `decision`, `unassigned`) and a bounded status reason.

Assignments live inside the memory revisions. Archive validation checks referenced item kinds, and restore-as-copy remaps category, project and section IDs, including project parents. Existing text edits and manual assignments retain their normal authority during a merge.

## Classifier behavior and boundaries

`memory_classification.py` uses the bounded decision-model transport with the model named by `SPACE_MEMORY_DECISION_MODEL`. Each claim generates one request containing its complete index/value and the complete eligible catalog's names, descriptions and kinds. Database IDs and citations are mapped in application code and are not sent as catalog identifiers. Memory text itself can still contain private data. Provider information and the served agent guide disclose this additional processing.

Each catalog item receives an independent Noul question with a structured task, target and rubric. Project questions require a specific identity and distinguish overlapping names; category and section questions assess substantive subject matter. A separate Noul question assesses cross-project applicability. This follows the decision-model vendors' recommendation to compose narrow independent judgments in application code. [TypeSafe primitives](https://docs.typesafe.ai/introduction)

The application accepts category matches at 0.85, project matches at 0.85 and global scope at 0.90. The project threshold was lowered from 0.90 on 2 October 2026 after fresh-case runs showed wrong projects never scoring above 0.51 while correct ones were missed at 0.86-0.89; the category threshold stayed at 0.85 because a wrong category reached 0.80 (see [SPACE_MAP.md](SPACE_MAP.md#threshold-review-2-october-2026)). A selected project takes precedence over global and adds its active parent section. These thresholds are conservative operating choices, not measured guarantees. Responses must answer exactly the questions asked, with matching types, finite probabilities in range and valid token usage. No model-produced ID is trusted.

Sections are never asked about: precision matters more than coverage here, and subject-matched sections were the main source of wrong assignments. A claim receives a section only as the active parent of a project it matched; manual assignments can still name any section. A catalog of at most 32 active categories and projects combined, whose serialized state fits in 12,000 characters for the claim, is sent whole. A larger or longer catalog is no longer left unassigned. Each claim is first embedded, then asked about a shortlist of at most 24 items: items named in the claim, the nearest items by embedding (up to 6 categories, 3 sections and 10 projects), and the sections of shortlisted projects. Descriptions are trimmed in the provider copy only. Catalog item embeddings are cached per item revision in `space.workspace_item_vectors` (migration `0028_classification_scale`) and computed the first time a large catalog needs them. Catalogs above 1,000 items still return `catalog_limit`, and a claim too long to fit even with empty descriptions returns `input_limit`. Claims stored with `catalog_limit` or `input_limit` before the shortlist existed are reclassified by a one-time background job queued by the next memory write. [Space map](SPACE_MAP.md#classification-at-any-catalog-size) records the shortlist's design and its measurements on a 100-item catalog.

Requests share the existing 80-call/180-second model session and count toward the Space's model request counter, have a five-second individual timeout, and make no retries. There can be up to 24 classifier calls for a 24-claim write. A provider/protocol failure leaves new or edited claims unassigned and stops further classifier attempts in that write; quota, authorization and total-budget failures remain fatal. There is no chat-model fallback for organization. Text extraction, embeddings and recall retain their existing providers and behavior.

Authorization and resource/catalog revision checks run around provider calls and again at publication. Concurrent catalog changes abort the write rather than attaching assignments based on a stale snapshot. Names/descriptions are untrusted model input, and labels are organizational metadata only.

## Evaluation, September 17, 2026

The classifier has its own evaluation; the earlier recall benchmark does not establish classification accuracy. All calls below used synthetic claims and the same eight-item catalog (three categories, two sections, three projects). Each report retains its frozen cases, expected complete assignment sets, source hashes, raw typed responses, usage and timings. Each pass made one sequential request per claim with no warmup or retries.

| Run | Complete assignment matches | Median | p95 | Estimated USD / 1,000 claims |
| --- | ---: | ---: | ---: | ---: |
| Initial broad instructions, 20 development cases | 8/20 | 571 ms | 674 ms | $0.0598 |
| Revised structured questions, same 20 cases | 20/20 | 556 ms | 616 ms | $0.1001 |
| Revised questions, 10 fresh cases frozen before testing | 9/10 | 550 ms | 651 ms | $0.1000 |

The initial rubric mostly abstained on clear matches. The revision replaced the shared paragraph with a specific task and target definition for each label type. The fresh-case miss correctly assigned Food but gave a lasting cilantro preference a global probability of 0.80, below the 0.90 threshold, leaving its scope unassigned. This small development check supports trying the feature; it does not establish production accuracy, calibration, robustness across users, large-catalog performance or better quality than Luna. No Luna classification comparison was run.

These runs called TypeSafe's own API, before Space reached decision models through OpenRouter. Prices use returned input-token usage at Jev's rate of $0.042 per million input tokens, with output uncharged, which OpenRouter also lists for `typesafe/jev-1.13`. These are estimates for this catalog, excluding extraction, embeddings, infrastructure and taxes; larger catalogs cost more. The revised development and fresh-case runs together cost about $0.003. [TypeSafe pricing announcement](https://typesafe.ai/blog/introducing-system-one-models-and-jev)

The raw reports of these runs and the benchmark harness that produced them are not part of this repository.

The backend tests cover closed-catalog selection, thresholds, malformed probabilities, provider failures, fatal budgets, manual pins, text edits, rebuilds, authorization and revision races, and archive copying with remapped IDs (`backend/tests/test_memory_classification.py`, `test_memory_classification_workspace.py`).
