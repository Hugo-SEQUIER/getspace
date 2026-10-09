from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from test_auth import signing as signing_fixture
from test_database_setup import database as database_fixture
from test_memora_workspace import claim_change, setup
from test_restore_staging import stage
from test_teams import create_team, join
from test_workspace import change, delegated
from test_workspace import command_database as command_database_fixture
from test_workspace import commands as commands_fixture
from test_workspace_archive import read_archive

from space import overview
from space import restore_merge as merge
from space.app import create_app
from space.memory import RecallRequest
from space.retention import purge
from space.security.auth import Unauthenticated
from space.settings import Settings
from space.workspace import CommandError, SaveResource

database = database_fixture
command_database = command_database_fixture
commands = commands_fixture
signing = signing_fixture


def test_counts_lines_and_idempotent_writes(commands):
    service, owner, space, _ = commands
    actor = delegated(service, owner, write=True)
    request = change().model_copy(update={"body": "First line\nSecond line\n"})
    saved = service.save(actor, space, request)
    service.save(actor, space, request)
    value = overview.snapshot(service, owner, space)
    assert value["totals"] == {
        "documents": 1,
        "tasks": 0,
        "memories": 0,
        "document_lines": 2,
        "measured_documents": 1,
    }
    assert value["summary"]["document_writes"] == 1
    assert value["summary"]["documents_created"] == 1
    assert str(value["agents"][0]["agent_id"]) == actor.agent_id
    service.save(
        owner,
        space,
        SaveResource(
            command_id=uuid4(),
            resource_id=UUID(saved["id"]),
            expected_revision=1,
            kind="document",
            title="Edited",
            body="One line",
        ),
    )
    value = overview.snapshot(service, owner, space, 7)
    assert value["totals"]["documents"] == 1
    assert value["totals"]["document_lines"] == 1
    assert value["summary"]["document_writes"] == 2
    assert value["summary"]["documents_created"] == 1
    assert {row["actor_type"] for row in value["agents"]} == {"website", "agent"}
    assert sum(row["document_writes"] or 0 for row in value["activity"]) == 2


def test_retrieval_results_errors_timing_and_unique_reuse(commands):
    service, owner, space, _ = commands
    service.save(owner, space, change())
    service.context(owner, space, RecallRequest(query="Private title", strategy="lexical"))
    service.context(owner, space, RecallRequest(query="nonexistentxyz", strategy="lexical"))
    with pytest.raises(CommandError):
        service.context(owner, space, RecallRequest(query="private", strategy="semantic"))
    claim = str(uuid4())
    result = {"items": [{"entry_id": claim}, {"entry_id": claim}]}
    overview.record_retrieval(service, owner, space, RecallRequest(query="secret"), 0.2, result)
    overview.record_retrieval(service, owner, space, RecallRequest(query="secret"), 0.4, result)
    value = overview.snapshot(service, owner, space)
    summary = value["summary"]
    assert summary["retrievals"] == 5
    assert summary["with_results"] == 3
    assert summary["empty"] == 1
    assert summary["errors"] == 1
    assert summary["reused_memories"] == 1
    assert 0 <= summary["p50_ms"] <= summary["p95_ms"]
    assert sum(row["retrievals"] for row in value["agents"]) == 5
    assert sum(row["retrievals"] for row in value["strategies"]) == 5
    assert sum(row["retrievals"] or 0 for row in value["activity"]) == 5
    assert "secret" not in str(value)


def test_periods_missing_history_and_legacy_line_counts(commands):
    service, owner, space, connect = commands
    saved = service.save(owner, space, change())
    with connect() as connection:
        connection.execute(
            "UPDATE space.resources SET content_lines=NULL WHERE id=%s", (saved["id"],)
        )
        connection.execute(
            "UPDATE space.spaces SET activity_started_at=%s WHERE id=%s",
            (datetime.now(UTC) - timedelta(days=10), space),
        )
        connection.execute(
            "UPDATE space.activity_events SET created_at=%s WHERE space_id=%s",
            (datetime.now(UTC) - timedelta(days=8), space),
        )
    week = overview.snapshot(service, owner, space, 7)
    month = overview.snapshot(service, owner, space, 30)
    assert week["summary"]["document_writes"] == 0
    assert week["summary"]["p50_ms"] is None
    assert month["summary"]["document_writes"] == 1
    assert month["activity"][0]["retrievals"] is None
    assert month["activity"][-1]["retrievals"] == 0
    assert month["totals"]["documents"] == 1
    assert month["totals"]["measured_documents"] == 0
    assert len(overview.snapshot(service, owner, space, 90)["activity"]) == 90
    with pytest.raises(CommandError):
        overview.snapshot(service, owner, space, 14)


