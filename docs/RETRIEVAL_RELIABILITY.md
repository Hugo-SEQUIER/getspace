# Retrieval reliability

Recall ranks memory **exactly within the authorized Space and embedding model**, then runs an independent evidence assessment. This page records why, the measurements behind it (taken between 14 and 17 September 2026 on disposable local databases and synthetic fixtures), and the remaining limits. The HNSW index from migration `0003` still exists, but serving does not use it; the change needed no migration.

The reports, fixtures and harnesses that produced the figures below are not part of this repository; the figures are copied from them.

## Candidate selection decision

`brain.search` materializes the authorized organization, Space and model's claim identifiers and cosine distances, then orders by distance and claim ID before applying the candidate limit. Row-level security still enforces the caller's identity. Vectors and claim text are not materialized or returned by this query. The planner cannot replace that boundary with a global HNSW nearest-neighbor scan. Approximate queries remain only as explicit evaluation controls; the shipped settings select exact ranking.

Exact ranking was chosen as the default because it gives a testable completeness contract. An ANN fallback triggered only by an under-filled result would still miss nearer claims whenever ANN returns a full but imperfect set. Exact ranking trades that uncertainty for work proportional to the Space's current-model corpus. It does not prove relevance or factual truth. The [pgvector documentation](https://github.com/pgvector/pgvector#filtering) describes post-filtering in approximate indexes; an independent SQL control tested the actual application query under its restricted runtime role.

The exact-ranking report recorded PostgreSQL 17.11, pgvector 0.8.6, 1,536-dimensional vectors and `work_mem=4MB`. Each size ran six distinct queries five times, with 5,000 same-Space old-model rows and 5,000 foreign-owner rows present. Serving ran as `space_app`; an independent exact query ran with explicit scope predicates as the disposable administrator. Every ordered candidate list matched, including after repeated execution with `hnsw.ef_search=1`. Wrong-model and forged-owner scope probes returned no rows. No serving plan used HNSW.

| Authorized current-model claims | Ordered control matches | Under-return | p50 database call | p95 database call | Backend VmHWM, KiB |
|---|---|---|---|---|---|
| 150 | 30/30 | 0 | 3.08 ms | 4.56 ms | 55,720 |
| 5,000 | 30/30 | 0 | 32.48 ms | 37.59 ms | 166,380 |
| 50,000 | 30/30 | 0 | 447.10 ms | 537.71 ms | 236,060 |

The largest sort used 34 KiB and no plan wrote temporary blocks. Backend RSS and high-water measurements include shared mappings and buffers, not just private query allocations. The fixture uses repeated dense perturbed vectors for scale rows and synthetic index records; it is not a real user's corpus. These measurements exclude embedding, model inference, concurrent traffic and network latency. The 50,000-claim cost is a real capacity consideration, not a latency guarantee for any server.

## Support, abstention and conflicting evidence

An optional decision-model route, off by default, assesses short complete passages before the text-model path described below. Confident, consistent selections keep the original passages and citations; longer inputs, conflicts, uncertainty and decision-model failures use the full text assessor. Switching the assessor does not change the stored-memory fingerprint.

Default `auto` and `semantic` recall use the exact SQL scores without re-embedding candidate claim text. The query is embedded once. All selected vector candidates can be loaded even when they span more than 32 memory resources. The standard bound is 40 vector claims plus ten lexical sources; lexical sources pass the same support check, so token overlap cannot bypass abstention.

The `evidence-v8` assessment accepts at most 50 candidates and 2,000 body characters per candidate. Application rules are sent as trusted system instructions, with the query and evidence in a separate user message. Assessments request temperature zero, except that an OpenAI-compatible provider with a configured reasoning effort receives the effort instead. One call checks whether the evidence supplies an answer, extracts verbatim answer-carrying quotes and classifies relevance and temporal intent. An explicit no-answer decision takes precedence over any selected labels. The model's draft answer is used only for assessment and is never returned as memory. A relevance label alone cannot select a record: the server requires a nonempty quote that occurs verbatim in the assessed body. Only surviving distinct bodies may enter one further conflict check, which cannot promote a rejected record. Provider requests keep the existing authorization, timeout, input-size and call-count checks, and revocation is rechecked before release.

The measurements and comparisons on this page used `openai/gpt-6-luna` through OpenRouter for extraction and assessment, with `SPACE_MEMORY_REASONING_EFFORT=none` for extraction and `SPACE_MEMORY_RELEVANCE_REASONING_EFFORT=low` for the relevance pass, and `text-embedding-3-small` for embeddings. The recommended text model is now `anthropic/claude-haiku-5.5` with both efforts at `low`: on 9 October 2026 it answered the same 32 reliability questions as Luna did, with no unsupported evidence and no failed flags, and extracted the smoke-test facts correctly. With `none`, Haiku 5.5 repeated its extraction answer until the length limit. The optional relevance model falls back to the extraction model when unset. Changing the assessor or the chat model does not invalidate stored memories; only a change of embedding model keeps memories out of semantic recall until they are re-embedded (see [Memory](MEMORY.md#configuring-a-provider)).

The model cannot generate returned text or citations: it labels existing candidate numbers. Duplicate, missing or unknown assessment IDs, invalid structured responses and out-of-range conflict references fail visibly rather than returning unfiltered records or disguising a processing failure as abstention. Returned text is the exact excerpt assessed, with truncation exposed. Original memory IDs, claim IDs, resource revisions and source citations remain attached.

Assessed results can be `direct`, `historical` or `conflicting`. A stale source cannot establish a current fact; it can be selected only for a historical query. Conflicting evidence keeps both original records and warns the caller not to choose a winner silently. A statement that a requested value is unknown does not supply that value. `found=false` means no supported answer was found in the retrieved evidence; it does not prove the Space has no relevant record. Explicit `lexical` and `resume` remain model-free and do not promise semantic support assessment. Related cue matches remain a separate optional tier and do not set `found`.

## Evaluation and verification

A Jev and Luna comparison evaluated evidence selection on 81 short synthetic cases supplied directly to the assessor. Luna with low reasoning matched the complete evidence set and labels on 78 of 81 cases, and returned unsupported evidence on 1 of 35 unanswerable queries. This excludes retrieval, extraction, embeddings and database work, so it is not an end-to-end product evaluation. Longer inputs, repeated trials and full retrieval checks remain outstanding.

The backend verification run of 14 September 2026 passed 346 tests with one optional client integration test skipped. Its regressions cover exact tie, model and owner filtering, all 40 candidate resources being available for support selection, unsupported lexical matches, stale and historical behavior, citations, revocation during assessment, quote validation, incomplete responses, trusted instruction separation and the relevance-only model override.

## Reproduce

From the repository root, with disposable local databases:

```sh
make evaluate-retrieval STRATEGY=exact
SPACE_RUN_DATABASE_TESTS=1 uv run --directory backend --locked pytest tests/test_exact_ranking.py tests/test_relevance.py tests/test_relevance_workspace.py
```

`make evaluate-retrieval` measures a strategy on the synthetic corpus in `backend/evaluations/retrieval/`; its first run embeds the corpus with the configured provider. The live harness used for the assessor comparison is not part of this repository.
