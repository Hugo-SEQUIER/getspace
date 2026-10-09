from copy import deepcopy
from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from test_database_setup import database as database_fixture
from test_memora_workspace import claim_change, record
from test_memory_classification_workspace import assign, item
from test_memory_relationships_workspace import (
    RelatingModels,
    first_claim,
    links,
    remember_request,
    seed,
    write,
)
from test_workspace import change, delegated
from test_workspace import command_database as command_database_fixture
from test_workspace import commands as commands_fixture

from space.memory import ExplicitMemoryLink, SetMemoryLinks
from space.resource_deletion import delete_memory
from space.workspace import CommandError

database = database_fixture
command_database = command_database_fixture
commands = commands_fixture


def explicit(target, relation="expands_on"):
    return {"resource_id": target["id"], "relation": relation}


def replacement(resource, targets):
    return SetMemoryLinks(
        command_id=uuid4(),
        resource_id=resource["id"],
        expected_revision=resource["revision"],
        links=targets,
    )


def assert_node_link(actual, target, relation="expands_on"):
    assert actual["origin"] == "explicit"
    assert actual["entry_id"] is None
    assert actual["related_entry_id"] is None
    assert actual["resource_id"] == target["id"]
    assert actual["revision"] == target["revision"]
    assert actual["relation"] == relation
    assert "score" not in actual
    assert actual.get("confidence") is None


@pytest.mark.parametrize("operation", ["prepared", "raw"])
def test_write_explicit_work_link_persists_verbatim_and_retry_has_no_provider_calls(
    commands, operation
):
    service, owner, space, _ = commands
    work = seed(commands, title="My work", text="I build software for clients.")
    service.memory_models = models = RelatingModels(classify=False, relate=False)
    text = "What I like in my work: solving useful problems. Café & <code>."
    request = remember_request(
        operation, text, title="What I like in my work", links=[explicit(work)]
    )
    saved = write(service, owner, space, request)
    assert first_claim(saved)["value"] == text
    assert saved["memory"]["links"] == [explicit(work)]
    assert record(service, owner, space, saved)["links"] == [explicit(work)]
    assert service.get_resource(owner, space, UUID(saved["id"]))["memory"]["links"] == [
        explicit(work)
    ]
    outgoing = links(service, owner, space, saved)
    incoming = links(service, owner, space, work)
    assert len(outgoing["links"]) == len(incoming["backlinks"]) == 1
    assert outgoing["backlinks"] == incoming["links"] == []
    assert_node_link(outgoing["links"][0], work)
    assert_node_link(incoming["backlinks"][0], saved)
    calls = deepcopy(models.calls)
    assert write(service, owner, space, request) == saved
    assert models.calls == calls
    assert not any(call[0] in {"classify", "relate", "model"} for call in calls)


@pytest.mark.parametrize("operation", ["prepared", "raw", "set"])
@pytest.mark.parametrize("invalid", ["missing", "document", "deleted", "foreign"])
def test_invalid_explicit_target_aborts_before_any_provider_or_new_revision(
    commands, operation, invalid
):
    service, owner, space, _ = commands
    source = seed(commands)
    if invalid == "missing":
        target = {"id": str(uuid4())}
    elif invalid == "document":
        target = service.save(owner, space, change())
    elif invalid == "deleted":
        target = seed(commands)
        delete_memory(service, owner, space, UUID(target["id"]), target["revision"])
    else:
        stranger = replace(owner, subject=str(uuid4()))
        foreign_space = UUID(service.bootstrap(stranger)["id"])
        target = write(service, stranger, foreign_space, remember_request())
    before = service.list_memories(owner, space)
    service.memory_models = models = RelatingModels()
    with pytest.raises(CommandError):
        if operation == "set":
            service.set_memory_links(owner, space, replacement(source, [explicit(target)]))
        else:
            write(service, owner, space, remember_request(operation, links=[explicit(target)]))
    assert models.calls == []
    assert service.list_memories(owner, space) == before
    assert service.get_resource(owner, space, UUID(source["id"]))["revision"] == source["revision"]


