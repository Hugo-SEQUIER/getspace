"""POST memory writes bypass generation and commit organized, encrypted, indexed text."""

from dataclasses import replace
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from test_auth import signing as signing_fixture
from test_database_setup import database as database_fixture
from test_memory_classification_workspace import item
from test_memory_write_efficiency import provider
from test_workspace import command_database as command_database_fixture
from test_workspace import commands as commands_fixture

from space import memory_pipeline
from space.app import create_app
from space.memory import RecallRequest
from space.settings import Settings

database = database_fixture
command_database = command_database_fixture
commands = commands_fixture
signing = signing_fixture


def test_post_remember_uses_only_jev_and_embedding_and_is_ready_on_return(
    commands, signing, monkeypatch
):
    service = commands[0]
    verifier, token = signing
    bearer = token()
    owner = verifier.verify(bearer)
    space = UUID(service.bootstrap(owner)["id"])
    section = item(service, owner, space, "section", "Work")
    category = item(service, owner, space, "category", "Development")
    project = item(service, owner, space, "project", "Atlas", section_id=section["id"])
    service.memory_models, requests = provider(monkeypatch, match=True)

    def no_extraction(*args, **kwargs):
        raise AssertionError("POST remember must never run text extraction")

    monkeypatch.setattr(memory_pipeline, "build", no_extraction)
    settings = Settings(environment="test", allowed_hosts=["testserver"])
    payload = {
        "command_id": str(uuid4()),
        "title": "Atlas decision",
        "text": "  Atlas uses PostgreSQL.\nKeep this exact wording.  ",
        "importance": 3,
        "topics": ["Development"],
    }
    with TestClient(create_app(settings, workspace=service, verifier=verifier)) as http:
        endpoint = f"/v1/spaces/{space}/memory/remember"
        auth = {"Authorization": "Bearer " + bearer}
        result = http.post(endpoint, headers=auth, json=payload)
        assert result.status_code == 200, result.text
        saved = result.json()
        claim = saved["memory"]["claims"][0]
        assert claim["value"] == payload["text"]
        assert claim["index"] == payload["title"]
        assert claim["importance"] == 3
        assert claim["classification"]["project_ids"] == [project["id"]]
        assert claim["classification"]["category_ids"] == [category["id"]]
        assert claim["classification"]["section_ids"] == [section["id"]]
        assert len(requests) == 2
        assert requests[0]["input"] == [payload["text"]]
        assert requests[1]["state"]["memory"]["text"] == payload["text"]
        assert http.post(endpoint, headers=auth, json=payload).json() == saved
        assert (
            http.post(endpoint, headers=auth, json={**payload, "text": "Changed"}).status_code
            == 409
        )
        assert len(requests) == 2, "Retries must not repeat provider requests"
        listed = http.get(f"/v1/spaces/{space}/memories", headers=auth).json()
        assert listed["items"][0]["claims"][0]["value"] == payload["text"]
        assert http.get(f"/v1/spaces/{space}/memory/jobs", headers=auth).json()["items"] == []

    with service.db.transaction(owner) as connection:
        service._scope(connection, owner, space)
        row = connection.execute(
            "SELECT encrypted_record FROM space.resource_revisions WHERE resource_id=%s",
            (saved["id"],),
        ).fetchone()
        assert payload["text"].encode() not in bytes(row["encrypted_record"])
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM space.memory_claims WHERE memory_id=%s",
                (saved["id"],),
            ).fetchone()["n"]
            == 1
        )
    # Exercise the committed semantic index without introducing an unrelated answer assessor.
    service.retrieval = replace(service.retrieval, assess_relevance=False)
    recalled = service.context(owner, space, RecallRequest(query="PostgreSQL", strategy="semantic"))
    assert any(entry["id"] == saved["id"] for entry in recalled["items"])
