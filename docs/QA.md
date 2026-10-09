# Questions and answers

*Short answers to the questions this system actually gets asked. Each answer ends with the file that proves it.*

Convention: **Proof** names the file a claim comes from. Many measurements were taken during development; their raw result files, under `backend/evaluations/pgvector/`, `backend/evaluations/scale/` and parts of `backend/evaluations/retrieval/`, are not part of this repository, and the proof names them for the record. Where a number has no artifact, the answer says so rather than quoting a figure.

## What this is

### What is Space, in one paragraph?

A workspace plus an MCP-accessible memory, backed by one PostgreSQL database on a server the owner controls. An authorized agent calls the MCP server to write documents, tasks and memory; each memory claim is stored in a revision as readable JSON, plus one index row holding the embedding, content hashes and cue hashes. Ranking happens inside the database, behind forced row-level security. The website reads, corrects and deletes memory.

**Proof:** `README.md`, `docs/ARCHITECTURE.md`.

### Is memory just conversation history?

No. Memory is supplied explicitly through `remember_claims`, queued for extraction with `extract_memory`, or saved as a session handoff with `save_handoff`. The synchronous `remember` and `build_memory` tools remain for compatibility. Connecting an agent imports nothing.

**Proof:** `docs/MEMORY.md`.

### Who decides what gets saved?

The submitting agent or the owner. Structured claims are validated and embedded as supplied. Raw sources are saved in extraction jobs; generated claims become recallable after processing commits them. No human approval step judges their truth. The distinction between saved input and ready memory is visible in the job status.

**Proof:** `docs/MEMORY.md`.

### What happens when recall finds nothing?

It says so. Every recall returns a `found` flag; an empty result carries a `note` that names the outcome, and the operating guidance forbids the agent from inventing an answer to fill the gap. The phrase "empty results are insufficient evidence" was deliberately removed, because it invited exactly that.

**Proof:** `backend/src/space/memory.py` (`EMPTY_RECALL_NOTES`).

### Where does the guidance for agents live?

In the MCP server, not in a document. `guide.py` serves it as the instruction string, a prompt and a resource URI, so it cannot drift away from the code that implements it.

**Proof:** `backend/src/space/guide.py`.

## Storage and limits

### Where is memory stored?

In the installation's PostgreSQL database. Revisions hold the claim text as readable JSON, and `space.memory_claims` holds the embeddings (1,536 dimensions by default) and index metadata. A separate extraction worker consumes jobs from `space.memory_jobs`; no additional queue service is required. The worker uses a restricted queue login and the normal authorized runtime path for content access.

**Proof:** `backend/migrations/versions/0003_memory_brain.sql`, `backend/src/space/security/payloads.py`.

### Is anything encrypted at rest?

No. Space stores content as readable JSON in PostgreSQL, and the claim index adds embeddings, hashes and identifiers beside it. Ranking needs the vectors in SQL; the text stays in the revision. Anyone who can read the database, its disk or a backup can read every document and memory, so protect the server accordingly.

**Proof:** `docs/PRIVACY.md`, `backend/src/space/security/payloads.py`.

### Can a database export reveal what was written?

Yes, all of it. A backup made by `scripts/backup.sh` is a complete `pg_dump` of the database, with every document and memory as readable JSON. Keep backups as private as the server.

**Proof:** `docs/PRIVACY.md`, `scripts/backup.sh`.

### How much can one Space remember?

Recall is bounded per call, not by the size of the Space, so nothing caps the corpus. One lexical search selects at most 256 candidate resources from the keyed term index and ranks 32 bodies, with up to 10 additional body reads for final metadata-only matches; vector recall ranks every claim of the Space exactly and loads the selected memories, and the brain browser reads memory in pages of at most 200 and searches the database for a query. A Space larger than any of those bounds is searched, not refused. The old 512-resource recall limit and the 12,288-claim ceiling (512 x 24) it produced were removed on 14 September 2026, when candidate selection moved into SQL. There is still no compaction or expiry.

**Proof:** `backend/src/space/memory.py` (`MAX_LEXICAL_CANDIDATES`, `MAX_LEXICAL_BODIES`, `MAX_MEMORY_LIST`), `backend/src/space/workspace.py` `_candidates`, `memory_pipeline.py` (`MAX_CLAIMS`).

