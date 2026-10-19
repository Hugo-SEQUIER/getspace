"""The shared command boundary for browser and MCP access to a Private Space."""

import asyncio
import hmac
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from contextvars import copy_context
from copy import deepcopy
from datetime import UTC, datetime
from time import perf_counter
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from psycopg.errors import RaiseException
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, ConfigDict, Field, model_validator

from space import (
    agents,
    brain,
    content_changes,
    memory_classification,
    memory_creator,
    memory_link_index,
    memory_node_links,
    memory_pipeline,
    memory_relationships,
    memory_routing,
    overview,
    quotas,
    recall_candidates,
    recall_context,
    reclassify_jobs,
    relevance,
    tasks,
)
from space.admission import BoundedAdmission, admitted
from space.job_context import active_job
from space.memory import (
    ENGINE,
    MAX_LEXICAL_BODIES,
    MAX_LEXICAL_CANDIDATES,
    MAX_MEMORY_LIST,
    MAX_MEMORY_LOAD,
    MAX_RECALL_CONCURRENCY,
    ChangeMemory,
    RecallMetadata,
    RecallRequest,
    ReclassifyMemory,
    RememberClaims,
    RememberMemory,
    SaveHandoff,
    SetMemoryLinks,
    bounded_result,
    rank_lexical,
    tokens,
)
from space.memory_pipeline import BuildMemory
from space.memory_snapshot import IncomingMemorySnapshot
from space.model_provider import MemoryModels, ModelError
from space.providers import (
    LOCAL_PROVIDER,
    KeyStore,
    ObjectStore,
    key_operation,
    unwrap_for_operation,
)
from space.read_timing import read_stage, record_timing
from space.security.auth import Identity
from space.security.payloads import decode_payload, encode_payload
from space.settings import Settings, database_connection_options
from space.terms import query_fingerprints, resource_fingerprints, term_key


class CommandError(Exception):
    def __init__(self, message: str, status: int = 400):
        self.message, self.status = message, status
        super().__init__(message)


class SnapshotChanged(CommandError):
    """A fresh snapshot can safely retry; an explicit revision conflict cannot."""


