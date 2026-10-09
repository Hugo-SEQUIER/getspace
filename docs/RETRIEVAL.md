# Retrieval quality: what we measure

The contract of the retrieval-quality work, started on 12 September 2026. Product context is in [Memory](MEMORY.md). The serving default is **exact per-Space and per-model ranking**, followed by an independent evidence assessment; [Retrieval reliability](RETRIEVAL_RELIABILITY.md) records that decision, its measurements and the remaining limits. The scale and ANN experiments below describe the earlier investigation that led to it. They ran on disposable local pgvector containers with synthetic data; the raw result files they cite, under `backend/evaluations/pgvector/` and `backend/evaluations/scale/`, are not part of this repository.

## Why recall ranks within one Space

One PostgreSQL database can hold many Spaces, each with its own memory. A self-hosted installation usually serves one owner, but the analysis below, made for thousands of Spaces in one database, explains why recall ranks exactly within one Space.

Model, with 4000 tenants over 90M rows: one tenant owns 0.025 percent of a global HNSW index, so an `ef_search = 40` scan examining 40 candidates expects `40 * 0.00025 = 0.01` rows belonging to the queried tenant.

Measured on a 200,000-row, 1,000-tenant rig where the queried tenant owns 130 rows (0.065 percent) and 129 of them carry the query's embedding model, for a limit of 10. Every row is 50 queries with 10 timing repeats, from `backend/evaluations/pgvector/raw/scale/06_scale_underreturn.json`:

| Setting | Mean rows returned | Range | p50 |
|---|---|---|---|
| `ef_search = 40`, iterative scan off, HNSW forced | 0.00 | 0-0 | 3.98 ms |
| `ef_search = 200`, iterative scan off, HNSW forced | 0.14 | 0-2 | 7.29 ms |
| `ef_search = 1000`, iterative scan off, HNSW forced | 0.66 | 0-3 | 63.88 ms |
| `iterative_scan = relaxed_order` | 8.72 | 4-10 | 62.84 ms |
| `iterative_scan = strict_order` | 8.50 | 4-10 | 52.50 ms |
| `relaxed_order`, `max_scan_tuples = 1000` | 1.02 | 0-7 | 6.20 ms |
| `relaxed_order`, `max_scan_tuples = 5000` | 3.04 | 0-8 | 9.07 ms |
| `relaxed_order`, `max_scan_tuples = 200000` | 8.72 | 4-10 | 55.42 ms |
| exact scan through the primary key, the control | 10.00 | 10-10 | not timed |

The shortfall is near total when the HNSW index serves the query, and raising `ef_search` alone does not fix it: ef_search 40, 200 and 1000 return 0.00, 0.14 and 0.66 rows of 10. Iterative scan improves this a great deal but **does not fix it**. `relaxed_order` still leaves 46 percent of queries short of the limit, and `strict_order` 52 percent. Only the exact scan fills the limit on every query, and it is the control arm without which this table would be vacuous.

`max_scan_tuples` has its optimum at the shipped default. At 1000 and 5000 the mean falls to 1.02 and 3.04 rows; raising it to 200000 changes nothing from 20000. So the default is already the right value and is not a tuning lever.

A global index also cannot be repaired with a composite index: pgvector 0.8.2, the version we run, rejects multicolumn HNSW. Whether a later version accepts it is unverified.

The plan the planner actually chooses changes the picture and is not yet settled for us. With a plain constant tenant predicate the planner preferred the primary key at 20k and 200k rows, which returns the exact answer but must read every row of that tenant and sort by distance, so its latency grows with tenant size. Our production predicate is not a constant: the policy adds a correlated `EXISTS` against `space.spaces`, and the query runs under forced row level security as the restricted role. That shape is being measured, because either plan is a problem at scale - one can under-return, the other is linear in the tenant's memory.

Either way the failure grows with tenant count: a single global index filtered after the scan fails harder as the product succeeds. Combined with the `found=false` contract, an under-filled result becomes a confident wrong answer to the user.

## Cue expansion: measured, and dormant

Expansion adds claims that share a generated cue with a matched claim. Measured on the 150-claim corpus it changed recall@10, MRR@10 and nDCG@10 by zero to four decimal places while raising the candidate count from 40 to 104 and multiplying p50 by 5.9 (11.6 to 68.9 ms). On the product fixture recall and MRR were better without it. Twelve queries built from a claim's own cue words gained nothing, and the reason is structural: the cue is generated from the claim's own `index` and `value`, so it cannot carry an association the embedding does not already see.

