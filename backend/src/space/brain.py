"""The autonomous memory brain: a pgvector index over memory claims.

The caller holds keys, content, model access and database scope; in production
that caller is Workspace. This module owns the SQL for the memory index and the
ranking rules that turn a query into candidates. It never calls a model and never
writes a resource revision.

Held here: embeddings, text and cue hashes, opaque identifiers, claim type, model
name, importance, revision and entry indices, and timestamps. The claim text itself lives
in the memory's resource revision.

Embedding is deliberately computed outside any database transaction, because the
restricted runtime login closes idle transactions after 30 seconds. Callers run
`pending` to learn what needs a vector, embed outside the transaction, then call
`apply` with those vectors.
"""

import hashlib
from collections.abc import Iterable
from contextlib import contextmanager
from dataclasses import dataclass

from space import memory_link_index
from space.memory import tokens

# The width of space.memory_claims.embedding. Migration 0003 creates vector(1536), the
# size of text-embedding-3-small; the installer resizes an empty index to
# SPACE_MEMORY_EMBEDDING_DIMENSIONS, and processes read the actual width at startup.
EMBEDDING_DIM = 1536
DIMENSIONS_SQL = (
    "SELECT atttypmod AS dimensions FROM pg_attribute "
    "WHERE attrelid='space.memory_claims'::regclass AND attname='embedding'"
)
ENGINE = "memora-brain-pgvector-v1"
MAX_CANDIDATES = 64
MAX_CUE_CANDIDATES = 64

# Server defaults, restated so a configuration can name what it is changing.
SERVER_EF_SEARCH = 40
SCAN_MODES = ("off", "relaxed_order", "strict_order")
EXPANSION_ORDERS = ("index", "overlap", "vector")


@dataclass(frozen=True)
class RetrievalConfig:
    """Candidate selection and the tuning surface for historical ANN experiments.

    Parameters: the HNSW search settings and the candidate bounds.
    What it does: names every knob the recall path can turn, so the evaluation
    harness can compare configurations without editing query code.
    Output: an immutable configuration. The defaults are exactly the shipped
    behaviour, so measuring the baseline needs no special case.
    """

    selection: str = "exact"
    # False is for historical threshold/control evaluations only.
    assess_relevance: bool = True
    # Fuse exact-word claim candidates inside the same bounded memory candidate pool.
    lexical_claims: bool = True
    ef_search: int | None = None
    iterative_scan: str = "off"
    scan_multiplier: int | None = None
    max_scan_tuples: int | None = None
    expansion_order: str = "index"
    # Cue expansion adds claims that share a generated cue with a selected claim.
    # Measured on the 150-claim corpus it changes no quality metric to four decimal
    # places while multiplying the candidate count by 2.6 and latency by 5.9, and
    # on the product fixture recall is better without it. No query could be built
    # that expansion uniquely answers, because corpus cues are labels derived from
    # the claim text. The associative benefit is therefore unmeasured rather than
    # disproved, so the switch exists but the shipped default keeps it on.
    expanding: bool = True
    # Cosine similarity below which a vector hit is treated as no match at all.
    # None means no floor, which is the shipped behaviour: nearest-neighbour
    # search always has a nearest, so without a floor an unrelated query still
    # returns claims and the caller cannot tell relevance from noise.
    score_floor: float | None = None
    candidate_multiplier: int = 4
    max_candidates: int = MAX_CANDIDATES
    max_cue_candidates: int = MAX_CUE_CANDIDATES

    def __post_init__(self):
        if self.selection not in {"exact", "approximate"}:
            raise ValueError("selection must be exact or approximate")
        if self.iterative_scan not in SCAN_MODES:
            raise ValueError(f"iterative_scan must be one of {SCAN_MODES}")
        if self.expansion_order not in EXPANSION_ORDERS:
            raise ValueError(f"expansion_order must be one of {EXPANSION_ORDERS}")
        if self.score_floor is not None and not -1.0 <= self.score_floor <= 1.0:
            raise ValueError("score_floor must be a cosine similarity between -1 and 1")
        if self.ef_search is not None and self.ef_search < 1:
            raise ValueError("ef_search must be at least 1")
        if self.candidate_multiplier < 1:
            raise ValueError("candidate_multiplier must be at least 1")


DEFAULT_RETRIEVAL = RetrievalConfig()


