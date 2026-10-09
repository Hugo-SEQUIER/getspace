import json
from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from test_auth import signing as signing_fixture
from test_database_setup import database as database_fixture
from test_memora_pipeline import Models
from test_workspace import (
    change,
    delegated,
)
from test_workspace import (
    command_database as command_database_fixture,
)
from test_workspace import (
    commands as commands_fixture,
)

from space.app import create_app
from space.memory import (
    RecallMetadata,
    RecallRequest,
    RememberMemory,
    SaveHandoff,
    SourceReference,
)
from space.settings import Settings
from space.workspace import CommandError, Workspace

database = database_fixture
command_database = command_database_fixture
commands = commands_fixture
signing = signing_fixture


def handoff(service, actor, space, sources=(), **kwargs):
    return service.save_handoff(
        actor,
        space,
        SaveHandoff(
            command_id=uuid4(),
            title="Agent memory handoff",
            summary="Memora pilot progress",
            decisions=["Keep canonical memory in Space"],
            progress=["Added explicit capture"],
            unresolved_questions=["Which embedding provider?"],
            next_actions=["Run fresh-session proof"],
            topics=["Memory"],
            sources=list(sources),
            **kwargs,
        ),
    )


def test_handoff_survives_new_workspace_and_independent_agent(commands):
    service, owner, space, _ = commands
    writer = delegated(service, owner, write=True)
    source = service.save(
        writer, space, change().model_copy(update={"kind": "task", "status": "in_progress"})
    )
    note = handoff(service, writer, space, [SourceReference(resource_id=source["id"], revision=1)])
    fresh = Workspace(
        service.db, service.keys, service.objects, service.resource_url, "https://space.test"
    )
    reader = delegated(fresh, owner)
    result = fresh.context(reader, space, RecallRequest(mode="resume"))
    assert [r["id"] for r in result["items"]] == [note["id"]]
    assert result["items"][0]["verification"] == "handoff"
    assert result["items"][0]["stale"] is False
    assert result["items"][0]["sources"][0]["revision"] == 1
    assert result["items"][0]["url"].startswith("https://space.test/resources/")
    # The handoff still cites the referenced task, which stays readable by ID.
    assert result["items"][0]["sources"][0]["resource_id"] == source["id"]
    assert fresh.get_resource(reader, space, UUID(source["id"]))["status"] == "in_progress"
    assert "Which embedding provider?" in result["items"][0]["excerpt"]
    with pytest.raises(CommandError) as error:
        handoff(fresh, reader, space)
    assert error.value.status == 403


def test_recall_on_an_empty_space_reports_absence_instead_of_inventing(commands):
    service, owner, space, _ = commands
    empty = service.context(owner, space, RecallRequest(query="quarterly revenue targets"))
    assert empty["found"] is False
    assert empty["items"] == []
    assert "no supported memory" in empty["note"]
    assert "Do not invent" in empty["note"]

    resumed = service.context(owner, space, RecallRequest(mode="resume"))
    assert resumed["found"] is False
    assert "nothing to resume" in resumed["note"]

    # A populated Space reports the opposite, so the flag is not always false.
    handoff(service, owner, space)
    matched = service.context(owner, space, RecallRequest(query="memory"))
    assert matched["found"] is True
    assert matched["items"] and "note" not in matched


def test_handoff_retries_sources_and_readable_storage(commands):
    service, owner, space, _ = commands
    ref = service.save(owner, space, change())
    request = SaveHandoff(
        command_id=uuid4(),
        title="Handoff secret 773",
        summary="Memory secret 779",
        topics=["Topic secret 991"],
        sources=[SourceReference(resource_id=ref["id"], revision=1)],
    )
    first = service.save_handoff(owner, space, request)
    assert service.save_handoff(owner, space, request) == first
    with pytest.raises(CommandError, match="already used"):
        service.save_handoff(owner, space, request.model_copy(update={"summary": "Changed"}))
    with service.db.transaction(owner) as connection:
        service._scope(connection, owner, space)
        before = connection.execute("SELECT count(*) AS n FROM space.events").fetchone()["n"]
        rows = connection.execute(
            "SELECT encrypted_metadata FROM space.resource_revisions"
        ).fetchall()
    service.context(owner, space, RecallRequest(query="secret"))
    stored = b"".join(bytes(row["encrypted_metadata"]) for row in rows)
    for text in (request.title, request.topics[0]):
        assert text.encode() in stored
    with service.db.transaction(owner) as connection:
        service._scope(connection, owner, space)
        assert (
            connection.execute("SELECT count(*) AS n FROM space.events").fetchone()["n"] == before
        )
    other = replace(owner, subject=str(uuid4()))
    other_space = UUID(service.bootstrap(other)["id"])
    with pytest.raises(CommandError) as error:
        handoff(service, other, other_space, request.sources)
    assert error.value.status == 404
    with pytest.raises(CommandError) as error:
        service.context(other, space, RecallRequest(mode="resume"))
    assert error.value.status == 404