def test_set_replaces_and_removes_only_explicit_links_without_model_or_embeddings(commands):
    service, owner, space, _ = commands
    first, second, source = [seed(commands) for _ in range(3)]
    service.memory_models = models = RelatingModels()
    request = replacement(source, [explicit(first)])
    added = service.set_memory_links(owner, space, request)
    assert added["revision"] == source["revision"] + 1
    assert added["memory"]["claims"] == source["memory"]["claims"]
    assert service.set_memory_links(owner, space, request) == added
    assert_node_link(links(service, owner, space, first)["backlinks"][0], added)
    changed = service.set_memory_links(
        owner, space, replacement(added, [explicit(second, "related")])
    )
    assert changed["memory"]["links"] == [explicit(second, "related")]
    assert links(service, owner, space, first)["backlinks"] == []
    assert_node_link(links(service, owner, space, changed)["links"][0], second, "related")
    assert_node_link(links(service, owner, space, second)["backlinks"][0], changed, "related")
    removal = replacement(changed, [])
    removed = service.set_memory_links(owner, space, removal)
    assert service.set_memory_links(owner, space, removal) == removed
    assert removed["memory"]["links"] == []
    assert links(service, owner, space, removed)["links"] == []
    assert links(service, owner, space, second)["backlinks"] == []
    assert models.calls == []


def test_manual_links_work_when_memory_provider_is_unavailable(commands):
    service, owner, space, _ = commands
    target, source = seed(commands), seed(commands)
    service.memory_models = None
    saved = service.set_memory_links(owner, space, replacement(source, [explicit(target)]))
    assert_node_link(links(service, owner, space, saved)["links"][0], target)


@pytest.mark.parametrize("endpoint", ["source", "target"])
@pytest.mark.parametrize("mutation", ["edit", "classify", "forget"])
def test_explicit_links_belong_to_nodes_and_survive_claim_changes(commands, endpoint, mutation):
    service, owner, space, _ = commands
    target = seed(commands)
    source = seed(commands, links=[explicit(target)])
    affected = source if endpoint == "source" else target
    if mutation == "classify":
        request = assign(affected, scope="global")
    else:
        request = claim_change(
            affected,
            first_claim(affected)["id"],
            mutation,
            value="My work has completely changed." if mutation == "edit" else None,
        )
    updated = service.change_memory(owner, space, request)
    if mutation == "forget":
        assert updated["memory"]["claims"] == []
    if endpoint == "source":
        source = updated
    else:
        target = updated
    outgoing = links(service, owner, space, source)
    incoming = links(service, owner, space, target)
    assert len(outgoing["links"]) == len(incoming["backlinks"]) == 1
    assert_node_link(outgoing["links"][0], target)
    assert_node_link(incoming["backlinks"][0], source)
    assert record(service, owner, space, source)["links"] == [explicit(target)]


@pytest.mark.parametrize("endpoint", ["source", "target"])
def test_deleting_a_memory_node_removes_its_explicit_edges(commands, endpoint):
    service, owner, space, _ = commands
    target = seed(commands)
    source = seed(commands, links=[explicit(target)])
    deleted = source if endpoint == "source" else target
    survivor = target if endpoint == "source" else source
    delete_memory(service, owner, space, UUID(deleted["id"]), deleted["revision"])
    result = links(service, owner, space, survivor)
    assert result["links"] == result["backlinks"] == []


def test_explicit_id_selects_one_of_two_identically_named_project_memories(commands):
    service, owner, space, _ = commands
    projects = [item(service, owner, space, "project", name) for name in ("Atlas", "Boreal")]
    work = []
    for project in projects:
        saved = seed(commands, title="My work", text="I build client software.")
        work.append(
            service.change_memory(
                owner, space, assign(saved, scope="project", project_ids=[project["id"]])
            )
        )
    unlinked = seed(commands, title="What I like in my work", text="I like solving problems.")
    assert links(service, owner, space, unlinked)["links"] == []
    selected = service.set_memory_links(owner, space, replacement(unlinked, [explicit(work[1])]))
    assert len(links(service, owner, space, selected)["links"]) == 1
    assert_node_link(links(service, owner, space, selected)["links"][0], work[1])
    assert links(service, owner, space, work[0])["backlinks"] == []


