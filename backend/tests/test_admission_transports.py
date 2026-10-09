"""Exercise bounded memory admission through real REST, MCP, and sync commands."""

import asyncio
import threading
import time
from contextlib import asynccontextmanager, contextmanager
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from space.app import create_app
from space.memory import ChangeMemory, RecallRequest, RememberMemory
from space.memory_pipeline import BuildMemory
from space.security import auth
from space.security.auth import Identity, TokenVerifier, Unauthenticated
from space.settings import Settings
from space.workspace import Workspace

SPACE = uuid4()
RESOURCE = "http://127.0.0.1:8000/mcp"
SAVED = {"id": str(uuid4()), "revision": 1}
RECALL = RecallRequest(query="release", strategy="lexical")


def request_for(operation):
    if operation == "remember":
        return RememberMemory(command_id=uuid4(), title="Release", text="Keep the release plan.")
    if operation == "build":
        return BuildMemory(command_id=uuid4(), source={"resource_id": uuid4(), "revision": 1})
    if operation == "change":
        return ChangeMemory(
            command_id=uuid4(),
            resource_id=uuid4(),
            expected_revision=1,
            entry_id=uuid4(),
            action="forget",
        )
    return RECALL


def method_for(workspace, operation):
    return getattr(
        workspace, {"build": "build_memory", "change": "change_memory"}.get(operation, operation)
    )


def fixture_app(monkeypatch, *, queue_size=0, timeout=3, database=None):
    settings = Settings(environment="test", allowed_hosts=["testserver"])
    workspace = Workspace(
        database,
        None,
        None,
        RESOURCE,
        recall_concurrency=1,
        recall_queue_size=queue_size,
        recall_queue_timeout=timeout,
        memory_write_concurrency=1,
        memory_write_queue_size=queue_size,
        memory_write_queue_timeout=timeout,
    )
    verifier = TokenVerifier(settings)
    expiry = int(time.time()) + 600
    identities = {
        "browser": Identity(verifier.issuer, str(uuid4()), expiry),
        "agent": Identity(verifier.issuer, str(uuid4()), expiry, str(uuid4()), str(uuid4())),
    }

    def verify(token, *, mcp=False):
        identity = identities[token]
        if mcp != bool(identity.client_id):
            raise Unauthenticated("Wrong transport audience")
        identity.check()
        return identity

    monkeypatch.setattr(verifier, "verify", verify)
    app = create_app(settings, workspace=workspace, verifier=verifier)
    return workspace, identities, app


def stub_bounded_reads(monkeypatch, workspace, *, stub_key=True):
    """Parameters: monkeypatch, the workspace and whether to stub its key read.

    What it does: supplies empty source and lexical candidate reads without a database.
    The optional key stub leaves real memory-write authorization checks intact.
    Output: transport fixtures that still exercise admission and grant rechecks.
    """
    if stub_key:
        key = SimpleNamespace(key=SimpleNamespace(material=b"\x00" * 32))
        monkeypatch.setattr(workspace, "_key", lambda *_: ({}, key))
    monkeypatch.setattr(workspace, "_recall_state", lambda *_: ("fixture snapshot", False))
    monkeypatch.setattr(workspace, "_source_items", lambda *_: ([], {}, {}))
    monkeypatch.setattr(workspace, "_items_for", lambda *_: ([], {}, {}))
    monkeypatch.setattr("space.recall_candidates.lexical", lambda *_args, **_kwargs: [])


@asynccontextmanager
async def client_for(app, workspace):
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app), base_url="http://testserver"
            ) as client:
                yield client
    finally:
        # create_app does not own an injected Workspace's lifecycle.
        await workspace.aclose()


async def rest(client, operation, request):
    suffix = "context" if operation == "context" else f"memory/{operation}"
    return await client.post(
        f"/v1/spaces/{SPACE}/{suffix}",
        headers={"Authorization": "Bearer browser"},
        json=request.model_dump(mode="json"),
    )


async def mcp(client, operation, request):
    name = {"context": "get_context", "build": "build_memory", "change": "change_memory"}.get(
        operation, operation
    )
    argument = "request" if operation == "context" else "change"
    response = await client.post(
        "/mcp/",
        headers={
            "Authorization": "Bearer agent",
            "Accept": "application/json, text/event-stream",
        },
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": name,
                "arguments": {"space_id": str(SPACE), argument: request.model_dump(mode="json")},
            },
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["result"]


