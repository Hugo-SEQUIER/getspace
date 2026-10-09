"""Memora's text pipeline over bounded sources that Workspace authorizes.

No file access or persistence here. A store belongs to one authorized operation.
Source revisions and edits are controlled by Workspace, never by a model.
Embeddings do not live in the memory record: brain.py indexes them in PostgreSQL.
"""

import json
import re
from copy import deepcopy
from uuid import UUID, uuid5

from pydantic import Field, model_validator
from rank_bm25 import BM25Plus

from space._vendor.memora.core.memory_entry import MemoryEntry
from space._vendor.memora.memora_client import MemoraClient
from space.memory import Input, SourceReference, document_text, tokens
from space.model_provider import ModelError

MAX_BUILD_CHARS = 12000
MAX_CLAIMS = 24
MAX_ENTRIES = 128


class BuildMemory(Input):
    command_id: UUID
    source: SourceReference
    resource_id: UUID | None = None
    expected_revision: int = Field(default=0, ge=0, strict=True)

    @model_validator(mode="after")
    def valid_revision(self):
        if bool(self.resource_id) != (self.expected_revision > 0):
            raise ValueError("Rebuild requires the memory resource ID and current revision")
        return self


class Config(dict):
    def __getattr__(self, name):
        return self[name]


def configuration(store, model):
    return Config(
        space_store=store,
        space_model=model,
        memory=Config(
            query_score_threshold=0.2,
            update_score_threshold=0.9,
            multimodal_support=False,
            enable_cue_index=True,
            enable_episodic_memory=True,
            use_segments_as_episodic=True,
            enhance_query=False,
            return_history=False,
            cue_top_k=10,
            top_k=10,
            enable_hybrid_search=True,
            hybrid_search_method="bm25",
            hybrid_top_k=10,
            bm25_score_threshold=0,
        ),
        retrieval=Config(enable_llm_filter=False, enable_relaxed_frontier=False),
        eval=Config(max_workers=1),
    )


def matches(entry, where):
    if not where:
        return True
    if "$and" in where:
        return all(matches(entry, item) for item in where["$and"])
    for key, condition in where.items():
        value = getattr(entry, key, None)
        if isinstance(condition, dict):
            for op, expected in condition.items():
                if op == "$eq" and value != expected:
                    return False
                if op == "$ne" and value == expected:
                    return False
                if op == "$in" and value not in expected:
                    return False
                if op not in {"$eq", "$ne", "$in"}:
                    raise ValueError("Unsupported memory filter")
        elif value != condition:
            return False
    return True


def embedding_text(entry):
    return entry.index + ("\n" + entry.value if entry.value else "")


class SpaceMemoryStore:
    """Memora storage contract with in-memory cosine/BM25 search and no telemetry.

    Parameters: the model session, any entries to load and no persisted vectors.
    What it does: serves Memora's builders and retrievers during one operation.
    Output: entry lookup, filtered search and a list of entries for the caller.
    """

    def __init__(self, model, entries=()):
        self.model = model
        self.entries = {e.index: e.model_copy(deep=True) for e in entries}
        self.vectors = {}
        self._bm25_indices = {}

    def get(self, key):
        entry = self.entries.get(key)
        return entry.model_copy(deep=True) if entry else None

    def upsert(self, index, value, metadata=None):
        if index not in self.entries and len(self.entries) >= MAX_ENTRIES:
            raise ModelError("Memory extraction exceeded the supported number of entries")
        metadata = {**(metadata or {}), "index": index, "value": value}
        for key in ("history", "image_urls", "episodic_memory_ids"):
            if isinstance(metadata.get(key), str):
                metadata[key] = json.loads(metadata[key])
        entry = MemoryEntry.model_validate(metadata)
        if entry.memory_type:
            entry.memory_type = entry.memory_type.lower()
        if "||" in index or len(index) > 300 or len(value) > MAX_BUILD_CHARS:
            raise ModelError("Memory model returned an unsupported entry")
        if any(len(cue) > 100 or "\n" in cue for cue in entry.get_cue_indices()):
            raise ModelError("Memory model returned an unsupported cue")
        self.entries[index] = entry
        return index

    def delete(self, key):
        self.entries.pop(key, None)

    def clear(self):
        self.entries.clear()
        self.vectors.clear()

    def count(self):
        return len(self.entries)

    def list_memories(self, limit=20):
        return [e.model_copy(deep=True) for e in list(self.entries.values())[:limit]]

    def _vectorize(self, entries):
        missing = [embedding_text(e) for e in entries if embedding_text(e) not in self.vectors]
        if missing:
            for text, vector in zip(missing, self.model.embed(missing), strict=True):
                self.vectors[text] = vector

    def query(self, query, k=5, where=None, include=None):
        entries = [e for e in self.entries.values() if matches(e, where)]
        if not entries:
            return []
        self._vectorize(entries)
        vector = self.model.embed([query])[0]
        results = []
        for entry in entries:
            stored = self.vectors[embedding_text(entry)]
            if len(stored) != len(vector):
                raise ModelError("Embedding dimensions changed; rebuild this memory")
            score = sum(a * b for a, b in zip(vector, stored, strict=True))
            results.append(entry.model_copy(update={"score": score}, deep=True))
        return sorted(results, key=lambda e: (-e.score, e.index))[:k]

    def build_bm25_index(self, user_id=None):
        # Memora expects this marker; the eligible corpus is immutable during one
        # retrieval and ranking never writes an on-disk index.
        self._bm25_indices[user_id] = True

    def bm25_search(self, query, k=10, where=None, threshold=0):
        entries = [e for e in self.entries.values() if matches(e, where) and e.is_primary_index()]
        if not entries:
            return []
        words = [tokens(embedding_text(e)) or [""] for e in entries]
        terms = tokens(query)
        scores = BM25Plus(words).get_scores(terms)
        return sorted(
            [
                e.model_copy(update={"score": float(score)}, deep=True)
                for e, text, score in zip(entries, words, scores, strict=True)
                if set(text).intersection(terms) and score > threshold
            ],
            key=lambda e: (-e.score, e.index),
        )[:k]