@pytest.mark.parametrize("endpoint", ["source", "target"])
def test_inferred_links_survive_manual_classification_but_not_content_edits(commands, endpoint):
    service, owner, space, _ = commands
    target = seed(commands)
    service.memory_models = RelatingModels(classify=False, relate=True)
    source = write(service, owner, space, remember_request())
    assert links(service, owner, space, source)["links"][0]["origin"] == "inferred"
    affected = source if endpoint == "source" else target
    updated = service.change_memory(owner, space, assign(affected, scope="global"))
    if endpoint == "source":
        source = updated
    else:
        target = updated
    outgoing = links(service, owner, space, source)["links"]
    incoming = links(service, owner, space, target)["backlinks"]
    assert len(outgoing) == len(incoming) == 1
    assert outgoing[0]["origin"] == incoming[0]["origin"] == "inferred"
    assert outgoing[0]["revision"] == target["revision"]
    assert incoming[0]["revision"] == source["revision"]
    service.memory_models = RelatingModels(classify=False, relate=False)
    service.change_memory(
        owner,
        space,
        claim_change(
            updated, first_claim(updated)["id"], "edit", value="A completely unrelated new fact."
        ),
    )
    assert links(service, owner, space, source)["links"] == []
    assert links(service, owner, space, target)["backlinks"] == []


def test_set_requires_current_revision_and_write_access(commands):
    service, owner, space, _ = commands
    target, source = seed(commands), seed(commands)
    request = replacement(source, [explicit(target)])
    reader = delegated(service, owner)
    with pytest.raises(CommandError) as denied:
        service.set_memory_links(reader, space, request)
    assert denied.value.status == 403
    saved = service.set_memory_links(owner, space, request)
    with pytest.raises(CommandError) as stale:
        service.set_memory_links(owner, space, request.model_copy(update={"command_id": uuid4()}))
    assert stale.value.status == 409
    with pytest.raises(CommandError) as reused:
        service.set_memory_links(owner, space, request.model_copy(update={"links": []}))
    assert reused.value.status == 409
    assert service.get_resource(owner, space, UUID(source["id"])) == saved


@pytest.mark.parametrize(
    "extra", [{"entry_id": str(uuid4())}, {"score": 0.97}, {"name": "My work"}]
)
def test_explicit_links_are_id_only_nodes_not_claims_or_scores(extra):
    with pytest.raises(ValidationError):
        ExplicitMemoryLink(resource_id=uuid4(), relation="expands_on", **extra)


@pytest.mark.parametrize("operation", ["prepared", "raw", "set"])
def test_explicit_links_are_bounded_and_require_memory_ids(operation):
    targets = [{"resource_id": uuid4(), "relation": "related"} for _ in range(21)]

    def request(values):
        if operation == "set":
            return SetMemoryLinks(
                command_id=uuid4(), resource_id=uuid4(), expected_revision=1, links=values
            )
        return remember_request(operation, links=values)

    assert len(request(targets[:20]).links) == 20
    assert request([]).links == []
    with pytest.raises(ValidationError):
        request(targets)
    with pytest.raises(ValidationError):
        request([{"resource_id": "My work", "relation": "expands_on"}])
    with pytest.raises(ValidationError):
        request([{"resource_id": uuid4(), "relation": "invented"}])


def test_self_link_is_rejected():
    resource_id = uuid4()
    with pytest.raises(ValidationError):
        SetMemoryLinks(
            command_id=uuid4(),
            resource_id=resource_id,
            expected_revision=1,
            links=[{"resource_id": resource_id, "relation": "related"}],
        )


def test_removing_manual_links_preserves_independent_inferred_links(commands):
    service, owner, space, _ = commands
    target = seed(commands)
    service.memory_models = models = RelatingModels(classify=False, relate=True)
    source = write(service, owner, space, remember_request(links=[explicit(target)]))
    outgoing = links(service, owner, space, source)["links"]
    assert len(outgoing) == 1
    assert_node_link(outgoing[0], target)
    stored = record(service, owner, space, source)
    assert stored["links"] == [explicit(target)]
    inferred = stored["claims"][0]["relationships"]["links"]
    assert len(inferred) == 1
    assert inferred[0]["resource_id"] == target["id"]
    calls = deepcopy(models.calls)
    removed = service.set_memory_links(owner, space, replacement(source, []))
    remaining = links(service, owner, space, removed)["links"]
    assert len(remaining) == 1
    assert remaining[0]["origin"] == "inferred"
    assert remaining[0]["resource_id"] == target["id"]
    assert models.calls == calls
