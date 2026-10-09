"""Original creator metadata, separate from the author of a later revision."""

from copy import deepcopy
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from space import agents, memory_pipeline
from space.memory import Input, RecallMetadata
from space.memory_snapshot import IncomingMemorySnapshot


class AgentSnapshot(agents.AgentProfile):
    id: UUID
    revision: int = Field(ge=1, strict=True)


class MemoryCreator(Input):
    kind: Literal["human", "agent"]
    agent: AgentSnapshot | None = None
    basis: Literal["captured", "user_attributed"]

    @model_validator(mode="after")
    def consistent(self):
        if (self.kind == "agent") != (self.agent is not None):
            raise ValueError("An agent creator requires its profile")
        return self


class SetMemoryCreator(Input):
    command_id: UUID
    resource_id: UUID
    expected_revision: int = Field(ge=1, strict=True)
    creator_agent_id: UUID | None = None
    entry_ids: list[UUID] = Field(min_length=1, max_length=24)

    @model_validator(mode="after")
    def unique(self):
        if len(set(self.entry_ids)) != len(self.entry_ids):
            raise ValueError("Choose each memory entry once")
        return self


def capture(record, profile):
    creator = MemoryCreator(
        kind="agent" if profile else "human", agent=profile, basis="captured"
    ).model_dump(mode="json")
    for claim in record["claims"]:
        claim.setdefault("creator", deepcopy(creator))


def assign(workspace, identity, space_id, request):
    """Apply a user's explicit attribution to selected claims in one immutable revision."""
    from space.workspace import CommandError, SaveResource

    # Attribution is a user correction, never an agent changing its acting identity.
    with workspace.db.transaction(identity) as connection:
        workspace._scope(connection, identity, space_id, human=True, api_key=True, write=True)
    fingerprint, previous = workspace._memory_retry(identity, space_id, "creator", request)
    if previous:
        return previous
    snapshot = IncomingMemorySnapshot(request.resource_id, request.expected_revision, None)
    resource = workspace.get_resource(
        identity, space_id, request.resource_id, request.expected_revision, _internal=True
    )
    if not resource.get("_memory"):
        raise CommandError("Memory not found", 404)
    record = deepcopy(resource["_memory"])
    ids = {str(id_) for id_ in request.entry_ids}
    if not ids <= {c["id"] for c in record["claims"]}:
        raise CommandError("Memory entry not found", 404)
    profile = None
    if request.creator_agent_id:
        with workspace.db.transaction(identity) as connection:
            scope = workspace._scope(
                connection, identity, space_id, human=True, api_key=True, write=True
            )
            row = connection.execute(
                "SELECT * FROM space.agent_identities WHERE id=%s AND deleted_at IS NULL",
                (request.creator_agent_id,),
            ).fetchone()
            if row is None:
                raise CommandError("Choose an active agent from this Space", 404)
            key_row = workspace._key_row(connection, scope, row["key_id"])
        key = workspace._unwrap_key(scope["organization_id"], scope["space_id"], key_row)
        profile = {
            "id": str(row["id"]),
            "revision": row["revision"],
            **workspace._unpack(
                key, scope, row["id"], row["revision"], "agent", row["encrypted_metadata"]
            ),
        }
    creator = MemoryCreator(
        kind="agent" if profile else "human", agent=profile, basis="user_attributed"
    ).model_dump(mode="json")
    for claim in record["claims"]:
        if claim["id"] in ids:
            claim["creator"] = deepcopy(creator)
    return workspace._save(
        identity,
        space_id,
        SaveResource(
            command_id=request.command_id,
            resource_id=request.resource_id,
            expected_revision=request.expected_revision,
            kind="document",
            title=resource["title"],
            body=resource["body"],
            status=resource["status"],
            recall=RecallMetadata.model_validate(resource["recall"]),
        ),
        memory=record,
        index={
            "claims": memory_pipeline.index_rows(record),
            "vectors": {},
            "model": None,
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
        snapshot=snapshot,
    )