def test_corrections_exclusions_and_dependent_notes_survive_reconstruction(commands):
    service, owner, space, _ = commands
    source = service.save(owner, space, change())
    note = handoff(service, owner, space, [SourceReference(resource_id=source["id"], revision=1)])
    dependent = handoff(
        service, owner, space, [SourceReference(resource_id=note["id"], revision=1)]
    )
    update = change().model_copy(
        update={
            "resource_id": UUID(source["id"]),
            "expected_revision": 1,
            "title": "Corrected decision 482",
            "body": "Corrected decision",
        }
    )
    service.save(owner, space, update)
    resumed = service.context(owner, space, RecallRequest(mode="resume"))["items"]
    assert {item["id"] for item in resumed} == {note["id"], dependent["id"]}
    # A moved source revision is reported on the citing item, never hidden.
    assert {item["id"] for item in resumed if item["stale"]} == {note["id"]}
    matched = service.context(owner, space, RecallRequest(query="Corrected"))["items"]
    assert [item["id"] for item in matched] == [source["id"]]
    assert matched[0]["stale"] is False and matched[0]["revision"] == 2
    assert matched[0]["excerpt"] == "Corrected decision"
    service.save(
        owner,
        space,
        update.model_copy(
            update={
                "command_id": uuid4(),
                "expected_revision": 2,
                "recall": RecallMetadata(excluded=True),
            }
        ),
    )
    fresh = Workspace(service.db, service.keys, service.objects, service.resource_url)
    assert fresh.context(owner, space, RecallRequest(query="Corrected"))["items"] == []
    # Exclusion controls recall, not authorized historical source inspection.
    assert fresh.get_resource(owner, space, UUID(source["id"]), 1)["body"] == change().body


def test_plain_document_edit_preserves_handoff_and_exclusion(commands):
    service, owner, space, _ = commands
    note = handoff(service, owner, space)
    update = change().model_copy(
        update={
            "resource_id": UUID(note["id"]),
            "expected_revision": 1,
            "body": "Manually corrected",
        }
    )
    saved = service.save(owner, space, update)
    assert saved["recall"]["category"] == "handoff"
    assert (
        service.context(owner, space, RecallRequest(mode="resume"))["items"][0]["excerpt"]
        == "Manually corrected"
    )
    # An exclusion set by one edit survives the next plain edit, whose request omits
    # the recall metadata that holds it.
    service.save(
        owner,
        space,
        update.model_copy(
            update={
                "command_id": uuid4(),
                "expected_revision": 2,
                "recall": RecallMetadata(category="handoff", excluded=True),
            }
        ),
    )
    excluded = service.save(
        owner,
        space,
        update.model_copy(update={"command_id": uuid4(), "expected_revision": 3}),
    )
    assert excluded["recall"]["category"] == "handoff"
    assert excluded["recall"]["excluded"] is True
    assert service.context(owner, space, RecallRequest(mode="resume"))["items"] == []


def test_revocation_during_recall_denies_stale_disclosure(commands, monkeypatch):
    service, owner, space, _ = commands
    handoff(service, owner, space)
    reader = delegated(service, owner)
    body = Workspace._body

    def revoke_while_reading(self, identity, space_id, item, keys, scope):
        content = body(self, identity, space_id, item, keys, scope)
        service.revoke(owner, UUID(reader.client_id))
        return content

    monkeypatch.setattr(Workspace, "_body", revoke_while_reading)
    with pytest.raises(CommandError) as error:
        service.context(reader, space, RecallRequest(mode="resume"))
    assert error.value.status == 403


@pytest.mark.parametrize("mutation", ["correct", "exclude"])
def test_concurrent_source_change_during_recall_returns_only_the_revision_it_read(
    commands, monkeypatch, mutation
):
    service, owner, space, _ = commands
    note = handoff(service, owner, space)
    reader = delegated(service, owner)
    body = Workspace._body
    changed = []

    def change_while_reading(self, identity, space_id, item, keys, scope):
        content = body(self, identity, space_id, item, keys, scope)
        if not changed:
            changed.append(True)
            update = {
                "resource_id": UUID(note["id"]),
                "expected_revision": 1,
                "body": "Corrected handoff",
            }
            if mutation == "exclude":
                update["recall"] = RecallMetadata(excluded=True)
            service.save(owner, space, change().model_copy(update=update))
        return content

    monkeypatch.setattr(Workspace, "_body", change_while_reading)
    result = service.context(reader, space, RecallRequest(mode="resume"))
    assert [item["id"] for item in result["items"]] == [note["id"]]
    assert "Memora pilot progress" in result["items"][0]["excerpt"]
    monkeypatch.undo()
    # The committed change governs every later read of the Space.
    following = service.context(reader, space, RecallRequest(mode="resume"))
    assert [item["id"] for item in following["items"]] == (
        [] if mutation == "exclude" else [note["id"]]
    )
    if mutation == "correct":
        assert following["items"][0]["excerpt"] == "Corrected handoff"