def build(source, batch_id, model, provider_fingerprint, importance=0) -> dict:
    """Extract proposed memory from one source revision with the pinned Memora client.

    Parameters: the source record with title/body/recall, the memory resource ID used
    as the deterministic claim namespace, the model session, the provider pin and the
    importance the writer declares for the claims this build produces.
    What it does: runs the chat or document builder over bounded segments.
    Output: the memory record and the claim rows the index should hold.
    """
    text = source["title"] + "\n" + document_text(source["body"])
    if len(text) > MAX_BUILD_CHARS:
        raise ModelError(
            "Memory building supports 12,000 characters per source; split the document"
        )
    store = SpaceMemoryStore(model)
    client = MemoraClient(cfg=configuration(store, model), user_id=str(batch_id))
    for offset in range(0, len(text), 6000):
        client.add(
            text[offset : offset + 6000],
            type="chat" if source["recall"]["category"] == "handoff" else "markdown",
            metadata={
                "timestamp": source["updated_at"],
                "segment_topic": source["title"][:200],
                "segment_index": offset // 6000,
            },
        )
    claims, index = [], []
    for entry in store.entries.values():
        if not entry.is_primary_index() or entry.memory_type == "episodic":
            continue
        if not entry.value.strip() or len(entry.value) > 2000:
            raise ModelError("Memory extraction returned an unsupported claim")
        claim_id = str(uuid5(batch_id, entry.index))
        claims.append(
            {
                "id": claim_id,
                "index": entry.index,
                "value": entry.value,
                "memory_type": entry.memory_type or "factual",
                "edited": False,
                "importance": importance,
                "created_at": source["updated_at"],
            }
        )
        index.append(
            {
                "id": claim_id,
                "entry_index": len(index),
                "memory_type": entry.memory_type or "factual",
                "importance": importance,
                "text": entry.value,
                "cues": [cue for cue in entry.get_cue_indices() if cue.strip()],
            }
        )
    if len(claims) > MAX_CLAIMS:
        raise ModelError("Memory building supports 24 claims per source; split the document")
    value = {
        "version": 2,
        "provider_fingerprint": provider_fingerprint,
        "claims": claims,
        "forgotten": [],
        "entries": [entry.model_dump() for entry in store.entries.values()],
        "build_stats": client.get_last_build_stats(),
    }
    if len(json.dumps(value).encode()) > 850_000:
        raise ModelError("Memory exceeds its size limit; split the document")
    return {"value": value, "index": index}


def stored_entries(value) -> list[dict]:
    """Cue-graph entries of a memory record.

    Parameters: one decrypted memory record.
    What it does: reads the current `entries` field, or the `index.entries` field used
    by records written before the pgvector index existed.
    Output: the stored Memora entries, or an empty list.
    """
    if "entries" in value:
        return value["entries"]
    return value.get("index", {}).get("entries", [])


