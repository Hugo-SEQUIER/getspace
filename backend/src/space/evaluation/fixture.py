"""The disposable database, its fixture and the scope the queries run under.

Parameters: a corpus, its baked embeddings and the size of the stale and decoy rows.
What it does: starts a pgvector container, applies the real migrations, seeds the corpus
through `brain.apply`, adds the rows the search filter must reject, and analyzes the
table so the plan a run reports is the plan it measured.
Output: a running database plus the Fixture record of what was seeded.
"""

from __future__ import annotations

import math
import secrets
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from hashlib import sha256
from shutil import which
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from space import brain
from space.database_setup import SCHEMA_MARKER, apply_foundation

from .corpus import Claim, Corpus, Embeddings
from .errors import EvaluationError

IMAGE = "pgvector/pgvector:pg17"
OLD_MODEL = "text-embedding-ada-002"
ROLES = ("runtime", "migration", "admin")
STATEMENT_TIMEOUT_MS = 600_000
DEFAULT_DECOYS = 0
DEFAULT_STALE = 20000
DEFAULT_TWINS = None
# How the old-model rows relate to the in-scope claims. Our schema cannot produce
# "duplicates": brain.apply deletes and reinserts a claim when its model changes, so a
# rebuilt claim never keeps an old-model twin. "duplicates" is kept only to show what
# that unreachable geometry would have measured.
STALE_GEOMETRIES = ("unrelated", "similar", "duplicates")
DEFAULT_STALE_GEOMETRY = "unrelated"