def load_dimensions(connection) -> int:
    """Adopt the claim index's embedding width from the schema, its source of truth."""
    global EMBEDDING_DIM
    row = connection.execute(DIMENSIONS_SQL).fetchone()
    value = row["dimensions"] if isinstance(row, dict) else row[0]
    if not isinstance(value, int) or value < 1:
        raise RuntimeError("The memory index has no fixed embedding width")
    EMBEDDING_DIM = value
    return value


def _load_vector_settings(connection) -> None:
    """Load the vector library so its HNSW settings exist in this session.

    pgvector registers the hnsw.* settings when the library is first used in a
    backend, so a pooled connection that has not touched the type yet cannot set
    them. Probing the type once makes the settings available.
    """
    connection.execute("SELECT '[1]'::vector")


def _wanted_settings(config: RetrievalConfig) -> dict:
    """The HNSW settings a configuration changes, ignoring everything else."""
    wanted = {
        "hnsw.ef_search": config.ef_search,
        "hnsw.iterative_scan": config.iterative_scan,
        "hnsw.scan_mem_multiplier": config.scan_multiplier,
        "hnsw.max_scan_tuples": config.max_scan_tuples,
    }
    return {
        name: value
        for name, value in wanted.items()
        if value is not None and not (name == "hnsw.iterative_scan" and value == "off")
    }


def _apply(connection, wanted: dict) -> None:
    _load_vector_settings(connection)
    for name, value in wanted.items():
        connection.execute("SELECT set_config(%s, %s, true)", (name, str(value)))


def configure(connection, config: RetrievalConfig) -> None:
    """Apply the session-local HNSW settings a configuration asks for.

    Parameters: an open connection and a retrieval configuration.
    What it does: sets the settings the configuration names, for this transaction.
        The caller must already be inside a transaction; use `settings_in_force`
        when that is not guaranteed.
    Output: None. Does nothing for the default configuration.
    """
    wanted = _wanted_settings(config)
    if wanted:
        _apply(connection, wanted)


@contextmanager
def settings_in_force(connection, config: RetrievalConfig):
    """Guarantee that the configured HNSW settings are in force for the query.

    Parameters: an open connection and a retrieval configuration.
    What it does: `set_config(..., true)` is SET LOCAL, so it is discarded at the
        end of the current transaction, and in autocommit that is the very statement
        that sets it: the setting silently does nothing. This opens a transaction
        when the connection is in autocommit, so the settings and the query share
        one, and does nothing at all for the default configuration.
    Output: a context manager to run the query inside.
    """
    wanted = _wanted_settings(config)
    if not wanted:
        yield
        return
    if getattr(connection, "autocommit", False):
        with connection.transaction():
            _apply(connection, wanted)
            yield
        return
    _apply(connection, wanted)
    yield


class IndexConflict(Exception):
    """The index changed between planning embeddings and applying them."""


class DimensionError(Exception):
    """The embedding model does not match the vector column in migration 0003."""


def cue_hash(topic: str) -> bytes:
    """Hash a topic label so shared cues join without readable cue text."""
    return hashlib.sha256(" ".join(tokens(topic)).encode()).digest()


def text_hash(text: str) -> bytes:
    """Fingerprint claim text so unchanged memories keep their embedding."""
    return hashlib.sha256(text.encode()).digest()


def vector_literal(values: Iterable[float]) -> str:
    """Render a normalized embedding for a %s::vector parameter."""
    items = list(values)
    if len(items) != EMBEDDING_DIM:
        raise DimensionError(
            f"The configured embedding model returned {len(items)} dimensions; "
            f"the memory index expects {EMBEDDING_DIM}"
        )
    return "[" + ",".join(f"{value:.9g}" for value in items) + "]"


def _desired(claims: list[dict]) -> dict:
    return {
        claim["id"]: {
            "entry_index": claim["entry_index"],
            "memory_type": claim["memory_type"],
            "importance": int(claim.get("importance", 0)),
            "text": claim["text"],
            "text_hash": text_hash(claim["text"]),
            "cue_hashes": sorted({cue_hash(topic) for topic in claim["cues"] if tokens(topic)}),
        }
        for claim in claims
    }


