# Space memory with Memora

Space owns durable memory and uses the text inference runtime from [Microsoft Memora](https://github.com/microsoft/Memora/tree/dec3f8f2444eace7004fc084abe1be9f3d88270e). Explicit handoffs, extraction from selected sources, direct agent writes, semantic and prompted retrieval, and a pgvector claim index are implemented. Memory is autonomous: a claim is live as soon as it is stored, and the website's memory page is a brain browser for reading, correcting and deleting memory. There is no review queue. The [vendor manifest](../backend/src/space/_vendor/memora/README.md) records the pin, the MIT license, the included algorithms and the adaptations.

See [memory node links and inferred relationships](MEMORY_RELATIONSHIPS.md) for explicit connections between memories (detail → subject), automatic backlinks and separate content-dependent suggestions, and [memory organization](MEMORY_ORGANIZATION.md) for categories, sections and project assignments.

## Continue work across sessions

1. Ask an authorized agent to save a handoff with `save_handoff`. It records a summary, decisions, progress, unresolved questions, next actions, topics and source IDs and revisions in a versioned document.
2. Open a fresh session and ask "Where were we?" The agent resolves the Space with `workspace_info`, then calls `get_context` with `mode="resume"`.
3. Recall returns eligible handoffs and current sources with attribution, revisions and links to the website's saved-revision reader.

Connecting an agent never imports conversation history. Handoffs and lexical recall work without a model provider. Set `SPACE_WEB_URL` (in the compose stack, `SPACE_PUBLIC_URL`) to the website's actual origin for correct citation links.

```json
{
  "space_id": "<authorized Space UUID>",
  "request": {"mode": "resume", "query": "", "limit": 5, "max_chars": 12000}
}
```

`save_handoff.change` accepts `command_id`, `title`, `summary`, `decisions`, `progress`, `unresolved_questions`, `next_actions`, `topics` and `sources: [{resource_id, revision}]`. Updates require `resource_id` and the current `expected_revision`; creates use revision zero. Reuse the exact command ID and payload for retries.

## Write and change long-term memory

Use `remember` (`POST /v1/spaces/{space_id}/memory/remember`) for authored memory. It preserves the supplied text, classifies it when classification is configured, embeds it, and commits the claims with their index entries before returning HTTP 200. It makes no extraction or cue-generation requests and creates no background job. Text up to 2,000 characters becomes one claim; longer text, up to the 12,000-character input limit, is split losslessly into consecutive claims of at most 2,000 characters. Classification runs per claim, and embeddings are sent as one batch. `memory_type` describes the input format for extraction jobs only; direct authored claims default to factual. Use `remember_claims` for 1 to 24 already separated claims with explicit factual or procedural types and importance. Automatic task assignment is not part of the organization catalog.

Use `extract_memory` only when supplied text or a current source revision needs to be mined into generated claims. It saves the input as a job in PostgreSQL and returns `queued`; poll `memory_job` for `processing`, `ready` or `failed`. The input becomes generated memory when processing commits the revision and its claim index. The website shows this status across reloads. There is no human review queue and no `review_memory` command. `change_memory` edits, organizes or permanently deletes one claim.

`build_memory` remains a synchronous source-extraction interface. Text extraction jobs select the extraction pipeline in the worker; ordinary `remember` requests do not. Jobs survive disconnects and process restarts, but recheck the original agent grant before reading content and before publishing; revoking the connection stops unfinished work, even if the same agent connects again later. Retries reuse an unchanged command ID and payload, and a committed command returns the revision it originally saved. [Architecture](ARCHITECTURE.md#background-work) describes the queue bounds, leases and retries.

A build selects a current source revision in the same Space, accepts at most 12,000 source characters, rejects another memory resource as its source, and creates one memory resource per source. A rebuild passes the existing memory `resource_id` and its current `expected_revision`; building the same source again without them fails with a conflict. Rebuilds preserve edits and deletions whose claim ID still matches. An edited claim keeps the editor's text, records the generated text as `generated_value`, and stops retrieving through its old generated cues. A deleted claim is not restored by a rebuild.

Claims are named by a deterministic UUID derived from the memory resource ID and the claim index, so equal text from two sources keeps separate citations and an edit never disturbs a neighbouring claim. The pinned extraction pipeline uses chat builders when `build_memory` selects a saved handoff or a text extraction job has type `chat`, and document builders for other sources. `save_handoff` itself only saves the handoff; it does not extract claims or call a model. Extracted entries must be primary, non-episodic, non-empty and at most 2,000 characters; one build permits 24 claims and 128 internal entries. Episodic entries the extractor produces stay in the memory record and are not indexed as claims. A fatal model failure creates no partial memory resource, and a successful retry reuses the saved result without repeating paid model calls. Classification failures leave claims unassigned, while embedding failures prevent the write.

## Original creators

New memories capture the writer's agent identity, or a human marker, in each claim's `creator` metadata. This is separate from the latest revision's `author_agent`; editing or rebuilding a matching claim preserves its creator. The memory list and recall expose this metadata, and the website uses it for creator display and filtering. Claims written before creators were recorded stay unknown rather than inheriting the latest editor.

The owner, or a personal API key with write access, can correct attribution through `POST /v1/spaces/{space_id}/memory/creator`, passing `command_id`, `resource_id`, `expected_revision`, `entry_ids` and an active same-Space `creator_agent_id` (or null for a human). Corrections use `basis="user_attributed"`; automatically captured creators use `basis="captured"`. The correction creates a new revision without changing claim text, classifications, source references or embeddings, and does not change the acting identity. Agents cannot call this endpoint. Exports keep creator snapshots, and a restore remaps agent IDs when those agents are restored together.

## Memory graph

The website's graph view renders saved claims as nodes and links pairs that share an active project or category assignment. Multiple shared assignments produce one undirected connection. Unassigned memories remain visible as isolated nodes; matching text, the same creator or a common memory type alone does not create a link. Node colors use categories or creators, and selecting a node can focus its neighborhood to a depth of one to three connections. Search and filters constrain both nodes and links; older pages that are not loaded yet are not part of the graph.

Graph positions are a force layout for navigation, not a projection of embedding vectors, and assignment links do not claim semantic similarity or factual support. Rendering the graph uses existing memory and catalog responses, with no embedding calls and no database writes.

## Granularity: how many claims a source becomes

The two pinned builders split facts differently, and this is upstream behaviour rather than a Space policy.

- The chat builder serves text extraction jobs with `memory_type="chat"` and `build_memory` over a saved handoff. Its prompt asks for exhaustive extraction and one entry per distinct fact. A four-fact synthetic paragraph became four claims in a recorded provider check.
- The document builder serves `build_memory` over other saved sources and text extraction jobs with `memory_type="markdown"`. Its prompt keeps a coherent segment as a single detailed memory and splits only distinct independent ideas. The same paragraph became one or two claims in that check.

The practical consequence is curation. Use `remember_claims` when the agent has already separated facts: one supplied claim stays one independently editable claim. The extraction builders may choose a different granularity, so editing or deleting an extracted claim changes every fact bundled inside it. Extraction is also non-deterministic: the same input can yield a different number of claims on two runs, which is why the provider check asserts a floor rather than an exact count.

Deletion is real removal. `change_memory` with `action="forget"` drops the claim from the current memory and deletes its index row, so recall stops returning it. It is not a reversible exclusion, and no command restores the deleted claim. Earlier revisions of the memory remain, as for every other resource, and still contain the claim for anyone who can read them. The brain browser asks for confirmation before it runs, and an agent with a write grant can call the same command.

## The claim index

Migration `0003_memory_brain` installs the PostgreSQL `vector` extension in the `space` schema and creates `space.memory_claims`, one row per current claim of the current revision of a memory resource. A row holds the opaque identifiers, the embedding, a SHA-256 hash of the claim text, SHA-256 hashes of the claim's generated cue labels, the memory type, the embedding model name, the declared importance (0 to 3), the originating source resource and revision, and timestamps. A claim is keyed by organization, Space, memory resource and claim ID, so rebuilding or deleting one memory touches only that memory's rows. Ranking uses cosine distance between normalized vectors. The table also carries an HNSW cosine index, which serving does not use (see [Retrieval](#retrieval)), and a GIN index over the cue hashes for cue expansion. `brain.py` owns this SQL; it never calls a model and never writes a resource revision.

Index rows are written in the same database transaction as the revision they describe, so the index and the revision cannot disagree. Embeddings are computed outside every database transaction: the caller asks `brain.pending` which claims need a vector, embeds them, then applies the result. The restricted runtime login ends a transaction idle for 30 seconds, and a model call can take longer. If the index changes between those two steps, the write fails with a retryable conflict instead of storing a stale row.

Every embedding in the index has the same width: 1,536 dimensions, the size of `text-embedding-3-small`, unless `SPACE_MEMORY_EMBEDDING_DIMENSIONS` sets another (16 to 2000). The installer resizes the index while it is empty and refuses another width once embeddings are stored, so choose the embedding model before storing memories. Restoring an archive whose index has another width embeds its memories again with the configured model. Every row also records its embedding model name. A claim stored under another model of the same width is re-embedded on its next write, and `memory_info` and the brain browser report how many rows still use another model.

Claim text is not in the index. It lives in the memory's revision, stored as readable JSON like every other payload; the index holds embeddings, hashes, identifiers and timestamps. [Data and privacy](PRIVACY.md) describes who can read what.

Capture remains explicit: an agent or the owner chooses finished claims, raw text or a saved source. `extract_memory` creates durable background work; it does not import history or mine unrelated documents. `remember_claims` keeps prepared facts as written. The worker uses the same revision and index transaction as synchronous memory writes.

## Retrieval

For a startup brief, call `get_context` with `mode="resume"` and `compact=true`. Recent handoff selection and citations are unchanged, but long structured handoffs receive an excerpt of at most 1,200 characters that reserves space for the summary, next actions, unresolved questions, decisions and progress. These are passages copied from the stored revision, not generated summaries. Omitted text sets `truncated=true`; open the cited revision for complete context. Short handoffs keep their full text, and manually edited or ambiguous layouts use a normal prefix excerpt. The response still obeys `max_chars`, which may shorten excerpts further. Compact mode is valid only for resume and makes no model calls. Topic search remains necessary for older constraints and project-specific evidence.

Use `get_context` with `mode="search"` and a nonempty query:

| Strategy | Behavior |
|---|---|
| `lexical` | BM25 over claim titles and values and over source titles, topics and selected bodies; no model call |
| `semantic` | Current-model vector claims fused with keyword claims, lexical sources and optional explicit one-hop candidates, then evidence assessment |
| `prompt` | Exact candidates, Memora's model-directed expansion and re-query policy, then the same evidence assessment |
| `auto` (default) | The assessed retrieval when a provider is configured; lexical claims and sources when it is disabled |

Memory ranking happens in the database. A materialized query computes distances for every claim of the Space under the current embedding model before sorting and limiting candidates, so the global HNSW index cannot preselect them. Only identifiers and distances are materialized, not vectors or text. The default requests four candidates per output item, at most 40. Every selected memory resource can be loaded, even when the candidates span more than 32 resources. Ordinary semantic and auto recall embeds the query once and uses the stored claim vectors; it does not re-embed the selected claims. Cue expansion runs only for the explicit related tier. Exact ranking costs time linear in the Space's claim count; it is not bounded database work.

Non-memory sources are selected with keyed term fingerprints and ranked over metadata and up to 32 source bodies. A separate keyword branch preselects at most 32 current memory resources and BM25-ranks their indexed claim titles and values. In assessed semantic and auto search, reciprocal rank fusion deduplicates vector and keyword claims by resource and claim inside the same memory candidate budget; only actual vector hits carry a cosine `score`. Excluded resources are filtered before bodies are loaded, and keyword claims must match the current embedding model. `RetrievalConfig.lexical_claims=False` restores vector-only candidate generation for controlled comparisons; a configured cosine floor also keeps that vector-only behavior. The explicit `prompt` strategy keeps its own candidate path.

With `SPACE_MEMORY_RELEVANCE_PROVIDER=decision`, the optional decision-model route assesses complete evidence units of at most 500 characters first. Authored claims stay whole. Longer source resources may become lossless contiguous paragraph or sentence views with exact `passage.start` and `passage.end` character offsets and unchanged citations. All original evidence must fit in at most 50 units and a 12,000-character serialized state, including the query, titles, context and JSON framing. Oversized claims, long table or code structures and exceeded budgets fall back to the chat assessor; no ineligible candidate is silently dropped. Selected split views carry `truncated=true`, and final output limits can shorten a span further and adjust its end offset. The decision model is an external service on OpenRouter's Decisions API, off by default; see [Data and privacy](PRIVACY.md#what-leaves-the-server) and [Decision models](#decision-models).

The earlier Memora similarity threshold of `0.2` did not establish relevance: in negative-control measurements it admitted unrelated claims. A configured cosine floor is only an optional geometric cutoff. Returned `score` values are similarities, not confidence or truth probabilities. [Retrieval reliability](RETRIEVAL_RELIABILITY.md) records the current decision, measurements and limits.

Up to the requested limit of lexical sources join the bounded memory pool, never exceeding 50 original candidates. Chat assessment uses at most 2,000 body characters per candidate, drafts an internal evidence-only answer and validates verbatim supporting quotes. A second bounded call can mark conflicts among the survivors, but cannot promote rejected evidence. Invalid responses fail visibly. `lexical` and `resume` make no model calls and do not promise support assessment.

Each selected item carries a separate `evidence_context`: the original resource title, the referenced project, category and section names, and visible source titles at their cited revisions. This bounded metadata is untrusted description for disambiguation, never an instruction or a replacement for support in the body. Missing or capped context is marked `incomplete`; catalog descriptions and unrelated records are not disclosed. Context reaches the decision-model, chat and conflict checks and counts toward their request budgets. It does not rewrite stored claims or change write-time inferred-link judgments.

Assessed responses include `assessment` with `candidate_count`, the final route's `assessed_units`, `selected_units`, `route` and `fallback_reason`. A confident empty decision reports the route `decision`, not a chat fallback. These counts precede final output limiting and contain no candidate text.

`include_linked=true` enables explicit node-link expansion for assessed semantic and auto search only. It reads one hop in both directions from at most five retrieved memory nodes, considers at most 25 neighboring resources and reserves at most five claim slots inside the existing memory ceiling. A one-slot budget keeps direct retrieval only. There is no inferred-link traversal or recursion. Linked candidates carry `candidate_origin="linked"` and `linked_from`, and still pass the ordinary access, exclusion, freshness and support checks. Linked recall requires an enabled provider, indexed memory and no cosine floor. Links are not proof; this option differs from `include_related` below.

Every recall result carries `found`. If the retrieved evidence does not support an answer, assessed recall returns `found=false`, an empty `items` list and an explicit instruction not to invent memory. This is an abstention over retrieved evidence, not proof that no record exists or that a fact is false. The character budget never turns a match into that abstention: the first ranked answer sheds optional context (`evidence_context`, `classification`, `creator`, `author_agent`), then shortens its excerpt around the query match, so it fits even the 2,000-character minimum. Only when even its citation fields exceed `max_chars` does recall return `found=false` with a separate note asking for a larger `max_chars`. Assessed items carry `relevance` of `direct`, `historical` or `conflicting`; contradictions remain unresolved, and both records keep their original citations. Excluded resources are never returned. `mode="resume"` remains a deterministic recent-handoff flow.

Recall returns ranked results in `items`. Passing `include_related=true` adds a second, separate tier in `related`: claims that share a generated cue with a matched claim. Related claims are never ranked, never enter `items`, and are filtered against the ranked list, so asking for them cannot change the answer. They are capped at five, share the character budget, and carry a `related_note` telling the caller they are pointers rather than answers. The tier is off by default: measured on two fixtures, cue expansion changed no quality metric while raising the candidate count from 40 to 104 and multiplying latency, and no query could be built that it uniquely answers.

Result items carry `verification` of `memory`, `handoff` or `user_authored`, plus `stale`. Explicit semantic or prompt requests fail clearly when no provider is configured or no memory is indexed. Changing only the chat model does not invalidate retrieval; semantic branches select claims indexed with the current embedding model.

A changed source marks dependent memory stale. Assessed recall selects such evidence only for an explicitly historical query with supported quotes, and labels it historical; a direct label alone cannot override the stale flag. The memory browser and revision reads keep access to the older record. Sources are untrusted data; memory is what agents recorded or derived. Verify important facts and cite source IDs and revisions.

Stored claims keep their memory resource and revision in their identity, so equal claim text from different sources keeps separate citations. Cue expansion connects only current indexed claims. Unchanged claims keep their stored vector when a memory is rewritten; only new or edited claims are embedded again.

## Persistence, permissions and processing

Recall, build, remember and change use the existing Space read and write permissions. The live grant and a digest of the Space's resources (their count, and each identifier with its current revision) are checked before processing, before and after each provider call, and before disclosure or publication. The check reads no content, so it works at any Space size. Models run outside database transactions. A concurrent source change causes a retryable conflict, and a revoked connection gets nothing back. Recall writes no resource revisions.

Two migrations support that read path. `0005_resource_terms` adds `space.resource_terms`: a keyed HMAC fingerprint of every token of a resource's current revision, in two fields (the title with its topic labels, and the body), with occurrence and token counts, so the database selects candidates with an indexed query. The fingerprint key is an HKDF subkey of the Space key. `0006_memory_marker` adds the readable `space.resources.memory_kind`: NULL for a document or a task, otherwise `handoff` or `memory`, which lets recall select memory in SQL. The resource kind that clients see is unchanged.

### Configuring a provider

The default provider is `disabled`. [Self-hosting](SELF_HOSTING.md#configuration) lists the settings. An OpenAI-compatible example with chat and embeddings on two services:

```dotenv
SPACE_MEMORY_PROVIDER=openai_compatible
SPACE_MEMORY_PROVIDER_URL=https://openrouter.ai/api/v1
SPACE_MEMORY_API_KEY=<provider key>
SPACE_MEMORY_TEXT_MODEL=anthropic/claude-haiku-5.5
SPACE_MEMORY_REASONING_EFFORT=low
SPACE_MEMORY_RELEVANCE_REASONING_EFFORT=low
SPACE_MEMORY_EMBEDDING_MODEL=text-embedding-3-small
SPACE_MEMORY_EMBEDDING_URL=https://api.openai.com/v1
SPACE_MEMORY_EMBEDDING_API_KEY=<embedding key>
```

For a reasoning model, the effort is configuration rather than a property of the model name, and a request with an effort carries no temperature. Use `low` for both with `anthropic/claude-haiku-5.5`: with `none`, it repeated its extraction answer until the length limit, so extraction failed. `none` suits `openai/gpt-6-luna` for extraction. A model that takes a temperature leaves both settings unset. Chat and embeddings may run on different services, which is why the embedding endpoint and key are separate settings; unset, both calls go to the provider URL. `SPACE_MEMORY_RELEVANCE_MODEL` optionally names a separate assessing model.

`SPACE_MEMORY_ANTHROPIC_API_KEY` is optional. When it is set, extraction and relevance chat go first to the Anthropic Messages API with `SPACE_MEMORY_ANTHROPIC_MODEL` (default `claude-haiku-5-5`), constrained to the response schema through `output_config.format`. Any failure there (an HTTP error, overload, a refusal, a truncated or invalid answer) retries the same request once on the configured chat model, inside the same request budget, so a fallback counts as a second model request. The reasoning-effort settings apply to both routes; `none` sends Anthropic `thinking: disabled`, and no request to Anthropic carries a temperature. Decisions and embeddings are unchanged. The key is part of the chat fingerprint, so setting or clearing it makes queued extraction jobs ask for resubmission, like any other chat-provider change. Anthropic requests have no `store=false` option.

### Decision models

Three optional memory features ask a decision model, which answers typed questions with probabilities instead of text: `SPACE_MEMORY_RELEVANCE_PROVIDER=decision` assesses short recall passages before the text assessor, `SPACE_MEMORY_CLASSIFICATION_PROVIDER=decision` organizes each written claim against the catalog, and `SPACE_MEMORY_RELATIONSHIP_PROVIDER=decision` links related memories. All three default to off. They reach the model named by `SPACE_MEMORY_DECISION_MODEL` (default `perplexity/pplx-decider-v1.1-27b`) on OpenRouter's Decisions API, with `SPACE_MEMORY_API_KEY` unless `SPACE_MEMORY_DECISION_API_KEY` is set, and need a memory provider. `SPACE_MEMORY_DECISION_URL` points at another Decisions API, and a decision that takes longer than `SPACE_MEMORY_DECISION_TIMEOUT` seconds (default 5) falls back like any other decision failure. The earlier provider value `typesafe` still means `decision`. `compose.yaml` passes these settings to the API and the worker.

### Benchmarking decision models

`make benchmark-decisions MODELS=perplexity/pplx-decider-v1.1-27b,typesafe/jev-1.13` runs the same invented cases through each model: 97 recall questions over fictional records, and 30 memories to file against a fictional catalog (`backend/evaluations/decisions/`). It builds every request with the application's own code and applies the application's thresholds, so a score is the decision Space would actually make. Each model gets a row with its exact answers, wrong answers, fallbacks, invalid answers, errors, median and p95 latency, and cost per 1,000 decisions. `MODELS=all` runs every text decision model OpenRouter lists, `ARGS=--list` shows them with their prices, and `ARGS="--out report.json"` keeps every case's outcome. A model that always answers no would score 41 of 97 and 6 of 30, because those cases expect nothing to be selected or assigned. The run costs provider usage, typically a few cents per model.

A run on 9 October 2026 compared five models, once each:

| Model | Recall: right / wrong / unsure, of 97 | Recall time, median / p95 | Filing: right, of 30 | $ per 1,000 recalls |
|---|---|---|---|---|
| `perplexity/pplx-decider-v1.1-27b` | 66 / 2 / 29 | 605 / 1,588 ms | 18 | 0.14 |
| `typesafe/jev-1.13` | 65 / 0 / 32 | 407 / 487 ms | 20 | 0.27 |
| `openai/gpt-6-luna-decisions` | 27 / 0 / 68, 2 errors | 443 / 716 ms | 21 | 0.52 |
| `cloudflare/clef-flash` | 28 / 0 / 68, 1 error | 584 / 2,343 ms | 15 | 0.12 |
| `jaredpalmer/kev-4b` | 30 / 9 / 58 | 1,637 / 4,762 ms | 17 | 0.21 |

An unsure answer falls back to the text assessor, so it costs time rather than accuracy; a wrong answer is trusted. Perplexity's decider is recommended for the most correct recall answers at about half Jev's price. Its two wrong answers kept an outdated record beside the current one, marked as historical, or called a record of a change current instead of historical. Jev made no wrong recall answers and answered fastest. A difference of one or two cases is within the noise of a single run.

With `SPACE_MEMORY_PROVIDER=ollama`, Space calls Ollama's own chat and embedding endpoints; an Ollama server on the same machine or network keeps all text local. Set `SPACE_MEMORY_EMBEDDING_DIMENSIONS` to the embedding model's width before storing memories, for example 768 for `nomic-embed-text`. The extraction and recall quality of local models has not been measured here.

Two identities are recorded, and each covers one thing. The **chat identity** is the provider, the chat endpoint and the text model; it is stored with an extraction job, so a queued job never runs under a different chat configuration. The **embedding identity** is the embedding model and the service that serves it; it is stored with each saved memory, because it decides whether a stored vector is comparable to a new query.

Changing the chat model therefore needs no rebuild. Stored vectors and cue entries stay valid, and semantic, prompted and `auto` recall keep working on memories saved under the previous model. Changing the embedding model is the case that matters: claims embedded by another model are not selected by the vector search, so those memories stay out of semantic recall until they are re-embedded on their next write. `memory_info` reports how many claims carry another embedding model as `index.other_model`, the number to watch. Lexical search and resume recall never depend on either identity.

`memory_info` and the website show the endpoint and model names, never the key. Source text and memory text are sent for extraction and embeddings; queries and permitted memory context may be sent during recall. Chat requests set `store=false`, which is not a promise that the provider retains nothing. Agent tokens, API keys and the access key are never sent to a provider. Provider endpoints must use HTTPS, except on localhost or a private network.

Recall never crosses Spaces. Forgetting a claim removes it from the index; earlier revisions of the memory remain readable to anyone who can read the Space. `delete_memory` removes a whole generated record with all of its claims, revisions and index rows, and it follows the write grant, so agents may call it, unlike document deletion. A backup taken before the deletion still contains the memory.

## Interfaces and limits

MCP includes `memory_info`, `list_memories`, `remember`, `remember_claims`, `extract_memory`, `memory_job`, `build_memory`, `change_memory`, `delete_memory`, `save_handoff`, `set_memory_links`, `get_memory_links` and `get_context`, alongside the resource tools. A fresh MCP connection discovers newly added tools.

Space also serves its own operating guidance rather than leaving it to a document that can drift. Every connecting client receives it as the MCP instruction string, and the fuller version is readable as the `memory_guide` prompt and the `space://memory/guide` resource. The text is generated from the running configuration, so it names the provider, models and limits actually in effect, states that memory is autonomous and irreversible, and lists what an agent should and should not store. Because capture is agent-driven, this guidance is the control that shapes what memory holds.

The REST API offers the same operations under `/v1/spaces/{space_id}`:

| Endpoint | Purpose |
|---|---|
| `GET /memory/provider` | Provider, models and index counts |
| `GET /memories`, `GET /memories/{resource_id}` | List or read memories |
| `DELETE /memories/{resource_id}?expected_revision=N` | Delete a generated memory record |
| `GET`, `PUT /memories/{resource_id}/links` | Read or replace explicit links |
| `POST /memory/remember`, `POST /memory/claims` | Store authored text or prepared claims |
| `POST /memory/jobs`, `GET /memory/jobs`, `GET /memory/jobs/{job_id}` | Queue extraction and follow it |
| `POST /memory/build` | Synchronous extraction |
| `POST /memory/change` | Edit, classify, route or forget a claim |
| `POST /memory/creator` | Correct claim attribution |
| `POST /handoffs` | Save a handoff |
| `POST /context` | Recall |

Search queries travel in POST bodies. [The API reference](openapi.json) documents each route a personal API key may call.

Recall is bounded per call. Lexical source search selects at most 256 candidates, ranks the first 32 bodies, and can read up to 10 more bodies for metadata-only matches. Vector-selected memory resources can all be loaded, up to the candidate ceiling. Keyword claims add at most 32 preselected memory resources; opt-in explicit links inspect at most 25 neighboring resources. These branches share the final memory candidate ceiling rather than adding unbounded assessment input. Context lookup reads at most 64 referenced labels and 50 cited source revisions, without source bodies, and keeps at most three names per kind or source list per item. Memory browsing returns at most 200 resources per page. Ranked recall returns no cursor. Each API process admits 12 recalls with 128 waiting; synchronous memory writes share 2 active slots with 32 waiting, with five-second queue deadlines. Durable extraction separately permits at most 100 unfinished jobs per Space and one active job per worker process.

Raw extraction accepts 12,000 characters including the title, processes 6,000-character segments and permits 24 claims, 128 internal entries and an 850 KB memory record within the 1 MiB payload ceiling. Structured writes accept 1 to 24 supplied claims of at most 2,000 characters each and embed their exact values. When `SPACE_MEMORY_CLASSIFICATION_PROVIDER=decision`, each unpinned claim also gets one bounded classification call. Index search selects four times the requested item limit by default; optional cue expansion selects at most 64 related claims from 256 cue hashes. Model sessions permit 80 calls in 180 seconds, requests of at most 400 KB, responses of at most 2 MB and embedding batches of 32. Recall returns at most ten items and 20,000 JSON characters, with defaults of five items and 12,000 characters; individual excerpts are at most 4,000 characters.

Over-capacity requests fail instead of searching a silently incomplete corpus. `found` reports whether anything matched, and an empty result adds a `note` telling the caller that memory holds nothing and must not be invented. `has_more` indicates output truncation; refine the query or open the cited sources. These are safety bounds, not measured capacity or latency at scale.

## Verification

The memory tests use fake providers and disposable PostgreSQL containers. From the repository root:

```sh
make check
make check-database
SPACE_RUN_DATABASE_TESTS=1 uv run --directory backend --locked pytest tests/test_evidence_passages.py tests/test_relevance.py tests/test_decision_relevance.py tests/test_recall_candidates.py tests/test_recall_context.py tests/test_rag_recall_workspace.py
make check-memory-models
make benchmark-memory
```

The database tests start a disposable `pgvector/pgvector:pg17` container, so migration `0003_memory_brain` runs with the `vector` extension available. `make check-memory-models` runs both extraction builders and recall against the configured provider on synthetic text and incurs API usage; it writes no Space data. `make benchmark-memory` times lexical ranking in process on synthetic data and excludes the database and models.

Not implemented: background capture, binary or multimodal parsing, research training (GRPO), a remote Memora service, and broad quality and cost benchmarks. Recall quality, correction effort and provider cost have not been measured for autonomous memory. This implements Memora's text inference workflow, not every feature of its research repository.
