from copy import deepcopy
from uuid import UUID, uuid4

import pytest
from test_database_setup import database as database_fixture
from test_memora_pipeline import Models
from test_workspace import command_database as command_database_fixture
from test_workspace import commands as commands_fixture
from test_workspace import delegated

from space import agents, archive_schema, memory_pipeline
from space.memory import ChangeMemory, RememberClaims
from space.memory_creator import SetMemoryCreator
from space.security.auth import Unauthenticated
from space.workspace import CommandError

database = database_fixture
command_database = command_database_fixture
commands = commands_fixture


def prepared(commands):
    service, owner, space, _ = commands
    service.memory_models = Models()
    profile = agents.save_profile(
        service, owner, agents.AgentProfile(name="Hermes Agent", harness="hermes")
    )
    saved = service.remember_claims(
        owner,
        space,
        RememberClaims(
            command_id=uuid4(),
            title="Two memories",
            claims=[
                {"index": "one", "value": "Exact first sentence."},
                {"index": "two", "value": "Exact second sentence."},
            ],
        ),
    )
    return service, owner, space, profile, saved


def request(saved, profile):
    return SetMemoryCreator(
        command_id=uuid4(),
        resource_id=saved["id"],
        expected_revision=saved["revision"],
        entry_ids=[c["id"] for c in saved["memory"]["claims"]],
        creator_agent_id=profile["id"],
    )


def test_attribution_preserves_content_vectors_and_revision_history(commands):
    service, owner, space, profile, saved = prepared(commands)
    original = deepcopy(saved["memory"]["claims"])
    assert all(c["creator"]["kind"] == "human" for c in original)
    service.memory_models.calls.clear()
    change = request(saved, profile)
    updated = service.set_memory_creator(owner, space, change)
    assert service.set_memory_creator(owner, space, change) == updated
    assert not service.memory_models.calls
    for before, after in zip(original, updated["memory"]["claims"], strict=True):
        assert {k: v for k, v in after.items() if k != "creator"} == {
            k: v for k, v in before.items() if k != "creator"
        }
        assert after["creator"] == {"kind": "agent", "agent": profile, "basis": "user_attributed"}
    assert service.get_resource(owner, space, UUID(saved["id"]), 1)["memory"]["claims"] == original
    page = service.list_memories(owner, space)
    assert page["provider"]["index"]["claims"] == 2
    assert page["items"][0]["claims"][0]["creator"]["agent"]["name"] == "Hermes Agent"
    current = service.get_resource(owner, space, UUID(saved["id"]), _internal=True)
    archive_schema._memory(current["_memory"])
    rebuilt = deepcopy(current["_memory"])
    for claim in rebuilt["claims"]:
        claim.pop("creator")
    memory_pipeline.retain_edits(rebuilt, current["_memory"])
    assert all(c["creator"]["agent"] == profile for c in rebuilt["claims"])
    edited = service.change_memory(
        owner,
        space,
        ChangeMemory(
            command_id=uuid4(),
            resource_id=saved["id"],
            expected_revision=2,
            entry_id=original[0]["id"],
            action="edit",
            value="Explicit corrected sentence.",
        ),
    )
    assert edited["memory"]["claims"][0]["creator"]["agent"] == profile


def test_attribution_rejects_agents_unknown_profiles_entries_and_stale_revisions(commands):
    service, owner, space, profile, saved = prepared(commands)
    change = request(saved, profile)
    with pytest.raises(Unauthenticated):
        service.set_memory_creator(delegated(service, owner, write=True), space, change)
    for update in ({"creator_agent_id": uuid4()}, {"entry_ids": [uuid4()]}):
        with pytest.raises(CommandError):
            service.set_memory_creator(owner, space, change.model_copy(update=update))
    service.set_memory_creator(owner, space, change)
    with pytest.raises(CommandError):
        service.set_memory_creator(owner, space, change.model_copy(update={"command_id": uuid4()}))


def test_new_agent_memory_captures_original_creator(commands):
    service, owner, space, profile, _ = prepared(commands)
    writer = delegated(service, owner, write=True)
    saved = service.remember_claims(
        writer,
        space,
        RememberClaims(
            command_id=uuid4(),
            title="Agent",
            claims=[{"index": "a", "value": "Agent text"}],
        ),
    )
    creator = saved["memory"]["claims"][0]["creator"]
    assert creator["basis"] == "captured" and creator["agent"]["id"] == writer.agent_id