def _stored(connection, memory_id) -> dict:
    return {
        str(row["claim_id"]): row
        for row in connection.execute(
            "SELECT claim_id, text_hash, embedding_model FROM space.memory_claims "
            "WHERE memory_id=%s",
            (memory_id,),
        ).fetchall()
    }


def pending(connection, *, memory_id, claims: list[dict], model_name: str) -> list[dict]:
    """Claims that need a new embedding, because they are new, edited or re-modelled.

    Parameters: an open connection already scoped to the Space, the memory identity,
    the intended claim list and the configured embedding model name.
    What it does: compares intended claims with the stored index rows.
    Output: the claim records whose text changed, plus every claim when the model did.
    """
    desired, stored = _desired(claims), _stored(connection, memory_id)
    return [
        {"id": claim_id, "text": want["text"]}
        for claim_id, want in desired.items()
        if claim_id not in stored
        or bytes(stored[claim_id]["text_hash"]) != want["text_hash"]
        or stored[claim_id]["embedding_model"] != model_name
    ]


def apply(
    connection,
    *,
    memory_id,
    revision: int,
    claims: list[dict],
    model_name: str | None,
    vectors: dict,
    source_resource_id=None,
    source_revision: int | None = None,
) -> dict:
    """Reconcile the index rows of one memory with its current claim list.

    Parameters: an open connection scoped to the Space, the memory identity, its new
    revision, the claim list, the embedding model and the vectors already embedded.
    A None model preserves existing embeddings during deletion; it cannot insert
    or change claim text.
    What it does: removes forgotten claims, refreshes unchanged rows without paying for
    another embedding, and inserts new or edited claims.
    Output: counts of inserted, refreshed and removed rows. Raises IndexConflict when a
    required vector is missing, so the caller retries instead of storing a stale row.
    """
    desired, stored = _desired(claims), _stored(connection, memory_id)
    removed = [claim_id for claim_id in stored if claim_id not in desired]
    missing = [
        claim_id
        for claim_id in desired
        if claim_id not in stored
        or bytes(stored[claim_id]["text_hash"]) != desired[claim_id]["text_hash"]
        or (model_name is not None and stored[claim_id]["embedding_model"] != model_name)
    ]
    absent = [claim_id for claim_id in missing if claim_id not in vectors]
    if absent or (missing and model_name is None):
        raise IndexConflict("Memory changed while embeddings were computed; retry")
    changed = [
        claim_id
        for claim_id in missing
        if claim_id in stored
        and bytes(stored[claim_id]["text_hash"]) != desired[claim_id]["text_hash"]
    ]
    if removed or changed:
        connection.execute(
            "DELETE FROM space.memory_links WHERE "
            "(source_memory_id=%s AND source_claim_id=ANY(%s::uuid[])) OR "
            "(target_memory_id=%s AND target_claim_id=ANY(%s::uuid[]))",
            (memory_id, removed + changed, memory_id, removed + changed),
        )
    if removed or missing:
        connection.execute(
            "DELETE FROM space.memory_claims WHERE memory_id=%s AND claim_id=ANY(%s::uuid[])",
            (memory_id, removed + missing),
        )
    refreshed = [claim_id for claim_id in desired if claim_id not in missing]
    for claim_id in refreshed:
        want = desired[claim_id]
        connection.execute(
            "UPDATE space.memory_claims SET revision=%s, entry_index=%s, memory_type=%s, "
            "cue_hashes=%s, importance=%s, source_resource_id=%s, source_revision=%s "
            "WHERE memory_id=%s AND claim_id=%s",
            (
                revision,
                want["entry_index"],
                want["memory_type"],
                want["cue_hashes"],
                want["importance"],
                source_resource_id,
                source_revision,
                memory_id,
                claim_id,
            ),
        )
    if missing:
        connection.cursor().executemany(
            "INSERT INTO space.memory_claims(organization_id, space_id, memory_id, claim_id, "
            "revision, entry_index, memory_type, importance, text_hash, cue_hashes, embedding, "
            "embedding_model, source_resource_id, source_revision) VALUES "
            "(space.requested_organization_id(), space.requested_space_id(), %s, %s, %s, %s, %s, "
            "%s, %s, %s, %s::vector, %s, %s, %s)",
            [
                (
                    memory_id,
                    claim_id,
                    revision,
                    desired[claim_id]["entry_index"],
                    desired[claim_id]["memory_type"],
                    desired[claim_id]["importance"],
                    desired[claim_id]["text_hash"],
                    desired[claim_id]["cue_hashes"],
                    vector_literal(vectors[claim_id]),
                    model_name,
                    source_resource_id,
                    source_revision,
                )
                for claim_id in missing
            ],
        )
    memory_link_index.prune(connection, memory_id)
    return {"inserted": len(missing), "refreshed": len(refreshed), "removed": len(removed)}


