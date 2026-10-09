"""Parameters: disposable PostgreSQL and synthetic encrypted recall metadata.
What it does: checks batched context reads, exact provenance, limits and disclosure gates.
Output: deterministic scope counts and parity checks without provider or body I/O.
"""

from collections import Counter
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from test_database_setup import database as database_fixture
from test_memory_classification_workspace import item as label
from test_recall_candidates import remember
from test_workspace import change, delegated
from test_workspace import command_database as command_database_fixture
from test_workspace import commands as commands_fixture

from space import recall_context, workspace_items
from space.memory import RecallMetadata
from space.workspace import CommandError

database = database_fixture
command_database = command_database_fixture
commands = commands_fixture


def source(service, owner, space, title, *, previous=None, excluded=False):
    """Parameters: source title, optional prior revision and exclusion.
    What it does: saves synthetic metadata through the normal revision-checked API.
    Output: a real encrypted source revision in the disposable database.
    """
    return service.save(
        owner,
        space,
        change().model_copy(
            update={
                "resource_id": UUID(previous["id"]) if previous else None,
                "expected_revision": previous["revision"] if previous else 0,
                "title": title,
                "body": "Authored body must not be hydrated by attach.",
                "recall": RecallMetadata(excluded=excluded),
            }
        ),
    )


def reference(saved):
    """Parameters: saved source revision. What it does: cites it exactly. Output: reference."""
    return {"resource_id": saved["id"], "revision": saved["revision"]}


def observe(service, monkeypatch):
    """Parameters: synthetic workspace and monkeypatch fixture.
    What it does: counts scopes and forbids decoding and key work in SQL or any body/model read.
    Output: transaction counts and exact metadata decryption events.
    """
    transaction, scope = service.db.transaction, service._scope
    unwrap, decrypt = service.keys.unwrap, service._unpack
    counts, decrypted = Counter(), []

    @contextmanager
    def counted_transaction(identity):
        counts["transactions"] += 1
        with transaction(identity) as connection:
            counts["active"] += 1
            try:
                yield connection
            finally:
                counts["active"] -= 1

    def counted_scope(*args, **kwargs):
        counts["scopes"] += 1
        return scope(*args, **kwargs)

    def checked_unwrap(*args):
        assert counts["active"] == 0
        counts["unwraps"] += 1
        return unwrap(*args)

    def checked_decrypt(key, scope, resource_id, revision, purpose, value):
        assert counts["active"] == 0
        decrypted.append((str(resource_id), revision, purpose))
        return decrypt(key, scope, resource_id, revision, purpose, value)

    def forbidden_io(*args, **kwargs):
        pytest.fail("Context metadata must not hydrate bodies or call a model")

    monkeypatch.setattr(service.db, "transaction", counted_transaction)
    monkeypatch.setattr(service, "_scope", counted_scope)
    monkeypatch.setattr(service.keys, "unwrap", checked_unwrap)
    monkeypatch.setattr(service, "_unpack", checked_decrypt)
    monkeypatch.setattr(service.objects, "get", forbidden_io)
    monkeypatch.setattr(service, "_bodies", forbidden_io)
    monkeypatch.setattr(service, "_model_session", forbidden_io)
    return counts, decrypted


@pytest.mark.parametrize(
    "labels,sources,scopes",
    [
        (True, True, 3),
        (True, False, 3),
        (False, True, 2),
        (False, False, 1),
    ],
)
def test_attach_keeps_exact_fields_and_required_rechecks_in_bounded_scopes(
    commands,
    monkeypatch,
    labels,
    sources,
    scopes,
):
    service, owner, space, _ = commands
    project = label(service, owner, space, "project", "Current project")
    category = label(service, owner, space, "category", "Current category")
    section = label(service, owner, space, "section", "Current section")
    first = source(service, owner, space, "Exact historical title")
    second = source(service, owner, space, "Current renamed title", previous=first)
    selected = {
        "id": str(uuid4()),
        "title": "Claim title",
        "revision": 7,
        "body": "Exact authored evidence.",
        "stale": True,
        "verification": "memory",
        "evidence_context": {"resource_title": "Memory resource title"},
        "classification": {"scope": "project"},
        "sources": [reference(first), reference(second)] if sources else [],
    }
    expected_context = {"resource_title": "Memory resource title", "scope": "project"}
    if labels:
        selected["classification"].update(
            {
                "category_ids": [category["id"]],
                "section_ids": [section["id"]],
            }
        )
        selected["_context_project"] = project["id"]
        expected_context.update(
            {
                "projects": ["Current project"],
                "categories": ["Current category"],
                "sections": ["Current section"],
            }
        )
    if sources:
        expected_context["source_titles"] = ["Exact historical title", "Current renamed title"]
    expected = deepcopy(selected)
    expected.pop("_context_project", None)
    expected["evidence_context"] = expected_context
    counts, decrypted = observe(service, monkeypatch)
    recall_context.attach(service, owner, space, [selected])
    assert selected == expected
    assert counts["transactions"] == counts["scopes"] == scopes
    assert counts["active"] == 0
    source_events = [event for event in decrypted if event[2] == "metadata"]
    if sources:
        assert source_events[0] == (first["id"], 2, "metadata")
        assert sorted(source_events[1:]) == [
            (first["id"], 1, "metadata"),
            (first["id"], 2, "metadata"),
        ]
    else:
        assert source_events == []


