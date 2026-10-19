# Memory node links and inferred relationships

## Purpose
Connect contextual memory nodes, not merely similar claims. An explicit `What I like in my work` → `My work` connection creates a reverse backlink without an AI veto. Inferred links remain separately identified, content-dependent suggestions.

## When to use
Use explicit connections when the agent or user knows the target node. Use decision-model inference for discoverable contextual relationships, not to resolve an ambiguous node name silently. The recall assessor and website shared-category graph are unchanged.

## Context map
- `backend/src/space/memory.py` — explicit link requests and inferred relationship schema.
- `backend/src/space/memory_node_links.py` — exact-ID, same-Space target validation before model work.
- `backend/src/space/workspace.py` — create/update orchestration, transactional persistence and authorized navigation.
- `backend/src/space/memory_relationships.py` — bounded candidates and independent decision-model pair judgments.
- `backend/src/space/memory_link_index.py` — derived explicit/inferred navigation indexes.
- `backend/src/space/memory_snapshot.py` — dependencies checked before provider disclosure and commit.
- `backend/src/space/restore_merge.py` — origin-aware two-pass restore with final-content validation.
- `backend/migrations/versions/0024_node_links.sql` — explicit node table, inferred text pins and scoped cascading resource FKs (extends `0023_internal_table_rls`).

## Workflow / behavior
### Explicit connections
`remember` and `remember_claims` accept `links: [{"resource_id": "<existing memory UUID>", "relation": "expands_on"}]`. Up to 20 distinct target nodes are allowed. Relations are `related` (default) or directional `expands_on` (detail → subject). Node identity is the memory resource UUID, not a claim ID or a title. Duplicate, self, nonexistent, non-memory and cross-Space targets are rejected before provider work. Several plausible subjects require choosing their exact IDs explicitly; there is no name-based guessing.

To replace a node's explicit connections, call MCP `set_memory_links(space_id, change={command_id, resource_id, expected_revision, links})` or `PUT /v1/spaces/{space_id}/memories/{resource_id}/links` with the same change body. The write grant is required. It makes no classification, embedding or generation calls. `links: []` removes explicit links only. Command retries return the original committed revision.

Explicit links are stored in the memory record's `links` field. They survive text, metadata, classification and claim edits, even removal of all claims. Deleting a node cascades its index edges. Excluded nodes are hidden from navigation. Source staleness does not delete intentional node connections; a connection is not an assertion that its content is current.

### Inferred suggestions
1. Preserve authored text. Classify claims against the project/category/section catalog when `SPACE_MEMORY_CLASSIFICATION_PROVIDER=decision`, then embed the claim once.
2. With `SPACE_MEMORY_RELATIONSHIP_PROVIDER=decision`, reuse the vector to search the current same-model claim index. Exclude the whole incoming resource in SQL before ranking. Take a bounded pool of 25, filter excluded/source-stale nodes, and assess up to five eligible existing claims. There is no whole-document candidate or unbounded refill.
3. Send complete claim pairs within 12,000 serialized state characters. Each pair has independent `noul` questions for a concrete contextual relationship and for detail-to-subject direction. A relationship requires confidence ≥0.9. A confident expansion becomes `expands_on`; otherwise a confident concrete relationship becomes `related`. A weak pair does not suppress a confident neighbor. There is no chat fallback. This threshold is policy, not a measured accuracy guarantee.
4. Store judgments, confidence, endpoint text hashes and status with the claim. Status is `matched`, `no_match`, `unavailable`, `disabled` or `input_limit`. Authorization, quota and snapshot failures abort; provider unavailability is recorded without losing the authored text. Commit the memory, its vectors and the navigation indexes atomically.

New inferred edges are bound to both assessed claim values, not entire resource revisions. Metadata-only revisions and unchanged re-embedding preserve them; changed/forgotten endpoint text invalidates them. Legacy links without hashes retain their conservative target-revision pin. The stored observed revision remains historical; use navigation for currently valid links. Explicit edges take precedence over inferred edges for the same directed node pair; the stored inferred judgment remains available, and can become visible again after explicit removal if its content is still valid.

### Reads and restore
MCP `get_memory_links(space_id, resource_id, limit=50)` and `GET /v1/spaces/{space_id}/memories/{resource_id}/links?limit=50` return `links`, `backlinks` and `has_more`. Entries expose `origin: explicit|inferred`, the other `resource_id`, its current `revision` and the original edge `relation`. Inferred entries also have focal `entry_id` and other `related_entry_id`; explicit node entries set both to null. Backlinks reverse navigation, not the meaning of `expands_on`. Reads make no provider calls. Visibility is filtered before the limit, so hidden neighbors do not hide a later eligible backlink. Each direction is limited to 50; this version has no continuation cursor.

Restore distinguishes live retained judgments from archived judgments before remapping. It validates final source/target content rather than rebinding an old judgment to a retained human edit. Live revisions never receive archive offsets blindly. Explicit references remap IDs, not content judgments; retained live `links`, including an intentional empty list, are authoritative. Missing archive targets are dropped. Both indexes rebuild after all current records exist.

## Decisions and constraints
- Links are connections, not citations, factual proof, merges or a single-parent tree. Multiple connections are allowed.
- There is no frontend backlink UI, inference quality benchmark, historical backfill, background relinking or deferred enrichment queue in this change. Inference/classification remain synchronous; large claim batches retain their existing end-to-end deadline risk.
- Candidate selection is bounded, not exhaustive. `no_match` does not prove that no useful contextual node exists. Same-write/same-resource inferred links are not discovered; use explicit links between committed nodes.
- Provider disclosure and commit recheck access and consulted dependency revisions. No cross-Space model candidates or links are allowed.
- Index rows expose opaque graph topology, relation labels and inferred value hashes, but no text, confidence or status. Explicit topology is indexed independently of claim rows and model configuration.
- Explicit links need no decision-model setting. Decision-model classification and inference remain independently opt-in, and they send claim text to the decision model, an external service; see [Data and privacy](PRIVACY.md#what-leaves-the-server). `compose.yaml` passes `SPACE_MEMORY_RELATIONSHIP_PROVIDER` and the decision settings to the `api` and `worker` services; restart them after changing `.env`.

## Verification / maintenance
From `backend/`, run `SPACE_RUN_DATABASE_TESTS=1 uv run --no-env-file --locked pytest tests/test_explicit_memory_links.py tests/test_memory_relationships.py tests/test_memory_relationships_workspace.py tests/test_memory_link_index.py tests/test_memory_node_index.py tests/test_memory_links_archive.py tests/test_memory_links_api.py`. These use disposable PostgreSQL and fake providers. Recheck authorization, restore provenance, content pins, deletion and pre-limit visibility when changing the contract. Mechanics tests do not establish real Jev precision or recall.