def prepared(request, resource_id, provider_fingerprint, created_at):
    """Convert validated claims to the existing recall format, with no generated cues."""
    claims = [
        # Routing is stored as the claim's own assignment, not as the request field.
        claim.model_dump(exclude={"routes_to"})
        | {
            "id": str(uuid5(resource_id, claim.index)),
            "edited": False,
            "created_at": created_at,
        }
        for claim in request.claims
    ]
    return {
        "version": 2,
        "provider_fingerprint": provider_fingerprint,
        "input_kind": "structured_claims",
        **(
            {"provenance": request.provenance.model_dump(mode="json")} if request.provenance else {}
        ),
        "claims": claims,
        "forgotten": [],
        "entries": [
            MemoryEntry(
                index=claim.index, value=claim.value, memory_type=claim.memory_type
            ).model_dump()
            for claim in request.claims
        ],
        "build_stats": {},
    }


def authored(request, resource_id, provider_fingerprint, created_at):
    """Keep authored text verbatim, splitting only to fit the stored claim size limit."""
    parts = [request.text[offset : offset + 2000] for offset in range(0, len(request.text), 2000)]
    title = re.sub(r"\|{2,}", "|", request.title)
    claims = []
    for number, value in enumerate(parts, 1):
        index = title if len(parts) == 1 else f"{title[:280]} ({number}/{len(parts)})"
        claims.append(
            {
                "id": str(uuid5(resource_id, index)),
                "index": index,
                "value": value,
                "memory_type": "factual",
                "importance": request.importance,
                "edited": False,
                "created_at": created_at,
            }
        )
    return {
        "version": 2,
        "provider_fingerprint": provider_fingerprint,
        "input_kind": "authored_text",
        "claims": claims,
        "forgotten": [],
        "entries": [
            MemoryEntry(index=c["index"], value=c["value"], memory_type="factual").model_dump()
            for c in claims
        ],
        "build_stats": {},
    }


def index_rows(value) -> list[dict]:
    """Index rows for the current claims of a stored memory record.

    Parameters: one decrypted memory record.
    What it does: pairs each claim with the cue labels generated for it, so an edit
    or removal can be re-indexed without re-running extraction.
    Output: rows ready for brain.apply.
    """
    entries = {str(entry.get("index")): entry for entry in stored_entries(value)}
    rows = []
    for claim in value["claims"]:
        generated = str(entries.get(str(claim["index"]), {}).get("cue_indices", ""))
        cues = [] if claim.get("edited") else [cue for cue in generated.split("||") if cue.strip()]
        rows.append(
            {
                "id": claim["id"],
                "entry_index": len(rows),
                "memory_type": claim.get("memory_type", "factual"),
                "importance": claim.get("importance", 0),
                "text": claim["value"],
                "cues": cues,
            }
        )
    return rows


def public_memory(value):
    """Client-visible claims and import evidence, without the internal cue graph."""
    return {
        "claims": deepcopy(value["claims"]),
        "version": value["version"],
        **({"provenance": deepcopy(value["provenance"])} if value.get("provenance") else {}),
        **({"links": deepcopy(value["links"])} if "links" in value else {}),
    }


def retain_edits(memory, previous) -> None:
    """Keep human edits and forgotten memories across a rebuild of the same source."""
    if "links" in previous:
        memory["links"] = deepcopy(previous["links"])
    before = {claim["id"]: claim for claim in previous["claims"]}
    forgotten = set(previous.get("forgotten", []))
    kept = []
    for claim in memory["claims"]:
        if claim["id"] in forgotten:
            continue
        prior = before.get(claim["id"])
        if prior and prior.get("edited"):
            claim.update(
                value=prior["value"],
                edited=True,
                generated_value=claim["value"],
                created_at=prior.get("created_at", claim["created_at"]),
            )
        if prior and prior.get("classification", {}).get("method") == "manual":
            claim["classification"] = deepcopy(prior["classification"])
        if prior and "creator" in prior:
            claim["creator"] = deepcopy(prior["creator"])
        if prior and prior["value"] == claim["value"] and "relationships" in prior:
            claim["relationships"] = deepcopy(prior["relationships"])
        kept.append(claim)
    memory["claims"] = kept
    memory["forgotten"] = sorted(forgotten)


def change(memory, entry_id: str, action: str, value: str | None = None) -> None:
    """Apply one edit or permanent removal to a memory record."""
    claim = next((c for c in memory["claims"] if c["id"] == entry_id), None)
    if not claim:
        raise KeyError(entry_id)
    if action == "forget":
        memory["claims"] = [c for c in memory["claims"] if c["id"] != entry_id]
        memory["forgotten"] = sorted(set(memory.get("forgotten", [])) | {entry_id})
        return
    claim.setdefault("generated_value", claim["value"])
    claim["value"] = value
    claim["edited"] = True


def memory_body(value):
    return "\n\n".join(f"## {c['index']}\n{c['value']}" for c in value["claims"]) or "No memory."