class DisposableDatabase:
    """A throwaway pgvector database built from the real migrations.

    Parameters: the container name and port, the three generated passwords and the
    engine that applied the migrations.
    What it does: owns the container lifecycle and hands out the two logins the
    product uses: the restricted runtime login and the setup login.
    Output: a context manager. Leaving the block removes the container.
    """

    def __init__(
        self,
        *,
        name: str,
        port: int,
        admin_password: str,
        migration_password: str,
        app_password: str,
        engine: Any,
    ) -> None:
        self.name = name
        self.port = port
        self.admin_password = admin_password
        self.migration_password = migration_password
        self.app_password = app_password
        self.engine = engine

    @classmethod
    def start(
        cls, *, image: str = IMAGE, memory: str = "8g", timeout: float = 90.0
    ) -> DisposableDatabase:
        """Start PostgreSQL 17 with pgvector and apply migrations to head.

        Parameters: the image, the size of the temporary data directory and the
        readiness timeout in seconds.
        What it does: runs the same disposable local container recipe the database
        test suite uses, then applies the real migration chain through
        `space.database_setup.apply_foundation`.
        Output: a started database. Raises EvaluationError when Docker is missing.
        """
        docker = which("docker")
        if docker is None:
            raise EvaluationError("Docker is required to run the retrieval harness")
        name = "space-retrieval-eval-" + uuid4().hex[:12]
        admin_password, migration_password, app_password = (
            secrets.token_urlsafe(32) for _ in range(3)
        )
        subprocess.run(  # noqa: S603 - fixed local disposable Docker command, no shell
            [
                docker,
                "run",
                "--rm",
                "--detach",
                "--name",
                name,
                "--publish",
                "127.0.0.1::5432",
                "--tmpfs",
                f"/var/lib/postgresql/data:rw,nosuid,size={memory}",
                "--env",
                "POSTGRES_PASSWORD=" + admin_password,
                image,
            ],
            check=True,
            capture_output=True,
        )
        engine = None
        try:
            port = cls._port(docker, name, timeout)
            database = cls(
                name=name,
                port=port,
                admin_password=admin_password,
                migration_password=migration_password,
                app_password=app_password,
                engine=None,
            )
            database._prepare_login()
            engine = create_engine(
                "postgresql+psycopg://",
                creator=lambda: database.connect(role="migration"),
                poolclass=NullPool,
                hide_parameters=True,
            )
            with engine.begin() as connection:
                apply_foundation(connection, app_password)
            database.engine = engine
            return database
        except Exception:
            if engine is not None:
                engine.dispose()
            subprocess.run(  # noqa: S603 - only remove the container created above
                [docker, "rm", "--force", name], check=False, capture_output=True
            )
            raise

    @staticmethod
    def _port(docker: str, name: str, timeout: float) -> int:
        deadline = time.monotonic() + timeout
        while True:
            try:
                address = subprocess.check_output(  # noqa: S603 - fixed local docker command
                    [docker, "port", name, "5432/tcp"], text=True
                ).strip()
                return int(address.rsplit(":", 1)[1])
            except (subprocess.CalledProcessError, IndexError, ValueError):
                if time.monotonic() >= deadline:
                    raise EvaluationError(
                        "The disposable database did not publish a port"
                    ) from None
                time.sleep(0.2)

    def connect(self, *, role: str = "runtime") -> psycopg.Connection:
        """Open one connection as the runtime login, the migration login or the admin.

        Parameters: `role`, one of "runtime" (the restricted application login that the
        Space serves traffic with), "migration" (the schema owner) or "admin" (the
        disposable container superuser).
        What it does: mirrors the connection recipe of the disposable test database,
        waits for the server to accept work, and rejects a runtime login that is not the
        restricted `space_app` role.
        Output: an open psycopg connection.
        """
        if role not in ROLES:
            raise EvaluationError(f"Unknown database role {role!r}")
        user = {"runtime": "space_app", "migration": "space_migrator", "admin": "postgres"}[role]
        password = {
            "runtime": self.app_password,
            "migration": self.migration_password,
            "admin": self.admin_password,
        }[role]
        # The application reads rows by column name. Migration and admin connections
        # keep tuple rows, which the migration tooling expects. A measurement gets a
        # longer statement timeout than the 15 s role default, so a slow strategy slows
        # the run down instead of aborting it; latency_p95_ms is where that shows.
        row_options = (
            {"row_factory": dict_row, "options": f"-c statement_timeout={STATEMENT_TIMEOUT_MS}"}
            if role == "runtime"
            else {}
        )
        deadline = time.monotonic() + 60
        while True:
            try:
                connection = psycopg.connect(
                    host="127.0.0.1",
                    port=self.port,
                    dbname="postgres",
                    sslmode="disable",
                    user=user,
                    password=password,
                    connect_timeout=5,
                    **row_options,
                )
                connection.execute("SELECT 1")
                if role == "runtime":
                    self._configure(connection)
                return connection
            except psycopg.OperationalError:
                # A published port does not mean the server is ready to accept work yet.
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.25)

    @staticmethod
    def _configure(connection) -> None:
        """Refuse a runtime login that could bypass row-level security.

        Parameters: an open connection.
        What it does: asserts the restricted application role, as `Database` does.
        Output: None. Raises EvaluationError when the login is wrong.
        """
        row = connection.execute(
            "SELECT current_user = 'space_app' AS expected, "
            "rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb AS privileged "
            "FROM pg_roles WHERE rolname=current_user"
        ).fetchone()
        connection.commit()
        if not row or not row["expected"] or row["privileged"]:
            raise EvaluationError("The runtime login must be the restricted space_app role")

    def _prepare_login(self) -> None:
        with self.connect(role="admin") as connection:
            connection.execute(
                sql.SQL(
                    "CREATE ROLE space_migrator LOGIN NOSUPERUSER NOBYPASSRLS CREATEROLE "
                    "PASSWORD {}"
                ).format(sql.Literal(self.migration_password))
            )
            connection.execute(
                "GRANT CONNECT, CREATE ON DATABASE postgres TO space_migrator WITH GRANT OPTION"
            )
            connection.execute("CREATE SCHEMA space AUTHORIZATION space_migrator")
            connection.execute(
                sql.SQL("COMMENT ON SCHEMA space IS {}").format(sql.Literal(SCHEMA_MARKER))
            )
            connection.execute("CREATE EXTENSION vector SCHEMA space")

    def stop(self) -> None:
        """Remove the container and close the migration engine."""
        if self.engine is not None:
            self.engine.dispose()
        docker = which("docker")
        if docker is not None:
            subprocess.run(  # noqa: S603 - only removes the container this object started
                [docker, "rm", "--force", self.name], check=False, capture_output=True
            )

    def __enter__(self) -> DisposableDatabase:
        return self

    def __exit__(self, *exc_info) -> None:
        self.stop()