def test_only_active_current_same_space_labels_are_decrypted(commands, monkeypatch):
    service, owner, space, _ = commands
    active = label(service, owner, space, "project", "Old project name")
    workspace_items.save(
        service,
        owner,
        space,
        workspace_items.SaveItem(
            command_id=uuid4(),
            item_id=active["id"],
            expected_revision=1,
            kind="project",
            data={"name": "Current project name"},
        ),
    )
    archived = label(service, owner, space, "category", "Archived private name")
    workspace_items.visibility(
        service,
        owner,
        space,
        archived["id"],
        workspace_items.ChangeVisibility(command_id=uuid4(), expected_revision=1, deleted=True),
    )
    stranger = replace(owner, subject=str(uuid4()))
    foreign_space = UUID(service.bootstrap(stranger)["id"])
    foreign = label(service, stranger, foreign_space, "section", "Foreign private name")
    folder = label(service, owner, space, "folder", "Not a classification label")
    selected = {
        "title": "Evidence",
        "classification": {
            "project_ids": [active["id"], active["id"]],
            "category_ids": [archived["id"], folder["id"]],
            "section_ids": [foreign["id"], str(uuid4())],
        },
    }
    counts, decrypted = observe(service, monkeypatch)
    recall_context.attach(service, owner, space, [selected])
    assert selected["evidence_context"] == {
        "resource_title": "Evidence",
        "projects": ["Current project name"],
        "incomplete": True,
    }
    assert decrypted == [(active["id"], 2, "workspace_item")]
    assert counts["scopes"] == 3


@pytest.mark.parametrize("current_excluded", [False, True])
@pytest.mark.parametrize("historical_excluded", [False, True])
def test_current_visibility_precedes_cited_decryption_and_cited_exclusion_still_applies(
    commands,
    monkeypatch,
    current_excluded,
    historical_excluded,
):
    service, owner, space, _ = commands
    first = source(service, owner, space, "Cited title", excluded=historical_excluded)
    source(service, owner, space, "Current title", previous=first, excluded=current_excluded)
    selected = {"title": "Evidence", "sources": [reference(first)]}
    counts, decrypted = observe(service, monkeypatch)
    recall_context.attach(service, owner, space, [selected])
    assert selected["evidence_context"] == (
        {"resource_title": "Evidence", "incomplete": True}
        if current_excluded or historical_excluded
        else {"resource_title": "Evidence", "source_titles": ["Cited title"]}
    )
    assert decrypted == (
        [(first["id"], 2, "metadata")]
        if current_excluded
        else [
            (first["id"], 2, "metadata"),
            (first["id"], 1, "metadata"),
        ]
    )
    assert counts["unwraps"] == (1 if current_excluded else 2)
    assert counts["scopes"] == 2


def test_foreign_missing_and_generated_sources_never_reach_decryption(commands, monkeypatch):
    service, owner, space, _ = commands
    stranger = replace(owner, subject=str(uuid4()))
    foreign_space = service.bootstrap(stranger)["id"]
    foreign = source(service, stranger, foreign_space, "Foreign source title")
    memory = remember(service, owner, space)
    selected = {
        "title": "Evidence",
        "sources": [
            reference(foreign),
            {"resource_id": str(uuid4()), "revision": 1},
            reference(memory),
        ],
    }
    counts, decrypted = observe(service, monkeypatch)
    recall_context.attach(service, owner, space, [selected])
    assert selected["evidence_context"] == {"resource_title": "Evidence", "incomplete": True}
    assert decrypted == [] and counts["unwraps"] == 0
    assert counts["scopes"] == 2


def test_mixed_sources_filter_hidden_citations_and_never_substitute_missing_revisions(
    commands,
    monkeypatch,
):
    service, owner, space, _ = commands
    hidden = source(service, owner, space, "Hidden historical title")
    source(service, owner, space, "Excluded current head", previous=hidden, excluded=True)
    visible = source(service, owner, space, "Visible cited title")
    source(service, owner, space, "Visible current title", previous=visible)
    selected = {
        "title": "Evidence",
        "sources": [
            reference(hidden),
            reference(visible),
            {"resource_id": visible["id"], "revision": 999},
        ],
    }
    counts, decrypted = observe(service, monkeypatch)
    recall_context.attach(service, owner, space, [selected])
    assert selected["evidence_context"] == {
        "resource_title": "Evidence",
        "source_titles": ["Visible cited title"],
        "incomplete": True,
    }
    assert decrypted == [
        (hidden["id"], 2, "metadata"),
        (visible["id"], 2, "metadata"),
        (visible["id"], 1, "metadata"),
    ]
    assert counts["scopes"] == 2