def test_capacity_and_content_free_failure(commands, monkeypatch):
    service, owner, space, _ = commands
    handoff(service, owner, space)
    service.memory_models = Models()
    service.remember(
        owner,
        space,
        RememberMemory(command_id=uuid4(), title="Capacity note", text="Dinner is at seven."),
    )
    # The browser no longer refuses a Space for holding too much: it pages instead. A page
    # bound of zero still returns the newest memory rather than failing, and never leaks
    # content beyond what the caller asked for.
    monkeypatch.setattr("space.workspace.MAX_MEMORY_LIST", 0)
    page = service.list_memories(owner, space)
    assert len(page["items"]) == 1
    assert page["next_cursor"] is None
    # Recall has no Space-size cap any more: candidates come from the term index. A bound
    # of zero therefore produces an honest empty result, never a broken call and never a
    # leak of what the Space holds.
    monkeypatch.setattr("space.workspace.MAX_LEXICAL_CANDIDATES", 0)
    monkeypatch.setattr("space.recall_candidates.MAX_LEXICAL_RESOURCES", 0)
    result = service.context(owner, space, RecallRequest(query="Dinner", strategy="lexical"))
    assert result["found"] is False
    assert result["note"]
    assert "Dinner is at seven" not in json.dumps(result)


@pytest.mark.parametrize("compact", [False, True])
def test_two_fresh_mcp_sessions_and_rest_return_same_context(commands, signing, compact):
    service = commands[0]
    verifier, token = signing
    browser_token = token()
    owner = verifier.verify(browser_token)
    space = UUID(service.bootstrap(owner)["id"])
    writer = delegated(service, owner, write=True)
    reader = delegated(service, owner)
    settings = Settings(environment="test", allowed_hosts=["testserver"])

    def rpc(http, identity, name, arguments):
        headers = {
            "Authorization": "Bearer "
            + token(
                aud=verifier.resource,
                client_id=identity.client_id,
                space_grant_id=identity.grant_id,
            ),
            "X-Space-Agent-ID": identity.agent_id,
            "Accept": "application/json, text/event-stream",
        }
        result = http.post(
            "/mcp/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
        )
        assert result.status_code == 200, result.text
        return result.json()["result"]

    with TestClient(create_app(settings, workspace=service, verifier=verifier)) as http:
        saved = rpc(
            http,
            writer,
            "save_handoff",
            {
                "space_id": str(space),
                "change": {
                    "command_id": str(uuid4()),
                    "title": "Fresh MCP continuity",
                    "summary": "Chose encrypted Space storage",
                    "progress": ["Browser styling checked. " * 50] * 8,
                    "unresolved_questions": ["Zero-value acceptance is still unverified"],
                    "next_actions": ["Verify source citations"],
                    "topics": ["memory"],
                },
            },
        )["structuredContent"]
    fresh = Workspace(service.db, service.keys, service.objects, service.resource_url)
    with TestClient(create_app(settings, workspace=fresh, verifier=verifier)) as http:
        request = {"mode": "resume", "compact": compact}
        arguments = {"space_id": str(space), "request": request}
        recalled = rpc(http, reader, "get_context", arguments)["structuredContent"]
        assert recalled["items"][0]["id"] == saved["id"]
        assert ("Verify source citations" in recalled["items"][0]["excerpt"]) == compact
        assert ("still unverified" in recalled["items"][0]["excerpt"]) == compact
        assert recalled["items"][0]["truncated"]
        assert fresh.get_resource(reader, space, UUID(saved["id"]))["body"] == saved["body"]
        rest = http.post(
            f"/v1/spaces/{space}/context",
            headers={"Authorization": "Bearer " + browser_token},
            json=request,
        )
        assert rest.json() == recalled
        assert rest.headers["Cache-Control"] == "no-store"
        invalid = rpc(
            http,
            reader,
            "get_context",
            {"space_id": str(space), "request": {"mode": "PRIVATE-ERROR-CANARY"}},
        )
        assert invalid["isError"] and "PRIVATE-ERROR-CANARY" not in json.dumps(invalid)


def test_a_generated_memory_is_not_listed_as_a_document(commands):
    """A memory is stored as a document, but it is not one and must not be listed as one.

    The claim rows it owns are the readable marker, so the list can exclude it without
    decrypting metadata for rows it would then drop from the page.
    """
    service, owner, space, _ = commands
    writer = delegated(service, owner, write=True)
    document = service.save(writer, space, change())
    memory = handoff(
        service, writer, space, [SourceReference(resource_id=document["id"], revision=1)]
    )
    listed = [item["id"] for item in service.list_resources(writer, space, "document")["items"]]
    assert listed == [document["id"]]
    # Hidden from the list, not from the Space: both stay readable by ID.
    assert service.get_resource(writer, space, UUID(memory["id"]))["body"]
    assert service.get_resource(writer, space, UUID(document["id"]))["body"]


def test_a_space_of_memories_lists_no_documents(commands):
    """Memory is filtered in SQL, so a Space holding only memory lists nothing at all.

    The marker makes the list exact: no memory is decrypted only to be dropped, and an
    empty page is not followed by a cursor into rows that will never be documents.
    """
    service, owner, space, _ = commands
    writer = delegated(service, owner, write=True)
    for _ in range(51):
        handoff(service, writer, space)
    page = service.list_resources(writer, space, "document")
    assert page["items"] == []
    assert page["next_cursor"] is None
