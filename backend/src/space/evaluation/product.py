"""Product-path retrieval measurement: Workspace.context, end to end.

Parameters: the labelled corpus, its baked embeddings, the caller limit and the
`query_score_threshold` values to sweep.
What it does: builds real encrypted memory resources through `Workspace.build_memory`
with a fixture model, then recalls every query through `Workspace.context`, which is the
path a user actually takes: `brain.search`, cue expansion, the vendored Memora retriever
and its `query_score_threshold`.
Output: a report whose layer is "product", with recall, MRR, nDCG, the negative-control
false positive rate and the empty-result rate per threshold value.

The index-layer harness in `harness.py` measures `brain.search` and `brain.expand` alone.
That layer has no relevance floor, so its negative_false_positive_rate is a statement
about the index, not about what a user sees. Every report names its layer for that
reason.
"""

from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from space import memory_pipeline
from space.memory import RecallMetadata, RecallRequest
from space.memory_pipeline import BuildMemory
from space.model_provider import MemoryModels, TypeSafeError
from space.providers import ScopedKey
from space.security.auth import Identity as AuthIdentity
from space.security.payloads import SpaceKey
from space.workspace import Database, SaveResource, Workspace

from .corpus import Corpus, Embeddings, persist_embeddings, text_key
from .errors import EvaluationError
from .fixture import DisposableDatabase
from .metrics import (
    DEFAULT_LIMIT,
    QueryOutcome,
    aggregate,
    cosine,
    summarize,
)

PRODUCT_FLOORS = (0.0, 0.2, 0.3, 0.4, 0.5, 0.6)
PRODUCT_CLAIMS = 60
PRODUCT_QUERIES = 30


class HarnessDatabase(Database):
    """A Database that talks to the disposable harness container.

    Parameters: a runtime DSN and the generated runtime password.
    What it does: uses the production transaction path, but skips TLS verification
    because the container is a throwaway loopback database.
    Output: a Workspace-ready database.
    """

    def __init__(self, dsn: str, password: str) -> None:
        self.pool = ConnectionPool(
            dsn,
            min_size=0,
            max_size=4,
            open=False,
            configure=self._configure,
            kwargs={"row_factory": dict_row, "password": password},
        )
        self.open()


class HarnessKeys:
    """In-process key custody: generates and unwraps Space keys, never persists them."""

    reference = "retrieval-eval"

    def __init__(self) -> None:
        self.material: dict[bytes, ScopedKey] = {}

    def generate(self, organization_id, space_id):
        key = ScopedKey(organization_id, space_id, SpaceKey(str(uuid4()), secrets.token_bytes(32)))
        wrapped = secrets.token_bytes(64)
        self.material[wrapped] = key
        return key, wrapped

    def unwrap(self, organization_id, space_id, row):
        key = self.material[bytes(row["wrapped_key"])]
        key.get_key(organization_id, space_id, str(row["id"]))
        return key


class HarnessObjects:
    """In-memory object store for the ciphertext the Workspace uploads."""

    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}

    def put(self, name, ciphertext):
        self.values[name] = ciphertext

    def get(self, name):
        return self.values[name]


FIXTURE_FINGERPRINT = "retrieval-eval-cached-v1"