async def started(event):
    assert await asyncio.wait_for(asyncio.to_thread(event.wait, 3), 4)


def assert_rest_busy(response):
    assert response.status_code == 503, response.text
    assert response.headers["Retry-After"] == "1"
    assert "busy" in response.json()["error"]
    assert "backoff" in response.json()["error"]


def assert_mcp_busy(result):
    assert result["isError"] is True
    text = " ".join(part.get("text", "") for part in result["content"])
    assert "busy" in text and "1 second" in text and "backoff" in text


@pytest.mark.parametrize("queue_size,timeout", [(0, 3), (1, 0.03)], ids=["full", "timeout"])
def test_write_limits_are_shared_by_sync_rest_and_mcp_without_stalling_reads(
    monkeypatch, queue_size, timeout
):
    async def exercise():
        workspace, identities, app = fixture_app(
            monkeypatch, queue_size=queue_size, timeout=timeout
        )
        entered, release = threading.Event(), threading.Event()
        operations = []

        def retry(identity, space_id, operation, request):
            operations.append(operation)
            if operation == "remember":
                entered.set()
                assert release.wait(5)
            return b"fingerprint", SAVED

        monkeypatch.setattr(workspace, "_memory_retry", retry)
        monkeypatch.setattr(workspace, "_recheck", lambda *_: None)
        monkeypatch.setattr(workspace, "_memory_items", lambda *_: ([], {}, {}, []))
        stub_bounded_reads(monkeypatch, workspace)
        monkeypatch.setattr(workspace, "info", lambda _: {"id": str(SPACE)})
        async with client_for(app, workspace) as client:
            holding = asyncio.create_task(
                asyncio.to_thread(
                    workspace.remember, identities["browser"], SPACE, request_for("remember")
                )
            )
            try:
                await started(entered)
                assert_rest_busy(
                    await asyncio.wait_for(rest(client, "build", request_for("build")), 2)
                )
                assert_mcp_busy(
                    await asyncio.wait_for(mcp(client, "change", request_for("change")), 2)
                )
                assert operations == ["remember"], "Rejected work must not enter the command body"

                info = await asyncio.wait_for(
                    client.get("/v1/workspace", headers={"Authorization": "Bearer browser"}), 2
                )
                assert info.status_code == 200
                recalled = await asyncio.wait_for(rest(client, "context", RECALL), 2)
                assert recalled.status_code == 200 and recalled.json()["found"] is False
                assert (await asyncio.wait_for(mcp(client, "context", RECALL), 2))[
                    "structuredContent"
                ]["found"] is False
                assert not release.is_set(), "Reads must finish while the write is still running"

                release.set()
                assert await asyncio.wait_for(holding, 2) == SAVED
                assert (await rest(client, "build", request_for("build"))).json() == SAVED
                assert (await mcp(client, "change", request_for("change")))[
                    "structuredContent"
                ] == SAVED
                assert operations == ["remember", "build", "change"]
            finally:
                release.set()
                await asyncio.gather(holding, return_exceptions=True)

    asyncio.run(exercise())


@pytest.mark.parametrize("queue_size,timeout", [(0, 3), (1, 0.03)], ids=["full", "timeout"])
def test_recall_limits_are_shared_by_sync_rest_and_mcp(monkeypatch, queue_size, timeout):
    async def exercise():
        workspace, identities, app = fixture_app(
            monkeypatch, queue_size=queue_size, timeout=timeout
        )
        entered, release = threading.Event(), threading.Event()
        holder = Identity(identities["browser"].issuer, str(uuid4()), int(time.time()) + 3600)
        bodies = []

        def recheck(identity, space_id):
            if identity.subject == holder.subject:
                entered.set()
                assert release.wait(5)
            else:
                bodies.append(identity.subject)

        monkeypatch.setattr(workspace, "_recheck", recheck)
        monkeypatch.setattr(workspace, "_memory_items", lambda *_: ([], {}, {}, []))
        stub_bounded_reads(monkeypatch, workspace)
        async with client_for(app, workspace) as client:
            holding = asyncio.create_task(
                asyncio.to_thread(workspace.context, holder, SPACE, RECALL)
            )
            try:
                await started(entered)
                assert_rest_busy(await asyncio.wait_for(rest(client, "context", RECALL), 2))
                assert_mcp_busy(await asyncio.wait_for(mcp(client, "context", RECALL), 2))
                assert bodies == []
                release.set()
                assert (await asyncio.wait_for(holding, 2))["found"] is False
                assert (await rest(client, "context", RECALL)).status_code == 200
                assert (await mcp(client, "context", RECALL))["structuredContent"]["found"] is False
            finally:
                release.set()
                await asyncio.gather(holding, return_exceptions=True)

    asyncio.run(exercise())


