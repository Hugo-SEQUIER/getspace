"""Opt-in real Codex continuity proof using synthetic data and disposable PostgreSQL.

The clients run in separate empty directories, with fresh ephemeral sessions and
no user MCP configuration. Only the fixture Space endpoint is supplied. No source
content or expected answer is given to the second session.
"""

import json
import os
import shutil
import socket
import subprocess
import threading
import time
from uuid import UUID

import pytest
import uvicorn
from test_auth import signing as signing_fixture
from test_database_setup import database as database_fixture
from test_workspace import (
    command_database as command_database_fixture,
)
from test_workspace import (
    commands as commands_fixture,
)
from test_workspace import (
    delegated,
)

from space.app import create_app
from space.memory import RecallRequest
from space.settings import Settings

database = database_fixture
command_database = command_database_fixture
commands = commands_fixture
signing = signing_fixture

pytestmark = pytest.mark.skipif(
    os.getenv("SPACE_RUN_CODEX_MEMORY_TESTS") != "1",
    reason="Opt in with SPACE_RUN_CODEX_MEMORY_TESTS=1 and SPACE_RUN_DATABASE_TESTS=1",
)


def test_fresh_codex_recovers_decision_next_step_and_source(commands, signing, tmp_path):
    codex = shutil.which("codex")
    assert codex, "Codex CLI is required for this opt-in test"
    service = commands[0]
    verifier, token = signing
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    url = f"http://127.0.0.1:{port}/mcp/"
    service.resource_url = verifier.resource = url
    owner = verifier.verify(token())
    space = UUID(service.bootstrap(owner)["id"])
    writer = delegated(service, owner, write=True)
    reader = delegated(service, owner)
    settings = Settings(environment="test", mcp_resource_url=url)
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(settings, workspace=service, verifier=verifier),
            host="127.0.0.1",
            port=port,
            log_level="error",
            access_log=False,
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline, "Fixture MCP server did not start"
        time.sleep(0.05)

    def run_client(name, identity, prompt):
        directory = tmp_path / name
        directory.mkdir()
        headers = json.dumps(
            {
                "Authorization": "Bearer "
                + token(
                    aud=url,
                    client_id=identity.client_id,
                    space_grant_id=identity.grant_id,
                )
            }
        )
        # TOML inline tables use '=', unlike JSON objects. The only value is a
        # generated fixture JWT; it is never printed or written to the report.
        header_value = json.loads(headers)["Authorization"]
        command = [
            codex,
            "exec",
            "--ignore-user-config",
            "--ephemeral",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "-C",
            str(directory),
            "--json",
            "-c",
            f"mcp_servers.continuity.url={json.dumps(url)}",
            "-c",
            f"mcp_servers.continuity.http_headers={{Authorization={json.dumps(header_value)},"
            f"X-Space-Agent-ID={json.dumps(identity.agent_id)}}}",
            "-c",
            "features.shell_tool=false",
            "-c",
            "mcp_servers.continuity.startup_timeout_sec=20",
            "-",
        ]
        completed = subprocess.run(  # noqa: S603 - fixed executable and synthetic fixture arguments
            command,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=180,
        )
        # Persist only model events containing synthetic test data, never config/JWT.
        events = []
        for line in completed.stdout.splitlines():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        safe = json.dumps(events).replace(header_value, "[fixture authorization redacted]")
        if completed.returncode != 0:
            raise AssertionError(safe[-4000:])
        assert any("mcp" in str(event).lower() for event in events), safe[-4000:]
        answers = [
            event["item"]["text"]
            for event in events
            if event.get("type") == "item.completed"
            and event.get("item", {}).get("type") == "agent_message"
        ]
        assert answers, "Codex did not produce a final answer"
        return safe, answers[-1]

    try:
        run_client(
            "writer",
            writer,
            "Use only the continuity MCP tools. This is a synthetic acceptance test. "
            "Resolve my Space. Save a document titled 'Juniper release decision' saying: "
            "We chose a staged rollout for Juniper. The unresolved question is the launch date. "
            "Create a task titled 'Validate Juniper rollback' with status in_progress. "
            "Then explicitly save a session handoff: decision staged rollout; progress rollback "
            "validation started; unresolved launch date; next action validate rollback. "
            "Use topic Juniper and cite both saved resource IDs and their revisions as sources. "
            "Finish after confirming the handoff was saved. Do not use shell or other tools.",
        )
        expected = service.context(owner, space, RecallRequest(mode="resume"))
        assert expected["items"], "Writer did not save a retrievable handoff"
        note_id = expected["items"][0]["id"]
        output, answer = run_client(
            "reader",
            reader,
            "Where were we? Use the continuity MCP to recover our decisions, current work, "
            "unresolved questions and next steps. Include source links and revisions. "
            "Use only MCP tools; do not inspect files or use the shell.",
        )
        for required in ("Juniper", "staged", "rollback", "launch", note_id):
            assert required.casefold() in answer.casefold(), (
                f"Missing {required} in answer: {answer}"
            )
        assert "get_context" in output and "revision" in answer
        assert "/resources/" in answer
        assert any(status in answer.lower() for status in ("in progress", "in_progress", "started"))
        assert "save_handoff" not in output, "Reader must not mutate saved memory"
        report = {
            "result": "passed",
            "client": "Codex CLI, two fresh ephemeral processes",
            "engine": expected["engine"],
            "sources": len(expected["items"]),
            "storage": "Disposable PostgreSQL, encrypted fixture objects, process-only test keys",
            "checks": [
                "decision",
                "current task",
                "unresolved question",
                "next action",
                "source ID and revision",
            ],
            "reader_answer": answer,
        }
        print("CODEX_MEMORY_PROOF=" + json.dumps(report))
    finally:
        server.should_exit = True
        thread.join(timeout=10)
