# Vocabulary

This page defines the words this system uses. It exists because the same sentence can be correct and still unreadable: a claim, an index row, a lease and a related item are four different things, and the code sometimes uses one English word for two of them. Each entry names the file that defines the behaviour, so a disagreement between this page and the code means the code is right and this page is stale.

How to read this page:

- One meaning per term, and a qualifier where one word carries two meanings. [Words that are easy to confuse](#words-that-are-easy-to-confuse) lists the collisions.
- Values are the shipped defaults, not proposals. [Limits and defaults](#limits-and-defaults) repeats every number with the constant that holds it.
- Only names that exist in the API, the database or the code are documented here.

## Space, identity and access

| Term | What it means | Defined by |
| --- | --- | --- |
| Owner | The one person an installation belongs to. There are no user accounts: the owner is a fixed principal with the issuer `space`. | `backend/src/space/security/auth.py` (`owner_identity`) |
| Organization | One tenant row: the owner's personal organization, or a team organization. It holds Spaces and their keys. | `backend/migrations/versions/0001_private_space.sql`, `Workspace._scope` |
| Principal | An identity record: an issuer and a subject. A self-hosted installation has one, the owner. | `0001_private_space.sql` |
| Space | The unit of memory. Every Space-owned table is keyed by `(organization_id, space_id)`. | `0001_private_space.sql` |
| Actor | The verified caller of one command: issuer, subject, expiry, and optionally `client_id` and `grant_id`. A worker rebuilds the actor from the queued job row instead of trusting a fresh login. | `backend/src/space/security/auth.py`, `jobs.process` |
| Access mode | `key`: the website and the REST API require the access key, and agents need their own tokens. `private`: no access check at all. | `SPACE_ACCESS_MODE`, `backend/src/space/settings.py` |
| Access key | `SPACE_ACCESS_KEY`. Unlocks the website and acts as the owner's bearer token on the REST API; refused on MCP. | `backend/src/space/security/auth.py` |
| Agent token | A `spc_` bearer token that stands for one connection grant, created on the website or through the device code. Valid on MCP only, for 180 days, stored as a SHA-256 digest. | `backend/src/space/device_auth.py` |
| OAuth sign-in | How apps such as claude.ai connect with only the MCP address: they register themselves, the owner approves them on `/connect/authorize`, and they hold an `spo_` access token (one hour) and a rotating `spr_` refresh token for one connection grant. Key mode only. | `backend/src/space/oauth_server.py` |
| Personal API key | An `spk_` bearer token for the REST API only, limited to one Space and read-only or read-write. Stored as a SHA-256 digest. | `backend/src/space/api_keys.py` |
| Grant | A connection grant row in `space.oauth_grants` (a historical name), bound to one client, the MCP address and `can_write`. Every transaction rechecks it, so revocation stops unfinished work. | `Workspace._scope` |
| Revocation | Setting `revoked_at`. The next transaction fails with 403, and an uncommitted job is marked `access_revoked`. | `Workspace.revoke`, `jobs.process` |
| Row-level security (RLS) | PostgreSQL policies that require `organization_id` and `space_id` to equal the values the transaction requested. Space-owned tables use `ENABLE` and `FORCE ROW LEVEL SECURITY`. | `backend/migrations/versions/` |
| Queue login | The separate least-privilege login a worker uses to claim its queue: `space_worker` for extraction jobs, `space_webhook` for webhook deliveries. It cannot read resource content. | `backend/src/space/worker_setup.py` |
| Space key | A random 32-byte key per Space in `space.space_keys`. It encrypts nothing; it keys the term fingerprints and the command fingerprints. | `backend/src/space/providers.py` |
| Lease receipt | The `(job id, lease id)` pair a worker stores for the duration of one job, so every transaction it opens is fenced. | `backend/src/space/job_context.py` |

## Content: resources, revisions and commands

| Term | What it means | Defined by |
| --- | --- | --- |
| Resource | One stored item: a source document, a task, a handoff or a memory. | `backend/migrations/versions/0002_resources.sql` |
| Revision | An immutable version of a resource. The first write is revision 1; every later write must name the revision it extends. | `Workspace._save` (`expected_revision + 1`) |
| Current revision | The latest committed revision. Jobs and writes check it before use, and a mismatch raises 409. | `Workspace._save`, `jobs.enqueue` |
| Kind | The client-visible kind: `document` or `task`. | `SaveResource.kind` in `backend/src/space/workspace.py` |
| memory_kind | A readable marker on the resource so recall can select memory in SQL: `NULL` for a source document or task, `handoff` or `memory`. | `backend/migrations/versions/0006_memory_marker.sql` |
| Source document | Evidence a memory may cite. It is never memory itself. | `docs/MEMORY.md` |
| Handoff | Saved session state: summary, decisions, progress, unresolved questions and next actions. It is read through `resume` recall, stays lexically searchable as evidence, and has no claim index rows. | `SaveHandoff` in `backend/src/space/memory.py`, `0006_memory_marker.sql` |
| Memory | A resource whose payload carries a memory record of claims. It is written by `remember`, `remember_claims`, `build_memory` or `change_memory`, and it is marked `memory_kind = 'memory'`. | `Workspace._save`, `memory_pipeline.prepared` |
| Recall metadata | The block that describes how a resource is recalled: `category` (`source`, `handoff` or `memory`), `topics`, `sources` and `excluded`. | `RecallMetadata` in `backend/src/space/memory.py` |
| Command | One client operation with one `command_id`, for example one `extract_memory` call. | `Workspace._save`, `jobs.enqueue` |
| Command ID / idempotent replay | Repeating a `command_id` with the same payload returns the originally committed revision and spends no model call. The same ID with a different payload raises 409 `This command ID was already used for another operation.` The queue applies the same rule to a job ID and says `This job ID was already used for another request or grant`. | `Workspace._previous`, `jobs.enqueue` |
| Command fingerprint | An HMAC of the request under the Space key. A reused command ID is compared by fingerprint, so a different payload cannot replay an old result. | `Workspace._save`, `Workspace._memory_retry` |

## Memory anatomy

A memory is one record. The record holds claims, the entries they were extracted from, and the list of claims that were forgotten. Claims are what recall returns; entries are what the extractor produced.

| Term | What it means | Defined by |
| --- | --- | --- |
| Memory record | The payload under `content["memory"]`: `version`, `provider_fingerprint`, `claims`, `forgotten`, `entries`, `build_stats` (plus `input_kind` for structured claims). | `memory_pipeline.build`, `memory_pipeline.prepared` |
| Claim | One durable, self-contained statement plus its metadata. A memory is a set of claims. | `memory_pipeline.build`, `memory_pipeline.prepared` |
| Claim index (label) | The short name of a claim, for example `Commute`. It is unique inside one memory and is part of the claim ID derivation. The word "index" is overloaded; see the confusion list. | `PreparedClaim.index` in `backend/src/space/memory.py` |
| Claim text | The statement itself, for example `Mira drives a car.` It is stored only in the memory's revision, never in the claim index table. | `memory_pipeline.memory_body`, `docs/PRIVACY.md` |
| Claim ID / `entry_id` | `uuid5(memory resource id, claim index)`. It is stable across edits, so an index row can be reconciled and a forgotten claim stays forgotten. Callers see it as `entry_id`. | `memory_pipeline.build`, `memory_pipeline.prepared` |
| Memory type (of a claim) | `factual` or `procedural` for claims written through the API. The database also allows `episodic`, but extraction drops episodic entries, so episodic claims are not created. | `PreparedClaim.memory_type`, `memory_pipeline.build` |
| Importance | The writer's judgement, `0` to `3`, where `0` means undeclared. The retriever cannot infer it, so it is declared rather than counted. | `RememberMemory.importance`, migration `0004_memory_importance.sql` |
| Entry | One stored extraction record: the model's own row with `index`, `value`, `memory_type`, `cue_indices` and `linked_memory`. Entries carry the cue graph; claims are the recallable subset of them. | `memory_pipeline.build`, `space/_vendor/memora/core/memory_entry.py` |
| Cue | A generated label that groups claims which belong together. Cues are stored as entry text and are the input to cue expansion. | `memory_pipeline.build`, `brain.expand` |
| Cue hash | SHA-256 of the normalized cue label, so the index can find cue-sharing claims without storing the label. The hash is unkeyed, unlike the lexical term fingerprints. | `brain.cue_hash`, `backend/src/space/terms.py` |
| Text hash | Unkeyed SHA-256 of the claim text. It detects an edit without keeping the text in the index. | `brain.text_hash`, `brain._desired` |
| Embedding | A vector of the claim text: 1,536 dimensions unless `SPACE_MEMORY_EMBEDDING_DIMENSIONS` sets another width. | `brain.EMBEDDING_DIM`, migration `0003_memory_brain.sql`, `database_setup.ensure_embedding_width` |
| Embedding model | The model name stored beside every vector. Recall filters on it, so two models never share one ranking. | `MemoryModels.embedding_model` |
| Provider fingerprint | The `provider_fingerprint` stored in a memory record: a hash of the provider, the embedding endpoint and the embedding model. Recall compares no fingerprint; the index rows' `embedding_model` decides which claims semantic recall can select, so changing only the chat model needs no rebuild. | `MemoryModels.embedding_fingerprint`, `memory_pipeline.ranked_claims` |
| Forgotten | A claim the user permanently removed. It leaves `claims`, is listed in `forgotten`, and its index row is deleted, so a rebuild does not resurrect it. | `memory_pipeline.change`, `brain.apply` |
| Edited | A claim whose text a human replaced. It keeps an embedding of the new text, loses its generated cues, and is excluded from cue-linked retrieval. | `memory_pipeline.index_rows`, `memory_pipeline.retrieve` |
| Rebuild | Re-running extraction for the same source into the same memory resource. Human edits survive it. | `Workspace.build_memory`, `memory_pipeline.retain_edits` |

## The claim index

| Term | What it means | Defined by |
| --- | --- | --- |
| Claim index (table) | `space.memory_claims`. One row per recallable claim of the current revision, holding `memory_id`, `claim_id`, `revision`, `entry_index`, `memory_type`, `importance`, `text_hash`, `cue_hashes`, `embedding`, `embedding_model`, `source_resource_id`, `source_revision` and `created_at`. | migration `0003_memory_brain.sql` |
| Index row | One row of that table. The table is a set of pointers plus geometry: it ranks without a claim text column. | `backend/src/space/brain.py` |
| Selected for indexing | The claims the write path puts in the index. First every claim of the record, minus forgotten ones. Then, of those, only the claims that need a new vector: new claims, claims whose text hash changed, and claims embedded with a different model. | `memory_pipeline.index_rows`, `brain.pending` |
| Current | A row exists only while its claim is live. The vector recall SQL filters by Space and `embedding_model`, not by revision, because the write path reconciles rows in the same transaction as the revision it describes. The lexical candidate SQL additionally pins the resource's current revision. | `brain.search_statement`, `Workspace._save`, `Workspace._rank_sources` |
| Reconciliation | What one write does to the index rows of one memory: delete removed and changed rows, refresh unchanged rows without paying for a new embedding, insert the rows that hold vectors. | `brain.apply` |
| IndexConflict | Raised when a required vector is missing because the memory changed while embeddings were computed. The caller retries instead of storing a stale row. | `brain.apply` |

## Recall

| Term | What it means | Defined by |
| --- | --- | --- |
| Recall / context | The read operation that returns memory and source evidence for a query. Agents call it as `get_context`. | `RecallRequest`, `Workspace.context` |
| Mode | `search` for a topic query, `resume` for recent handoffs and memories. `resume` needs no query and allows `compact` excerpts. | `RecallRequest.mode` |
| Strategy | `auto`, `lexical`, `semantic` or `prompt`. `auto` prefers the vector path when a provider is configured and memory exists, and falls back to lexical otherwise. | `RecallRequest.strategy`, `Workspace.context` |
| Lexical index | The keyed term-fingerprint table `space.resource_terms`, used for BM25 ranking over bounded candidates. Terms are HMACs under a key derived from the Space key. | migration `0005_resource_terms.sql`, `backend/src/space/terms.py`, `Workspace._rank_sources` |
| Ranked item | One record in the response `items` list. It carries `id`, `revision`, `title`, `author`, `category`, `reason`, `excerpt`, `truncated`, `url` and, for memory, `entry_id`, `memory_type`, `edited` and `importance`. | `memory._entry` |
| Score | For a semantic hit, `1 - cosine distance`. Higher is closer. | `brain.search_statement` |
| Reason | Why an item came back: `matched` for lexical, `memory_semantic` or `memory_prompt` for vector retrieval, `recent_handoff` for `resume`. | `Workspace.context`, `memory_pipeline.retrieve` |
| Verification | The evidence class of an item: `memory` for a claim, `handoff` for a handoff, `user_authored` for everything a person wrote directly. | `memory_pipeline.virtual_memories`, `memory._entry` |
| Evidence assessment | A second, bounded model pass over at most 50 candidates of at most 2000 characters each. It labels each candidate `direct`, `irrelevant`, `historical` or `conflicting` and returns verbatim answer quotes. It is gated by the retrieval setting `assess_relevance`, on by default. | `backend/src/space/relevance.py` |
| Related tier | Claims that share a generated cue with a matched claim. They are not ranked by relevance to the query, they live under their own response key, they are capped at 5, and they are only delivered when the caller sets `include_related`, although the internal `expanding` switch defaults on. They never displace a ranked item. | `brain.expand`, `memory.MAX_RELATED`, `RetrievalConfig.expanding` |
| Stale | The item cites a source revision that is no longer current. It stays readable, and a current-state answer must not use it. | `Workspace._stale`, `relevance.PROMPT` |
| Excerpt, `truncated`, `has_more`, `max_chars` | Output budgeting: each item is excerpted around the query within 4000 characters, and the response respects `max_chars`. | `memory.excerpt`, `memory.bounded_result` |
| Engine | The name of the ranking path that produced the response: `memora-brain-pgvector-v1` or `memora-brain-lexical-v1`. | `brain.ENGINE`, `memory.ENGINE` |
| Empty recall note | The explicit text returned when nothing was found, which tells the caller to report the absence and not to reconstruct memory. | `memory.EMPTY_RECALL_NOTES` |

## Extraction jobs and workers

| Term | What it means | Defined by |
| --- | --- | --- |
| Extraction | The model work that turns text or a current source revision into claims. | `docs/MEMORY.md`, `memory_pipeline.build` |
| Memory job | One queued extraction unit, stored in `space.memory_jobs` with the submitting actor and grant receipt. | migration `0008_memory_jobs.sql` |
| Worker | `python -m space.job_worker`. A supervisor process runs one fresh child per attempt, so one job at a time; only the child opens the one runtime connection and the one queue connection. | `backend/src/space/job_worker.py`, `docs/ARCHITECTURE.md#background-work` |
| Job deadline | The supervisor kills and reaps its child after 240 seconds by default, covering startup, claim, processing, publication and cleanup. `--job-deadline` accepts 1 to 270 seconds, which stays below the 300-second lease. A killed attempt keeps its lease, so recovery waits for lease expiry and a later claim. | `job_worker.JOB_DEADLINE_SECONDS`, `job_worker.supervise`, `backend/tests/test_job_worker.py` |
| Job status | `queued`, `processing`, `ready` or `failed`. `recall_ready` is true only for `ready`. | migration `0008_memory_jobs.sql`, `jobs.public_job` |
| Lease | The right to process one job: `status = 'processing'`, a `lease_id`, and `lease_until = now() + 300 s`. An expired lease makes the job reclaimable, but only by a new claim, so recovery waits for the worker's next check and free capacity. | `jobs.claim`, `jobs.LEASE_SECONDS` |
| Space rotation | Fair selection between Spaces. `space.memory_job_schedule` persists each Space's last claim time; the scheduler prefers a Space with no live extraction, then the least recently served Space, and keeps FIFO order inside that Space. A lone eligible Space can use several workers. | `space.memory_job_schedule` (migration `0010_job_scheduling.sql`), `jobs.claim`, `backend/tests/test_memory_jobs.py` |
| Fencing | Every transaction a job opens rechecks the lease, and publication requires the same `lease_id` with an unexpired lease. An expired worker can neither commit nor publish. | `space.memory_job_lease_active`, `Workspace._scope`, `jobs.finish` |
| Attempt | The `attempts` counter, incremented once per lease. Three attempts may do model work; a later claim refuses new model work with `Extraction attempts exhausted`. | `jobs.MAX_ATTEMPTS`, `jobs.process` |
| Recovery-only attempt | At most one further lease that may only look up the command receipt, never start new model work. If it also expires, or if children keep dying before the attempt check, the queue marks the job `failed` with `recovery_failed`, which means the outcome could not be confirmed. | `jobs.MAX_RECOVERY_ATTEMPTS`, `jobs.claim`, `backend/tests/test_memory_jobs.py` |
| Backoff | The delay before a retry is eligible: `min(60, 2 ** attempts)` seconds. | `jobs.finish` |
| Error code | The machine-readable outcome on a failed job: `access_revoked`, `source_or_revision_changed`, `extraction_failed`, `context_changed`, `temporarily_unavailable` or `recovery_failed`. | `jobs.process`, `jobs.claim` |
| Provider pin | The `provider_fingerprint` stored on a job: a hash of the provider, its endpoint and the text model. A job whose pin differs from the configured chat provider does no new model work; unless its result was already committed, it fails with `Extraction provider changed; submit a new job`. | `MemoryModels.chat_fingerprint`, `jobs.process` |
| Queue bound | At most 100 unfinished jobs per Space. Beyond it, enqueue fails with 503 `This Space's extraction queue is full; retry after jobs finish`. The bound is per Space. | `jobs.MAX_PENDING_PER_SPACE` |
| Idle check | While the queue is empty, a continuously running worker asks one cheap question every 3 seconds (`--poll-interval`) and starts an attempt only when a job is eligible: queued and due, or holding an expired lease. This also picks up delayed retries and crashed attempts. | `job_worker.PENDING_SQL`, `backend/src/space/queue_watch.py` |
| Drain and once | Worker batch flags: `--drain --max-jobs N` runs at most N attempts and exits; `--once` runs at most one. A failed or killed attempt counts toward the batch, and the supervisor exits nonzero when a batch contained one. | `job_worker.main` |

## Index row versus revision

This split is the reason the vocabulary above keeps hashes and vectors separate from text. Both sides are readable by anyone with database access, the index as columns and the revision as JSON; Space does not encrypt content. The index keeps only what ranking needs in SQL, and the text keeps a single home in the revision.

| In the claim index (`space.memory_claims`) | In the memory's revision |
| --- | --- |
| Claim IDs, memory IDs, revisions, entry indices, timestamps | Claim text (`value`) and the memory title |
| `embedding` vector and `embedding_model` name | Cue labels, before hashing |
| `text_hash` and `cue_hashes` | The memory record's `entries` text |
| Claim type and declared importance | Creator, classification and links |

[Data and privacy](PRIVACY.md) lists what is stored and who can read it.

## Limits and defaults

| Limit | Value | Constant or place |
| --- | --- | --- |
| Claims per memory | 24 | `MAX_CLAIMS`, `RememberClaims.claims` |
| Claim text length | 2000 characters | `PreparedClaim.value` |
| Claim index (label) length | 300 characters, unique per memory, and it may not contain the two-character separator the cue format uses | `PreparedClaim.index`, `PreparedClaim.usable` |
| Importance | 0 to 3, default 0 | `PreparedClaim.importance` |
| Topics per memory | 12, each at most 100 characters | `RecallMetadata.topics` |
| Source references per memory | 20 | `RecallMetadata.sources` |
| Cues per claim | 3, enforced by the vendor response schema and the provider prompt, which Space never re-counts | `_vendor/memora/core/cue_index_generator.py`, `ModelSession.invoke` |
| Extraction input size | 12000 characters including the title | `MAX_BUILD_CHARS`, `RememberMemory.text` |
| Embedding dimension | 1536 by default, 16 to 2000 | `SPACE_MEMORY_EMBEDDING_DIMENSIONS`, `brain.EMBEDDING_DIM` |
| Embedding inputs per request | 32 | `ModelSession.embed` |
| Model session | 180 seconds, at most 80 provider calls, at most 400000 bytes of request body | `ModelSession` |
| Completion cap | 6000 tokens, and a 2000000-byte response ceiling | `ModelSession.invoke`, `_post` |
| Candidate cap | 64 claims, 64 cue candidates, multiplier 4 | `RetrievalConfig` |
| HNSW `ef_search` | 40 server default; exact selection is the shipped path | `brain.SERVER_EF_SEARCH`, `RetrievalConfig.selection` |
| Lexical candidates | 256 candidates, 32 bodies ranked | `MAX_LEXICAL_CANDIDATES`, `MAX_LEXICAL_BODIES` |
| Memories per list, and per recall load | 200 listed. Recall loads `max(32, its selected order)`, so the load bound never truncates the ranked or cue-expanded order; the shipped guide states an effective bound of 40 at the default multiplier. | `MAX_MEMORY_LIST`, `MAX_MEMORY_LOAD`, `Workspace.context` |
| Recall request | query at most 500 characters, limit 1 to 10 (default 5), `max_chars` 2000 to 20000 (default 12000) | `RecallRequest` |
| Evidence assessment | 50 records of at most 2000 characters | `relevance.MAX_EVIDENCE`, `MAX_EVIDENCE_CHARS` |
| Recall lane | 12 active, 128 waiting, 5-second queue deadline | `Workspace.__init__` |
| Memory write lane | 2 active, 32 waiting, 5-second queue deadline | `Workspace.__init__` |
| Job queue per Space | 100 unfinished jobs | `jobs.MAX_PENDING_PER_SPACE` |
| Job lease | 300 seconds | `jobs.LEASE_SECONDS` |
| Job deadline per attempt | 240 seconds, 1 to 270 allowed | `job_worker.JOB_DEADLINE_SECONDS` |
| Job attempts | 3 work attempts, plus at most 1 recovery-only attempt | `jobs.MAX_ATTEMPTS`, `jobs.MAX_RECOVERY_ATTEMPTS` |
| Worker batch | 1 to 100 attempts, default 20 | `job_worker.main` |
| Database statement, lock and idle-transaction timeouts | 15 s, 5 s, 30 s | `backend/src/space/database_setup.py`, `worker_setup.py` |

## Words that are easy to confuse

| Word | Two meanings, and how to tell them apart |
| --- | --- |
| claim | A statement inside a memory (`claim` in this page), or the act of taking a job lease (`jobs.claim`). This page says "lease" for the second meaning. |
| index | A claim's short label (`index`), one row of the vector table ("index row"), the whole vector table ("claim index"), the cue graph's entry key (`cue_indices`), and a claim's position (`entry_index`). |
| memory_type | On a claim it is `factual` or `procedural`. On `RememberMemory` it is `chat` or `markdown`, which describes the text format handed to extraction. |
| entry versus claim | An entry is the extractor's own row, including cue-only rows. A claim is one recallable statement derived from a primary entry. |
| memory versus resource | A memory is a resource with a memory record and `memory_kind = 'memory'`. Not every resource is a memory. |
| command versus job | A command is the client operation. A job is the queued work it may create. One command commits one revision, whether or not a job was needed. |
| provider versus model | The provider is the endpoint and credential; the model is the pinned name. The provider fingerprint covers both, plus the URL. |
| source versus evidence | A source is a specific resource revision. Evidence is any item a recall returned that supports an answer, including memory. |
| selected | At write time, "selected for indexing" means which claims enter the index. At read time, "selected claims" means the ranking's winners. They are different sets. |
| attempt versus job | One job may be attempted several times: three work attempts plus one recovery-only attempt. `--max-jobs` bounds attempts in a batch, not distinct jobs. |

## Where to read more

| Document | What it adds |
| --- | --- |
| [Space memory](MEMORY.md) | The write paths, provider setup and memory limits in full. |
| [Retrieval](RETRIEVAL.md) | The ranking contract, candidate bounds and the no-silent-under-return clause. |
| [Data and privacy](PRIVACY.md) | What is stored, who can read it and what reaches a model provider. |
| [Database](DATABASE.md) | Every table, its policy and its grants. |
| [Architecture](ARCHITECTURE.md) | Process and module boundaries, the queues and leases, and which module owns which vocabulary. |
| [MCP access](MCP.md) | The tool names an agent calls, with their inputs. |
| [Questions and answers](QA.md) | Short answers to the questions this system gets asked. |