def forget(connection, memory_id) -> int:
    """Parameters: scoped connection, node. What it does: forgets claims. Output: count.
    Explicit node links remain until the node itself is deleted or its links are edited.
    """
    connection.execute(
        "DELETE FROM space.memory_links WHERE source_memory_id=%s OR target_memory_id=%s",
        (memory_id, memory_id),
    )
    return connection.execute(
        "DELETE FROM space.memory_claims WHERE memory_id=%s", (memory_id,)
    ).rowcount


def search_statement(
    vector, model_name, limit, config=DEFAULT_RETRIEVAL, *, exclude_memory_id=None
):
    """Share the actual serving SQL with EXPLAIN and evaluation callers.

    Materialize only scoped identifiers and distances, never the vectors. The
    boundary prevents an ordered global HNSW scan from choosing candidates before
    owner/Space/model filtering. RLS still checks the caller's live authority.
    """
    literal = vector_literal(vector)
    exclusion = " AND memory_id<>%s" if exclude_memory_id is not None else ""
    excluded = (exclude_memory_id,) if exclude_memory_id is not None else ()
    if config.selection == "exact":
        return (
            "WITH scoped AS MATERIALIZED ("  # noqa: S608 - exclusion is a fixed SQL fragment
            "SELECT memory_id, claim_id, entry_index, revision, "
            "embedding <=> %s::vector AS distance FROM space.memory_claims "
            "WHERE organization_id=space.requested_organization_id() "
            "AND space_id=space.requested_space_id() AND embedding_model=%s" + exclusion + ") "
            "SELECT memory_id, claim_id, entry_index, revision, 1-distance AS score "
            "FROM scoped ORDER BY distance, claim_id LIMIT %s",
            (literal, model_name, *excluded, limit),
        )
    return (
        "SELECT memory_id, claim_id, entry_index, revision, "  # noqa: S608 - fixed fragment
        "1 - (embedding <=> %s::vector) AS score FROM space.memory_claims "
        "WHERE embedding_model=%s"
        + exclusion
        + " ORDER BY embedding <=> %s::vector, claim_id LIMIT %s",
        (literal, model_name, *excluded, literal, limit),
    )


def search(
    connection,
    vector: list[float],
    model_name: str,
    limit: int,
    *,
    config: RetrievalConfig = DEFAULT_RETRIEVAL,
    exclude_memory_id=None,
) -> list[dict]:
    """Nearest current claims for one query embedding, best first.

    Parameters: an open connection scoped to the Space, the query embedding, the
    configured embedding model, the row limit, retrieval configuration and optional
    focal memory ID to exclude in SQL before limiting.
    What it does: exactly ranks every authorized claim of the requested model by
    cosine distance before limiting results. claim_id breaks ties. Approximation
    is retained only as an explicit evaluation control, never a serving fallback.
    Output: claim rows carrying memory_id, claim_id, entry_index, revision and a
    cosine score, best first.
    """
    if limit < 1:
        return []
    statement, parameters = search_statement(
        vector, model_name, limit, config, exclude_memory_id=exclude_memory_id
    )
    with settings_in_force(connection, config):
        return connection.execute(statement, parameters).fetchall()