It is therefore off by default and reachable only through `include_related`, and it returns a separate `related` tier that cannot displace a ranked result.

Two open questions, both unmeasured, both recorded here so they are not mistaken for settled:

- **Reach at scale.** `expand` selects with `ORDER BY memory_id, entry_index LIMIT 64`, so once more than 64 claims share a cue, which survive is arbitrary with respect to relevance. Reach should therefore degrade as a corpus grows. The experiment to isolate this was run but its seeding was defective: no seeded row carried the planted cue, so the zeros are a broken fixture and not a null result. The question remains open. `overlap` ordering is the principled candidate; `vector` ordering is not, because an associated claim is by definition not similar to the query.
- **Value of associations.** No corpus yet exists in which a shared cue links claims that embeddings do not link, so the benefit case is untested rather than disproved.

## Recency and age: measured, and not wired

Asked whether age should be a ranking parameter. Measured on 20 temporal queries, 12 asking for the current state of a superseded fact and 8 asking for a durable fact with a newer chatter claim as the trap:

| variant | current MRR@10 | durable MRR@10 |
|---|---|---|
| off (shipped) | 0.5417 | 1.0000 |
| global multiplier, 180 d, weight 1.0 | 0.9583 | 0.5042 |
| global multiplier, 30 d, weight 1.0 | 0.1783 | 0.1436 |
| tie-break inside a 0.02 score band | 0.5833 | 1.0000 |

The durable class already scores perfectly without any prior, because the embedding ranks the durable fact above the newer chatter on its own. So there is no headroom to win there, and every prior can only lose. A global multiplier loses on at least one class at every setting. The banded tie-break is the only variant that never loses, and it gains 0.0416 MRR on the current-state class - which is **one query reordered out of twelve**. That is a direction, not a proven effect, and nothing in the product weights by recency.

A retrieval count was considered and rejected: it would require a write on every read, which recall does not do today, and it ranks whatever it happened to see first above everything else. Importance is declared by the writer instead, and is stored but not weighted.

Plan stability was a suspected production risk, and it is **not demonstrated**. An earlier run recorded 504.8 ms and 52.1 ms p50 for the same query at 50,000 claims, but that run is marked invalid for other reasons and its own `plan_counts` attributes all 80 queries to the embedding index, so the timing difference cannot be attributed to a plan. The measurement was repeated: on a 20,000-row rig the planner's choice was stable **within every selectivity cell**, and it switched from the primary-key exact scan to the embedding index only when the tenant owned about 93 percent of the matching rows, 18,599 of 20,000 (`backend/evaluations/pgvector/raw/small/12_plan_stability.json`). Both plans filled the limit, so the switch is correct rather than a hazard. Treat any statement that the planner picks one path on one run and another on the next as unverified.

## The claim we are building toward

"Best" is defined here as measurable, not asserted:

1. Highest recall@k and nDCG@10 on a labelled corpus of paraphrase queries.
2. No silent under-return at the search stage: when at least the requested candidate count exists in scope, search fills that count or reports the shortfall. Relevance filtering and negative-control behavior are measured separately.
3. Bounded cost: a fixed cap on candidate claims read and model calls made.
4. Measured latency, reported as p50 and p95.

The reports in `backend/evaluations/retrieval/` record these metrics on the synthetic fixture. We cannot claim leadership against other systems until we run a public benchmark, which is a separate decision.

## Measured facts about the current implementation

Probed on 12 September 2026 against PostgreSQL 17.6 with pgvector 0.8.2, before exact ranking became the serving default. The bullets about HNSW settings and post-filtering describe that earlier code; serving now computes exact distances within the Space and does not use the HNSW index.

