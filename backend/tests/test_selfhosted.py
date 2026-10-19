"""A self-hosted installation end to end, against a disposable local PostgreSQL.

Runs the real installer, then the real application with the production verifier over
REST and the MCP endpoint: the access key, website-issued agent tokens, readable storage,
unlimited usage, private mode and the workers' idle queue checks.
"""

import os
import secrets
import shutil
import subprocess
import time
from types import SimpleNamespace
from urllib.parse import quote
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from space import job_worker, webhook_worker
from space.app import create_app
from space.database_setup import InstallSettings
from space.install import install, wait_for_database
from space.queue_watch import QueueWatch
from space.settings import Settings

ACCESS_KEY = "synthetic-access-key-" + "k" * 32
LOGINS = ("space_app", "space_worker", "space_webhook")


@pytest.fixture(scope="module")
def server():
    if os.getenv("SPACE_RUN_DATABASE_TESTS") != "1":
        pytest.skip("Set SPACE_RUN_DATABASE_TESTS=1 to run isolated local PostgreSQL tests")
    docker = shutil.which("docker")
    if docker is None:
        pytest.fail("Docker is required for the opted-in database tests")
    name = "space-install-test-" + uuid4().hex[:12]
    admin = secrets.token_urlsafe(24)
    subprocess.run(  # noqa: S603 - fixed local disposable Docker command, no shell
        [
            docker,
            "run",
            "--rm",
            "--detach",
            "--pull",
            "never",
            "--name",
            name,
            "--publish",
            "127.0.0.1::5432",
            "--tmpfs",
            "/var/lib/postgresql/data:rw,nosuid,size=256m",
            "--env",
            "POSTGRES_PASSWORD=" + admin,
            "--env",
            "POSTGRES_DB=space",
            "pgvector/pgvector:pg17",
        ],
        check=True,
        capture_output=True,
    )
    try:
        address = subprocess.check_output(  # noqa: S603
            [docker, "port", name, "5432/tcp"], text=True
        ).strip()
        port = int(address.rsplit(":", 1)[1])

        def url(user, password):
            return f"postgresql://{user}:{quote(password, safe='')}@127.0.0.1:{port}/space"

        passwords = {login: secrets.token_urlsafe(24) for login in LOGINS}

        def install_settings(**overrides):
            values = {
                "migration_database_url": url("postgres", admin),
                "database_url": url("space_app", passwords["space_app"]),
                "job_database_url": url("space_worker", passwords["space_worker"]),
                "webhook_database_url": url("space_webhook", passwords["space_webhook"]),
                "database_sslmode": "disable",
            }
            return InstallSettings(_env_file=None, **(values | overrides))

        def app_settings(**overrides):
            values = {
                "environment": "test",
                "database_url": url("space_app", passwords["space_app"]),
                "database_sslmode": "disable",
                "allowed_hosts": ["testserver"],
                "web_url": "http://testserver",
                "mcp_resource_url": "http://testserver/mcp/",
                "access_key": ACCESS_KEY,
            }
            return Settings(_env_file=None, **(values | overrides))

        settings = install_settings()
        wait_for_database(settings, 30)
        # PostgreSQL restarts once after initialisation; retry the first install briefly.
        deadline = time.monotonic() + 30
        while True:
            try:
                install(settings)
                break
            except psycopg.OperationalError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.5)

        def admin_connection():
            return psycopg.connect(url("postgres", admin), sslmode="disable")

        yield SimpleNamespace(
            url=url,
            passwords=passwords,
            install_settings=install_settings,
            app_settings=app_settings,
            admin=admin_connection,
        )
    finally:
        subprocess.run(  # noqa: S603 - only remove the test container created above
            [docker, "rm", "--force", name], check=False, capture_output=True
        )


def mcp(http, name, arguments, headers=None):
    response = http.post(
        "/mcp/",
        headers={"Accept": "application/json, text/event-stream", **(headers or {})},
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
    )
    return response


def document(title, body):
    return {
        "command_id": str(uuid4()),
        "expected_revision": 0,
        "kind": "document",
        "title": title,
        "body": body,
    }


def test_install_migrates_to_head_and_follows_configured_passwords(server):
    with server.admin() as connection:
        head = connection.execute("SELECT version_num FROM space.alembic_version").fetchone()[0]
        assert head == "0038_export_snapshots"
        assert connection.execute(
            "SELECT count(*) FROM pg_roles WHERE rolname = ANY(%s)", (list(LOGINS),)
        ).fetchone()[0] == len(LOGINS)
    # Running it again changes nothing and still verifies every login.
    install(server.install_settings())
    rotated = secrets.token_urlsafe(24)
    install(server.install_settings(database_url=server.url("space_app", rotated)))
    with psycopg.connect(server.url("space_app", rotated), sslmode="disable") as runtime:
        assert runtime.execute("SELECT current_user").fetchone()[0] == "space_app"
    with pytest.raises(psycopg.OperationalError):
        psycopg.connect(server.url("space_app", server.passwords["space_app"]), sslmode="disable")
    install(server.install_settings())


