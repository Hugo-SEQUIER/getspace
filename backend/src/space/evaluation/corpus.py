"""The labelled corpus and the baked embedding cache.

Parameters: the corpus path and the embedding cache path.
What it does: loads and validates the corpus, then resolves the text of every claim and
query to a vector without touching the provider again.
Output: validated corpus records and the embedding set the harness measures with.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from space import brain
from space.model_provider import MemoryModels
from space.settings import BACKEND_DIR, CONFIG_FILE, Settings

from .errors import CorpusError, EvaluationError

CORPUS_PATH = BACKEND_DIR / "evaluations" / "retrieval" / "corpus.json"
EMBEDDINGS_PATH = BACKEND_DIR / "evaluations" / "retrieval" / "embeddings.json"
KINDS = ("paraphrase", "keyword", "multi-hop", "partial", "negative")
MEMORY_TYPES = ("factual", "procedural", "episodic")
MIN_CLAIM_WORDS = 5
MAX_CLAIM_WORDS = 60


def text_key(text: str) -> str:
    """Cache key for one text.

    Parameters: the exact text that will be embedded.
    What it does: fingerprints the text with SHA-256.
    Output: a lowercase hex digest, stable across runs.
    """
    return sha256(text.encode()).hexdigest()


@dataclass(frozen=True)
class Claim:
    """One memory claim of the labelled corpus.

    Parameters: the claim identity, its text, its cue topics, its source and its type.
    What it does: carries the corpus row exactly as the product would index it.
    Output: an immutable claim record.
    """

    id: str
    text: str
    topics: tuple[str, ...]
    source: str
    memory_type: str


@dataclass(frozen=True)
class Query:
    """One labelled query.

    Parameters: the query text, the claim ids that answer it and its kind.
    What it does: carries ground truth; an empty `relevant` is a negative control.
    Output: an immutable query record.
    """

    query: str
    relevant: tuple[str, ...]
    kind: str


@dataclass(frozen=True)
class Corpus:
    """A labelled corpus plus the checks that make it trustworthy."""

    claims: tuple[Claim, ...]
    queries: tuple[Query, ...]
    path: Path

    def claim_ids(self) -> set[str]:
        return {claim.id for claim in self.claims}

    def texts(self) -> list[str]:
        """Every text the harness must embed: claim texts first, then query texts."""
        return [claim.text for claim in self.claims] + [query.query for query in self.queries]

    def kind_counts(self) -> dict[str, int]:
        counts = dict.fromkeys(KINDS, 0)
        for query in self.queries:
            counts[query.kind] = counts.get(query.kind, 0) + 1
        return counts


def load_corpus(path: str | Path = CORPUS_PATH) -> Corpus:
    """Read and validate the labelled corpus.

    Parameters: the path of a corpus JSON file.
    What it does: checks identifiers, word counts, kinds and ground-truth
    references, so a broken corpus fails loudly instead of scoring quietly.
    Output: a validated Corpus. Raises CorpusError on any inconsistency.
    """
    path = Path(path)
    try:
        document = json.loads(path.read_text())
    except FileNotFoundError:
        raise CorpusError(f"Corpus not found: {path}") from None
    except json.JSONDecodeError as error:
        raise CorpusError(f"Corpus is not valid JSON: {error}") from None
    claims, queries = document.get("claims"), document.get("queries")
    if not isinstance(claims, list) or not claims:
        raise CorpusError("Corpus needs a non-empty claims list")
    if not isinstance(queries, list) or not queries:
        raise CorpusError("Corpus needs a non-empty queries list")
    parsed: list[Claim] = []
    seen: set[str] = set()
    for index, row in enumerate(claims):
        try:
            claim = Claim(
                id=str(row["id"]),
                text=str(row["text"]),
                topics=tuple(str(topic) for topic in row["topics"]),
                source=str(row["source"]),
                memory_type=str(row["memory_type"]),
            )
        except (KeyError, TypeError) as error:
            raise CorpusError(f"Claim {index} is missing the field {error}") from None
        words = len(claim.text.split())
        if not MIN_CLAIM_WORDS <= words <= MAX_CLAIM_WORDS:
            raise CorpusError(
                f"Claim {claim.id} has {words} words; the corpus allows "
                f"{MIN_CLAIM_WORDS} to {MAX_CLAIM_WORDS}"
            )
        if claim.id in seen:
            raise CorpusError(f"Duplicate claim id {claim.id}")
        if not 1 <= len(claim.topics) <= 3 or any(not topic.strip() for topic in claim.topics):
            raise CorpusError(f"Claim {claim.id} needs 1 to 3 non-empty topics")
        if claim.memory_type not in MEMORY_TYPES:
            raise CorpusError(f"Claim {claim.id} has memory_type {claim.memory_type!r}")
        if not claim.source.strip():
            raise CorpusError(f"Claim {claim.id} needs a source")
        seen.add(claim.id)
        parsed.append(claim)
    parsed_queries: list[Query] = []
    for index, row in enumerate(queries):
        try:
            query = Query(
                query=str(row["query"]),
                relevant=tuple(str(claim_id) for claim_id in row["relevant"]),
                kind=str(row["kind"]),
            )
        except (KeyError, TypeError) as error:
            raise CorpusError(f"Query {index} is missing the field {error}") from None
        if query.kind not in KINDS:
            raise CorpusError(f"Query {query.query!r} has kind {query.kind!r}")
        if not query.query.strip():
            raise CorpusError("Every query needs text")
        unknown = sorted(set(query.relevant) - seen)
        if unknown:
            raise CorpusError(f"Query {query.query!r} references unknown claims {unknown}")
        if query.kind == "negative" and query.relevant:
            raise CorpusError(f"Negative query {query.query!r} must have no relevant claim")
        if query.kind != "negative" and not query.relevant:
            raise CorpusError(f"Query {query.query!r} needs at least one relevant claim")
        parsed_queries.append(query)
    return Corpus(claims=tuple(parsed), queries=tuple(parsed_queries), path=path)


@dataclass(frozen=True)
class Embeddings:
    """Baked embeddings plus the provenance of the bake.

    Parameters: the provider and model that produced the vectors, the dimension,
    the vectors keyed by text fingerprint and the provider calls this bake made.
    What it does: serves a vector for any text without touching the network.
    Output: an immutable embedding set.
    """

    provider: str
    model: str
    dimension: int
    vectors: Mapping[str, tuple[float, ...]]
    calls: int = 0
    embedded: int = 0
    cache_path: Path = EMBEDDINGS_PATH

    def vector(self, text: str) -> tuple[float, ...]:
        """The baked vector of one text. Raises EvaluationError when it is missing."""
        key = text_key(text)
        vector = self.vectors.get(key)
        if vector is None:
            raise EvaluationError(f"No baked embedding for text {key[:12]}...")
        return vector

    def info(self) -> dict:
        return {
            "provider": self.provider,
            "model": self.model,
            "dimension": self.dimension,
            "cached_texts": len(self.vectors),
            "embedded_this_run": self.embedded,
            "provider_calls": self.calls,
        }


def _models() -> MemoryModels:
    return MemoryModels(Settings(_env_file=CONFIG_FILE))


def load_embeddings(path: str | Path = EMBEDDINGS_PATH) -> dict:
    """Read the on-disk embedding cache. A missing file is an empty cache."""
    path = Path(path)
    if not path.exists():
        return {"provider": None, "model": None, "dimension": None, "vectors": {}}
    try:
        document = json.loads(path.read_text())
    except json.JSONDecodeError as error:
        raise EvaluationError(f"Embedding cache is not valid JSON: {error}") from None
    vectors = document.get("vectors")
    if not isinstance(vectors, dict):
        raise EvaluationError("Embedding cache has no vectors object")
    return document


def write_embeddings(path: str | Path, document: Mapping) -> None:
    """Write the embedding cache so a normal run never embeds twice."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(document, separators=(",", ":"), sort_keys=True)
    path.write_text(payload)