### Why is the corpus no longer capped?

The 512-resource limit existed only to bound the cost of reading a manifest of every resource on every recall. Keyed term fingerprints in `space.resource_terms` made candidate selection possible in SQL, so the limit and its 413 were deleted rather than raised. The serving-cost question did not disappear with it, it moved: exact vector ranking costs time linear in a Space's claim count, with no refusal to stop it.

**Proof:** `docs/MEMORY.md`, `backend/src/space/terms.py`.

### Is deletion real, or a flag?

Real removal of the index row. Staleness is a first-class state elsewhere, but a forgotten claim does not linger in the claim index. Earlier revisions of the memory still contain its text until the whole memory is deleted.

**Proof:** `docs/MEMORY.md`.

## Retrieval quality

### How good is retrieval?

Two numbers, and they are not interchangeable. The **index layer** scores `recall@10` 0.9234, MRR 0.7976 and nDCG 0.8052 on 150 synthetic claims with no stale rows. The **product layer** scores `recall@10` 0.9474 with cue expansion off at both layers on a 57-claim fixture.

**Proof:** `backend/evaluations/retrieval/report-small-space.json` (not included), `backend/evaluations/retrieval/report-isolation.json`.

### Why can an index-layer number not be quoted as product quality?

Because the product path runs the vendored Memora retriever and the evidence assessment on top of the index, and the two layers disagree. Quoting the higher of the two as "our recall" inflates the result.

**Proof:** `docs/RETRIEVAL.md`, `backend/src/space/evaluation/product.py`.

### Why did a query sometimes return nothing when matching rows existed?

Because a single global HNSW index cannot see a tenant filter. It gathers `ef_search` candidates from every tenant, then the filter discards most of them. For a tenant owning 0.065 percent of a 200,000-row table, the expected number of surviving candidates at the default `ef_search = 40` is 0.026. Serving no longer uses that index; see below.

**Proof:** `docs/RETRIEVAL.md`.

### Does raising `ef_search` fix that?

No. It went from 40 to 1000 and the mean went from 0.00 to 0.66 rows of 10, at 63.88 ms p50. Making the index exhaustive is not a fix, it is a slower way to still be wrong.

**Proof:** `docs/RETRIEVAL.md`, `backend/evaluations/pgvector/raw/scale/06_scale_underreturn.json`.

### Does `hnsw.iterative_scan` fix it?

It improves it and does not fix it. `relaxed_order` returns a mean of 8.72 rows of 10 with 46 percent of queries still short of the limit; `strict_order` returns 8.50 with 52 percent short. Both are large improvements on 0.00, and neither is a guarantee.

**Proof:** `backend/evaluations/pgvector/raw/scale/06_scale_underreturn.json`.

### Does a partial per-model index fix it?

No, and this project claimed it did before measuring properly. Under the default plan mode the partial index returned 2 rows of 10 in 30 of 30 executions, the same as the full index. It only reaches 10 when the plan is forced to be custom.

**Proof:** `backend/evaluations/pgvector/raw/small/14_partial_index_remeasure.json`.

### Then what is the fix?

Stop asking a global approximate index to answer a Space-scoped query. Serving ranks exactly: it computes the distance to every claim of the queried Space under the current embedding model, then sorts, so it cannot discard matching rows after counting them. The capacity study also proposed a btree on `(space_id, embedding_model)` and hash partitioning on `space_id`; neither was adopted.

**Proof:** `backend/src/space/brain.py` (`search_statement`), `docs/RETRIEVAL_RELIABILITY.md`.

### Can a similarity threshold filter out irrelevant results?

No tested floor separates the fixture's relevant and irrelevant results. On the product fixture every floor from 0.0 to 0.6 admitted at least one negative control, and `negative_false_positive_rate` was 1.00 at every one of those thresholds. On the index layer, the distributions overlap: relevant claims run 0.2164 to 0.8376 and the best negative-control hit runs 0.3201 to 0.7396. No single cosine floor can retain every relevant claim while rejecting every negative-control hit in that fixture. That is why recall relies on a separate evidence assessment instead of a floor.