@dataclass(frozen=True)
class Identity:
    """The scope one query runs under, exactly as the application sets it."""

    organization_id: str
    space_id: str
    principal_id: str
    key_id: str
    subject: str

    @classmethod
    def new(cls, label: str) -> Identity:
        """Build a fresh identity for one scope label."""
        return cls(
            organization_id=str(uuid4()),
            space_id=str(uuid4()),
            principal_id=str(uuid4()),
            key_id=str(uuid4()),
            subject=f"retrieval-eval-{label}",
        )


def claim_uuid(claim_id: str) -> str:
    """The stable database identity of a corpus claim.

    Parameters: the corpus claim id.
    What it does: derives a UUID from the id, so ties broken by claim_id order the
    same way on every run and two runs stay comparable.
    Output: a UUID string.
    """
    return str(uuid5(NAMESPACE_URL, f"space-retrieval-eval/claim/{claim_id}"))


def set_scope(connection, identity: Identity) -> None:
    """Set the transaction-local scope the application sets before every query.

    Parameters: an open connection and the identity to act as.
    What it does: writes the same `space.*` session variables the application sets
    (`Database.transaction` and `Workspace._set_scope`), so the row-level security
    predicate runs instead of being bypassed.
    Output: None.
    """
    for name, value in (
        ("space.identity_issuer", "https://identity.invalid"),
        ("space.identity_subject", identity.subject),
        ("space.organization_id", identity.organization_id),
        ("space.space_id", identity.space_id),
    ):
        connection.execute("SELECT set_config(%s, %s, true)", (name, value))


def seed_identity(connection, identity: Identity) -> None:
    """Create the principal, organization, Space and wrapped key of one scope.

    Parameters: an open connection and the identity to create.
    What it does: mirrors the application bootstrap rows the Space needs to exist.
    Output: None.
    """
    set_scope(connection, identity)
    connection.execute(
        "INSERT INTO space.principals(id,issuer,subject) VALUES(%s,%s,%s)",
        (identity.principal_id, "https://identity.invalid", identity.subject),
    )
    connection.execute(
        "INSERT INTO space.organizations(id,owner_principal_id,default_space_id) VALUES(%s,%s,%s)",
        (identity.organization_id, identity.principal_id, identity.space_id),
    )
    connection.execute(
        "INSERT INTO space.spaces(organization_id,id,metadata_key_id,encrypted_metadata) "
        "VALUES(%s,%s,%s,%s)",
        (identity.organization_id, identity.space_id, identity.key_id, secrets.token_bytes(64)),
    )
    connection.execute(
        "INSERT INTO space.space_keys(organization_id,space_id,id,wrapping_provider,"
        "wrapping_key_reference,wrapped_key) VALUES(%s,%s,%s,'retrieval-eval','retrieval-eval',%s)",
        (identity.organization_id, identity.space_id, identity.key_id, secrets.token_bytes(96)),
    )


def _resource(connection, identity: Identity, memory_id: str) -> None:
    """Insert the resource row a memory index row must reference.

    Parameters: a scoped connection, the identity and the memory resource id.
    What it does: writes `space.resources` and its current revision in one transaction.
    Output: None.
    """
    connection.execute(
        "INSERT INTO space.resources(organization_id,space_id,id,kind,current_revision) "
        "VALUES(space.requested_organization_id(),space.requested_space_id(),%s,'document',1)",
        (memory_id,),
    )
    connection.execute(
        "INSERT INTO space.resource_revisions(organization_id,space_id,resource_id,revision,key_id,"
        "encrypted_metadata,encrypted_record,author_principal_id) "
        "VALUES(space.requested_organization_id(),space.requested_space_id(),%s,1,%s,%s,%s,%s)",
        (memory_id, identity.key_id, b"retrieval-eval", b"retrieval-eval", identity.principal_id),
    )


