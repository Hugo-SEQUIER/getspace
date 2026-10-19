"""Parameters: document Markdown containing local image data.
What it does: checks image-free indexing, excerpts, assessment and extraction inputs.
Output: focused regressions; saved resource bodies remain unchanged.
"""

import json
from contextlib import nullcontext
from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_memora_pipeline import Models, source
from test_memory import item, resource, url
from test_relevance import Assessor
from test_relevance import item as evidence_item

from space import memory_pipeline, relevance, workspace_search
from space.memory import RecallRequest, bounded_result, document_text, excerpt, rank_lexical
from space.model_provider import ModelError
from space.providers import ScopedKey
from space.security.payloads import SpaceKey
from space.terms import resource_fingerprints, term_key
from space.workspace import SaveResource, Workspace


@pytest.mark.parametrize("mime", ["png", "jpeg", "webp", "gif"])
def test_supported_images_keep_descriptions_and_surrounding_markdown(mime):
    body = f"# Notes\n\nBefore ![Review screenshot](data:image/{mime};base64,QUFB/) after."
    assert document_text(body) == "# Notes\n\nBefore Review screenshot after."


def test_multiple_images_and_empty_descriptions_are_removed_without_changing_other_links():
    body = "![First](data:image/png;base64,QUFB)\n![](data:image/gif;base64,R0lG) tail"
    assert document_text(body) == "First\n tail"
    unchanged = (
        "![Remote](https://example.com/image.png) "
        "[Link](data:image/png;base64,QUFB) "
        "![Vector](data:image/svg+xml;base64,QUFB) "
        "![Text](data:text/plain;base64,QUFB)"
    )
    assert document_text(unchanged) == unchanged


def test_encoded_image_bytes_do_not_count_as_source_tokens_or_change_lexical_scores():
    body = "Before ![Review screenshot](data:image/png;base64," + "QUF/" * 6000 + ") milestone."
    readable = "Before Review screenshot milestone."
    key = term_key(b"k" * 32)
    assert resource_fingerprints("Notes", ["review"], body, key) == resource_fingerprints(
        "Notes", ["review"], readable, key
    )
    original = resource("Notes", body)
    snapshot = deepcopy(original)
    ranked = rank_lexical([(original, body)], "milestone screenshot")
    baseline = rank_lexical([(original, readable)], "milestone screenshot")
    assert ranked[0][2] == baseline[0][2]
    assert ranked[0][1] == body
    assert rank_lexical([(original, body)], "QUF base64 png") == []
    assert original == snapshot


@pytest.mark.parametrize("compact", [False, True])
def test_recall_excerpts_keep_text_after_large_images_and_leave_the_body_unchanged(compact):
    body = "Before.\n\n![Review screenshot](data:image/webp;base64," + "QUFB" * 6000
    body += ")\n\nRelease milestone complete."
    readable = "Before.\n\nReview screenshot\n\nRelease milestone complete."
    original = resource("Notes", body, category="handoff" if compact else "source")
    snapshot = deepcopy(original)
    assert excerpt(body, "milestone", 80) == (readable, True)
    request = (
        RecallRequest(mode="resume", compact=True)
        if compact
        else RecallRequest(query="milestone", max_chars=2000)
    )
    result = bounded_result([item(original)], request, url)
    assert result["items"][0]["excerpt"] == readable
    assert result["items"][0]["truncated"]
    assert original == snapshot


@pytest.mark.parametrize("repetitions", [1, 6000])
def test_image_candidates_use_stripped_model_inputs_without_invalid_raw_passage_offsets(
    repetitions,
):
    body = "Before ![Review screenshot](data:image/png;base64," + "QUFB" * repetitions
    body += ") milestone complete."
    readable = "Before Review screenshot milestone complete."
    original = evidence_item(body=body)
    original.pop("entry_id")
    snapshot = deepcopy(original)
    model = Assessor([{"candidate": 0, "relevance": "direct"}])
    model.settings = SimpleNamespace(memory_relevance_provider="decision")
    diagnostics = {}
    selected = relevance.select([original], "milestone", model, diagnostics=diagnostics)
    assert model.calls[0]["candidates"][0]["text"] == readable
    assert selected[0]["body"] == readable and selected[0]["evidence_truncated"]
    assert "passage" not in selected[0]
    assert selected[0]["sources"] == original["sources"]
    assert selected[0]["revision"] == original["revision"]
    assert diagnostics["route"] == "chat" and diagnostics["fallback_reason"] == "unsafe_structure"
    assert original == snapshot