**Proof:** `backend/evaluations/retrieval/report-threshold-small-space.json` (not included), `docs/RETRIEVAL_RELIABILITY.md`.

### Is a full result a correct result?

Not necessarily, and this is the quieter failure. With the global index, a large tenant got a full list: 10 rows of 10 at every `ef_search`, with `recall@10` of 0.160 at the default. Nothing in the response signalled that most of those rows were the wrong ones. That is why the target is written as `recall@k` **and** no silent under-return.

**Proof:** `backend/evaluations/pgvector/raw/small/02_ef_search.json`, `docs/RETRIEVAL.md`.

## Ranking

### How are results ranked?

By fusion rather than by adding scores. Vector and keyword claim candidates are combined by reciprocal rank fusion, by position, inside one candidate budget; lexical sources join the same bounded pool. The evidence assessment then decides which candidates support an answer.

**Proof:** `backend/src/space/recall_candidates.py`, `backend/src/space/ranking.py`.

### Is recency used in ranking?

No. Recency was measured and not wired. The durable class already scores a perfect MRR without any prior, so every prior variant could only lose on it; the banded tie-break was the one variant that never lost, and it gained one query reordered out of twelve. That is a direction, not an effect.

**Proof:** `docs/RETRIEVAL.md`, `backend/evaluations/retrieval/report-recency.json` (not included).

### Is `importance` used in ranking?

It is written and not weighted. The column exists, the MCP tools accept it, and nothing in ranking reads it. Declaring importance rather than counting it was deliberate: a retrieval count would need a write on every read and would rank whatever it happened to see first above everything else.

**Proof:** `backend/migrations/versions/0004_memory_importance.sql`, `docs/RETRIEVAL.md`.

### What is the `related` tier?

An opt-in, capped set of associated memories returned beside the ranked results. It appears only when the caller passes `include_related`, it is limited to five, and it cannot displace a ranked item. Cue expansion was measured as buying nothing: on the 150-claim corpus it changed no quality metric to four decimal places while the candidates rose from 40 to 104 and p50 from 11.6 to 68.9 ms.

**Proof:** `backend/src/space/memory.py` (`MAX_RELATED`), `docs/RETRIEVAL.md`.

## Scale

### Are Spaces isolated from each other?

Yes. Every Space-owned table forces row-level security keyed by organization and Space, and the restricted runtime login cannot bypass it. A self-hosted installation has one owner and normally one Space; the backend also supports organizations with several Spaces, which the website hides. Cross-owner reads and writes are covered by `test_cross_owner_reads_and_writes_are_denied`.

**Proof:** `backend/src/space/workspace.py` (`_scope`), `backend/tests/test_workspace.py`, `docs/DATABASE.md`.

### How many partitions should the claim table have?

None: partitioning was measured and not adopted. The measured query times favoured more partitions, not fewer: 2.456, 1.902 and 1.745 ms p50 at 16, 64 and 256.

**Proof:** `backend/evaluations/pgvector/raw/scale/07_partitions.json`.

### What happens when recall is over capacity?

Each API process runs up to 12 recalls and lets another 128 wait in FIFO order for at most 5 seconds. Memory writes have separate slots: 2 running and 32 waiting, also for at most 5 seconds, so a slow extraction does not occupy recall's workers. A full queue or expired wait returns HTTP 503 with `Retry-After: 1` over REST, or a busy tool error over MCP; clients should retry with backoff. The database pool still defaults to 4 connections per process. These are configurable safety bounds, not evidence of a supported load; that requires a representative load test.

**Proof:** `backend/src/space/settings.py`, `backend/src/space/admission.py`, `backend/src/space/workspace.py`.

### Does partitioning repair the post-filter?

No. With a local HNSW index forced, a 130-row tenant under-returns at every partition count, with mean rows of 0.29, 1.71 and 6.00 of 10. Partitioning would win only because the plan becomes an exact scan of one small partition, not because the index improves.

**Proof:** `backend/evaluations/pgvector/raw/scale/07_partitions.json`.

## Trusting the numbers

### Why does every number carry its layer?