def virtual_memories(resource, *, include_forgotten=False):
    """Present current claims of one memory resource as recallable items."""
    value = resource.get("_memory")
    if not value:
        return []
    forgotten = set() if include_forgotten else set(value.get("forgotten", []))
    return [
        {
            **resource,
            "entry_id": claim["id"],
            "title": claim["index"],
            "resource_title": resource["title"],
            "body": claim["value"],
            "verification": "memory",
            "review_state": "live",
            "memory_type": claim.get("memory_type", "factual"),
            "edited": bool(claim.get("edited")),
            "classification": deepcopy(claim.get("classification")),
            "creator": deepcopy(claim.get("creator")),
            "importance": int(claim.get("importance", 0)),
            "author": "agent",
        }
        for claim in value["claims"]
        if claim["id"] not in forgotten
    ]


def ranked_claims(resources, allowed, scores):
    """Present the exact SQL candidates without re-embedding their stored text.

    The caller passes the claims the vector index selected for the configured embedding
    model, so a memory whose claims were embedded by another model never appears here.
    No provider fingerprint is compared: a chat-model change does not invalidate stored
    vectors or cues, and an embedding-model change is already enforced per claim.
    """
    views = []
    for resource in resources:
        data = resource.get("_memory")
        keep = allowed.get(str(resource["id"]), set())
        if not data or not keep:
            continue
        for view in virtual_memories(resource):
            if view["entry_id"] in keep:
                score = scores[str(resource["id"])][view["entry_id"]]
                views.append(view | {"score": score})
    return sorted(views, key=lambda view: (-view["score"], view["entry_id"]))


def retrieve(resources, request, model, allowed, scores=None):
    """Memora semantic or prompted retrieval over the authorized candidate memories.

    Parameters: memory resources, the request, the model session, a mapping of memory ID
    to the claim IDs the vector index selected for the configured embedding model, and
    optionally those claims' cosine similarities by memory ID and claim ID.
    What it does: rebuilds a bounded cue graph for those claims only, then asks the
    pinned Memora retriever. Claims outside the candidate set are never reachable.
    Output: ranked (item, reason) pairs. The caller merges these with lexical results.
    """
    entries, by_key, shared_cues = [], {}, {}
    for resource in resources:
        data = resource.get("_memory")
        keep = allowed.get(str(resource["id"]), set())
        if not data or not keep:
            continue
        views = {v["entry_id"]: v for v in virtual_memories(resource)}
        current = {c["id"]: c for c in data["claims"]}
        # The index selects stable claim IDs; stored entries are keyed by entry index.
        claim_of = {c["index"]: c["id"] for c in data["claims"]}
        raw = [MemoryEntry.model_validate(entry) for entry in stored_entries(data)]
        prefix = f"{resource['id']}@{resource['revision']}:"
        selected, editable = [], {}
        for entry in raw:
            if entry.is_primary_index():
                claim_id = claim_of.get(entry.index)
                if claim_id not in keep or claim_id not in views:
                    continue
                claim = current[claim_id]
                if claim.get("edited"):
                    # An edited memory must not retrieve through its old generated cues.
                    entry.cue_indices = ""
                    entry.value = claim["value"]
                # Carry the vector similarity so the caller can apply a
                # relevance floor and show why a claim was returned.
                view = views[claim_id]
                score = (scores or {}).get(str(resource["id"]), {}).get(claim_id)
                by_key[prefix + entry.index] = view if score is None else view | {"score": score}
                selected.append(entry)
            else:
                links = [
                    k
                    for k in entry.get_linked_memories()
                    if claim_of.get(k) in keep and not current[claim_of[k]].get("edited")
                ]
                if not links:
                    continue
                entry.linked_memory = "||".join(prefix + k for k in links)
                editable[entry.index] = entry
        for entry in selected:
            entry.index = prefix + entry.index
            entry.episodic_memory_ids = []
        for entry in editable.values():
            original = entry.index
            entry.index = "cue:" + " ".join(tokens(original))
            if entry.index in shared_cues:
                entry.linked_memory = "||".join(
                    sorted(
                        set(entry.get_linked_memories())
                        | set(shared_cues[entry.index].get_linked_memories())
                    )
                )
            shared_cues[entry.index] = entry
        entries.extend(selected)
    if not by_key:
        return []
    store = SpaceMemoryStore(model, entries + list(shared_cues.values()))
    client = MemoraClient(cfg=configuration(store, model), user_id="authorized-space-snapshot")
    results = client.advance_query(
        request.query,
        top_k=request.limit,
        query_type="prompt" if request.strategy == "prompt" else "semantic",
    )
    return [
        (by_key[entry.index], "memory_" + request.strategy)
        for entry in results
        if entry.index in by_key
    ]