def test_all_periods_match_individual_windows_including_exact_timing_and_reuse(commands):
    service, owner, space, connect = commands
    service.save(owner, space, change())
    shared, older = str(uuid4()), str(uuid4())
    for strategy, duration, claims in (
        ("auto", 0.1, [shared]),
        ("lexical", 0.9, [shared, older]),
        ("semantic", 4.0, [older]),
    ):
        overview.record_retrieval(
            service,
            owner,
            space,
            RecallRequest(query="private", strategy=strategy),
            duration,
            {"items": [{"entry_id": claim} for claim in claims]},
        )
    midnight = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    with connect() as connection:
        connection.execute(
            "UPDATE space.spaces SET activity_started_at=%s WHERE id=%s",
            (midnight - timedelta(days=100), space),
        )
        for strategy, age in (("auto", 6), ("lexical", 29), ("semantic", 89)):
            connection.execute(
                "UPDATE space.activity_events SET created_at=%s WHERE space_id=%s AND strategy=%s",
                (midnight - timedelta(days=age), space, strategy),
            )
    bundle = overview.snapshot(service, owner, space, all_periods=True)
    assert bundle["days"] == 90
    assert len(bundle["activity"]) == 90
    for days, count in ((7, 1), (30, 2), (90, 3)):
        single = overview.snapshot(service, owner, space, days)
        period = bundle["periods"][days]
        assert period["summary"]["retrievals"] == count
        for key in ("period_start", "summary", "agents", "strategies"):
            assert period[key] == single[key]
        assert bundle["activity"][-days:] == single["activity"]
    assert bundle["periods"][7]["summary"]["reused_memories"] == 1
    assert bundle["periods"][90]["summary"]["reused_memories"] == 2
    assert bundle["periods"][30]["summary"]["p50_ms"] == 500


def test_all_periods_empty_space_has_zero_counts_and_unknown_timing(commands):
    service, owner, space, _ = commands
    bundle = overview.snapshot(service, owner, space, all_periods=True)
    for period in bundle["periods"].values():
        assert period["summary"]["retrievals"] == 0
        assert period["summary"]["reused_memories"] == 0
        assert period["summary"]["p50_ms"] is None
        assert period["agents"] == []
        assert period["strategies"] == []


def test_overview_isolated_and_rejected_calls_do_not_record(commands):
    service, owner, space, _ = commands
    stranger = replace(owner, subject=str(uuid4()))
    other_space = UUID(service.bootstrap(stranger)["id"])
    service.save(owner, space, change())
    with service.db.transaction(owner) as connection:
        scope = service._scope(connection, owner, space)
    with service.db.transaction(stranger) as connection:
        service._set_scope(connection, scope["organization_id"], space)
        assert connection.execute("SELECT * FROM space.activity_events").fetchall() == []
    assert overview.snapshot(service, stranger, other_space)["totals"]["documents"] == 0
    with pytest.raises(CommandError):
        overview.snapshot(service, stranger, space)
    with pytest.raises(CommandError):
        service.context(stranger, space, RecallRequest(query="private"))
    actor = delegated(service, owner)
    with pytest.raises(Unauthenticated):
        overview.snapshot(service, actor, space)
    service.revoke(owner, UUID(actor.client_id))
    with pytest.raises(CommandError):
        service.context(actor, space, RecallRequest(query="private"))
    assert overview.snapshot(service, owner, space)["summary"]["retrievals"] == 0


def test_telemetry_failure_does_not_change_retrieval_result(commands, monkeypatch):
    service, owner, space, _ = commands
    service.save(owner, space, change())
    original = service._scope

    def broken_record(workspace, identity, space_id, request, elapsed, result):
        def fail(*args, **kwargs):
            raise RuntimeError("Telemetry database unavailable")

        monkeypatch.setattr(service, "_scope", fail)
        try:
            recorder(workspace, identity, space_id, request, elapsed, result)
        finally:
            monkeypatch.setattr(service, "_scope", original)

    recorder = overview.record_retrieval
    monkeypatch.setattr(overview, "record_retrieval", broken_record)
    result = service.context(owner, space, RecallRequest(query="private", strategy="lexical"))
    assert result["found"] is True