def memory_uuid(source: str) -> str:
    """The stable memory resource id of one corpus source."""
    return str(uuid5(NAMESPACE_URL, f"space-retrieval-eval/memory/{source}"))


def seed_corpus(
    connection, identity: Identity, corpus: Corpus, embeddings: Embeddings, *, model_name: str
) -> dict[str, str]:
    """Index every corpus claim through the product's own insert path.

    Parameters: a scoped connection, the identity, the corpus, the baked embeddings
    and the embedding model name.
    What it does: creates one memory resource per source and calls `brain.apply`, so
    the harness seeds exactly the rows the product writes, with the same cue hashes.
    Output: the mapping from database claim id to corpus claim id.
    """
    grouped: dict[str, list[Claim]] = {}
    for claim in corpus.claims:
        grouped.setdefault(claim.source, []).append(claim)
    mapping: dict[str, str] = {}
    for source, claims in grouped.items():
        memory_id = memory_uuid(source)
        _resource(connection, identity, memory_id)
        rows = [
            {
                "id": claim_uuid(claim.id),
                "entry_index": index,
                "memory_type": claim.memory_type,
                "text": claim.text,
                "cues": list(claim.topics),
            }
            for index, claim in enumerate(claims)
        ]
        vectors = {
            row["id"]: list(embeddings.vector(claim.text))
            for row, claim in zip(rows, claims, strict=True)
        }
        brain.apply(
            connection,
            memory_id=memory_id,
            revision=1,
            claims=rows,
            model_name=model_name,
            vectors=vectors,
        )
        mapping.update({claim_uuid(claim.id): claim.id for claim in claims})
    return mapping


FILLER_NOISE = 0.4


def _noise_vector(seed: int, dimension: int) -> list[float]:
    """A deterministic pseudo-random vector in [-1, 1).

    Parameters: a seed and the vector dimension.
    What it does: reads words from a SHA-256 counter stream, so the fixture is
    reproducible without the `random` module and without a provider call.
    Output: the noise values.
    """
    values: list[float] = []
    counter = 0
    while len(values) < dimension:
        digest = sha256(f"filler:{seed}:{counter}".encode()).digest()
        for offset in range(0, len(digest), 4):
            word = int.from_bytes(digest[offset : offset + 4], "big")
            values.append(word / 2**31 - 1)
        counter += 1
    return values[:dimension]


def _perturbed(
    vectors: Sequence[Sequence[float]], *, seed: int = 0, noise: float = FILLER_NOISE
) -> list[list[float]]:
    """Nearby copies of real embeddings, for rows a filter must reject.

    Parameters: the corpus embeddings, a seed that separates filler pools and the noise
    scale (`0.4` is a near copy, `1.2` is same-domain but not a duplicate).
    What it does: adds deterministic noise to each embedding and renormalizes, so a
    filler row sits close to, but not on top of, a real claim: the position another
    model version or a neighbouring tenant occupies.
    Output: one unit vector per input vector, computed locally and cached by the caller.
    """
    pool: list[list[float]] = []
    for index, vector in enumerate(vectors):
        raw = _noise_vector(seed + index, len(vector))
        # Normalize the noise first: unnormalized noise has norm ~sqrt(n/3), which
        # would swamp the embedding and make the "near copy" a random vector instead.
        noise_norm = math.sqrt(sum(value * value for value in raw)) or 1.0
        mixed = [
            value + noise * (offset / noise_norm) for value, offset in zip(vector, raw, strict=True)
        ]
        norm = math.sqrt(sum(value * value for value in mixed)) or 1.0
        pool.append([value / norm for value in mixed])
    return pool