class CachedModels(MemoryModels):
    """A memory model client that embeds from the baked cache and extracts canned claims.

    Parameters: the baked embeddings.
    What it does: serves `embed` from the cache and answers the extraction prompts from
    `outputs`, which the harness sets per source before each build. Cached texts make no
    provider call; a composite text the bake never embedded is fetched once from the
    configured provider and added to the cache.
    Output: a MemoryModels replacement that Workspace and the pipeline accept.
    """

    def __init__(self, embeddings: Embeddings, *, relevance_models=None) -> None:
        self.embeddings = embeddings
        self.enabled = True
        self.calls: list = []
        self.provider_calls = 0
        self.outputs: list = []
        self.cues: list = []
        self._provider = None
        self.relevance_models = relevance_models
        self.relevance_calls = 0
        # The inherited close() drains the connection pool, so the double carries the same
        # client state the real client initialises.
        self._client = None
        self._client_lock = threading.Lock()
        self._closed = False
        # relevance.select decides whether Jev assesses by reading `settings` from the
        # session, which production sessions expose. Left unset, this harness keeps its
        # historical behaviour: the configured text model assesses.
        self.session_settings = None

    @property
    def embedding_model(self) -> str:
        return self.embeddings.model

    # The harness holds no provider settings, so both identities are the fixture constant.
    # A saved memory records the embedding identity, and an extraction job the chat one.
    @property
    def chat_fingerprint(self) -> str:
        return FIXTURE_FINGERPRINT

    @property
    def embedding_fingerprint(self) -> str:
        return FIXTURE_FINGERPRINT

    def embed(self, texts):
        """The vector of each text: from the cache, or from the provider once.

        Parameters: the texts the product path asks to embed (a claim keeps its cue label
        in the embedded text, so the composite text is not always cached yet).
        What it does: serves cached vectors, and embeds any new text through the real
        provider, adding it to the cache the caller persists afterwards.
        Output: one vector per text.
        """
        from space.settings import CONFIG_FILE, Settings

        missing = [text for text in texts if text_key(text) not in self.embeddings.vectors]
        if missing:
            if self._provider is None:
                self._provider = MemoryModels(Settings(_env_file=CONFIG_FILE))
            session = self._provider.session(lambda: None)
            produced = session.embed(missing)
            self.provider_calls += session.calls
            for text, vector in zip(missing, produced, strict=True):
                self.embeddings.vectors[text_key(text)] = tuple(round(value, 7) for value in vector)
        return [list(self.embeddings.vector(text)) for text in texts]

    def info(self) -> dict:
        return {
            "enabled": True,
            "provider": "retrieval-eval-cache",
            "endpoint": None,
            "text_model": "fixture",
            "embedding_model": self.embeddings.model,
        }

    def session(self, authorize):
        parent = self

        class Session:
            settings = parent.session_settings

            def invoke(self, input, prompt_args=None, response_format=None, **kwargs):
                parent.calls.append(("model", input))
                if response_format is None:
                    return {"action": "STOP"}
                name = response_format.__name__
                if (
                    name in {"EvidenceAssessment", "EvidenceConflicts"}
                    and parent.relevance_models is not None
                ):
                    parent.relevance_calls += 1
                    return parent.relevance_models.session(authorize).invoke(
                        input, response_format=response_format, **kwargs
                    )
                if name == "MemoryOutputs":
                    return response_format.model_validate({"entries": list(parent.outputs)})
                if name == "BatchCueIndices":
                    return response_format.model_validate(
                        {
                            "results": [
                                {"memory_index": entry["index"], "cue_indices": list(parent.cues)}
                                for entry in parent.outputs
                            ]
                        }
                    )
                if name == "MemoryUpdateDecision":
                    return response_format.model_validate({"should_update": False})
                raise EvaluationError(f"The product harness cannot answer {name}")

            def embed(self, texts):
                parent.calls.append(("embed", list(texts)))
                return parent.embed(texts)

            def evaluate_typesafe(self, payload):
                """Delegate the short-passage assessment to the real relevance model.

                The shipped Jev path calls this directly on the session, so a harness that
                omits it can never exercise Jev routing.
                """
                parent.calls.append(("typesafe", payload))
                if parent.relevance_models is None:
                    raise TypeSafeError("The product harness has no relevance model")
                return parent.relevance_models.session(authorize).evaluate_typesafe(payload)

        return Session()


@contextmanager
def product_config(**overrides) -> Iterator[None]:
    """Run the vendored retriever with different configuration values.

    Parameters: keyword overrides such as `query_score_threshold=0.4` or
    `enable_cue_index=False`.
    What it does: wraps `memory_pipeline.configuration` so the config the retriever is
    built with carries the requested values, then restores it. No file on disk changes.
    Output: a context manager.
    """
    original = memory_pipeline.configuration

    def patched(store, model):
        config = original(store, model)
        for name, value in overrides.items():
            config["memory"][name] = value
        return config

    memory_pipeline.configuration = patched
    try:
        yield
    finally:
        memory_pipeline.configuration = original


def query_score_threshold(value: float) -> Iterator[None]:
    """Run the vendored retriever with a different `query_score_threshold`."""
    return product_config(query_score_threshold=value)


@dataclass(frozen=True)
class ProductFixture:
    """The seeded product fixture: real resources, real keys, a fixture model."""

    service: Workspace
    identity: AuthIdentity
    space: UUID
    models: CachedModels
    claim_map: Mapping[str, str] = field(default_factory=dict)
    memories: int = 0
    source_map: Mapping[str, str] = field(default_factory=dict)

    @property
    def claims(self) -> int:
        return len(self.claim_map)

    def info(self) -> dict:
        return {
            "layer": "product",
            "path": "Workspace.context -> brain.search, cue expansion, Memora retriever",
            "claims_indexed": len(self.claim_map),
            "memory_resources": self.memories,
            "model_calls": len([call for call in self.models.calls if call[0] == "model"]),
            "provider_calls": self.models.provider_calls,
        }