@pytest.mark.parametrize("boundary", ["foreign_space", "wrong_environment"])
def test_attach_rejects_unauthorized_scope_before_decryption(commands, monkeypatch, boundary):
    service, owner, space, _ = commands
    project = label(service, owner, space, "project", "Private project")
    first = source(service, owner, space, "Private source")
    if boundary == "foreign_space":
        actor = replace(owner, subject=str(uuid4()))
        service.bootstrap(actor)
    else:
        actor = delegated(service, owner)
        service.resource_url = "https://another-environment.invalid/mcp/"
    _, decrypted = observe(service, monkeypatch)
    selected = {
        "title": "Evidence",
        "_context_project": project["id"],
        "sources": [reference(first)],
    }
    with pytest.raises(CommandError) as error:
        recall_context.attach(service, actor, space, [selected])
    assert error.value.status == (404 if boundary == "foreign_space" else 403)
    assert decrypted == [] and "evidence_context" not in selected


@pytest.mark.parametrize("moment", ["before_current_read", "after_current_read"])
def test_source_head_changes_do_not_replace_exact_citations(commands, monkeypatch, moment):
    service, owner, space, _ = commands
    first = source(service, owner, space, "Cited historical title")
    original = service._item_rows
    changed = False

    def change_head(connection, scope, space_id, ids, **kwargs):
        nonlocal changed
        if changed:
            return original(connection, scope, space_id, ids, **kwargs)
        changed = True
        if moment == "before_current_read":
            source(service, owner, space, "Hidden current head", previous=first, excluded=True)
            return original(connection, scope, space_id, ids, **kwargs)
        rows = original(connection, scope, space_id, ids, **kwargs)
        source(service, owner, space, "Renamed current head", previous=first)
        return rows

    monkeypatch.setattr(service, "_item_rows", change_head)
    selected = {"title": "Evidence", "sources": [reference(first)], "stale": True}
    recall_context.attach(service, owner, space, [selected])
    assert changed and selected["stale"] is True
    assert selected["evidence_context"] == (
        {"resource_title": "Evidence", "incomplete": True}
        if moment == "before_current_read"
        else {"resource_title": "Evidence", "source_titles": ["Cited historical title"]}
    )


@pytest.mark.parametrize("stage", ["label", "cited_source"])
def test_revocation_during_decode_keeps_label_and_final_access_rechecks(
    commands,
    monkeypatch,
    stage,
):
    service, owner, space, _ = commands
    project = label(service, owner, space, "project", "Private project")
    first = source(service, owner, space, "Private cited source")
    source(service, owner, space, "New source head", previous=first)
    reader = delegated(service, owner)
    decrypt, recheck = service._unpack, service._recheck
    events = []

    def revoke_after_decrypt(key, scope, resource_id, revision, purpose, value):
        result = decrypt(key, scope, resource_id, revision, purpose, value)
        events.append((str(resource_id), revision, purpose))
        if (stage == "label" and purpose == "workspace_item") or (
            stage == "cited_source" and purpose == "metadata" and revision == 1
        ):
            service.revoke(owner, UUID(reader.client_id))
        return result

    def checked_recheck(identity, space_id):
        events.append("recheck")
        return recheck(identity, space_id)

    monkeypatch.setattr(service, "_unpack", revoke_after_decrypt)
    monkeypatch.setattr(service, "_recheck", checked_recheck)
    selected = {
        "title": "Evidence",
        "_context_project": project["id"],
        "sources": [reference(first)],
    }
    with pytest.raises(CommandError) as error:
        recall_context.attach(service, reader, space, [selected])
    assert error.value.status == 403
    if stage == "label":
        assert events == [(project["id"], 1, "workspace_item"), "recheck"]
    else:
        assert events == [
            (project["id"], 1, "workspace_item"),
            "recheck",
            (first["id"], 2, "metadata"),
            (first["id"], 1, "metadata"),
            "recheck",
        ]


def test_attach_keeps_label_and_source_limits_and_marks_omissions(commands, monkeypatch):
    service, owner, space, _ = commands
    categories = [label(service, owner, space, "category", f"Category {i}") for i in range(66)]
    sources = [source(service, owner, space, f"Source {i}") for i in range(51)]
    selected = [
        {
            "title": f"Evidence {i}",
            "classification": {"category_ids": [c["id"] for c in categories[i * 3 : i * 3 + 3]]},
            "sources": [reference(s) for s in sources[i * 3 : i * 3 + 3]],
        }
        for i in range(22)
    ]
    selected[0]["classification"]["category_ids"].append(categories[-1]["id"])
    selected[0]["sources"].append(reference(sources[-1]))
    counts, decrypted = observe(service, monkeypatch)
    recall_context.attach(service, owner, space, selected)
    for i, value in enumerate(selected):
        expected = {
            "resource_title": f"Evidence {i}",
            "categories": [f"Category {n}" for n in range(i * 3, min(i * 3 + 3, 64))],
        }
        if i < 17:
            expected["source_titles"] = [f"Source {n}" for n in range(i * 3, min(i * 3 + 3, 50))]
        if i in {0, 16, 21}:
            expected["incomplete"] = True
        assert value["evidence_context"] == expected
    assert Counter(purpose for _, _, purpose in decrypted) == {
        "workspace_item": 64,
        "metadata": 100,
    }
    assert counts["scopes"] == 3
