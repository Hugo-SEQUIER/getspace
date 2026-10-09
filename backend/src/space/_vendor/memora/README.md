# Memora text inference runtime

Source: https://github.com/microsoft/Memora
Pinned commit: `dec3f8f2444eace7004fc084abe1be9f3d88270e`
License: MIT, retained in LICENSE.

The import includes the upstream MemoraClient, AgentMemory, chat/document builders, cue generation/expansion, semantic retrieval and prompted policy retrieval. Space calls these algorithms through `memory_pipeline.py`. The original three top-level imports remain compatibility reexports.

## Space adaptations

- Package imports use this namespace. Configuration and model/store construction use injected Space adapters instead of OmegaConf, Chroma, Redis or upstream model clients. No independent plaintext database is created.
- SpaceMemoryStore supplies bounded in-memory cosine/BM25 search. The pipeline serializes claims and cue-graph entries inside a normal Space resource revision. Embeddings are stored separately in PostgreSQL by `brain.py`; they are not inside that payload.
- Content-bearing prints/logging and permissive error fallbacks are removed. Failures propagate with content-free errors. Unused token counting and optional heavy dependencies are omitted.
- Query filters preserve caller restrictions and include procedural claims. Deduplication uses revision-bound entry identity instead of equal text. Semantic retrieval permits AgentMemory's primary/cue branch filters. Space joins shared cues only across currently eligible sources.
- Changed claims with colliding indexes are updated or assigned a unique index instead of silently dropped. The document episodic builder accepts the segment argument passed by its caller. Typed model outputs have explicit size bounds; Space validates policy responses and review state separately.
- Ruff formatting/import cleanup is applied. Long upstream prompts retain an E501 exception. Local model/log adapters live under `utils/`.

Only text inference is supported at the Space boundary: callers supply strings and configuration disables multimodal ingestion, although upstream multimodal helpers remain. Remote Memora service mode, file parsers and GRPO/research training raise explicit errors or are omitted. Research evaluation datasets/harnesses and upstream persistence are not shipped. This does not establish the paper's benchmark quality or full upstream API compatibility.

Update only against a pinned revision. Review prompt/algorithm changes and run pipeline, provider, Workspace and fresh-client tests. See [Space memory](../../../../../docs/MEMORY.md) for the product boundary.