def product_corpus(corpus: Corpus, *, claims: int = PRODUCT_CLAIMS, queries: int = PRODUCT_QUERIES):
    """Slice the corpus down to a product-sized fixture.

    Parameters: the corpus and how many claims and queries to keep.
    What it does: keeps the first `claims` claims and every query whose ground truth is
    fully inside that slice, plus negative controls, until `queries` queries are kept.
    Output: (claims, queries) tuples ready to build resources from.
    """
    kept_claims = corpus.claims[:claims]
    ids = {claim.id for claim in kept_claims}
    selected = [
        query for query in corpus.queries if query.kind == "negative" or set(query.relevant) <= ids
    ]
    return kept_claims, tuple(selected[:queries])


def build_product_fixture(
    database: DisposableDatabase,
    corpus: Corpus,
    embeddings: Embeddings,
    *,
    claims: int = PRODUCT_CLAIMS,
    queries: int = PRODUCT_QUERIES,
    relevance_models=None,
) -> tuple[ProductFixture, tuple]:
    """Create one Space and build real memory resources from the corpus.

    Parameters: the disposable database, the corpus, the baked embeddings and the size
    of the product fixture.
    What it does: bootstraps a Space, saves one source document per corpus source, builds
    memory for each through `Workspace.build_memory`, and maps every indexed claim id back
    to its corpus claim id.
    Output: the fixture and the selected queries.
    """
    claims_kept, queries_kept = product_corpus(corpus, claims=claims, queries=queries)
    with database.connect() as connection:
        dsn = connection.info.dsn
    models = CachedModels(embeddings, relevance_models=relevance_models)
    service = Workspace(
        HarnessDatabase(dsn, database.app_password),
        HarnessKeys(),
        HarnessObjects(),
        "http://127.0.0.1:8000/mcp/",
        memory_models=models,
    )
    identity = AuthIdentity("https://identity.invalid", str(uuid4()), int(time.time()) + 3600)
    space = UUID(service.bootstrap(identity)["id"])
    grouped: dict[str, list] = {}
    for claim in claims_kept:
        grouped.setdefault(claim.source, []).append(claim)
    claim_map: dict[str, str] = {}
    memories = 0
    source_map = {}
    for source, group in grouped.items():
        body = "\n".join(claim.text for claim in group)
        saved = service.save(
            identity,
            space,
            SaveResource(
                command_id=uuid4(),
                expected_revision=0,
                kind="document",
                title=source[:300],
                body=body,
                recall=RecallMetadata(category="source", topics=[source]),
            ),
        )
        models.outputs = [
            {"memory_type": claim.memory_type, "index": claim.id, "value": claim.text}
            for claim in group
        ]
        source_map[source] = saved["id"]
        # The vendored cue schema accepts at most three cues per claim.
        models.cues = sorted({topic for claim in group for topic in claim.topics})[:3]
        built = service.build_memory(
            identity,
            space,
            BuildMemory(command_id=uuid4(), source={"resource_id": saved["id"], "revision": 1}),
        )
        record = service.get_resource(
            identity, space, UUID(built["id"]), built["revision"], _internal=True
        )["_memory"]
        values = {claim.text: claim.id for claim in group}
        for claim in record["claims"]:
            corpus_id = values.get(claim["value"])
            if corpus_id:
                claim_map[str(claim["id"])] = corpus_id
        memories += 1
    persist_embeddings(embeddings)
    return (
        ProductFixture(
            service=service,
            identity=identity,
            space=space,
            models=models,
            claim_map=claim_map,
            memories=memories,
            source_map=source_map,
        ),
        queries_kept,
    )


# Layer A is our `brain.expand` call; layer B is the vendored cue index. With a
# `score_floor` configured, `Workspace.context` already stops expanding, so a floor of
# 0.0 (which drops nothing) is the switch that isolates layer A.
EXPANSION_MODES = ("both_on", "both_off", "layer_a_off", "layer_b_off")


def expansion_settings(mode: str, fixture: ProductFixture) -> tuple[float | None, dict]:
    """Translate an expansion mode into a Workspace floor and a retriever override.

    Parameters: the mode name and the fixture whose service is configured.
    What it does: layer A off is expressed as a `score_floor` of 0.0, which disables
    `brain.expand` without dropping a claim; layer B off is `enable_cue_index=False`,
    which makes the vendored retriever query primary entries only.
    Output: (the score floor for the Workspace, the retriever config overrides).
    """
    if mode not in EXPANSION_MODES:
        raise EvaluationError(f"Unknown expansion mode {mode!r}")
    floor = 0.0 if mode in ("both_off", "layer_a_off") else None
    overrides = {"enable_cue_index": False} if mode in ("both_off", "layer_b_off") else {}
    return floor, overrides