Because the layers answer different questions and differ by a lot. Index-layer `recall@10` is 0.9234 on 150 synthetic claims; product-layer is 0.9474 on a 57-claim fixture. An unlabelled recall number is not usable, and quoting the wrong one is the easiest way to mislead without lying.

**Proof:** `docs/RETRIEVAL.md`.

### What is a vacuous run?

A measurement that produced zeros because nothing was measured. `report-cue-reach.json` reported every cell as zero, including a count that does not depend on the feature under test, and its own `status` field said the seeded cue hashes never reached the table. The zeros are not a result. The rule that came out of it: a metrics table needs a control arm and a non-vacuity assertion before anyone interprets it.

**Proof:** `backend/evaluations/retrieval/report-cue-reach.json` (not included).

### Why was the 10.0-row figure withdrawn?

Because the run that produced it used 7 query vectors instead of 50. The scale rig's setup script overwrote its pick accumulator per block, and the before and after counts are recorded. Seven vectors cannot measure a 46 percent failure rate, so the figure was a small-sample reading rather than a competing result. It is now 8.72 rows of 10.

**Proof:** `backend/evaluations/pgvector/raw/scale/queries_fix.json`, `backend/evaluations/pgvector/raw/scale/06_scale_underreturn.json`.

### Is the planner unstable?

Plan instability is **not demonstrated**. An earlier run appeared to show it, but that run was invalid and its own `plan_counts` attributed all 80 queries to one index. The repeat showed the planner's choice is stable within every selectivity cell, and it switched from the exact scan to the embedding index only when a tenant owned about 93 percent of the matching rows. Both plans filled the limit, so the switch was correct. Serving now forces the exact path, so this question no longer affects recall.

**Proof:** `backend/evaluations/pgvector/raw/small/12_plan_stability.json`, `backend/evaluations/pgvector/raw/small/15_plan_stability_valid.json`.

### Do we claim to be the best at retrieval?

Not against other systems. "Best" is defined here as four measurable properties: `recall@k` and nDCG on a labelled corpus, no silent under-return, bounded candidate and model-call cost, and reported p50 and p95 latency. No public benchmark has been run, so a leadership claim beyond those four is unsupported.

**Proof:** `docs/RETRIEVAL.md`.

### How do I check a number myself?

Name the file it came from, then read that file. For a measurement whose raw file is not in this repository, rerun it instead: `make evaluate-retrieval` measures retrieval strategies on the synthetic corpus in a disposable database, and `make check-database` runs the database tests. If a number has no file behind it, it does not count.

**Proof:** `Makefile`, `backend/scripts/evaluate_retrieval.py`.

## Working on the code

### Where do I start reading?

`memory.py` for the contract, `brain.py` for the SQL and the search, `workspace.py` for authorization and orchestration, `guide.py` for what the system tells agents. The test files under `backend/tests/` show the expected behaviour for each boundary.

**Proof:** `docs/ARCHITECTURE.md`.

### Why are transaction-local settings dangerous here?

`set_config(..., true)` sets a value only for the current transaction, so outside a transaction it is silently discarded. That defect broke the tuning path twice: the code appeared to configure the index and the setting was gone by the time the query ran. Settings are now asserted back with `current_setting` on the same connection.

**Proof:** `backend/src/space/brain.py` (`settings_in_force`).

### Is `ranking.py` used?

Partly. Recall uses `reciprocal_rank_fusion` to combine vector and keyword claim candidates. The other functions, for recency, priors, MMR and deduplication, are pure and tested, but only the evaluation harness calls them.

**Proof:** `backend/src/space/ranking.py`, `backend/src/space/recall_candidates.py`, `backend/src/space/evaluation/strategies.py`.

## The answer to the hardest question

### If you had to cut this to one sentence for a technical peer, what would you say?

Space stores an agent's memory as claims in readable revisions, with a vector index beside them in one PostgreSQL database, and its real engineering problem is not storing or searching memory but keeping a Space-scoped query from silently returning too few rows or the wrong ones. A shared approximate index did exactly that in measurements, so serving now ranks exactly within the queried Space; the cost of that choice grows linearly with the Space's claim count.

**Proof:** `docs/RETRIEVAL.md`, `docs/RETRIEVAL_RELIABILITY.md`.