- The HNSW index is built with default `m=16`, `ef_construction=64`.
- `hnsw.ef_search` is the default 40 and is never set.
- `hnsw.iterative_scan` is `off`.
- `brain.search` applies a filter (`embedding_model`) and the row-level security predicate for organization and Space. An HNSW scan post-filters those rows, so a query can return fewer rows than its limit even when matching claims exist. With the empty-recall contract this can report "nothing found" wrongly.
- `brain.expand` orders cue-expanded claims by `memory_id, entry_index` and not by relevance, so related claims arrive in an arbitrary order.
- `ORDER BY embedding <=> query, claim_id` breaks ties on `claim_id`, so equal scores order deterministically.
- No index exists on `source_resource_id` or `embedding_model`.
- Candidate selection for sources now happens in SQL. `Workspace._candidates` matches the keyed HMAC fingerprints stored in `space.resource_terms` for each resource's current revision, field 0 being the title with its topic labels and field 1 the body, and scores by how many query words each field holds. No payload is read to select a candidate. The first pass that ranked titles and topic labels only, and so searched bodies only for sources whose metadata already matched, was removed on 14 September 2026.
- Ranking then adds metadata BM25 over the candidates to body BM25 over the leading ones, so a source that matches on its title or topic labels alone is returned, and so is a source that matches on its body alone. The second gate, which dropped a title-only or body-only match, is gone. The measurement recorded for that gate was taken on the code as it stood before the change, one document per case with single-token queries: a token in both the title and the body returned the document, while a token only in the title, or only in the body, returned nothing. A term present in every document still returns nothing, because it carries no BM25 weight. Both the pre-change result and the pre-change note are recorded in `backend/evaluations/scale/raw/recall_manifest_cost.json` under `retrieval_gates`, which now describes history rather than the shipped behaviour.
- An excluded resource is still never a candidate and is never returned. A handoff stays lexically searchable as source evidence; only a generated memory takes part in the claim index.

## Interfaces

### `space.ranking` - pure functions, no database and no model call

Recall uses `reciprocal_rank_fusion` (`recall_candidates.py`). The other functions are proven in tests and the evaluation harness but are not wired into recall.

- `reciprocal_rank_fusion(rankings, k=60) -> dict[str, float]`
- `recency_weight(updated_at, now, half_life_days) -> float`
- `apply_prior(scores, weights, *, floor) -> dict[str, float]`
- `mmr_select(vectors, scores, limit, diversity) -> list[int]`
- `dedupe_by_memory(items, limit) -> list`

### `space.evaluation` and the CLI

```sh
uv run --directory backend --locked python scripts/evaluate_retrieval.py \
    --corpus evaluations/retrieval/corpus.json --strategy <name> [--json out.json]
```

For index fixtures, `--geometry unrelated` is the default stale-row distribution. Use `--geometry similar` for same-domain stale rows or `--geometry duplicates` to reproduce the diagnostic old-model-twin fixture; the latter is not a reachable current product state. A positive `--twins` count requires `similar` or `duplicates` geometry and is rejected with `unrelated`.

Required metrics per strategy: `recall@1`, `recall@5`, `recall@10`, `mrr@10`, `ndcg@10`, `map@10`, `under_return_rate`, `under_return_rate_at_limit`, `latency_p50_ms`, `latency_p95_ms`, `embed_calls`, `candidates_read`.

On the index layer, `under_return_rate` is the fraction of all queries for which the search stage returns fewer than `search_limit` candidates while at least `search_limit` matching claims exist in scope: `search_returned < search_limit <= available`. It diagnoses the filtered-HNSW shortfall and must be zero for an acceptable search strategy. The candidate request can exceed the caller's item limit.

`under_return_rate_at_limit` instead counts `len(returned) < limit <= available` over all queries after ranking and filtering. Scope availability is not a relevance label: a negative control or a relevance floor can intentionally produce fewer results, so this field alone is not an ANN correctness test. The product layer supplies its final result count and caller limit for both metrics; it does not measure the search stage separately. See `backend/src/space/evaluation/metrics.py` and `product.py` for the recorded fields.

Corpus shape:

```json
{
  "claims": [{"id": "claim-1", "text": "The project uses PostgreSQL for durable storage.", "topics": ["storage"], "source": "architecture", "memory_type": "factual"}],
  "queries": [{"query": "Where does the project persist its data?", "relevant": ["claim-1"], "kind": "paraphrase"}]
}
```

Each claim requires `memory_type` (`factual`, `procedural` or `episodic`), 5–60 words of text, 1–3 non-empty topics and a non-empty source. Claim IDs must be unique, and each `relevant` ID must name a claim in the corpus. A query with an empty `relevant` list must have `kind="negative"`; it is a negative control and must return no item. Other query kinds are `paraphrase`, `keyword`, `multi-hop` and `partial`, and require at least one relevant claim. Embeddings are baked once into a cache file and reused; the harness must never re-embed on every run.