def run_product_query(
    fixture: ProductFixture, query, *, limit: int = DEFAULT_LIMIT
) -> QueryOutcome:
    """Recall one query through the product path.

    Parameters: the product fixture, the labelled query and the caller limit.
    What it does: calls `Workspace.context`, keeps the memory items, maps their claim ids
    back to the corpus and records each item's similarity score when the recall path
    reports one.
    Output: one QueryOutcome for the product layer.
    """
    started = time.perf_counter()
    result = fixture.service.context(
        fixture.identity,
        fixture.space,
        RecallRequest(query=query.query, mode="search", limit=limit, strategy="semantic"),
    )
    latency_ms = (time.perf_counter() - started) * 1000
    returned, scores = [], []
    for item in result["items"]:
        entry_id = item.get("entry_id")
        corpus_id = fixture.claim_map.get(str(entry_id)) if entry_id else None
        if corpus_id is None:
            continue
        returned.append(corpus_id)
        scores.append(float(item.get("score") or 0.0))
    return QueryOutcome(
        query=query.query,
        kind=query.kind,
        relevant=query.relevant,
        returned=tuple(returned[:limit]),
        scores=tuple(scores[:limit]),
        candidates=len(returned),
        search_returned=len(returned),
        search_limit=limit,
        available=len(fixture.claim_map),
        latency_ms=latency_ms,
    )


def run_product_evaluation(
    corpus: Corpus,
    embeddings: Embeddings,
    *,
    floors: Sequence[float] = PRODUCT_FLOORS,
    modes: Sequence[str] = EXPANSION_MODES,
    limit: int = DEFAULT_LIMIT,
    claims: int = PRODUCT_CLAIMS,
    queries: int = PRODUCT_QUERIES,
    database: DisposableDatabase | None = None,
) -> dict:
    """Measure the product path once per expansion mode and threshold.

    Parameters: the corpus, the baked embeddings, the expansion modes, the thresholds to
    sweep, the caller limit, the fixture size and an optional already-started database.
    What it does: builds the fixture once, then recalls every query through
    `Workspace.context` under each threshold.
    Output: the product report, one entry per expansion mode, each carrying one entry per
    threshold.
    """
    if database is None:
        with DisposableDatabase.start() as owned:
            return _product_run(owned, corpus, embeddings, floors, modes, limit, claims, queries)
    return _product_run(database, corpus, embeddings, floors, modes, limit, claims, queries)


def _product_run(database, corpus, embeddings, floors, modes, limit, claims, queries) -> dict:
    fixture, selected = build_product_fixture(
        database, corpus, embeddings, claims=claims, queries=queries
    )
    combinations = []
    for mode in modes:
        entry = {"expansion": mode, "thresholds": []}
        for floor in floors:
            score_floor, overrides = expansion_settings(mode, fixture)
            overrides = {**overrides, "query_score_threshold": floor}
            outcomes = []
            provider_calls_before = fixture.models.provider_calls
            with product_config(**overrides):
                fixture.service.retrieval = replace(
                    fixture.service.retrieval, score_floor=score_floor, assess_relevance=False
                )
                for query in selected:
                    outcomes.append(run_product_query(fixture, query, limit=limit))
            entry["thresholds"].append(
                {
                    "threshold": round(floor, 4),
                    "metrics": aggregate(
                        outcomes,
                        limit=limit,
                        embed_calls=fixture.models.provider_calls - provider_calls_before,
                    ),
                    "negative_similarity": summarize(
                        [
                            score
                            for outcome in outcomes
                            if outcome.kind == "negative"
                            for score in outcome.scores
                        ]
                    ),
                }
            )
        combinations.append(entry)
    fixture.service.retrieval = replace(fixture.service.retrieval, score_floor=None)
    relevant = relevant_claim_similarity(corpus, embeddings, selected)
    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "layer": "product",
        "path": "Workspace.context with the vendored Memora retriever",
        "corpus": {
            "path": str(corpus.path),
            "claims_used": len(fixture.claim_map),
            "queries_used": len(selected),
            "kinds": _kind_counts(selected),
        },
        "fixture": fixture.info(),
        "limit": limit,
        "relevant_claim_similarity": relevant,
        "combinations": combinations,
    }


def relevant_claim_similarity(corpus, embeddings, selected) -> dict:
    """The similarity of every relevant claim to its own query, measured from the cache."""
    claims = {claim.id: claim for claim in corpus.claims}
    scores = []
    for query in selected:
        if not query.relevant:
            continue
        vector = embeddings.vector(query.query)
        for claim_id in query.relevant:
            claim = claims.get(claim_id)
            if claim:
                scores.append(cosine(vector, embeddings.vector(claim.text)))
    return summarize(scores)


def _kind_counts(queries) -> dict:
    counts: dict[str, int] = {}
    for query in queries:
        counts[query.kind] = counts.get(query.kind, 0) + 1
    return counts