def _unrelated_pool(count: int, dimension: int = brain.EMBEDDING_DIM) -> list[list[float]]:
    """Unit vectors unrelated to the corpus, for filler rows nothing is measured on.

    Parameters: how many vectors and the dimension.
    What it does: takes deterministic pseudo-random directions and normalizes them, so
    filler rows are far from every query and cost no provider call.
    Output: the vector pool.
    """
    pool = []
    for index in range(count):
        values = _noise_vector(index, dimension)
        norm = math.sqrt(sum(value * value for value in values)) or 1.0
        pool.append([value / norm for value in values])
    return pool


FILLER_TEMPLATES = (
    "{n}. The platform team moved the nightly export job to 03:00 UTC after the incident review.",
    "{n}. Weekly sync note: the billing spike was traced to a retry storm in the webhook worker.",
    "{n}. The design review agreed to keep the onboarding screen to three steps for this quarter.",
    "{n}. Support reported slow search on large workspaces; the fix ships behind a flag next week.",
    "{n}. Costs: the object storage bill grew 12 percent, mostly from uncompressed daily backups.",
    "{n}. The contractor finishes the migration audit on Friday and hands over the runbook.",
    "{n}. The old notification service is deprecated; callers should move to the queue endpoint.",
    "{n}. Sprint planning: two engineers on reliability, one on the new import path, no overtime.",
)
FILLER_CHUNK = 200


def filler_text(index: int) -> str:
    """A deterministic, realistic filler claim text for an unlabelled index row."""
    template = FILLER_TEMPLATES[index % len(FILLER_TEMPLATES)]
    return template.format(n=f"Filler {index + 1}")


def _fill_rows(
    connection,
    identity: Identity,
    *,
    count: int,
    model_name: str,
    vectors: Sequence[Sequence[float]],
    label: str,
) -> int:
    """Insert unlabelled filler rows that share the corpus embedding geometry.

    Parameters: a scoped connection, the identity, how many rows, the model name they
    claim to be embedded with, a pool of vectors to reuse and a label for the id.
    What it does: writes rows the harness never scores, so the table holds content a
    filter must remove: rows of another tenant, or rows of an older model version.
    Output: the number of rows inserted.
    """
    memory_id = str(uuid5(NAMESPACE_URL, f"space-retrieval-eval/filler/{label}"))
    _resource(connection, identity, memory_id)
    statement = (
        "INSERT INTO space.memory_claims(organization_id,space_id,memory_id,claim_id,revision,"
        "entry_index,memory_type,text_hash,cue_hashes,embedding,embedding_model) "
        "SELECT space.requested_organization_id(),space.requested_space_id(),%s,"
        "gen_random_uuid(),1,g,'factual',sha256(g::text::bytea),'{}'::bytea[],%s::vector,%s "
        "FROM generate_series(1,%s) g"
    )
    inserted = 0
    while inserted < count:
        step = min(FILLER_CHUNK, count - inserted)
        vector = vectors[inserted % len(vectors)]
        connection.execute(statement, (memory_id, brain.vector_literal(vector), model_name, step))
        inserted += step
    return inserted


@dataclass(frozen=True)
class Fixture:
    """What was seeded, so every reported number can be reproduced.

    Parameters: the scopes, the models and the row counts of the disposable database.
    What it does: records the fixture conditions a metric depends on.
    Output: an immutable fixture record with a JSON summary.
    """

    identity: Identity
    decoy_identity: Identity | None
    model: str
    stale_model: str
    corpus_claims: int
    memories: int
    stale_claims: int
    twin_claims: int
    decoy_claims: int
    rows: int
    claim_ids: Mapping[str, str]
    verified: Mapping[str, int] = field(default_factory=dict)

    def info(self) -> dict:
        return {
            "corpus_claims": self.corpus_claims,
            "memories": self.memories,
            "stale_claims_in_space": self.stale_claims,
            "stale_rows_near_a_corpus_claim": self.twin_claims,
            "decoy_claims_other_tenant": self.decoy_claims,
            "index_rows_total": self.rows,
            "verified": dict(self.verified),
            "embedding_model": self.model,
            "stale_embedding_model": self.stale_model,
            "evaluated_scope": {
                "organization_id": self.identity.organization_id,
                "space_id": self.identity.space_id,
            },
            "note": (
                "Stale rows carry an older embedding_model in the evaluated Space and "
                "decoy rows live in a second tenant, so the search filter has candidates "
                "to reject, as a shared table does in production."
            ),
        }