def persist_embeddings(embeddings: Embeddings, path: str | Path | None = None) -> None:
    """Write an embedding set back to the cache, so the next run embeds nothing new.

    Parameters: the embedding set and an optional override of its original cache path.
    What it does: stores every vector the set holds, including any the product path added
    while it ran.
    Output: None.
    """
    write_embeddings(
        embeddings.cache_path if path is None else path,
        {
            "provider": embeddings.provider,
            "model": embeddings.model,
            "dimension": embeddings.dimension,
            "vectors": {key: list(value) for key, value in sorted(embeddings.vectors.items())},
        },
    )


def bake_embeddings(
    texts: Iterable[str],
    *,
    path: str | Path = EMBEDDINGS_PATH,
    rebake: bool = False,
    models: MemoryModels | None = None,
) -> Embeddings:
    """Embed every text that is not in the cache yet, once.

    Parameters: the texts the run needs, the cache path, a rebake flag and an
    optional model client (tests inject a stub).
    What it does: reuses cached vectors keyed by the text fingerprint, embeds only
    the missing texts through the project's own MemoryModels client and writes the
    cache back. A cache baked with another model is discarded, never mixed.
    Output: the complete embedding set with the number of provider calls made.
    """
    unique = list(dict.fromkeys(texts))
    document = load_embeddings(path)
    models = models or _models()
    model = models.embedding_model
    cached = document["vectors"] if not rebake else {}
    if document.get("model") not in (None, model):
        cached = {}
    vectors: dict[str, tuple[float, ...]] = {
        key: tuple(value) for key, value in cached.items() if isinstance(value, list) and value
    }
    missing = [text for text in unique if text_key(text) not in vectors]
    calls = 0
    if missing:
        if not models.enabled:
            raise EvaluationError(
                "The memory provider is not configured, so embeddings cannot be baked"
            )
        session = models.session(lambda: None)
        dimension = brain.EMBEDDING_DIM
        for start in range(0, len(missing), 256):
            batch = missing[start : start + 256]
            produced = session.embed(batch)
            for text, vector in zip(batch, produced, strict=True):
                if len(vector) != dimension:
                    raise EvaluationError(
                        f"{model} returned {len(vector)} dimensions, expected {dimension}"
                    )
                vectors[text_key(text)] = tuple(round(value, 7) for value in vector)
        calls = session.calls
        write_embeddings(
            path,
            {
                "provider": models.info()["provider"],
                "model": model,
                "dimension": brain.EMBEDDING_DIM,
                "vectors": {key: list(value) for key, value in sorted(vectors.items())},
            },
        )
    return Embeddings(
        provider=str(models.info()["provider"]),
        model=model,
        dimension=brain.EMBEDDING_DIM,
        vectors=vectors,
        calls=calls,
        embedded=len(missing),
        cache_path=Path(path),
    )