class ScopeDatabase:
    """Only supply authorization rows; real Workspace._scope decides access."""

    def __init__(self):
        self.grant_active = True

    @contextmanager
    def transaction(self, identity):
        yield self

    def execute(self, query, parameters=None):
        if "FROM space.resolve_space(" in query:
            row = {
                "organization_id": uuid4(),
                "space_id": SPACE,
                "principal_id": uuid4(),
                "kind": "personal",
                "role": "owner",
            }
        elif "FROM space.oauth_grants" in query:
            row = {"can_write": True} if self.grant_active else None
        elif "set_config" in query:
            row = None
        else:
            raise AssertionError(f"Queued unauthorized work reached storage: {query}")
        return SimpleNamespace(fetchone=lambda: row)


@pytest.mark.parametrize("operation", ["remember", "build", "change", "context"])
@pytest.mark.parametrize("invalidation", ["expired", "revoked"])
def test_queued_commands_recheck_identity_and_grants_before_memory_work(
    monkeypatch, operation, invalidation
):
    async def exercise():
        database = ScopeDatabase()
        workspace, identities, app = fixture_app(monkeypatch, queue_size=1, database=database)
        entered, release = threading.Event(), threading.Event()
        admitted = asyncio.Event()
        holder = Identity(identities["browser"].issuer, str(uuid4()), int(time.time()) + 3600)
        work = []
        retry = workspace._memory_retry
        recheck = workspace._recheck

        def stalled_retry(identity, space_id, operation, request):
            if identity.subject != holder.subject:
                return retry(identity, space_id, operation, request)
            entered.set()
            assert release.wait(5)
            return b"fingerprint", SAVED

        def stalled_recheck(identity, space_id):
            if identity.subject != holder.subject:
                return recheck(identity, space_id)
            entered.set()
            assert release.wait(5)

        def memory_items(identity, space_id):
            work.append(identity.subject)
            return [], {}, {}, []

        monkeypatch.setattr(workspace, "_memory_retry", stalled_retry)
        monkeypatch.setattr(workspace, "_recheck", stalled_recheck)
        monkeypatch.setattr(workspace, "_memory_items", memory_items)
        stub_bounded_reads(monkeypatch, workspace, stub_key=operation == "context")

        def recall_state(identity, space_id):
            work.append(identity.subject)
            return "fixture snapshot", False

        monkeypatch.setattr(workspace, "_recall_state", recall_state)
        gate = workspace._recall_admission if operation == "context" else workspace._write_admission
        arun = gate.arun

        async def observed_admission(*args, **kwargs):
            admitted.set()
            # The actual gate reserves before its first await, so the observer
            # cannot resume until this request has entered admission.
            return await arun(*args, **kwargs)

        monkeypatch.setattr(gate, "arun", observed_admission)
        async with client_for(app, workspace) as client:
            holding = asyncio.create_task(
                asyncio.to_thread(
                    method_for(workspace, operation), holder, SPACE, request_for(operation)
                )
            )
            queued = None
            try:
                await started(entered)
                request = request_for(operation)
                queued = asyncio.create_task(
                    rest(client, operation, request)
                    if invalidation == "expired"
                    else mcp(client, operation, request)
                )
                await asyncio.wait_for(admitted.wait(), 2)
                assert not queued.done()
                if invalidation == "expired":
                    clock = identities["browser"].expires_at + 1
                    monkeypatch.setattr(auth, "time", SimpleNamespace(time=lambda: clock))
                else:
                    database.grant_active = False
                release.set()
                result = await asyncio.wait_for(queued, 2)
                if invalidation == "expired":
                    assert result.status_code == 401, result.text
                else:
                    assert result["isError"] is True
                    assert "revoked" in " ".join(part.get("text", "") for part in result["content"])
                await asyncio.wait_for(holding, 2)
                assert all(subject == holder.subject for subject in work)
                if operation == "context":
                    assert work == [holder.subject]
            finally:
                release.set()
                await asyncio.gather(
                    *[task for task in (holding, queued) if task is not None],
                    return_exceptions=True,
                )

    asyncio.run(exercise())