def seed_fixture(
    database: DisposableDatabase,
    corpus: Corpus,
    embeddings: Embeddings,
    *,
    model_name: str,
    decoys: int = DEFAULT_DECOYS,
    stale: int = DEFAULT_STALE,
    twins: int | None = DEFAULT_TWINS,
    geometry: str = DEFAULT_STALE_GEOMETRY,
) -> Fixture:
    """Seed the corpus plus the rows a real filter must reject, then analyze.

    Parameters: the disposable database, the corpus, the baked embeddings, the model
    name, how many stale and decoy rows to add, how many of the stale rows are near
    copies of a corpus claim (`twins`; `None` means one per claim for similar/duplicates
    geometry and zero for unrelated geometry), and how the stale rows relate
    to the claims (`geometry`).
    What it does: seeds the corpus through `brain.apply`, adds unlabelled rows that the
    searched scope must filter out, and analyzes the table as the admin login so the
    planner works from real statistics.
    Output: the Fixture describing what was seeded.
    """
    if geometry not in STALE_GEOMETRIES:
        raise EvaluationError(f"Unknown stale geometry {geometry!r}")
    if twins is not None and twins > 0 and geometry == "unrelated":
        raise EvaluationError("Positive twins require similar or duplicates stale geometry")
    identity = Identity.new("primary")
    stale_model = OLD_MODEL
    real = [embeddings.vector(claim.text) for claim in corpus.claims]
    pooled = _perturbed(real)
    twins = min(stale, len(corpus.claims) if twins is None else max(0, twins))
    archive = max(0, stale - twins)
    if stale and geometry == "unrelated":
        archive, twins = stale, 0
    with database.connect() as connection:
        connection.execute("SET statement_timeout = '600s'")
        seed_identity(connection, identity)
        claim_ids = seed_corpus(connection, identity, corpus, embeddings, model_name=model_name)
        if twins:
            # The previous model's embeddings of the same claims: the rows a model
            # filter must reject, and the rows that compete for a candidate slot.
            _fill_rows(
                connection,
                identity,
                count=twins,
                model_name=stale_model,
                vectors=pooled,
                label="stale-twins",
            )
        if archive:
            # Old-model rows of memories that were never rebuilt: topically similar at
            # best, usually unrelated to the in-scope claims.
            pool = (
                _perturbed(real, noise=1.2)
                if geometry == "similar"
                else _unrelated_pool(len(corpus.claims))
            )
            _fill_rows(
                connection,
                identity,
                count=archive,
                model_name=stale_model,
                vectors=pool,
                label=f"stale-{geometry}",
            )
        connection.commit()
    decoy_identity = None
    if decoys:
        decoy_identity = Identity.new("decoy")
        with database.connect() as connection:
            connection.execute("SET statement_timeout = '600s'")
            seed_identity(connection, decoy_identity)
            _fill_rows(
                connection,
                decoy_identity,
                count=decoys,
                model_name=model_name,
                vectors=_perturbed(real, seed=1_000_000),
                label="decoy",
            )
            connection.commit()
    with database.connect(role="admin") as connection:
        # A Space holds a tiny share of a shared table, so the default sample can
        # misjudge the scope and make the planner flip between the exact primary-key
        # path and the HNSW index between runs. Aim the statistics at the scope columns
        # so the plan a run reports is the plan it measured.
        for column in ("organization_id", "space_id", "embedding_model"):
            connection.execute(
                sql.SQL(
                    "ALTER TABLE space.memory_claims ALTER COLUMN {} SET STATISTICS 1000"
                ).format(sql.Identifier(column))
            )
        connection.execute("ANALYZE space.memory_claims")
        connection.commit()
    with database.connect() as connection:
        # A measurement is only allowed to run against a fixture that holds the corpus.
        set_scope(connection, identity)
        verified = verify_seeding(connection, claim_ids, model_name)
    with database.connect(role="admin") as connection:
        # Count what the database actually holds, not what the caller asked for.
        total_rows, current_rows, stale_rows = connection.execute(
            "SELECT count(*), count(*) FILTER (WHERE embedding_model=%s), "
            "count(*) FILTER (WHERE embedding_model<>%s) FROM space.memory_claims",
            (model_name, model_name),
        ).fetchone()
        connection.commit()
    return Fixture(
        identity=identity,
        decoy_identity=decoy_identity,
        model=model_name,
        stale_model=stale_model,
        corpus_claims=len(corpus.claims),
        memories=len({claim.source for claim in corpus.claims}),
        verified=verified,
        stale_claims=int(stale_rows),
        twin_claims=twins,
        decoy_claims=max(0, int(current_rows) - len(corpus.claims)),
        rows=int(total_rows),
        claim_ids=claim_ids,
    )