def test_memory_claim_counts_do_not_include_edits_or_retry_duplicates(commands):
    service, owner, space, _, request = setup(commands)
    saved = service.build_memory(owner, space, request)
    service.build_memory(owner, space, request)
    claim = saved["memory"]["claims"][0]["id"]
    service.change_memory(owner, space, claim_change(saved, claim, "edit", value="New commute"))
    value = overview.snapshot(service, owner, space)
    assert value["totals"]["documents"] == 1
    assert value["totals"]["memories"] == 2
    assert value["summary"]["memories_created"] == 2
    assert value["summary"]["memory_writes"] == 2


def test_team_viewers_can_read_overview_and_record_retrievals(commands):
    service, owner, personal, _ = commands
    team = create_team(service, owner)
    sid = UUID(team["id"])
    viewer, _ = join(service, owner, team, role="viewer")
    service.save(owner, sid, change())
    service.context(viewer, sid, RecallRequest(query="private", strategy="lexical"))
    value = overview.snapshot(service, viewer, sid)
    assert value["totals"]["documents"] == 1
    assert value["summary"]["retrievals"] == 1
    assert overview.snapshot(service, owner, personal)["summary"]["retrievals"] == 0


def test_overview_rest_period_validation_and_mcp_retrieval_tracking(commands, signing):
    service = commands[0]
    verifier, token = signing
    browser_token = token()
    owner = verifier.verify(browser_token)
    settings = Settings(environment="test", allowed_hosts=["testserver"])
    with TestClient(create_app(settings, workspace=service, verifier=verifier)) as http:
        auth = {"Authorization": "Bearer " + browser_token}
        space = http.post("/v1/workspace", headers=auth).json()["id"]
        agent = delegated(service, owner)
        headers = {
            "Authorization": "Bearer "
            + token(
                aud=verifier.resource, client_id=agent.client_id, space_grant_id=agent.grant_id
            ),
            "X-Space-Agent-ID": agent.agent_id,
            "Accept": "application/json, text/event-stream",
        }
        recalled = http.post(
            "/mcp/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "get_context",
                    "arguments": {
                        "space_id": space,
                        "request": {"query": "private", "strategy": "lexical"},
                    },
                },
            },
        ).json()["result"]
        assert not recalled.get("isError"), recalled
        for days in (7, 30, 90):
            response = http.get(f"/v1/spaces/{space}/overview?days={days}", headers=auth)
            assert response.status_code == 200, response.text
            assert response.json()["days"] == days
            assert response.json()["summary"]["empty"] == 1
            assert response.headers["Cache-Control"] == "no-store"
        assert http.get(f"/v1/spaces/{space}/overview?days=8", headers=auth).status_code == 400
        bundle = http.get(f"/v1/spaces/{space}/overview?days=90&all_periods=true", headers=auth)
        assert bundle.status_code == 200, bundle.text
        assert set(bundle.json()["periods"]) == {"7", "30", "90"}
        assert bundle.json()["periods"]["7"]["summary"]["empty"] == 1
        assert http.get(f"/v1/spaces/{space}/overview", headers=headers).status_code == 401
        assert http.get(f"/v1/spaces/{space}/overview").status_code == 401


def test_activity_retention_is_independent_of_command_retention(commands):
    service, owner, space, connect = commands
    service.save(owner, space, change())
    with connect() as connection:
        connection.execute(
            "UPDATE space.activity_events SET created_at=now()-interval '91 days' "
            "WHERE space_id=%s",
            (space,),
        )
    service.save(owner, space, change())
    with connect() as connection:
        assert purge(connection, days=180) == {"events": 0, "commands": 0, "activity_events": 1}
    assert overview.snapshot(service, owner, space)["summary"]["document_writes"] == 1


def test_restoring_a_document_recomputes_current_line_count(commands):
    service, owner, space, _ = commands
    saved = service.save(owner, space, change())
    archive = read_archive(service, owner, space)
    service.save(
        owner,
        space,
        change().model_copy(
            update={
                "resource_id": UUID(saved["id"]),
                "expected_revision": 1,
                "body": "one\ntwo\nthree",
            }
        ),
    )
    assert overview.snapshot(service, owner, space)["totals"]["document_lines"] == 3
    upload = stage(service, owner, space, archive)
    choices = {"resource:" + saved["id"]: "replace"}
    review = merge.review(service, owner, space, upload.id, merge.Review(choices=choices))
    merge.commit(
        service, owner, space, upload.id, merge.Commit(snapshot=review["snapshot"], choices=choices)
    )
    value = overview.snapshot(service, owner, space)
    assert value["totals"]["document_lines"] == 1
    assert value["totals"]["measured_documents"] == 1
    assert value["summary"]["document_writes"] == 2