def test_extraction_limits_readable_text_and_never_sends_image_bytes_to_the_builder():
    body = "Before ![Review screenshot](data:image/jpeg;base64," + "QUFB" * 6000
    body += ") milestone complete."
    original = source() | {"body": body}
    snapshot = deepcopy(original)
    models = Models()
    built = memory_pipeline.build(
        original, uuid4(), models.session(lambda: None), models.embedding_fingerprint
    )
    segments = [
        call[2]["segment_content"]["text"]
        for call in models.calls
        if call[0] == "model" and call[2] and "segment_content" in call[2]
    ]
    assert segments == [original["title"] + "\nBefore Review screenshot milestone complete."]
    assert built["value"]["claims"] and original == snapshot
    assert "data:image/" not in json.dumps(models.calls)
    before = len(models.calls)
    with pytest.raises(ModelError, match="12,000"):
        memory_pipeline.build(
            original | {"body": body + "x" * 12001},
            uuid4(),
            models.session(lambda: None),
            models.embedding_fingerprint,
        )
    assert len(models.calls) == before


@pytest.mark.parametrize("query", ["", "screenshot", "milestone", "QUFB", "base64"])
def test_workspace_search_matches_readable_source_text_and_never_shows_encoded_snippets(
    monkeypatch,
    query,
):
    body = "Before ![Review screenshot](data:image/png;base64," + "QUFB" * 6000
    body += ") milestone complete."
    original = resource("Notes", body)
    snapshot = deepcopy(original)
    resource_id = uuid4()
    row = {"cursor": f"r:{resource_id}", "id": resource_id, "revision": 1}
    version = {
        "resource_id": resource_id,
        "revision": 1,
        "kind": "document",
        "key_id": "key",
        "wrapping_provider": "test",
        "wrapping_key_reference": "test",
        "wrapped_key": b"",
        "object_key": None,
        "encrypted_record": b"",
        "created_at": datetime.now(UTC),
        "author_client_id": None,
        "author_principal_id": None,
    }
    results = {
        workspace_search.CORPUS: [row],
        workspace_search.RESOURCES: [version],
    }
    connection = SimpleNamespace(
        execute=lambda sql, *args: SimpleNamespace(
            fetchall=lambda: results[sql], fetchone=lambda: {"content_revision": 7}
        )
    )
    workspace = SimpleNamespace(
        db=SimpleNamespace(transaction=lambda identity: nullcontext(connection)),
        _scope=lambda *args: {"organization_id": None, "space_id": None, "principal_id": None},
        _key_rows=Workspace._key_rows,
        _unwrap_key=lambda *args: None,
        _unpack=lambda *args: original,
        _summary=Workspace._summary,
    )
    monkeypatch.setattr(workspace_search, "require_snapshot", lambda *args: 7)
    result = workspace_search.search(workspace, None, None, workspace_search.Search(query=query))
    if query in {"QUFB", "base64"}:
        assert result["items"] == []
    else:
        assert result["items"][0]["description"] == "Before Review screenshot milestone complete."
    assert original == snapshot


def test_encrypted_source_body_round_trips_through_public_resource_reads(monkeypatch):
    scope = {"organization_id": uuid4(), "space_id": uuid4(), "principal_id": uuid4()}
    resource_id = uuid4()
    key = ScopedKey(scope["organization_id"], scope["space_id"], SpaceKey(str(uuid4()), b"k" * 32))
    body = "Before ![Review screenshot](data:image/png;base64," + "QUFB" * 6000 + ") after."
    request = SaveResource(
        command_id=uuid4(), expected_revision=0, kind="document", title="Notes", body=body
    )
    workspace = Workspace.__new__(Workspace)
    payload = workspace._pack(
        key, scope, resource_id, 1, "source", {"title": request.title, "body": request.body}
    )
    row = {
        "resource_id": resource_id,
        "kind": "document",
        "revision": 1,
        "key_id": key.key.key_id,
        "wrapping_provider": "fixture",
        "wrapping_key_reference": "fixture-key",
        "wrapped_key": b"wrapped",
        "object_key": None,
        "encrypted_record": payload,
        "created_at": datetime.now(UTC),
        "author_client_id": None,
    }
    connection = SimpleNamespace(execute=lambda *args: SimpleNamespace(fetchone=lambda: row))
    workspace.db = SimpleNamespace(transaction=lambda identity: nullcontext(connection))
    monkeypatch.setattr(workspace, "_scope", lambda *args: scope)
    monkeypatch.setattr(workspace, "_unwrap_key", lambda *args: key)
    for revision in (None, 1):
        read = workspace.get_resource(None, scope["space_id"], resource_id, revision)
        assert read["body"] == request.body == body
        assert document_text(read["body"]) == "Before Review screenshot after."