def test_owner_key_agent_tokens_and_readable_storage(server):
    with TestClient(create_app(server.app_settings())) as http:
        assert http.get("/v1/workspace").status_code == 401
        wrong = {"Authorization": "Bearer " + "x" * 40}
        assert http.get("/v1/workspace", headers=wrong).status_code == 401
        owner = {"Authorization": "Bearer " + ACCESS_KEY}
        info = http.get("/v1/workspace", headers=owner)
        assert info.status_code == 200, info.text
        space = info.json()["id"]
        assert info.json()["mcp_url"] == "http://testserver/mcp/"

        created = http.post(
            "/v1/agent-tokens",
            headers=owner,
            json={"name": "Claude Code", "harness": "claude-code", "can_write": True},
        )
        assert created.status_code == 200, created.text
        token = created.json()["access_token"]
        assert token.startswith("spc_") and created.json()["mcp_url"] == "http://testserver/mcp/"
        agent = {"Authorization": "Bearer " + token}
        assert http.get("/v1/workspace", headers=agent).status_code == 401
        assert mcp(http, "workspace_info", {}, owner).status_code == 401

        identified = mcp(
            http, "identify_agent", {"name": "Claude Code", "harness": "claude-code"}, agent
        )
        assert identified.status_code == 200, identified.text
        agent_id = identified.json()["result"]["structuredContent"]["agent"]["id"]
        agent["X-Space-Agent-ID"] = agent_id
        saved = mcp(
            http,
            "save_resource",
            {"space_id": space, "change": document("Plan", "Readable body 512")},
            agent,
        ).json()["result"]
        assert saved.get("isError") is not True, saved
        resource = saved["structuredContent"]["id"]
        read = http.get(f"/v1/spaces/{space}/resources/{resource}", headers=owner).json()
        assert read["body"] == "Readable body 512" and read["author"] == "agent"

        usage = http.get(f"/v1/spaces/{space}/usage", headers=owner).json()
        assert usage["storage_limit_bytes"] is None and usage["plan"] is None
        assert usage["items"] >= 1

        connections = http.get("/v1/agents", headers=owner).json()
        client = next(item["client_id"] for item in connections["items"] if item.get("client_id"))
        assert http.delete(f"/v1/agents/{client}", headers=owner).status_code == 200
        revoked = mcp(http, "workspace_info", {}, agent)
        assert revoked.status_code == 401

    with server.admin() as connection:
        stored = connection.execute(
            "SELECT convert_from(encrypted_record, 'UTF8')::jsonb ->> 'body' "
            "FROM space.resource_revisions WHERE resource_id=%s",
            (resource,),
        ).fetchone()[0]
        assert stored == "Readable body 512"
        assert (
            connection.execute(
                "SELECT count(*) FROM space.resource_revisions WHERE object_key IS NOT NULL"
            ).fetchone()[0]
            == 0
        )


def test_private_mode_needs_no_credentials_and_separates_agents(server):
    settings = server.app_settings(access_mode="private", access_key=None)
    with TestClient(create_app(settings)) as http:
        info = http.get("/v1/workspace")
        assert info.status_code == 200, info.text
        space = info.json()["id"]

        unidentified = mcp(
            http, "save_resource", {"space_id": space, "change": document("A", "B")}
        ).json()["result"]
        assert unidentified["isError"] is True
        assert "identif" in str(unidentified).lower()

        agents = {}
        for name, harness in (("Codex", "codex"), ("Claude Code", "claude-code")):
            result = mcp(http, "identify_agent", {"name": name, "harness": harness})
            assert result.status_code == 200, result.text
            agents[harness] = result.json()["result"]["structuredContent"]["agent"]["id"]
        assert agents["codex"] != agents["claude-code"]
        # Identifying again reuses the identity.
        again = mcp(http, "identify_agent", {"name": "Codex", "harness": "codex"})
        assert again.json()["result"]["structuredContent"]["agent"]["id"] == agents["codex"]

        authors = set()
        for harness, agent_id in agents.items():
            saved = mcp(
                http,
                "save_resource",
                {"space_id": space, "change": document(f"From {harness}", "Shared plan")},
                {"X-Space-Agent-ID": agent_id},
            ).json()["result"]
            assert saved.get("isError") is not True, saved
            authors.add(saved["structuredContent"]["author_client_id"])
        assert len(authors) == 2


@pytest.mark.parametrize(
    ("module", "login"),
    [(job_worker, "space_worker"), (webhook_worker, "space_webhook")],
)
def test_worker_idle_checks_run_as_their_restricted_login(server, module, login):
    watch = QueueWatch(
        server.url(login, server.passwords[login]),
        {"sslmode": "disable"},
        module.restrict_connection,
        module.PENDING_SQL,
    )
    try:
        assert watch.pending() is False
        assert watch.failing is False
    finally:
        watch.close()


def test_empty_memory_index_takes_the_configured_embedding_width(server, monkeypatch):
    from space import brain

    monkeypatch.setattr(brain, "EMBEDDING_DIM", brain.EMBEDDING_DIM)
    install(server.install_settings(memory_embedding_dimensions=768))
    with server.admin() as connection:
        assert connection.execute(brain.DIMENSIONS_SQL).fetchone()[0] == 768
        assert (
            connection.execute(
                "SELECT indexdef FROM pg_indexes WHERE indexname='memory_claims_embedding_idx'"
            )
            .fetchone()[0]
            .endswith("USING hnsw (embedding space.vector_cosine_ops)")
        )
    with TestClient(create_app(server.app_settings())):
        assert brain.EMBEDDING_DIM == 768
    install(server.install_settings())
    with server.admin() as connection:
        assert connection.execute(brain.DIMENSIONS_SQL).fetchone()[0] == 1536