def expand(
    connection,
    cue_hashes: list[bytes],
    limit: int,
    *,
    config: RetrievalConfig = DEFAULT_RETRIEVAL,
    vector: list[float] | None = None,
) -> list[dict]:
    """Current claims that share a generated cue with an already selected claim.

    Parameters: an open connection scoped to the Space, the cue hashes of the
    selected claims, the row limit, the retrieval configuration and optionally the
    query embedding.
    What it does: selects every claim sharing at least one cue, then orders that
    set. `index` preserves the shipped ordering, and `overlap` ranks by how many cues
    are shared, which is the principled order for an association. `vector` ranks by
    closeness to the query and is deliberately NOT the replacement: an associated
    claim is by definition not similar to the query, so ranking by similarity
    deprioritises exactly the claims that justify expansion.
    Output: cue-sharing claim rows in the configured order.
    """
    if not cue_hashes or limit < 1:
        return []
    # Known limitation, dormant while the related tier stays opt-in: expansion is enabled
    # by default (settings.memory_expansion), but callers only receive its claims when
    # they pass include_related, and the tier can never displace a ranked result. The `index` order
    # below is by memory UUID, so once more than `limit` claims share a cue, which of
    # them survive the cap is arbitrary with respect to relevance: reach degrades as
    # the corpus grows, silently. `overlap` is the principled replacement, because
    # shared-cue count is a property of the association rather than of the query.
    # `vector` is deliberately NOT the fix: an associated claim is by definition not
    # similar to the query, so ranking by similarity would deprioritise exactly the
    # claims that justify expansion. Unmeasured; do not switch on assumption.
    hashes = list(cue_hashes)[: config.max_candidates * config.candidate_multiplier]
    if config.expansion_order == "overlap":
        return connection.execute(
            "SELECT c.memory_id, c.claim_id, c.entry_index, c.revision, c.cue_hashes, "
            "shared.n AS shared FROM space.memory_claims c CROSS JOIN LATERAL ("
            "SELECT count(*) AS n FROM unnest(c.cue_hashes) AS cue WHERE cue = ANY(%s::bytea[])"
            ") AS shared WHERE c.cue_hashes && %s::bytea[] "
            "ORDER BY shared.n DESC, c.created_at DESC, c.memory_id, c.entry_index LIMIT %s",
            (hashes, hashes, limit),
        ).fetchall()
    if config.expansion_order == "vector":
        if vector is None:
            raise ValueError("vector ordering needs the query embedding")
        literal = vector_literal(vector)
        return connection.execute(
            "SELECT c.memory_id, c.claim_id, c.entry_index, c.revision, c.cue_hashes "
            "FROM space.memory_claims c WHERE c.cue_hashes && %s::bytea[] "
            "ORDER BY c.embedding <=> %s::vector, c.claim_id LIMIT %s",
            (hashes, literal, limit),
        ).fetchall()
    return connection.execute(
        "SELECT memory_id, claim_id, entry_index, revision, cue_hashes "
        "FROM space.memory_claims WHERE cue_hashes && %s::bytea[] "
        "ORDER BY memory_id, entry_index LIMIT %s",
        (hashes, limit),
    ).fetchall()


def cues_for(connection, memory_id, claim_ids: list[str]) -> list[bytes]:
    """Cue hashes of the given claims, used to find related current memories."""
    if not claim_ids:
        return []
    rows = connection.execute(
        "SELECT cue_hashes FROM space.memory_claims "
        "WHERE memory_id=%s AND claim_id=ANY(%s::uuid[])",
        (memory_id, claim_ids),
    ).fetchall()
    return sorted({cue for row in rows for cue in row["cue_hashes"]})


def vectors_for(connection, claim_ids: list[str], model_name: str) -> dict[str, list[float]]:
    """Stored embeddings of the given claims, for organizing claims whose text is unchanged."""
    if not claim_ids:
        return {}
    rows = connection.execute(
        "SELECT claim_id, embedding::text AS embedding FROM space.memory_claims "
        "WHERE claim_id=ANY(%s::uuid[]) AND embedding_model=%s",
        (claim_ids, model_name),
    ).fetchall()
    return {
        str(row["claim_id"]): [float(value) for value in row["embedding"].strip("[]").split(",")]
        for row in rows
    }


def memories_for_source(connection, resource_id) -> list[str]:
    """Memory resources already indexed for one source resource."""
    return [
        str(row["memory_id"])
        for row in connection.execute(
            "SELECT DISTINCT memory_id FROM space.memory_claims WHERE source_resource_id=%s",
            (resource_id,),
        ).fetchall()
    ]


def stats(connection, model_name: str) -> dict:
    """Index size and whether stored embeddings still match the configured model."""
    return dict(
        connection.execute(
            "SELECT count(*) AS claims, count(DISTINCT memory_id) AS memories, "
            "count(*) FILTER (WHERE embedding_model <> %s) AS other_model "
            "FROM space.memory_claims",
            (model_name,),
        ).fetchone()
    )
