"""Real resource reads: inline bodies, request timings and unchanged access boundaries."""

from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from test_auth import signing as signing_fixture
from test_database_setup import database as database_fixture
from test_workspace import change
from test_workspace import command_database as command_database_fixture
from test_workspace import commands as commands_fixture

from space import workspace_items, workspace_search
from space.app import create_app
from space.read_timing import collect_read_timings, read_stage, record_timing
from space.settings import Settings

database = database_fixture
command_database = command_database_fixture
commands = commands_fixture
signing = signing_fixture


def test_timings_are_isolated_and_follow_explicit_thread_context():
    def request(duration):
        with collect_read_timings() as timings:
            with ThreadPoolExecutor(max_workers=1) as worker:
                worker.submit(copy_context().run, record_timing, "object_get", duration).result()
            assert timings.header() == f"object_get;dur={duration * 1000:.1f}"
        record_timing("object_get", 99)
        assert timings.header() == f"object_get;dur={duration * 1000:.1f}"

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(request, [0.01, 0.02]))
    with collect_read_timings() as timings:
        with pytest.raises(RuntimeError), read_stage("space_key"):
            raise RuntimeError("private failure text")
        assert timings.header().startswith("space_key;dur=")
        assert "private" not in timings.header()
        with pytest.raises(ValueError, match="Unknown timing stage"):
            record_timing("private_identifier", 1)


@pytest.mark.parametrize("kind", ["document", "task"])
def test_inline_reads_never_schedule_an_object_request(commands, monkeypatch, kind):
    service, owner, space, _ = commands
    saved = service.save(owner, space, change().model_copy(update={"kind": kind}))
    monkeypatch.setattr(service.objects, "get", lambda *args: pytest.fail("Unexpected object read"))
    with collect_read_timings() as timings:
        assert service.get_resource(owner, space, UUID(saved["id"])) == saved
    assert "object_get" not in timings.durations


def test_key_failure_never_decodes_a_payload(commands, monkeypatch):
    service, owner, space, _ = commands
    saved = service.save(owner, space, change())

    def key(*args):
        raise RuntimeError("key unavailable")

    monkeypatch.setattr(service.keys, "unwrap", key)
    monkeypatch.setattr(service, "_unpack", lambda *args: pytest.fail("Decode after failure"))
    with pytest.raises(RuntimeError, match="key unavailable"):
        service.get_resource(owner, space, UUID(saved["id"]))


def test_rest_forwards_thread_timings_on_success_and_provider_failure(
    commands, signing, monkeypatch
):
    service, _, _, _ = commands
    verifier, token = signing
    browser_token = token()
    owner = verifier.verify(browser_token)
    space = UUID(service.bootstrap(owner)["id"])
    saved = service.save(owner, space, change())
    settings = Settings(environment="test", allowed_hosts=["testserver"])
    path = f"/v1/spaces/{space}/resources/{saved['id']}"
    with TestClient(create_app(settings, workspace=service, verifier=verifier)) as http:
        response = http.get(path, headers={"Authorization": "Bearer " + browser_token})
        assert response.status_code == 200
        for stage in ("api_total", "db_pool", "db_read", "space_key", "db_recheck"):
            assert stage + ";dur=" in response.headers["server-timing"]
        assert saved["title"] not in response.headers["server-timing"]
        assert response.headers["cache-control"] == "no-store"

        def fail(*args):
            raise RuntimeError("private upstream failure")

        monkeypatch.setattr(service.keys, "unwrap", fail)
        failed = http.get(path, headers={"Authorization": "Bearer " + browser_token})
        assert failed.status_code == 503
        assert "space_key;dur=" in failed.headers["server-timing"]
        assert "private upstream" not in failed.text
        denied = http.get(path)
        assert denied.status_code == 401
        assert "space_key" not in denied.headers["server-timing"]
        missing = http.get(
            f"/v1/spaces/{space}/resources/{uuid4()}",
            headers={"Authorization": "Bearer " + browser_token},
        )
        assert missing.status_code == 404
        assert "space_key" not in missing.headers["server-timing"]


def test_page_reads_carry_their_keys_without_a_separate_key_query(commands, monkeypatch):
    service, owner, space, _ = commands
    saved = service.save(owner, space, change())
    monkeypatch.setattr(service, "_key_row", lambda *args: pytest.fail("Extra key query"))
    listed = service.list_resources(owner, space, "document")
    assert [item["id"] for item in listed["items"]] == [saved["id"]]
    page = workspace_search.index(service, owner, space)
    assert [entry["id"] for entry in page["items"]] == [saved["id"]]
    assert workspace_items.listing(service, owner, space, "project")["items"] == []