class SaveResource(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    command_id: UUID
    resource_id: UUID | None = None
    expected_revision: int = Field(ge=0, strict=True)
    kind: Literal["document", "task"]
    title: str = Field(min_length=1, max_length=300)
    body: str = Field(default="", max_length=700_000)
    status: tasks.Status = "todo"
    recall: RecallMetadata | None = None
    project: UUID | None = None
    folder: UUID | None = None
    priority: Literal["High", "Medium", "Low"] | None = None

    @model_validator(mode="after")
    def valid_revision(self):
        if bool(self.resource_id) != (self.expected_revision > 0):
            raise ValueError(
                "Create with revision 0; updates require resource ID and current revision"
            )
        if not self.title.strip():
            raise ValueError("A title is required")
        if self.kind == "task" and self.recall and self.recall.category == "handoff":
            raise ValueError("Session handoffs must be documents")
        if self.kind != "document" and self.folder is not None:
            raise ValueError("Only documents can belong to a folder")
        if len(self.model_dump_json().encode()) > 1024 * 1024:
            raise ValueError("Resource exceeds 1 MiB")
        return self


class Database:
    def __init__(self, settings: Settings, *, pool_size: int | None = None):
        # A self-hosted installation has no environment fence: one database, one service.
        self.environment_id = None
        self.required_connections = settings.database_connection_budget
        self.pool = ConnectionPool(
            settings.database_url.get_secret_value(),
            min_size=0,
            max_size=pool_size or settings.database_pool_size,
            open=False,
            timeout=8,
            max_waiting=max(16, settings.database_pool_size * 4),
            configure=self._configure_budget,
            # Reject connections closed while idle before handing them to a request.
            check=ConnectionPool.check_connection,
            kwargs={
                **database_connection_options(settings),
                "connect_timeout": 5,
                "row_factory": dict_row,
            },
        )

    @staticmethod
    def _configure(connection):
        role = connection.execute(
            "SELECT current_user = 'space_app' AS expected, "
            "rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb AS privileged "
            "FROM pg_roles WHERE rolname=current_user"
        ).fetchone()
        connection.commit()
        if not role or not role["expected"] or role["privileged"]:
            raise RuntimeError("Runtime database must use the restricted space_app role")

    def _configure_budget(self, connection):
        self._configure(connection)
        row = connection.execute(
            "SELECT rolconnlimit FROM pg_roles WHERE rolname=current_user"
        ).fetchone()
        connection.commit()
        self.validate_budget(row["rolconnlimit"], self.required_connections)

    @staticmethod
    def validate_budget(allowance, required):
        if allowance >= 0 and allowance < required:
            raise RuntimeError(
                f"space_app allows {allowance} connections; configured deployment requires "
                f"{required}. Coordinate role allowance, pools, processes, replicas and overlap."
            )

    def open(self):
        self.pool.open()
        # Fail startup when configured pools cannot fit, instead of serving intermittent 503s.
        if hasattr(self, "required_connections"):
            try:
                with self.pool.connection():
                    pass
            except Exception:
                self.pool.close()
                raise

    def close(self):
        self.pool.close()

    def adopt_embedding_width(self):
        """Read the claim index's embedding width once per process, before any write."""
        with self.pool.connection() as connection, connection.transaction():
            return brain.load_dimensions(connection)

    @contextmanager
    def transaction(self, identity: Identity):
        identity.check()
        # Keep query order and an explicit atomic transaction, but batch commands
        # until a result is needed instead of waiting for each setup statement.
        try:
            with ExitStack() as stack:
                with read_stage("db_pool"):
                    connection = stack.enter_context(self.pool.connection())
                with read_stage("db_transaction"), connection.transaction(), connection.pipeline():
                    self.check_environment(connection)
                    connection.execute(
                        "SELECT set_config('space.identity_issuer', %s, true)", (identity.issuer,)
                    )
                    connection.execute(
                        "SELECT set_config('space.identity_subject', %s, true)", (identity.subject,)
                    )
                    yield connection
        except RaiseException as error:
            messages = {
                "space.storage_quota": "Space storage limit reached. Contact the operator.",
                "space.item_quota": "Space item limit reached. Contact the operator.",
                "space.model_quota": (
                    "Monthly model request limit reached. Source storage is available."
                ),
            }
            if error.diag.message_primary in messages:
                raise CommandError(messages[error.diag.message_primary], 429) from None
            raise

    @contextmanager
    def system_transaction(self):
        """A transaction with no user identity, only for signature-verified provider callbacks.

        Row-level security hides every tenant row; callers use narrow definer functions.
        """
        with self.pool.connection() as connection, connection.transaction():
            self.check_environment(connection)
            yield connection

    def check_environment(self, connection):
        row = connection.execute(
            "SELECT space.check_environment(%s) AS ready", (getattr(self, "environment_id", None),)
        ).fetchone()
        if not row["ready"]:
            raise CommandError("Environment unavailable during maintenance or recovery", 503)


def encode(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


MEMORY_PAGE_SQL = (
    "SELECT r.id AS resource_id, r.kind, r.memory_kind, v.revision, v.key_id, "
    "v.encrypted_metadata, v.encrypted_record, v.object_key, v.created_at, "
    "v.author_client_id, v.author_principal_id "
    "FROM space.resources r JOIN space.resource_revisions v "
    "ON (r.organization_id,r.space_id,r.id,r.current_revision)="
    "(v.organization_id,v.space_id,v.resource_id,v.revision) "
    "WHERE r.space_id=%s AND r.memory_kind='memory' "
    "AND (%s::uuid IS NULL OR (r.created_at, r.id) < "
    "(SELECT created_at, id FROM space.resources WHERE id=%s)) "
    "ORDER BY r.created_at DESC, r.id DESC LIMIT %s"
)
MATCHED_QUERY_TERMS_SQL = (
    "WITH matches AS ("
    "SELECT DISTINCT t.resource_id, t.revision, u.term FROM space.resource_terms t "
    "JOIN LATERAL (SELECT unnest(t.terms) AS term) u ON TRUE "
    "WHERE t.space_id=%s AND t.terms && %s::bytea[] AND u.term=ANY(%s::bytea[])), "
    "rarity AS (SELECT term, count(*) AS resources FROM matches GROUP BY term), "
    "ranked AS (SELECT m.resource_id, m.revision, sum(1.0 / r.resources) AS weight "
    "FROM matches m JOIN rarity r ON r.term=m.term GROUP BY m.resource_id, m.revision) "
)
CANDIDATES_SQL = MATCHED_QUERY_TERMS_SQL + (  # noqa: S608 - static SQL; all values are parameters
    "SELECT resource_id, revision, weight FROM ranked JOIN space.resources r "
    "ON r.id=ranked.resource_id AND r.current_revision=ranked.revision "
    "WHERE r.memory_kind IS DISTINCT FROM 'memory' "
    "ORDER BY weight DESC, resource_id LIMIT %s"
)
MEMORY_SEARCH_SQL = MATCHED_QUERY_TERMS_SQL + (
    "SELECT r.id AS resource_id, r.kind, r.memory_kind, v.revision, v.key_id, "
    "v.encrypted_metadata, v.encrypted_record, v.object_key, v.created_at, "
    "v.author_client_id, v.author_principal_id "
    "FROM ranked JOIN space.resources r ON r.id = ranked.resource_id "
    "JOIN space.resource_revisions v "
    "ON (r.organization_id,r.space_id,r.id,r.current_revision)="
    "(v.organization_id,v.space_id,v.resource_id,v.revision) "
    "WHERE r.space_id=%s AND r.memory_kind='memory' "
    "ORDER BY ranked.weight DESC, r.id LIMIT %s"
)


class Workspace:
    def __init__(
        self,
        database: Database,
        keys: KeyStore,
        objects: ObjectStore,
        resource_url: str,
        web_url: str = "http://127.0.0.1:3000",
        memory_models: MemoryModels | None = None,
        recall_concurrency: int = MAX_RECALL_CONCURRENCY,
        retrieval: brain.RetrievalConfig = brain.DEFAULT_RETRIEVAL,
        *,
        recall_queue_size: int = 128,
        recall_queue_timeout: float = 5,
        memory_write_concurrency: int = 2,
        memory_write_queue_size: int = 32,
        memory_write_queue_timeout: float = 5,
    ):
        self.db, self.keys, self.objects, self.resource_url = database, keys, objects, resource_url
        self.web_url = web_url.rstrip("/")
        self.forget_journal = None
        self._recall_admission = BoundedAdmission(
            recall_concurrency, recall_queue_size, recall_queue_timeout, name="Recall"
        )
        self._write_admission = BoundedAdmission(
            memory_write_concurrency,
            memory_write_queue_size,
            memory_write_queue_timeout,
            name="Memory processing",
        )
        self.retrieval = retrieval
        self._object_reads = ThreadPoolExecutor(max_workers=4, thread_name_prefix="space-object")
        self.memory_models = memory_models or MemoryModels(Settings(environment="test"))

    def _unwrap_key(self, organization_id, space_id, row):
        return unwrap_for_operation(self.keys, organization_id, space_id, row)

    def _model_session(
        self, identity, space_id, authorize, *, incoming_snapshot=None, recall_snapshot=None
    ):
        """Parameters: caller, disclosure check and optional write or recall snapshot.
        What it does: combines snapshot authorization and quota charging before disclosure.
        Output: model session retaining the independent post-provider authorization check.
        """
        model = self.memory_models.session(authorize)
        model.before_request = lambda: quotas.consume_model_request(self, identity, space_id)
        if incoming_snapshot is not None:
            model.prepare_request = lambda: self._authorize_model(
                identity, space_id, incoming_snapshot, write=True, charge=True
            )
        elif recall_snapshot is not None:
            model.prepare_request = lambda: self._authorize_model(
                identity, space_id, recall_snapshot, charge=True
            )
        return model

    async def aclose(self):
        await asyncio.gather(self._recall_admission.aclose(), self._write_admission.aclose())
        await asyncio.to_thread(self._object_reads.shutdown, wait=True)
        if hasattr(self.memory_models, "close"):
            await asyncio.to_thread(self.memory_models.close)

    @staticmethod
    def _set_scope(connection, organization_id, space_id):
        connection.execute(
            "SELECT set_config('space.organization_id', %s, true)", (str(organization_id),)
        )
        connection.execute("SELECT set_config('space.space_id', %s, true)", (str(space_id),))

    def _scope(
        self,
        connection,
        identity,
        space_id=None,
        *,
        write=False,
        human=False,
        api_key=False,
        allow_unidentified=False,
        grant_for_update=False,
    ):
        identity.check(human=human, api_key=api_key)
        selected = space_id or identity.selected_space_id
        if identity.api_key:
            # A key reaches only the Space it was created for; others look absent.
            if selected is not None and str(selected) != str(identity.api_key.space_id):
                raise CommandError("Space not found or access was removed.", 404)
            selected = identity.api_key.space_id
        if identity.client_id and selected is None:
            selected = connection.execute(
                "SELECT space.connection_space(%s,%s) AS id",
                (identity.client_id, identity.grant_id),
            ).fetchone()["id"]
            if selected is None:
                raise CommandError("Agent access is absent or revoked.", 403)
        row = connection.execute(
            "SELECT a.organization_id,a.space_id,o.kind,o.default_space_id,"
            "space.current_principal_id() AS principal_id,"
            "space.space_role(a.organization_id,a.space_id) AS role "
            "FROM space.resolve_space(%s) a JOIN space.organizations o "
            "ON o.id=a.organization_id",
            (selected,),
        ).fetchone()
        if row is None:
            raise CommandError("Space not found or access was removed.", 404)
        self._set_scope(connection, row["organization_id"], row["space_id"])
        if row["kind"] == "team":
            # Member removal and access edits take the exclusive version of this
            # lock. Re-read after locking to close the lookup/removal race.
            connection.execute(
                "SELECT pg_advisory_xact_lock_shared(hashtextextended(%s,16))",
                (str(row["organization_id"]),),
            )
            row["role"] = connection.execute(
                "SELECT space.space_role(%s,%s) AS role",
                (row["organization_id"], row["space_id"]),
            ).fetchone()["role"]
            if not row["role"]:
                raise CommandError("Space not found or access was removed.", 404)
        if write and row["role"] == "viewer":
            raise CommandError("You have view-only access to this Space.", 403)
        if write and identity.api_key and not identity.api_key.can_write:
            raise CommandError("This API key is read-only.", 403)
        if receipt := active_job.get():
            lease = connection.execute(
                "SELECT space.memory_job_lease_active(%s,%s) AS active",
                receipt,
            ).fetchone()
            if not lease["active"]:
                raise CommandError("The background job lease is no longer active", 409)
        if identity.client_id:
            grant = connection.execute(
                "SELECT id, can_write, agent_id FROM space.oauth_grants WHERE "  # noqa: S608
                "principal_id=%s AND client_id=%s "
                "AND id=%s AND resource=%s AND revoked_at IS NULL "
                + ("FOR UPDATE" if grant_for_update else "FOR SHARE"),
                (row["principal_id"], identity.client_id, identity.grant_id, self.resource_url),
            ).fetchone()
            if grant is None or (write and not grant["can_write"]):
                raise CommandError(
                    "Agent access is absent, revoked, or does not allow this operation.", 403
                )
            row["agent_id"] = grant["agent_id"]
            if not allow_unidentified:
                # Legacy queued work predates the identity contract; it may only
                # continue while its connection remains unidentified as well.
                legacy_job = active_job.get() and not identity.agent_id and not grant["agent_id"]
                if not legacy_job and (not identity.agent_id or not grant["agent_id"]):
                    raise CommandError(
                        "Agent identification required. Call identify_agent, then send "
                        "X-Space-Agent-ID or agent_id with every request.",
                        409,
                    )
                if identity.agent_id and str(grant["agent_id"]) != identity.agent_id:
                    raise CommandError("Agent ID does not match this connection", 403)
        return row

    def _key_row(self, connection, scope, key_id=None):
        return connection.execute(
            "SELECT k.* FROM space.space_keys k JOIN space.spaces s "
            "ON s.organization_id=k.organization_id AND s.id=k.space_id "
            "WHERE k.id=coalesce(%s, s.metadata_key_id)",
            (key_id,),
        ).fetchone()

    def _key(self, identity, space_id, *, write=False):
        with self.db.transaction(identity) as connection:
            scope = self._scope(connection, identity, space_id, write=write)
            row = self._key_row(connection, scope)
        with read_stage("space_key"):
            return scope, self._unwrap_key(scope["organization_id"], scope["space_id"], row)

    # Payloads are stored as readable JSON. The key, scope, resource, revision and
    # purpose arguments name what every write path stores; they do not change the
    # stored bytes.
    def _pack(self, key, scope, resource_id, revision, purpose, value):
        return encode_payload(value)

    def _unpack(self, key, scope, resource_id, revision, purpose, value):
        return decode_payload(value)

    def bootstrap(self, identity: Identity):
        identity.check(human=True)
        with self.db.transaction(identity) as connection:
            from space.environment import subject_hash

            if connection.execute(
                "SELECT space.identity_erased(%s) AS erased",
                (subject_hash(identity.issuer, identity.subject),),
            ).fetchone()["erased"]:
                raise CommandError("This account has been erased", 403)
            exists = connection.execute(
                "SELECT id FROM space.organizations WHERE kind='personal'"
            ).fetchone()
        if not exists:
            scope = {"organization_id": uuid4(), "space_id": uuid4()}
            key, wrapped = self.keys.generate(scope["organization_id"], scope["space_id"])
            collection_id = uuid4()
            metadata = self._pack(
                key, scope, scope["space_id"], 1, "metadata", {"name": "Private Space"}
            )
            schema = self._pack(
                key,
                scope,
                collection_id,
                1,
                "schema",
                {
                    "name": "Tasks",
                    "version": 1,
                    "fields": {
                        "title": "text",
                        "body": "text",
                        "status": ["todo", "in_progress", "done"],
                    },
                },
            )
            with self.db.transaction(identity) as connection:
                # Serialize bootstrap retries without any provider call under the lock.
                if connection.execute(
                    "SELECT space.identity_erased(%s) AS erased",
                    (subject_hash(identity.issuer, identity.subject),),
                ).fetchone()["erased"]:
                    raise CommandError("This account has been erased", 403)
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (identity.issuer + ":" + identity.subject,),
                )
                if not connection.execute(
                    "SELECT id FROM space.organizations WHERE kind='personal'"
                ).fetchone():
                    self._set_scope(connection, scope["organization_id"], scope["space_id"])
                    connection.execute(
                        "INSERT INTO space.principals(issuer,subject) VALUES(%s,%s) "
                        "ON CONFLICT DO NOTHING",
                        (identity.issuer, identity.subject),
                    )
                    principal = connection.execute("SELECT id FROM space.principals").fetchone()[
                        "id"
                    ]
                    connection.execute(
                        "INSERT INTO space.organizations(id,owner_principal_id,"
                        "default_space_id) VALUES(%s,%s,%s)",
                        (scope["organization_id"], principal, scope["space_id"]),
                    )
                    connection.execute(
                        "INSERT INTO space.spaces(organization_id,id,metadata_key_id,"
                        "encrypted_metadata) VALUES(%s,%s,%s,%s)",
                        (scope["organization_id"], scope["space_id"], key.key.key_id, metadata),
                    )
                    connection.execute(
                        "INSERT INTO space.space_keys(organization_id,space_id,id,"
                        "wrapping_provider,wrapping_key_reference,wrapped_key) "
                        "VALUES(%s,%s,%s,%s,%s,%s)",
                        (
                            scope["organization_id"],
                            scope["space_id"],
                            key.key.key_id,
                            LOCAL_PROVIDER,
                            self.keys.reference,
                            wrapped,
                        ),
                    )
                    connection.execute(
                        "INSERT INTO space.collections(organization_id,space_id,id,"
                        "system_role,key_id,encrypted_schema) VALUES(%s,%s,%s,"
                        "'tasks',%s,%s)",
                        (
                            scope["organization_id"],
                            scope["space_id"],
                            collection_id,
                            key.key.key_id,
                            schema,
                        ),
                    )
        return self.info(identity)

    def info(self, identity, space_id=None):
        with self.db.transaction(identity) as connection:
            scope = self._scope(connection, identity, space_id, allow_unidentified=True)
            row = connection.execute(
                "SELECT s.encrypted_metadata,s.visibility,c.id AS tasks_collection_id "
                "FROM space.spaces s JOIN space.collections c "
                "ON c.organization_id=s.organization_id AND c.space_id=s.id "
                "WHERE c.system_role='tasks'"
            ).fetchone()
            key_row = self._key_row(connection, scope)
        key = self._unwrap_key(scope["organization_id"], scope["space_id"], key_row)
        metadata = self._unpack(
            key, scope, scope["space_id"], 1, "metadata", row["encrypted_metadata"]
        )
        with self.db.transaction(identity) as connection:
            self._scope(connection, identity, scope["space_id"], allow_unidentified=True)
        return {
            "id": str(scope["space_id"]),
            "organization_id": str(scope["organization_id"]),
            "kind": scope["kind"],
            "role": scope["role"],
            "visibility": row["visibility"],
            "is_default": scope["space_id"] == scope["default_space_id"],
            "name": metadata["name"],
            "tasks_collection_id": str(row["tasks_collection_id"]),
            "mcp_url": self.resource_url,
            "agent_id": str(scope["agent_id"]) if scope.get("agent_id") else None,
            "space_map": {"tool": "get_space_map"},
        }

    def _recheck(self, identity, space_id):
        with self.db.transaction(identity) as connection:
            self._scope(connection, identity, space_id)

    @staticmethod
    def _summary(row, metadata, principal_id=None):
        return {
            "id": str(row["resource_id"]),
            "kind": "task" if row["kind"] == "record" else "document",
            "revision": row["revision"],
            "title": metadata["title"],
            "status": metadata.get("status", "todo"),
            "updated_at": row["created_at"].isoformat(),
            "author": "agent"
            if row["author_client_id"] or metadata.get("author_api_key")
            else (
                "Teammate"
                if row.get("author_principal_id")
                and principal_id
                and row["author_principal_id"] != principal_id
                else "you"
            ),
            "author_client_id": str(row["author_client_id"]) if row["author_client_id"] else None,
            "author_agent": metadata.get("author_agent"),
            "project": metadata.get("project"),
            "folder": metadata.get("folder"),
            "priority": metadata.get("priority"),
            "recall": metadata.get("recall", RecallMetadata().model_dump(mode="json")),
        }

    def list_resources(
        self,
        identity,
        space_id: UUID,
        kind: Literal["document", "task"],
        after: UUID | None = None,
        limit: int = 50,
    ):
        """One page of summaries. `limit` is validated by the API route (1 to 500)."""
        with self.db.transaction(identity) as connection:
            scope = self._scope(connection, identity, space_id)
            rows = connection.execute(
                "SELECT v.resource_id, v.revision, v.key_id, v.encrypted_metadata, "
                "v.created_at, v.author_client_id, v.author_principal_id, r.kind "
                "FROM space.resources r JOIN space.resource_revisions v "
                "ON (r.organization_id,r.space_id,r.id,"
                "r.current_revision)=(v.organization_id,v.space_id,"
                "v.resource_id,v.revision) "
                "WHERE r.kind=%s AND (%s::uuid IS NULL OR r.id>%s) "
                # Memory is not a document, and the readable marker says so, so no row is
                # decrypted only to be dropped. A handoff is excluded here too: it is read
                # through resume recall, not listed as a document.
                "AND r.memory_kind IS NULL "
                "ORDER BY r.id LIMIT %s",
                ("record" if kind == "task" else "document", after, after, limit + 1),
            ).fetchall()
            key_rows = {
                key_id: self._key_row(connection, scope, key_id)
                for key_id in {row["key_id"] for row in rows[:limit]}
            }
        keys = {
            id_: self._unwrap_key(scope["organization_id"], scope["space_id"], row)
            for id_, row in key_rows.items()
        }
        items = [
            self._summary(
                row,
                self._unpack(
                    keys[row["key_id"]],
                    scope,
                    row["resource_id"],
                    row["revision"],
                    "metadata",
                    row["encrypted_metadata"],
                ),
                scope["principal_id"],
            )
            for row in rows[:limit]
        ]
        self._recheck(identity, space_id)
        return {
            "items": items,
            "next_cursor": rows[limit - 1]["resource_id"] if len(rows) > limit else None,
        }

    def get_resource(
        self,
        identity,
        space_id: UUID,
        resource_id: UUID,
        revision: int | None = None,
        *,
        _internal=False,
    ):
        with read_stage("db_read"), self.db.transaction(identity) as connection:
            scope = self._scope(connection, identity, space_id)
            row = connection.execute(
                "SELECT v.*, r.kind, k.wrapping_provider, k.wrapping_key_reference, "
                "k.wrapped_key FROM space.resources r JOIN space.resource_revisions v "
                "ON (r.organization_id,r.space_id,r.id)=(v.organization_id,"
                "v.space_id,v.resource_id) "
                "JOIN space.space_keys k ON (k.organization_id,k.space_id,k.id)="
                "(v.organization_id,v.space_id,v.key_id) "
                "WHERE r.id=%s AND v.revision=coalesce(%s,r.current_revision)",
                (resource_id, revision),
            ).fetchone()
            if row is None:
                raise CommandError("Resource not found", 404)
            key_row = {
                "id": row["key_id"],
                "wrapping_provider": row["wrapping_provider"],
                "wrapping_key_reference": row["wrapping_key_reference"],
                "wrapped_key": row["wrapped_key"],
            }
        # Both remote reads depend only on the authorized revision. Keep key handling
        # on the calling thread so command-local key reuse retains its existing scope.
        pending = (
            self._object_reads.submit(
                copy_context().run, self._read_resource_object, row["object_key"], perf_counter()
            )
            if row["object_key"]
            else None
        )
        try:
            with read_stage("space_key"):
                key = self._unwrap_key(scope["organization_id"], scope["space_id"], key_row)
            payload = pending.result() if pending is not None else row["encrypted_record"]
        finally:
            # On key failure, cancel queued I/O or drain an active read before returning.
            if pending is not None and not pending.cancel():
                pending.exception()
        with read_stage("decode"):
            content = self._unpack(
                key,
                scope,
                resource_id,
                row["revision"],
                "source" if row["kind"] == "document" else "record",
                payload,
            )
        with read_stage("db_recheck"):
            self._recheck(identity, space_id)
        result = {**self._summary(row, content, scope["principal_id"]), "body": content["body"]}
        if content.get("memory"):
            result["memory"] = memory_pipeline.public_memory(content["memory"])
            if _internal:
                result["_memory"] = content["memory"]
        return result

    def _read_resource_object(self, object_key, queued_at):
        record_timing("object_queue", perf_counter() - queued_at)
        with read_stage("object_get"):
            return self.objects.get(object_key)

    @staticmethod
    def _previous(connection, identity, scope, command_id, fingerprint):
        previous = connection.execute(
            "SELECT * FROM space.commands WHERE id=%s", (command_id,)
        ).fetchone()
        return Workspace._validate_previous(identity, scope, previous, fingerprint)

    @staticmethod
    def _validate_previous(identity, scope, previous, fingerprint):
        if previous and (
            previous["principal_id"] != scope["principal_id"]
            or str(previous["client_id"] or "") != (identity.client_id or "")
            or not hmac.compare_digest(bytes(previous["fingerprint"]), fingerprint)
        ):
            raise CommandError("This command ID was already used for another operation.", 409)
        return previous

    def _write_context(self, identity, space_id, command_id):
        """Read key metadata and the command receipt together; unwrap outside the transaction."""
        with self.db.transaction(identity) as connection:
            scope = self._scope(connection, identity, space_id, write=True)
            key_row = self._key_row(connection, scope)
            previous = connection.execute(
                "SELECT * FROM space.commands WHERE id=%s", (command_id,)
            ).fetchone()
        key = self._unwrap_key(scope["organization_id"], scope["space_id"], key_row)
        return scope, key, previous

    @staticmethod
    def _memory_fingerprint(identity, key, operation, request):
        # Adding optional import evidence must not invalidate existing command receipts.
        payload = request.model_dump(mode="json")
        if payload.get("provenance") is None:
            payload.pop("provenance", None)
        if not payload.get("links"):
            payload.pop("links", None)
        return hmac.digest(
            key.key.material,
            b"space.memory.v1\0"
            + ((identity.agent_id + "\0").encode() if identity.agent_id else b"")
            + operation.encode()
            + b"\0"
            + encode(payload),
            "sha256",
        )

    def _memory_retry(self, identity, space_id, operation, request):
        """Fingerprint one memory command and return the revision it already saved.

        Parameters: the caller identity, the Space, the memory operation name and request.
        What it does: fingerprints the exact payload and looks for a committed command.
        Output: (fingerprint, saved resource or None). Reusing a command ID with a
        different payload raises, so a paid model call is never repeated or confused.
        """
        scope, key, previous = self._write_context(identity, space_id, request.command_id)
        fingerprint = self._memory_fingerprint(identity, key, operation, request)
        previous = self._validate_previous(identity, scope, previous, fingerprint)
        if previous is None:
            return fingerprint, None
        return fingerprint, self.get_resource(
            identity, space_id, previous["resource_id"], previous["revision"]
        )

    @key_operation
    def save(self, identity, space_id: UUID, request: SaveResource):
        return self._save(identity, space_id, request)

    def _save(
        self,
        identity,
        space_id,
        request,
        *,
        memory=None,
        fingerprint=None,
        snapshot=None,
        new_id=None,
        index=None,
        quota_deletion=False,
    ):
        scope, key, previous = self._write_context(identity, space_id, request.command_id)
        fingerprint = fingerprint or hmac.digest(
            key.key.material,
            b"space.command.v1\0"
            + ((identity.agent_id + "\0").encode() if identity.agent_id else b"")
            + encode(
                request.model_dump(
                    mode="json",
                    exclude=({"recall"} if request.recall is None else set())
                    | ({"project", "folder", "priority"} - request.model_fields_set),
                )
            ),
            "sha256",
        )
        previous = self._validate_previous(identity, scope, previous, fingerprint)
        if previous:
            # Return the original committed revision without staging another object.
            return self.get_resource(
                identity, space_id, previous["resource_id"], previous["revision"]
            )
        if request.kind == "task":
            tasks.ensure_status(self, identity, space_id, request.status)
        resource_id = request.resource_id or new_id or uuid4()
        revision = request.expected_revision + 1
        recall = request.recall
        previous_sources = set()
        project, folder, priority = request.project, request.folder, request.priority
        previous_folder = None
        if request.resource_id:
            # Generic document editors preserve recall controls and source references.
            # Read the expected immutable revision; the transaction still checks CAS.
            old = self.get_resource(identity, space_id, resource_id, request.expected_revision)
            previous_sources = {
                (ref["resource_id"], ref["revision"]) for ref in old["recall"]["sources"]
            }
            previous_folder = UUID(old["folder"]) if old.get("folder") else None
            if "folder" not in request.model_fields_set:
                folder = previous_folder
            if "project" not in request.model_fields_set:
                project = UUID(old["project"]) if old.get("project") else None
            if "priority" not in request.model_fields_set:
                priority = old.get("priority")
            if old["recall"]["category"] == "memory" and memory is None:
                raise CommandError(
                    "Use change_memory to edit or delete a memory, or build_memory to rebuild it",
                    409,
                )
            if recall is None:
                recall = RecallMetadata.model_validate(old["recall"])
        recall = (recall or RecallMetadata()).model_dump(mode="json")
        if recall["category"] == "memory" and memory is None:
            raise CommandError("Generated memory records require build_memory", 400)
        content = {
            "title": request.title,
            "body": request.body,
            "status": request.status,
            "recall": recall,
            "project": str(project) if project else None,
            "folder": str(folder) if folder else None,
            "priority": priority,
            "author_agent": agents.current_profile(self, identity)
            if scope.get("agent_id")
            else None,
            # A personal API key is the person's own client, not the person reading the
            # website, so its writes count as unseen until they open them.
            "author_api_key": identity.api_key.id if identity.api_key else None,
        }
        if memory is not None:
            if request.expected_revision == 0:
                memory_creator.capture(memory, content["author_agent"])
            content["memory"] = memory
        # The readable marker mirrors the recall category: NULL for a source document or
        # task, otherwise the memory kind. A handoff carries no generated record but is
        # still memory, so the category is the source of truth, not the record.
        memory_kind = recall["category"] if recall["category"] != "source" else None
        encrypted = self._pack(
            key,
            scope,
            resource_id,
            revision,
            "source" if request.kind == "document" else "record",
            content,
        )
        metadata = self._pack(
            key,
            scope,
            resource_id,
            revision,
            "metadata",
            {
                "title": request.title,
                "status": request.status,
                "recall": recall,
                "author_agent": content["author_agent"],
                "author_api_key": content["author_api_key"],
                "project": content["project"],
                "folder": content["folder"],
                "priority": content["priority"],
            },
        )
        # Searchable fingerprints of the same text, so recall can select this resource with
        # one indexed query. Written with the revision below, never separately.
        index_rows = resource_fingerprints(
            request.title, recall["topics"], request.body, term_key(key.key.material)
        )
        # Every body is stored inline with its revision: a resource is at most 1 MiB, which
        # the column holds, so a self-hosted installation needs no object storage.
        object_key = None
        saved_revision = None
        with self.db.transaction(identity) as connection:
            scope = self._scope(connection, identity, space_id, write=True)
            connection.execute("SELECT id FROM space.spaces FOR UPDATE")
            if quota_deletion:
                connection.execute("SELECT set_config('space.quota_deletion','on',true)")
            previous = self._previous(connection, identity, scope, request.command_id, fingerprint)
            if previous:
                resource_id, revision = previous["resource_id"], previous["revision"]
            else:
                if (
                    project
                    and not connection.execute(
                        "SELECT id FROM space.workspace_items WHERE id=%s AND kind='project'",
                        (project,),
                    ).fetchone()
                ):
                    raise CommandError("Project not found in this Space", 404)
                if folder and folder != previous_folder:
                    if not connection.execute(
                        "SELECT id FROM space.workspace_items "
                        "WHERE id=%s AND kind='folder' AND NOT deleted",
                        (folder,),
                    ).fetchone():
                        raise CommandError("Choose an active folder in this Space", 409)
                if object_key:
                    intent = connection.execute(
                        "DELETE FROM space.restore_objects WHERE object_key=%s "
                        "AND created_at>now()-interval '10 minutes' RETURNING object_key",
                        (object_key,),
                    ).fetchone()
                    if intent is None:
                        raise CommandError("Upload expired. Save again with a fresh command.", 409)
                content_changes.changed(
                    connection, content_changes.resource_kinds(request.kind, memory_kind)
                )
                if snapshot is not None:
                    self._check_manifest(connection, snapshot)
                for ref in recall["sources"]:
                    # Existing citations survive source deletion; new citations must resolve.
                    if (ref["resource_id"], ref["revision"]) in previous_sources:
                        continue
                    if (
                        UUID(ref["resource_id"]) == resource_id
                        or not connection.execute(
                            "SELECT 1 FROM space.resource_revisions "
                            "WHERE resource_id=%s AND revision=%s",
                            (ref["resource_id"], ref["revision"]),
                        ).fetchone()
                    ):
                        raise CommandError("Source revision not found in this Space", 404)
                if request.resource_id:
                    current = connection.execute(
                        "SELECT kind,current_revision FROM space.resources WHERE id=%s",
                        (resource_id,),
                    ).fetchone()
                    if not current:
                        raise CommandError("Resource not found", 404)
                    if current["current_revision"] != request.expected_revision:
                        raise CommandError(
                            "This resource changed. Reload its latest revision before saving.", 409
                        )
                    if current["kind"] != ("record" if request.kind == "task" else "document"):
                        raise CommandError("Resource kind cannot change", 409)
                    connection.execute(
                        "UPDATE space.resources SET current_revision=%s,memory_kind=%s WHERE id=%s",
                        (revision, memory_kind, resource_id),
                    )
                else:
                    collection = connection.execute(
                        "SELECT id FROM space.collections WHERE system_role='tasks'"
                    ).fetchone()
                    connection.execute(
                        "INSERT INTO space.resources(organization_id,space_id,id,"
                        "kind,collection_id,current_revision,memory_kind) VALUES(%s,%s,%s,%s,%s,"
                        "%s,%s)",
                        (
                            scope["organization_id"],
                            space_id,
                            resource_id,
                            "record" if request.kind == "task" else "document",
                            collection["id"] if request.kind == "task" else None,
                            revision,
                            memory_kind,
                        ),
                    )
                saved_revision = connection.execute(
                    "INSERT INTO space.resource_revisions(organization_id,"
                    "space_id,resource_id,revision,key_id,encrypted_metadata,"
                    "encrypted_record,object_key,author_principal_id,"
                    "author_client_id,content_bytes) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                    "RETURNING resource_id,revision,created_at,author_principal_id,"
                    "author_client_id",
                    (
                        scope["organization_id"],
                        space_id,
                        resource_id,
                        revision,
                        key.key.key_id,
                        metadata,
                        encrypted,
                        object_key,
                        scope["principal_id"],
                        identity.client_id,
                        len(encrypted),
                    ),
                )
                # Derived from the same plaintext as the revision, in the same transaction,
                # so the searchable index cannot drift from the resource. Only the current
                # revision is kept: an update replaces that resource's rows.
                connection.execute(
                    "DELETE FROM space.resource_terms WHERE organization_id=%s "
                    "AND space_id=%s AND resource_id=%s",
                    (scope["organization_id"], space_id, resource_id),
                )
                for field, field_terms, counts, total in index_rows:
                    connection.execute(
                        "INSERT INTO space.resource_terms(organization_id,space_id,"
                        "resource_id,revision,field,terms,occurrences,tokens) "
                        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
                        (
                            scope["organization_id"],
                            space_id,
                            resource_id,
                            revision,
                            field,
                            field_terms,
                            counts,
                            total,
                        ),
                    )
                connection.execute(
                    "INSERT INTO space.commands(organization_id,space_id,id,"
                    "principal_id,client_id,fingerprint,resource_id,revision) "
                    "VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        scope["organization_id"],
                        space_id,
                        request.command_id,
                        scope["principal_id"],
                        identity.client_id,
                        fingerprint,
                        resource_id,
                        revision,
                    ),
                )
                connection.execute(
                    "INSERT INTO space.events(organization_id,space_id,id,"
                    "command_id,event_type) VALUES(%s,%s,%s,%s,%s)",
                    (
                        scope["organization_id"],
                        space_id,
                        uuid4(),
                        request.command_id,
                        "resource.updated" if request.resource_id else "resource.created",
                    ),
                )
                overview.record_write(
                    connection, scope, identity, resource_id, request, memory_kind, index
                )
                if index:
                    # The vector index is written in the same transaction as the
                    # revision it describes, so the two can never disagree.
                    source = index.get("source")
                    try:
                        brain.apply(
                            connection,
                            memory_id=resource_id,
                            revision=revision,
                            claims=index["claims"],
                            model_name=index["model"],
                            vectors=index["vectors"],
                            source_resource_id=source[0] if source else None,
                            source_revision=source[1] if source else None,
                        )
                    except brain.IndexConflict as error:
                        raise CommandError(str(error), 409) from None
                    except brain.DimensionError as error:
                        raise CommandError(str(error), 400) from None
                    memory_link_index.replace(
                        connection,
                        resource_id,
                        revision,
                        memory["claims"],
                        links=memory.get("links", []),
                    )
                    memory_routing.replace(connection, resource_id, revision, memory["claims"])
            if saved_revision is not None:
                saved_row = saved_revision.fetchone()
        if saved_revision is None:
            # A concurrent identical command won. Return its exact committed revision.
            return self.get_resource(identity, space_id, resource_id, revision)
        # We already have the committed content. Recheck live access after commit,
        # but do not re-download the object or unwrap its key to reconstruct it.
        self._recheck(identity, space_id)
        result = {
            **self._summary(
                saved_row | {"kind": "record" if request.kind == "task" else "document"},
                content,
                scope["principal_id"],
            ),
            "body": content["body"],
        }
        if memory is not None:
            result["memory"] = memory_pipeline.public_memory(memory)
        return result

    def save_handoff(self, identity, space_id: UUID, request: SaveHandoff):
        return self.save(
            identity,
            space_id,
            SaveResource(
                command_id=request.command_id,
                resource_id=request.resource_id,
                expected_revision=request.expected_revision,
                kind="document",
                title=request.title,
                body=request.body(),
                recall=RecallMetadata(
                    category="handoff", topics=request.topics, sources=request.sources
                ),
            ),
        )

    def source_url(self, space_id, resource_id, revision):
        return f"{self.web_url}/resources/{resource_id}?space_id={space_id}&revision={revision}"

    @staticmethod
    def _manifest_digest(connection):
        """Fingerprint every resource of the Space without decrypting any of it.

        Parameters: a connection already scoped to one Space.
        What it does: aggregates each resource id with its current revision, so a change
            during model work is detected from readable columns alone, at any Space size.
        Output: a digest string, comparable across transactions.
        """
        resources = connection.execute(
            "SELECT count(*) AS resources, coalesce(md5(string_agg("
            "id::text || ':' || current_revision::text, ',' ORDER BY id)), '') AS fingerprint "
            "FROM space.resources"
        )
        workspace_items = connection.execute(
            "SELECT coalesce(md5(string_agg(id::text || ':' || current_revision::text, "
            "',' ORDER BY id)), '') AS fingerprint FROM space.workspace_items"
        )
        row, items = resources.fetchone(), workspace_items.fetchone()
        return f"{row['resources']}:{row['fingerprint']}:{items['fingerprint']}"

    @staticmethod
    def _check_manifest(connection, snapshot):
        """Recheck the inputs captured for this operation before disclosure or commit."""
        current = (
            snapshot.matches(connection)
            if isinstance(snapshot, IncomingMemorySnapshot)
            else Workspace._manifest_digest(connection) == snapshot
        )
        if not current:
            raise SnapshotChanged(
                "Space changed during memory processing. Retry for current context.", 409
            )

    def _authorize_model(self, identity, space_id, snapshot, *, write=False, charge=False):
        with read_stage("recall_auth"), self.db.transaction(identity) as connection:
            self._scope(connection, identity, space_id, write=write)
            self._check_manifest(connection, snapshot)
            if charge:
                connection.execute("SELECT space.consume_model_request()").fetchone()

    @key_operation
    def memory_links(self, identity, space_id, resource_id, *, limit=50):
        """Parameters: reader, focal node and bounded per-direction result limit.

        What it does: filters encrypted visibility before the SQL limit, so hidden
        neighbors cannot starve visible backlinks. Explicit and inferred links keep
        different content validity rules; neither requires a model call.
        Output: outgoing links, reverse backlinks and visible-result overflow flag.
        """
        if type(limit) is not int or not 1 <= limit <= 50:
            raise CommandError("Link limit must be between 1 and 50")
        with self.db.transaction(identity) as connection:
            self._scope(connection, identity, space_id)
            snapshot = self._manifest_digest(connection)
            if not connection.execute(
                "SELECT 1 FROM space.resources WHERE id=%s AND memory_kind='memory'",
                (resource_id,),
            ).fetchone():
                raise CommandError("Memory not found", 404)
            ids = memory_link_index.neighbor_ids(connection, resource_id)
        items, _, _ = self._items_for(identity, space_id, ids, memory=True)
        source_ids = {ref["resource_id"] for item in items for ref in item["recall"]["sources"]}
        current = self._current_revisions(identity, space_id, source_ids)
        visible = {item["id"] for item in items}
        fresh = {
            item["id"] for item in items if not self._stale(item["recall"]["sources"], current)
        }
        with self.db.transaction(identity) as connection:
            self._scope(connection, identity, space_id)
            self._check_manifest(connection, snapshot)
            return memory_link_index.read(
                connection, resource_id, limit, visible_ids=visible, fresh_ids=fresh
            )

    def memory_info(self, identity, space_id):
        with self.db.transaction(identity) as connection:
            self._scope(connection, identity, space_id)
            return self._memory_info(connection)

    def _memory_info(self, connection):
        """Read provider/index metadata using an already authorized transaction."""
        info = dict(self.memory_models.info())
        info["embedding_dim"] = brain.EMBEDDING_DIM
        info["index"] = brain.stats(connection, self.memory_models.embedding_model or "none") | {
            "engine": brain.ENGINE
        }
        return info

    def extract_memory(self, identity, space_id, request: RememberMemory | BuildMemory):
        from space.jobs import enqueue

        return enqueue(self, identity, space_id, request)

    def memory_job(self, identity, space_id, job_id):
        from space.jobs import get

        return get(self, identity, space_id, job_id)

    def memory_jobs(self, identity, space_id):
        from space.jobs import recent

        return recent(self, identity, space_id)

    def _digest(self, identity, space_id):
        """Read the Space's manifest digest, for the change check around a model call.

        Parameters: the caller identity and the Space.
        What it does: reads identifiers and revisions only, so a path that needs no memory
            set can still detect a concurrent change without decrypting anything.
        Output: the digest string.
        """
        with self.db.transaction(identity) as connection:
            self._scope(connection, identity, space_id)
            return self._manifest_digest(connection)

    def _incoming_memory_snapshot(self, identity, space_id, source):
        # Capture source and catalog dependencies first. Relationship discovery adds
        # only the memory/source revisions it actually reads before disclosure.
        # Live access and those dependencies are checked again at commit.
        with self.db.transaction(identity) as connection:
            self._scope(connection, identity, space_id, write=True)
            snapshot = IncomingMemorySnapshot.capture(
                connection, source, classify=memory_classification.enabled(self)
            )
            self._check_manifest(connection, snapshot)
            return snapshot

    def _recall_state(self, identity, space_id):
        """Read the snapshot and memory existence without decrypting the memory corpus."""
        with self.db.transaction(identity) as connection:
            self._scope(connection, identity, space_id)
            snapshot = self._manifest_digest(connection)
            indexed = (
                self.memory_models.enabled
                and connection.execute(
                    "SELECT EXISTS(SELECT 1 FROM space.resources WHERE memory_kind='memory') AS n"
                ).fetchone()["n"]
            )
        return snapshot, indexed

    def _unwrap_keys(self, scope, key_rows):
        """Unwrap previously selected keys outside the database transaction."""
        with read_stage("space_key"):
            return {
                id_: self._unwrap_key(scope["organization_id"], scope["space_id"], row)
                for id_, row in key_rows.items()
            }

    def _summaries(self, rows, keys, scope, *, include_excluded):
        """Decrypt the metadata of the given rows and shape them as summaries.

        Parameters: raw rows, unwrapped keys, the scope, and whether to keep excluded ones.
        What it does: decrypts metadata only. This is the per-item cost recall pays for the
            items it chose to read.
        Output: summaries carrying the storage location for a later body read.
        """
        items = []
        for row in rows:
            metadata = self._unpack(
                keys[row["key_id"]],
                scope,
                row["resource_id"],
                row["revision"],
                "metadata",
                row["encrypted_metadata"],
            )
            summary = self._summary(row, metadata, scope["principal_id"])
            if summary["recall"]["excluded"] and not include_excluded:
                continue
            items.append(summary | {"key_id": row["key_id"], "object_key": row["object_key"]})
        return items

    def _memory_items(self, identity, space_id, *, include_excluded=False, kind=None):
        """Read the Space's memory resources without touching the document corpus.

        Parameters: the caller identity, the Space and whether to keep excluded memories.
        What it does: selects resources by the readable memory marker and decrypts their
            metadata. Every memory in the Space is read, which keeps the check "does a
            memory already cite this source" exact. Documents are never read here.
        Output: (memory summaries, unwrapped keys, scope, manifest digest).
        """
        with self.db.transaction(identity) as connection:
            scope = self._scope(connection, identity, space_id)
            digest = self._manifest_digest(connection)
            rows = connection.execute(
                "SELECT r.id AS resource_id,r.kind,r.memory_kind,v.revision,v.key_id,"
                "v.encrypted_metadata,v.object_key,v.created_at,"
                "v.author_client_id,v.author_principal_id "
                "FROM space.resources r JOIN space.resource_revisions v "
                "ON (r.organization_id,r.space_id,r.id,r.current_revision)="
                "(v.organization_id,v.space_id,v.resource_id,v.revision) "
                "WHERE r.space_id=%s AND r.memory_kind IS NOT NULL "
                "AND (%s::text IS NULL OR r.memory_kind=%s) ORDER BY r.created_at, r.id",
                (space_id, kind, kind),
            ).fetchall()
            key_rows = {
                id_: self._key_row(connection, scope, id_) for id_ in {r["key_id"] for r in rows}
            }
        keys = self._unwrap_keys(scope, key_rows)
        return (
            self._summaries(rows, keys, scope, include_excluded=include_excluded),
            keys,
            scope,
            digest,
        )

    def _items_for(self, identity, space_id, resource_ids, *, memory=False):
        """Read the named source resources only.

        Parameters: the caller identity, the Space and the resource ids to read.
        What it does: reads the current revision of each named resource and decrypts its
            metadata. The caller decides which resources are worth reading, which is what
            replaces decrypting the whole corpus on every recall.
        Output: (summaries, unwrapped keys, scope); empty when nothing was named.
        """
        if not resource_ids:
            return [], {}, None
        with self.db.transaction(identity) as connection:
            scope = self._scope(connection, identity, space_id)
            rows, key_rows = self._item_rows(
                connection, scope, space_id, resource_ids, memory=memory
            )
        keys = self._unwrap_keys(scope, key_rows)
        # A resource the user excluded from recall is not a candidate, and never returned.
        items = self._summaries(rows, keys, scope, include_excluded=False)
        return items, keys, scope

    def _item_rows(self, connection, scope, space_id, resource_ids, *, memory=False):
        """Parameters: authorized transaction, scope and selected resource IDs.
        What it does: reads current encrypted metadata and key envelopes in caller order.
        Output: rows and key rows; no key work or payload decoding inside the transaction.
        """
        rows = connection.execute(
            "SELECT r.id AS resource_id,r.kind,r.memory_kind,v.revision,v.key_id,"
            "v.encrypted_metadata,v.object_key,v.created_at,"
            "v.author_client_id,v.author_principal_id "
            "FROM space.resources r JOIN space.resource_revisions v "
            "ON (r.organization_id,r.space_id,r.id,r.current_revision)="
            "(v.organization_id,v.space_id,v.resource_id,v.revision) "
            "WHERE r.space_id=%s AND r.id = ANY(%s) "
            "AND (r.memory_kind IS NOT DISTINCT FROM 'memory')=%s "
            "ORDER BY array_position(%s::uuid[],r.id)",
            (space_id, list(resource_ids), memory, list(resource_ids)),
        ).fetchall()
        key_rows = {
            id_: self._key_row(connection, scope, id_) for id_ in {r["key_id"] for r in rows}
        }
        return rows, key_rows

    def _candidates(self, identity, space_id, query_terms, limit):
        """Select candidate sources in SQL from the term index.

        Parameters: the caller identity, the Space, query fingerprints and the bound.
        What it does: matches the fingerprint arrays of each resource's current revision and
            scores what it finds by how rare each matched query word is. A word in one
            resource is worth 1 and a word in every resource is worth almost nothing, so a
            query that mixes a distinctive word with common ones cannot be filled by
            resources that hold only the common ones. Nothing is decrypted.
        Output: [(resource_id, revision, weight)] best first, at most `limit` entries.
        """
        if not query_terms:
            return []
        with self.db.transaction(identity) as connection:
            self._scope(connection, identity, space_id)
            rows = connection.execute(
                CANDIDATES_SQL,
                (space_id, query_terms, query_terms, limit),
            ).fetchall()
        return [
            (UUID(str(row["resource_id"])), row["revision"], float(row["weight"])) for row in rows
        ]

    def _source_items(self, identity, space_id, query_terms, limit):
        """Parameters: caller, Space, keyed query terms and existing source bound.
        What it does: selects source IDs and encrypted metadata in one authorized transaction.
        Output: visible summaries, unwrapped keys and scope; decryption occurs outside SQL.
        """
        if not query_terms:
            return [], {}, None
        with self.db.transaction(identity) as connection:
            scope = self._scope(connection, identity, space_id)
            candidates = connection.execute(
                CANDIDATES_SQL, (space_id, query_terms, query_terms, limit)
            ).fetchall()
            rows, key_rows = (
                self._item_rows(
                    connection, scope, space_id, [row["resource_id"] for row in candidates]
                )
                if candidates
                else ([], {})
            )
        keys = self._unwrap_keys(scope, key_rows)
        return self._summaries(rows, keys, scope, include_excluded=False), keys, scope

    def _current_revisions(self, identity, space_id, resource_ids):
        """Read the current revision of the named resources, for staleness checks.

        Parameters: the caller identity, the Space and the resource ids to check.
        What it does: reads identifiers and revisions only, so citing a source never
            requires decrypting anything.
        Output: {resource_id: current_revision}.
        """
        if not resource_ids:
            return {}
        with self.db.transaction(identity) as connection:
            self._scope(connection, identity, space_id)
            rows = connection.execute(
                "SELECT id, current_revision FROM space.resources WHERE id = ANY(%s)",
                (list(resource_ids),),
            ).fetchall()
        return {str(row["id"]): row["current_revision"] for row in rows}

    def _rank_sources(self, identity, space_id, sources, keys, scope, request):
        """Rank candidate sources over metadata and bodies without dropping either match.

        Parameters: candidates with metadata decrypted, keys, scope and the recall request.
        What it does: ranks metadata with BM25, ranks the bodies of the leading candidates,
            then adds the two scores. A title or topic match is returned even when the body
            does not match, and a body match even when the metadata does not.
        Output: [(item, body)] best first, at most request.limit entries.
        """
        metadata_ranked = rank_lexical(
            [(item, item["title"] + " " + " ".join(item["recall"]["topics"])) for item in sources],
            request.query,
        )
        metadata_scores = {item["id"]: score for item, _, score in metadata_ranked}
        hydrate = sources[:MAX_LEXICAL_BODIES]
        bodies = {
            item_id: content["body"]
            for item_id, content in self._bodies(identity, space_id, hydrate, keys, scope).items()
        }
        body_ranked = rank_lexical([(item, bodies[item["id"]]) for item in hydrate], request.query)
        body_scores = {item["id"]: score for item, _, score in body_ranked}
        by_id = {item["id"]: item for item in sources}
        combined = sorted(
            (
                (item_id, metadata_scores.get(item_id, 0.0) + body_scores.get(item_id, 0.0))
                for item_id in set(metadata_scores) | set(body_scores)
            ),
            key=lambda pair: (-pair[1], pair[0]),
        )
        missing = [by_id[id_] for id_, _ in combined[: request.limit] if id_ not in bodies]
        bodies.update(
            {
                item_id: content["body"]
                for item_id, content in self._bodies(
                    identity, space_id, missing, keys, scope
                ).items()
            }
        )
        ranked = []
        for item_id, _score in combined[: request.limit]:
            item = by_id[item_id]
            ranked.append((item, bodies[item_id]))
        return ranked

    def _bodies(self, identity, space_id, items, keys, scope, *, cached_bodies=None):
        """Parameters: authorized revisions, keys, scope and optional recall-local body map.
        What it does: loads each immutable revision once and bounds remote reads to four.
        Output: decrypted bodies; cached values never outlive their owning recall call.
        """
        if not items:
            return {}
        cache = {} if cached_bodies is None else cached_bodies
        references = {
            item["id"]: (
                str(scope["organization_id"]),
                str(space_id),
                item["id"],
                item["revision"],
                item["kind"],
                item["key_id"],
                item["object_key"],
            )
            for item in items
        }
        missing = [item for item in items if references[item["id"]] not in cache]
        inline = [item for item in missing if not item["object_key"]]
        payloads = {}
        if inline:
            with self.db.transaction(identity) as connection:
                self._scope(connection, identity, space_id)
                rows = connection.execute(
                    "SELECT resource_id,encrypted_record FROM space.resource_revisions "
                    "WHERE (resource_id,revision) IN "
                    "(SELECT * FROM unnest(%s::uuid[],%s::integer[]))",
                    ([item["id"] for item in inline], [item["revision"] for item in inline]),
                ).fetchall()
                payloads = {str(row["resource_id"]): row["encrypted_record"] for row in rows}
        pending = {
            item["id"]: self._object_reads.submit(
                copy_context().run, self._read_resource_object, item["object_key"], perf_counter()
            )
            for item in missing
            if item["object_key"]
        }
        try:
            loaded = {
                references[item["id"]]: self._body(
                    identity,
                    space_id,
                    item | {"_payload": pending[item["id"]].result()}
                    if item["id"] in pending
                    else item | {"_payload": payloads[item["id"]]},
                    keys,
                    scope,
                )
                for item in missing
            }
            cache.update(loaded)
            return {item["id"]: deepcopy(cache[references[item["id"]]]) for item in items}
        finally:
            # A failed read must not leave queued requests running after admission is released.
            for future in pending.values():
                future.cancel()
            for future in pending.values():
                if not future.cancelled():
                    future.exception()

    def _body(self, identity, space_id, item, keys, scope):
        """Decrypt one source body. Called only for selected items."""
        payload = item.get("_payload")
        if payload is None:
            payload = (
                self.objects.get(item["object_key"])
                if item["object_key"]
                else self._record_payload(identity, space_id, item)
            )
        with read_stage("decode"):
            return self._unpack(
                keys[item["key_id"]],
                scope,
                item["id"],
                item["revision"],
                "source" if item["kind"] == "document" else "record",
                payload,
            )

    def _record_payload(self, identity, space_id, item):
        with self.db.transaction(identity) as connection:
            self._scope(connection, identity, space_id)
            return connection.execute(
                "SELECT encrypted_record FROM space.resource_revisions "
                "WHERE resource_id=%s AND revision=%s",
                (item["id"], item["revision"]),
            ).fetchone()["encrypted_record"]

    @staticmethod
    def _stale(sources, current):
        """Whether any cited source revision moved on since this item was written."""
        return any(current.get(ref["resource_id"]) != ref["revision"] for ref in sources)

    @staticmethod
    def _item(resource, reason, *, body="", entry=None):
        item = {
            "id": resource["id"],
            "revision": resource["revision"],
            "kind": resource["kind"],
            "title": resource["title"],
            "status": resource["status"],
            "updated_at": resource["updated_at"],
            "author": resource["author"],
            "author_agent": resource.get("author_agent"),
            "category": resource["recall"]["category"],
            "sources": resource["recall"]["sources"],
            "reason": reason,
            "body": body,
            "stale": False,
            "evidence_context": {
                "resource_title": resource.get("resource_title", resource["title"])
            },
            "_context_project": resource.get("project"),
        }
        if entry is not None:
            if entry.get("score") is not None:
                item["score"] = entry["score"]
            for field in ("candidate_origin", "linked_from"):
                if entry.get(field):
                    item[field] = entry[field]
            item |= {
                "entry_id": entry["entry_id"],
                "title": entry["title"],
                "body": entry["body"],
                "author": "agent",
                "verification": "memory",
                "memory_type": entry["memory_type"],
                "edited": entry["edited"],
                "importance": entry.get("importance", 0),
                "classification": entry.get("classification", memory_classification.empty()),
                "creator": entry.get("creator"),
            }
        else:
            item["verification"] = "handoff" if item["category"] == "handoff" else "user_authored"
        return item

    def list_memories(
        self,
        identity,
        space_id,
        *,
        query: str = "",
        after: UUID | None = None,
        limit: int = MAX_MEMORY_LIST,
    ):
        """List one page of generated memory for the brain browser.

        Parameters: the caller identity, the Space, an optional query, a cursor resource id
            and a page size.
        What it does: selects the page in SQL by the readable marker, so one request
            decrypts one page rather than the whole Space. A query matches the keyed term
            fingerprints of every memory in the Space, which is what keeps a browser search
            from being limited to the page the caller already holds, and ranks by how rare
            each matched word is. Excluded memory is listed and flagged rather than hidden.
        Output: {items, next_cursor, provider}. Paging uses newest first; a ranked query
            returns its best matches and no cursor.
        """
        limit = max(1, min(int(limit), MAX_MEMORY_LIST))
        terms: list[bytes] = []
        scoped = None
        if tokens(query):
            _, scoped = self._key(identity, space_id)
            terms = query_fingerprints(query, term_key(scoped.key.material))
        with self.db.transaction(identity) as connection:
            scope = self._scope(connection, identity, space_id)
            snapshot = self._manifest_digest(connection)
            if terms:
                rows = connection.execute(
                    MEMORY_SEARCH_SQL, (space_id, terms, terms, space_id, limit)
                ).fetchall()
            else:
                rows = connection.execute(
                    MEMORY_PAGE_SQL, (space_id, after, after, limit + 1)
                ).fetchall()
            page = rows[:limit]
            key_rows = {
                key_id: self._key_row(connection, scope, key_id)
                for key_id in {row["key_id"] for row in page}
                if scoped is None or str(key_id) != scoped.key.key_id
            }
        # Provider I/O stays outside the transaction; each key is unwrapped once per call.
        keys = {
            key_id: self._unwrap_key(scope["organization_id"], scope["space_id"], key_row)
            for key_id, key_row in key_rows.items()
        }
        if scoped is not None:
            keys[UUID(scoped.key.key_id)] = scoped
        payloads = {str(row["resource_id"]): row["encrypted_record"] for row in page}
        items = self._summaries(page, keys, scope, include_excluded=True)
        current = self._current_revisions(
            identity,
            space_id,
            {ref["resource_id"] for item in items for ref in item["recall"]["sources"]},
        )
        # Older memories live in object storage. Use the shared bounded reader instead
        # of paying one sequential network request for each memory in the page.
        remote = self._bodies(
            identity,
            space_id,
            [item for item in items if payloads[item["id"]] is None],
            keys,
            scope,
        )
        result = []
        for item in items:
            content = (
                self._unpack(
                    keys[item["key_id"]],
                    scope,
                    item["id"],
                    item["revision"],
                    "source",
                    payloads[item["id"]],
                )
                if payloads[item["id"]] is not None
                else remote[item["id"]]
            )
            result.append(
                {
                    **{k: v for k, v in item.items() if k not in {"key_id", "object_key"}},
                    "stale": self._stale(item["recall"]["sources"], current),
                    "import_origin": (content["memory"].get("provenance") or {}).get("format"),
                    "claims": [
                        {
                            "id": claim["id"],
                            "index": str(claim["index"]),
                            "value": claim["value"],
                            "classification": claim.get(
                                "classification", memory_classification.empty()
                            ),
                            "memory_type": claim.get("memory_type", "factual"),
                            "edited": bool(claim.get("edited")),
                            "importance": int(claim.get("importance", 0)),
                            "created_at": claim.get("created_at"),
                            "creator": claim.get("creator"),
                            "source": item["recall"]["sources"][0]["resource_id"]
                            if item["recall"]["sources"]
                            else "agent",
                        }
                        for claim in content["memory"]["claims"]
                    ],
                }
            )
        # A ranked search returns its best matches; only newest-first paging has a cursor.
        next_cursor = None
        if not terms and len(rows) > limit and page:
            next_cursor = str(page[-1]["resource_id"])
        with self.db.transaction(identity) as connection:
            self._scope(connection, identity, space_id)
            self._check_manifest(connection, snapshot)
            provider = self._memory_info(connection)
        return {
            "items": result,
            "next_cursor": next_cursor,
            "provider": provider,
        }

    def _index_vectors(self, identity, space_id, memory_id, claims, session, *, new=False):
        """Embed only the claims whose text is new, edited or re-modelled.

        Embedding runs outside every transaction: the runtime login closes an idle
        transaction after 30 seconds and a model call can take longer.
        """
        if new:
            needed = claims
        else:
            with self.db.transaction(identity) as connection:
                self._scope(connection, identity, space_id, write=True)
                needed = brain.pending(
                    connection,
                    memory_id=memory_id,
                    claims=claims,
                    model_name=self.memory_models.embedding_model,
                )
        if not needed:
            return {}
        vectors = session.embed([item["text"] for item in needed])
        return {item["id"]: vector for item, vector in zip(needed, vectors, strict=True)}

    @admitted("_write_admission")
    @key_operation
    @reclassify_jobs.after_memory_write
    def build_memory(self, identity, space_id, request: BuildMemory):
        """Extract memory from one current source revision and index it immediately."""
        fingerprint, previous = self._memory_retry(identity, space_id, "build", request)
        if previous:
            return previous
        try:
            memories, keys, scope, snapshot = self._memory_items(
                identity, space_id, include_excluded=True
            )
            try:
                source = self.get_resource(identity, space_id, request.source.resource_id)
            except CommandError:
                raise CommandError(
                    "Build requires a current source revision in this Space", 409
                ) from None
            if source["revision"] != request.source.revision:
                raise CommandError("Build requires a current source revision in this Space", 409)
            if source["recall"]["category"] == "memory":
                raise CommandError("Build memory from the source, not from memory itself", 409)
            # Source references outlive index rows: forgetting the last claim must
            # not make this memory disappear or permit a fresh build to restore it.
            matching = [
                item
                for item in memories
                if item["recall"]["category"] == "memory"
                and any(ref["resource_id"] == source["id"] for ref in item["recall"]["sources"])
            ]
            existing = next(
                (
                    item
                    for item in matching
                    if not request.resource_id or item["id"] == str(request.resource_id)
                ),
                None,
            )
            if request.resource_id:
                if (
                    not existing
                    or existing["id"] != str(request.resource_id)
                    or existing["revision"] != request.expected_revision
                ):
                    raise CommandError(
                        "Rebuild requires the current memory resource and revision", 409
                    )
            elif existing:
                raise CommandError(
                    "Memory already exists for this source; rebuild with its resource ID "
                    "and current revision",
                    409,
                )
            resource_id = request.resource_id or uuid4()
            model = self._model_session(
                identity, space_id, lambda: self._authorize_model(identity, space_id, snapshot)
            )
            body = source["body"]
            built = memory_pipeline.build(
                source | {"body": body, "updated_at": source["updated_at"]},
                resource_id,
                model,
                self.memory_models.embedding_fingerprint,
            )
            record = built["value"]
            if existing:
                memory_pipeline.retain_edits(
                    record, self._body(identity, space_id, existing, keys, scope)["memory"]
                )
            # Index exactly the claims that will be stored, so a retained human edit
            # keeps the embedding of its own text instead of the generated text.
            rows = memory_pipeline.index_rows(record)
            vectors = self._index_vectors(identity, space_id, resource_id, rows, model)
            # Organization follows embedding: large catalogs are shortlisted by vector.
            memory_classification.apply(self, identity, space_id, record["claims"], model, vectors)
            memory_relationships.apply(
                self, identity, space_id, resource_id, record["claims"], vectors, model, snapshot
            )
            return self._save(
                identity,
                space_id,
                SaveResource(
                    command_id=request.command_id,
                    resource_id=request.resource_id,
                    expected_revision=request.expected_revision,
                    kind="document",
                    title=("Memory: " + source["title"])[:300],
                    body=memory_pipeline.memory_body(record),
                    recall=RecallMetadata(
                        category="memory",
                        sources=[request.source],
                        topics=source["recall"]["topics"],
                    ),
                ),
                memory=record,
                index={
                    "claims": rows,
                    "vectors": vectors,
                    "model": self.memory_models.embedding_model,
                    "source": (source["id"], source["revision"]),
                },
                fingerprint=fingerprint,
                snapshot=snapshot,
                new_id=resource_id,
            )
        except ModelError as error:
            raise CommandError(str(error), 503) from None

    @admitted("_write_admission")
    @key_operation
    @reclassify_jobs.after_memory_write
    def remember(self, identity, space_id, request: RememberMemory, *, extract=False):
        """Organize and store authored text; only the extraction worker opts into generation."""
        fingerprint, previous = self._memory_retry(identity, space_id, "remember", request)
        if previous:
            return previous
        try:
            snapshot = self._incoming_memory_snapshot(identity, space_id, request.source)
            resource_id = uuid4()
            explicit_links = memory_node_links.validate(
                self, identity, space_id, resource_id, request.links, snapshot
            )
            routes = memory_routing.validate(self, identity, space_id, request.routes_to)
            model = self._model_session(
                identity,
                space_id,
                lambda: self._authorize_model(identity, space_id, snapshot, write=True),
                incoming_snapshot=snapshot,
            )
            if extract:
                built = memory_pipeline.build(
                    {
                        "title": request.title,
                        "body": request.text,
                        "updated_at": datetime.now(UTC).isoformat(),
                        "recall": {
                            "category": "handoff" if request.memory_type == "chat" else "source"
                        },
                    },
                    resource_id,
                    model,
                    self.memory_models.embedding_fingerprint,
                    request.importance,
                )
                record = built["value"]
            else:
                record = memory_pipeline.authored(
                    request,
                    resource_id,
                    self.memory_models.embedding_fingerprint,
                    datetime.now(UTC).isoformat(),
                )
            record["links"] = explicit_links
            if routes:
                for claim in record["claims"]:
                    claim["routing"] = memory_routing.explicit(routes)
            rows = memory_pipeline.index_rows(record)
            vectors = self._index_vectors(identity, space_id, resource_id, rows, model, new=True)
            memory_classification.apply(self, identity, space_id, record["claims"], model, vectors)
            memory_relationships.apply(
                self, identity, space_id, resource_id, record["claims"], vectors, model, snapshot
            )
            return self._save(
                identity,
                space_id,
                SaveResource(
                    command_id=request.command_id,
                    expected_revision=0,
                    kind="document",
                    title=("Memory: " + request.title)[:300],
                    body=memory_pipeline.memory_body(record),
                    recall=RecallMetadata(
                        category="memory",
                        topics=request.topics,
                        sources=[request.source] if request.source else [],
                    ),
                ),
                memory=record,
                index={
                    "claims": rows,
                    "vectors": vectors,
                    "model": self.memory_models.embedding_model,
                    "source": (
                        (str(request.source.resource_id), request.source.revision)
                        if request.source
                        else None
                    ),
                },
                fingerprint=fingerprint,
                snapshot=snapshot,
                new_id=resource_id,
            )
        except ModelError as error:
            raise CommandError(str(error), 503) from None

    @admitted("_write_admission")
    @key_operation
    @reclassify_jobs.after_memory_write
    def remember_claims(self, identity, space_id, request: RememberClaims):
        """Persist finished claims verbatim, embedding and optionally organizing them."""
        if request.provenance:
            from space.memora_receipts import resolve_request

            request = resolve_request(self, identity, space_id, request)
        fingerprint, previous = self._memory_retry(identity, space_id, "remember_claims", request)
        if previous:
            return previous
        snapshot = self._incoming_memory_snapshot(identity, space_id, request.source)
        resource_id = uuid4()
        explicit_links = memory_node_links.validate(
            self, identity, space_id, resource_id, request.links, snapshot
        )
        memory_routing.validate(
            self,
            identity,
            space_id,
            list(dict.fromkeys(t for c in request.claims for t in c.routes_to)),
            limit=memory_routing.MAX_TARGETS * len(request.claims),
        )
        record = memory_pipeline.prepared(
            request,
            resource_id,
            self.memory_models.embedding_fingerprint,
            datetime.now(UTC).isoformat(),
        )
        record["links"] = explicit_links
        for claim, prepared in zip(record["claims"], request.claims, strict=True):
            if prepared.routes_to:
                claim["routing"] = memory_routing.explicit(prepared.routes_to)
        rows = memory_pipeline.index_rows(record)
        try:
            model = self._model_session(
                identity,
                space_id,
                lambda: self._authorize_model(identity, space_id, snapshot, write=True),
                incoming_snapshot=snapshot,
            )
            vectors = self._index_vectors(identity, space_id, resource_id, rows, model, new=True)
            memory_classification.apply(self, identity, space_id, record["claims"], model, vectors)
            memory_relationships.apply(
                self, identity, space_id, resource_id, record["claims"], vectors, model, snapshot
            )
            return self._save(
                identity,
                space_id,
                SaveResource(
                    command_id=request.command_id,
                    expected_revision=0,
                    kind="document",
                    title=("Memory: " + request.title)[:300],
                    body=memory_pipeline.memory_body(record),
                    recall=RecallMetadata(
                        category="memory",
                        topics=request.topics,
                        sources=[request.source] if request.source else [],
                    ),
                ),
                memory=record,
                index={
                    "claims": rows,
                    "vectors": vectors,
                    "model": self.memory_models.embedding_model,
                    "source": (str(request.source.resource_id), request.source.revision)
                    if request.source
                    else None,
                },
                fingerprint=fingerprint,
                snapshot=snapshot,
                new_id=resource_id,
            )
        except ModelError as error:
            raise CommandError(str(error), 503) from None

    @admitted("_write_admission")
    @key_operation
    def set_memory_links(self, identity, space_id, request: SetMemoryLinks):
        """Parameters: scoped writer, node revision and full replacement explicit links.

        What it does: records intentional node connections without Jev, generation or
        embedding. Empty links removes explicit edges; inferred judgments are unchanged.
        Output: saved memory revision; retries return the original committed result.
        """
        fingerprint, previous = self._memory_retry(identity, space_id, "set_links", request)
        if previous:
            return previous
        snapshot = self._digest(identity, space_id)
        resource = self.get_resource(
            identity, space_id, request.resource_id, request.expected_revision, _internal=True
        )
        if not resource.get("_memory"):
            raise CommandError("Memory not found", 404)
        record = deepcopy(resource["_memory"])
        record["links"] = memory_node_links.validate(
            self, identity, space_id, request.resource_id, request.links
        )
        refs = resource["recall"]["sources"]
        return self._save(
            identity,
            space_id,
            SaveResource(
                command_id=request.command_id,
                resource_id=request.resource_id,
                expected_revision=request.expected_revision,
                kind="document",
                title=resource["title"],
                body=memory_pipeline.memory_body(record),
                recall=RecallMetadata.model_validate(resource["recall"]),
            ),
            memory=record,
            index={
                "claims": memory_pipeline.index_rows(record),
                "vectors": {},
                "model": None,
                "source": (refs[0]["resource_id"], refs[0]["revision"]) if refs else None,
            },
            fingerprint=fingerprint,
            snapshot=snapshot,
        )

    @admitted("_write_admission")
    @key_operation
    def set_memory_creator(self, identity, space_id, request):
        return memory_creator.assign(self, identity, space_id, request)

    @admitted("_write_admission")
    @key_operation
    @reclassify_jobs.after_memory_write
    def change_memory(self, identity, space_id, request: ChangeMemory):
        """Edit or permanently remove one memory, then re-index only what changed."""
        fingerprint, previous = self._memory_retry(identity, space_id, "change", request)
        if previous:
            return previous
        snapshot = self._digest(identity, space_id)
        resource = self.get_resource(
            identity, space_id, request.resource_id, request.expected_revision, _internal=True
        )
        if not resource.get("_memory"):
            raise CommandError("Memory not found", 404)
        record = deepcopy(resource["_memory"])
        try:
            claim = next((c for c in record["claims"] if c["id"] == str(request.entry_id)), None)
            if claim is None:
                raise KeyError(str(request.entry_id))
            if request.action == "route":
                targets = memory_routing.validate(self, identity, space_id, request.routes_to)
                # An empty list is a deliberate clear that automatic routing must respect.
                claim["routing"] = (
                    memory_routing.explicit(targets)
                    if targets
                    else {"target_ids": [], "method": "manual"}
                )
            elif request.action == "classify":
                if request.classification is not None:
                    claim["classification"] = memory_classification.manual(
                        self, identity, space_id, request.classification
                    )
                else:
                    if not memory_classification.enabled(self):
                        raise CommandError("Automatic memory classification is not configured", 409)
                    claim.pop("classification", None)
                    model = self._model_session(
                        identity,
                        space_id,
                        lambda: self._authorize_model(identity, space_id, snapshot, write=True),
                        incoming_snapshot=snapshot,
                    )
                    memory_classification.apply(self, identity, space_id, [claim], model)
                    if claim["classification"]["reason"] in {
                        "unavailable",
                        "catalog_limit",
                        "input_limit",
                    }:
                        raise CommandError(
                            "Automatic classification unavailable; existing assignments kept.", 503
                        )
            else:
                memory_pipeline.change(record, str(request.entry_id), request.action, request.value)
        except ModelError as error:
            raise CommandError(str(error), 503) from None
        except KeyError:
            raise CommandError("Memory entry not found", 404) from None
        rows = memory_pipeline.index_rows(record)
        if request.action == "forget":
            if self.forget_journal is not None:
                with self.db.transaction(identity) as connection:
                    scope = self._scope(connection, identity, space_id, write=True)
                    head = connection.execute(
                        "SELECT current_revision FROM space.resources WHERE id=%s",
                        (request.resource_id,),
                    ).fetchone()
                    if not head or head["current_revision"] != request.expected_revision:
                        raise CommandError("Memory changed. Reload before forgetting.", 409)
                try:
                    self.forget_journal.append_forget(
                        organization_id=scope["organization_id"],
                        space_id=space_id,
                        resource_id=request.resource_id,
                        claim_id=request.entry_id,
                    )
                except Exception:
                    raise CommandError(
                        "Deletion journal unavailable. Retry forgetting shortly.", 503
                    ) from None
            # Deletion preserves surviving embeddings and their model. It must
            # remain available when the configured provider is disabled or changed.
            vectors, index_model = {}, None
        elif request.action in {"classify", "route"}:
            vectors, index_model = {}, None
        else:
            try:
                model = self._model_session(
                    identity,
                    space_id,
                    lambda: self._authorize_model(identity, space_id, snapshot, write=True),
                )
                vectors = self._index_vectors(identity, space_id, resource["id"], rows, model)
                memory_classification.apply(self, identity, space_id, [claim], model, vectors)
                memory_relationships.apply(
                    self, identity, space_id, resource["id"], [claim], vectors, model, snapshot
                )
                index_model = self.memory_models.embedding_model
            except ModelError as error:
                raise CommandError(str(error), 503) from None
        return self._save(
            identity,
            space_id,
            SaveResource(
                command_id=request.command_id,
                resource_id=request.resource_id,
                expected_revision=request.expected_revision,
                kind="document",
                title=resource["title"],
                body=memory_pipeline.memory_body(record),
                recall=RecallMetadata.model_validate(resource["recall"]),
            ),
            memory=record,
            index={
                "claims": rows,
                "vectors": vectors,
                "model": index_model,
                "source": (
                    (
                        resource["recall"]["sources"][0]["resource_id"],
                        resource["recall"]["sources"][0]["revision"],
                    )
                    if resource["recall"]["sources"]
                    else None
                ),
            },
            fingerprint=fingerprint,
            quota_deletion=request.action == "forget",
            snapshot=snapshot,
        )

    @admitted("_write_admission")
    @key_operation
    def reclassify_limited(self, identity, space_id, resource_id, job_id):
        """Reclassify only claims the old catalog limit left unassigned in one memory.

        Parameters: the queued writer's identity, the memory resource and the catch-up job.
        What it does: re-reads the current revision, so a concurrent edit wins; classifies
            the limited claims with the shortlist; leaves text and manual pins untouched.
        Output: the saved memory, or None when nothing in it needed reclassifying.
        """
        for _ in range(2):
            try:
                resource = self.get_resource(identity, space_id, resource_id, _internal=True)
            except CommandError as error:
                if error.status == 404:
                    return None
                raise
            claims = (resource.get("_memory") or {}).get("claims", [])
            if not any(reclassify_jobs.limited(c) for c in claims):
                return None
            request = ReclassifyMemory(
                command_id=uuid5(NAMESPACE_URL, f"{job_id}/{resource_id}/{resource['revision']}"),
                resource_id=resource_id,
                expected_revision=resource["revision"],
            )
            fingerprint, previous = self._memory_retry(identity, space_id, "reclassify", request)
            if previous:
                return previous
            record = deepcopy(resource["_memory"])
            claims = [c for c in record["claims"] if reclassify_jobs.limited(c)]
            snapshot = self._incoming_memory_snapshot(identity, space_id, None)
            model = self._model_session(
                identity,
                space_id,
                lambda snapshot=snapshot: self._authorize_model(
                    identity, space_id, snapshot, write=True
                ),
                incoming_snapshot=snapshot,
            )
            try:
                memory_classification.apply(self, identity, space_id, claims, model)
            except ModelError as error:
                raise CommandError(str(error), 503) from None
            if any(c["classification"]["reason"] == "unavailable" for c in claims):
                raise CommandError("Automatic classification unavailable; retry later", 503)
            refs = resource["recall"]["sources"]
            try:
                return self._save(
                    identity,
                    space_id,
                    SaveResource(
                        command_id=request.command_id,
                        resource_id=resource_id,
                        expected_revision=request.expected_revision,
                        kind="document",
                        title=resource["title"],
                        body=memory_pipeline.memory_body(record),
                        recall=RecallMetadata.model_validate(resource["recall"]),
                    ),
                    memory=record,
                    index={
                        "claims": memory_pipeline.index_rows(record),
                        "vectors": {},
                        "model": None,
                        "source": (refs[0]["resource_id"], refs[0]["revision"]) if refs else None,
                    },
                    fingerprint=fingerprint,
                    snapshot=snapshot,
                )
            except CommandError as error:
                # A concurrent edit wins; re-read once, then leave this memory for later.
                if error.status != 409:
                    raise
        return None

    @admitted("_recall_admission")
    @overview.track_retrieval
    @key_operation
    @read_stage("recall_total")
    def context(self, identity, space_id: UUID, request: RecallRequest):
        """Recall memories from the vector index and sources lexically.

        Parameters: the caller identity, the Space and the recall request.
        What it does: ranks memory in PostgreSQL by embedding distance, expands shared
        cues, and ranks other sources with BM25 over their metadata and selected bodies.
        Output: a bounded, cited result. Recall writes no revision.
        """
        self._recheck(identity, space_id)
        try:
            # Check existence without decrypting memory metadata. The vector index will
            # select the exact memories whose current metadata and bodies are needed.
            snapshot, indexed = self._recall_state(identity, space_id)
            if request.strategy in {"semantic", "prompt"} and not indexed:
                raise CommandError(
                    "Configure the memory provider and build memory before semantic recall",
                    409,
                )
            ranked, engine = [], ENGINE
            cached_bodies = {}
            session = None
            assessment = None
            memory_budget = min(
                request.limit * self.retrieval.candidate_multiplier,
                self.retrieval.max_candidates,
                relevance.MAX_EVIDENCE - request.limit,
            )
            if request.include_linked and (
                not indexed
                or not self.retrieval.assess_relevance
                or self.retrieval.score_floor is not None
            ):
                raise CommandError(
                    "Linked recall requires configured assessed search without a cosine floor", 409
                )
            # Defined here so a lexical-only recall never reaches an unbound name.
            related_only: dict[str, set] = {}
            if request.mode == "resume":
                memories, keys, scope, _ = self._memory_items(identity, space_id, kind="handoff")
                recent = sorted(
                    (i for i in memories if i["recall"]["category"] == "handoff"),
                    key=lambda i: (i["updated_at"], i["id"]),
                    reverse=True,
                )
                handoffs = recent[: request.limit]
                bodies = self._bodies(identity, space_id, handoffs, keys, scope)
                for item in handoffs:
                    body = bodies[item["id"]]["body"]
                    ranked.append(self._item(item, "recent_handoff", body=body))
            else:
                with read_stage("recall_sources"):
                    # Candidates come from the term index in SQL, so only resources that can
                    # match are decrypted. A title or topic match is returned even when the body
                    # does not match, and a body match even when the metadata does not.
                    _, scoped = self._key(identity, space_id)
                    query_terms = query_fingerprints(request.query, term_key(scoped.key.material))
                    sources, source_keys, source_scope = self._source_items(
                        identity, space_id, query_terms, MAX_LEXICAL_CANDIDATES
                    )
                    for item, body in self._rank_sources(
                        identity, space_id, sources, source_keys, source_scope, request
                    ):
                        ranked.append(self._item(item, "matched", body=body))
            # Auto falls back to lexical unless a provider is configured and memory exists.
            if (
                indexed
                and request.query
                and request.mode == "search"
                and request.strategy != "lexical"
            ):
                session = self._model_session(
                    identity,
                    space_id,
                    lambda: self._authorize_model(identity, space_id, snapshot),
                    recall_snapshot=snapshot,
                )
                with read_stage("recall_vector"):
                    vector = session.embed([request.query])[0]
                    floor = self.retrieval.score_floor
                    with self.db.transaction(identity) as connection:
                        scope = self._scope(connection, identity, space_id)
                        hits = brain.search(
                            connection,
                            vector,
                            self.memory_models.embedding_model,
                            memory_budget,
                            config=self.retrieval,
                        )
                        # An optional geometric cutoff is not evidence of relevance.
                        # The independent evidence assessment runs after hydration.
                        if floor is not None:
                            hits = [hit for hit in hits if hit["score"] >= floor]
                        wants_related = (
                            request.include_related and self.retrieval.expanding and floor is None
                        )
                        cues = sorted(
                            {
                                cue
                                for hit in hits
                                if wants_related
                                for cue in brain.cues_for(
                                    connection, hit["memory_id"], [str(hit["claim_id"])]
                                )
                            }
                        )
                        # Cue-expanded claims were never selected by similarity, so they
                        # would bypass a floor and make found=true meaningless, and they
                        # must never displace a ranked result. They are only gathered when
                        # the caller opts in, and are returned as a separate tier.
                        related = (
                            brain.expand(connection, cues, brain.MAX_CUE_CANDIDATES)
                            if wants_related
                            else []
                        )
                        load_order = list(
                            dict.fromkeys(str(row["memory_id"]) for row in [*hits, *related])
                        )
                        selected_rows, key_rows = (
                            self._item_rows(connection, scope, space_id, load_order, memory=True)
                            if load_order
                            else ([], {})
                        )
                    allowed, scores, order = {}, {}, []
                    for row in hits:
                        key = str(row["memory_id"])
                        allowed.setdefault(key, set()).add(str(row["claim_id"]))
                        if row.get("score") is not None:
                            scores.setdefault(key, {})[str(row["claim_id"])] = float(row["score"])
                        if key not in order:
                            order.append(key)
                    # Every cue-expanded claim is a related candidate. Ones that are also
                    # ranked answers are filtered out later, against the ranked list
                    # itself, so a weak vector hit that did not become an answer can still
                    # be offered as related.
                    related_only = {}
                    for row in related:
                        related_only.setdefault(str(row["memory_id"]), set()).add(
                            str(row["claim_id"])
                        )
                    # Related memories need their bodies loaded, but never enter the
                    # candidate set the ranker sees.
                    keys = self._unwrap_keys(scope, key_rows)
                    selected = self._summaries(selected_rows, keys, scope, include_excluded=False)
                    selected = selected[: max(MAX_MEMORY_LOAD, len(order))]
                    contents = self._bodies(
                        identity, space_id, selected, keys, scope, cached_bodies=cached_bodies
                    )
                    loaded = [
                        item | {"_memory": contents[item["id"]]["memory"]} for item in selected
                    ]
                if self.retrieval.assess_relevance and request.strategy != "prompt":
                    retrieved = [
                        (view, "memory_" + request.strategy)
                        for view in memory_pipeline.ranked_claims(loaded, allowed, scores)
                    ]
                else:
                    retrieved = memory_pipeline.retrieve(loaded, request, session, allowed, scores)
                if self.retrieval.assess_relevance and request.strategy != "prompt":
                    lexical = (
                        recall_candidates.lexical(
                            self,
                            identity,
                            space_id,
                            request.query,
                            memory_budget,
                            model_name=self.memory_models.embedding_model,
                            cached_bodies=cached_bodies,
                        )
                        if self.retrieval.lexical_claims and floor is None
                        else []
                    )
                    views = recall_candidates.merge(
                        [view for view, _ in retrieved], lexical, memory_budget
                    )
                    linked = []
                    if request.include_linked and memory_budget > 1:
                        seeds = list(dict.fromkeys(view["id"] for view in views))[:5]
                        linked = recall_candidates.linked(
                            self,
                            identity,
                            space_id,
                            seeds,
                            request.query,
                            min(5, memory_budget // 4 or 1),
                            model_name=self.memory_models.embedding_model,
                            cached_bodies=cached_bodies,
                        )
                        direct_keys = {(view["id"], view["entry_id"]) for view in views}
                        linked = [
                            view
                            for view in linked
                            if (view["id"], view["entry_id"]) not in direct_keys
                        ]
                    if linked:
                        views = views[: memory_budget - len(linked)] + linked
                    retrieved = [
                        (
                            view,
                            "memory_linked"
                            if view.get("candidate_origin") == "linked"
                            else "memory_lexical"
                            if view.get("candidate_origin") == "lexical"
                            else "memory_" + request.strategy,
                        )
                        for view in views
                    ]
                semantic = [self._item(view, reason, entry=view) for view, reason in retrieved]
                if semantic:
                    ranked, engine = semantic + ranked, brain.ENGINE
            elif request.mode == "search" and self.retrieval.lexical_claims:
                # Lexical-only and provider-disabled auto search need no embedding or model.
                lexical = recall_candidates.lexical(
                    self, identity, space_id, request.query, memory_budget
                )
                ranked = [
                    self._item(view, "memory_lexical", entry=view) for view in lexical
                ] + ranked
            # Staleness needs the current revision of the sources actually cited, which is
            # a bounded read of identifiers, not a decryption of the corpus.
            current = self._current_revisions(
                identity,
                space_id,
                {ref["resource_id"] for item in ranked for ref in item.get("sources", [])},
            )
            ordered, seen = [], set()
            for item in ranked:
                key = (item["id"], item.get("entry_id"))
                if key in seen:
                    continue
                seen.add(key)
                ordered.append(item | {"stale": self._stale(item["sources"], current)})
            if (
                ordered
                and request.mode == "search"
                and request.strategy != "lexical"
                and self.memory_models.enabled
                and self.retrieval.assess_relevance
            ):
                session = session or self._model_session(
                    identity,
                    space_id,
                    lambda: self._authorize_model(identity, space_id, snapshot),
                    recall_snapshot=snapshot,
                )
                with read_stage("recall_context"):
                    recall_context.attach(self, identity, space_id, ordered)
                self._authorize_model(identity, space_id, snapshot)
                assessment = {}
                with read_stage("recall_assessment"):
                    ordered = relevance.select(
                        ordered, request.query, session, diagnostics=assessment
                    )
            elif ordered and request.mode == "search":
                with read_stage("recall_context"):
                    recall_context.attach(self, identity, space_id, ordered)
            # The related tier comes from the memories already loaded and holds only
            # claims cue expansion added that did not become ranked answers. It is a
            # separate list, so it can never displace a ranked item.
            related_items = []
            if request.include_related and related_only:
                ranked_claims = {item.get("entry_id") for item in ordered}
                for resource in loaded:
                    claims = related_only.get(resource["id"])
                    if not claims:
                        continue
                    for view in memory_pipeline.virtual_memories(resource):
                        if view["entry_id"] in claims and view["entry_id"] not in ranked_claims:
                            related_items.append(self._item(resource, "related", entry=view))
            result = bounded_result(
                ordered,
                request,
                lambda id_, revision: self.source_url(space_id, id_, revision),
                engine=engine,
                related=related_items,
                assessment=assessment,
            )
            # A revoked grant must deny release; a concurrent write is not an error here.
            self._recheck(identity, space_id)
            return result
        except ModelError as error:
            raise CommandError(str(error), 503) from None
        except brain.DimensionError as error:
            raise CommandError(str(error), 400) from None

    def grants(self, identity):
        with self.db.transaction(identity) as connection:
            self._scope(connection, identity, human=True)
            rows = connection.execute(
                "SELECT id,client_id,agent_id,can_write,revoked_at,created_at FROM "
                "space.oauth_grants g WHERE NOT EXISTS (SELECT 1 FROM space.agent_identities a "
                "WHERE a.id=g.agent_id AND a.deleted_at IS NOT NULL) ORDER BY created_at DESC"
            ).fetchall()
        return {
            "items": [
                {
                    k: (
                        v.isoformat()
                        if hasattr(v, "isoformat")
                        else str(v)
                        if isinstance(v, UUID)
                        else v
                    )
                    for k, v in row.items()
                }
                for row in rows
            ]
        }

    def grant(self, identity, client_id: UUID, can_write: bool, agent_id: UUID | None = None):
        with self.db.transaction(identity) as connection:
            return self._grant(connection, identity, client_id, can_write, agent_id)

    def _grant(self, connection, identity, client_id, can_write, agent_id=None) -> str:
        """Within the caller's transaction, so a device-code approval and its grant
        commit or roll back together. Returns the grant ID."""
        self._scope(connection, identity, human=True, write=can_write)
        if (
            agent_id
            and not connection.execute(
                "SELECT id FROM space.agent_identities "
                "WHERE id=%s AND deleted_at IS NULL FOR SHARE",
                (agent_id,),
            ).fetchone()
        ):
            raise CommandError("Agent identity not found in this Space", 404)
        connection.execute(
            "SELECT space.set_connection_grant(%s,%s,%s,%s)",
            (client_id, self.resource_url, can_write, agent_id),
        )
        return str(
            connection.execute(
                "SELECT id FROM space.oauth_grants WHERE client_id=%s", (client_id,)
            ).fetchone()["id"]
        )

    def revoke(self, identity, client_id: UUID):
        with self.db.transaction(identity) as connection:
            self._scope(connection, identity, human=True)
            connection.execute(
                "UPDATE space.oauth_grants SET revoked_at=now() WHERE client_id=%s", (client_id,)
            )
        return {"revoked": True}
