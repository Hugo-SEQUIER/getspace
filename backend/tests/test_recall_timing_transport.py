"""Parameters: synthetic identities and the real MCP transport.

What it does: checks opt-in recall timing isolation and authorization failures.
Output: regression assertions without real providers, storage or credentials.
"""

import asyncio

from test_admission_transports import SPACE, client_for, fixture_app

from space.read_timing import STAGES, collect_read_timings, record_timing
from space.workspace import CommandError


async def recall(client, query, *, include_timings=None, token="agent"):  # noqa: S107
    arguments = {"space_id": str(SPACE), "request": {"query": query, "strategy": "lexical"}}
    if include_timings is not None:
        arguments["include_timings"] = include_timings
    headers = {"Accept": "application/json, text/event-stream"}
    if token:
        headers["Authorization"] = "Bearer " + token
    return await client.post(
        "/mcp/",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "get_context", "arguments": arguments},
        },
    )


def test_mcp_recall_timings_are_opt_in_numeric_and_request_local(monkeypatch):
    async def exercise():
        workspace, identities, app = fixture_app(monkeypatch)
        evidence = {"found": False, "items": [], "engine": "fixture"}

        def context(actor, space_id, request):
            assert actor.subject == identities["agent"].subject
            assert space_id == SPACE
            if request.query == "first":
                record_timing("db_transaction", 0.0125)
                record_timing("embedding", 0.004)
            else:
                record_timing("object_get", 0.023)
            return evidence

        monkeypatch.setattr(workspace, "context", context)
        async with client_for(app, workspace) as client:
            for option in (None, False):
                ordinary = await recall(client, "first", include_timings=option)
                assert ordinary.status_code == 200
                assert ordinary.json()["result"]["structuredContent"] == evidence
            first = await recall(client, "first", include_timings=True)
            result = first.json()["result"]["structuredContent"]
            assert result["timings_ms"] == {"db_transaction": 12.5, "embedding": 4.0}
            assert {
                k: v for k, v in result.items() if k not in {"timings_ms", "tool_total_ms"}
            } == evidence
            assert set(result["timings_ms"]) <= STAGES
            assert all(type(value) in {int, float} for value in result["timings_ms"].values())
            assert type(result["tool_total_ms"]) in {int, float} and result["tool_total_ms"] >= 0
            second = await recall(client, "second", include_timings=True)
            assert second.json()["result"]["structuredContent"]["timings_ms"] == {
                "object_get": 23.0
            }
            assert evidence == {"found": False, "items": [], "engine": "fixture"}

    asyncio.run(exercise())


def test_mcp_recall_timings_do_not_bypass_auth_or_disclose_error_timings(monkeypatch):
    async def exercise():
        workspace, _identities, app = fixture_app(monkeypatch)
        calls = []

        def forbidden(actor, space_id, request):
            calls.append(request.query)
            record_timing("space_key", 0.1)
            raise CommandError("Forbidden", 403)

        monkeypatch.setattr(workspace, "context", forbidden)
        async with client_for(app, workspace) as client:
            anonymous = await recall(client, "anonymous", include_timings=True, token=None)
            assert anonymous.status_code == 401 and calls == []
            denied = await recall(client, "denied", include_timings=True)
            result = denied.json()["result"]
            assert result["isError"] is True and calls == ["denied"]
            assert "timings_ms" not in str(result) and "tool_total_ms" not in str(result)

            def following(*_args):
                record_timing("db_transaction", 0.002)
                return {"found": False, "items": []}

            monkeypatch.setattr(workspace, "context", following)
            clean = await recall(client, "following", include_timings=True)
            assert clean.json()["result"]["structuredContent"]["timings_ms"] == {
                "db_transaction": 2.0
            }

    asyncio.run(exercise())


def test_timing_snapshot_is_detached():
    with collect_read_timings() as timings:
        record_timing("space_key", 0.0123)
        snapshot = timings.snapshot()
        snapshot["space_key"] = -1
        snapshot["private"] = "not a stage"
        assert timings.snapshot() == {"space_key": 12.3}
        assert timings.header() == "space_key;dur=12.3"