def verify_seeding(connection, claim_ids: Mapping[str, str], model_name: str) -> dict:
    """Refuse a fixture that cannot support a measurement.

    Parameters: a scoped connection, the corpus claim id map and the model name.
    What it does: counts the rows in scope and checks that every corpus claim the map
    names is really in the table. A run against an empty or half-seeded fixture would
    produce zeros that look like a result.
    Output: the verified counts. Raises EvaluationError when the fixture is unusable.
    """
    counts = connection.execute(
        "SELECT count(*) FILTER (WHERE embedding_model=%s) AS in_scope, count(*) AS total "
        "FROM space.memory_claims",
        (model_name,),
    ).fetchone()
    present = {
        str(row["claim_id"])
        for row in connection.execute("SELECT claim_id FROM space.memory_claims").fetchall()
    }
    missing = sorted(set(claim_ids) - present)
    verified = {
        "rows_in_scope": int(counts["in_scope"]),
        "rows_total": int(counts["total"]),
        "corpus_claims_expected": len(claim_ids),
        "corpus_claims_present": len(claim_ids) - len(missing),
    }
    if verified["rows_in_scope"] == 0:
        raise EvaluationError(
            f"The fixture holds no rows in scope, so every metric would be zero: {verified}"
        )
    if missing:
        raise EvaluationError(
            f"The fixture is missing {len(missing)} of {len(claim_ids)} corpus claims "
            f"(for example {missing[:3]}); the id map would not match the seeded rows"
        )
    return verified


def explain_search(
    connection,
    vector: Sequence[float],
    model_name: str,
    limit: int,
    *,
    config=brain.DEFAULT_RETRIEVAL,
) -> dict:
    """Report which plan the database chose for the shipped search statement.

    Parameters: a scoped connection, the query embedding, the model name and the limit.
    What it does: runs EXPLAIN over a statement that mirrors `brain.search` so the
    chosen node names can be reported next to the under-return number. It never
    produces a result the metrics use.
    Output: the plan node types and the index the scan used, if any.
    """
    statement, parameters = brain.search_statement(vector, model_name, limit, config)
    rows = connection.execute("EXPLAIN " + statement, parameters).fetchall()
    lines = [(row.get("QUERY PLAN") if isinstance(row, dict) else row[0]) or "" for row in rows]
    nodes = [line.strip().split("  (")[0].strip() for line in lines]
    index = next(
        (
            line.split("using", 1)[1].strip().split(" ")[0]
            for line in lines
            if "using" in line and "memory_claims" in line
        ),
        None,
    )
    return {"nodes": nodes[:6], "index": index}
