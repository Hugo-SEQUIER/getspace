"""Composition root: construct dependencies and mount the transport adapters."""

import asyncio
import hmac
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from space import __version__, oauth_server
from space.admission import AdmissionError
from space.api.mcp import create_mcp
from space.api.reference_docs import add_examples, route_options
from space.api.rest import create_routes
from space.brain import RetrievalConfig
from space.health_hosts import HealthCheckedHostMiddleware
from space.model_provider import MemoryModels
from space.oauth_server import OAuthTokens
from space.observability import RequestMetrics, database_health, database_pool_stats
from space.operational_errors import operation_unavailable
from space.providers import LocalKeys
from space.read_timing import collect_read_timings
from space.security.auth import TokenVerifier, Unauthenticated, owner_identity
from space.settings import CONFIG_FILE, Settings
from space.webhook_events import QUEUED_HEADER, watch_queued_deliveries
from space.workspace import CommandError, Database, Workspace

API_DESCRIPTION = """Build your own client for Space.

Create a personal API key in **Settings → API keys** and send it on every request as
`Authorization: Bearer spk_...`. A key acts as you in the one Space it was created for,
with your current role there. A read-only key can read and search; a read-write key can
also create, change and delete content. Keys cannot manage agents, keys, members,
restores or webhooks, and they do not work on the MCP endpoint.

Errors are JSON objects with an `error` message. Writes take an `expected_revision` or a
`command_id` where shown, so a retry never applies twice. Treat a key like a password:
keep it out of public web pages and revoke it in Settings if it leaks.
"""
API_TAGS = [
    {"name": "Space", "description": "The key's Space, its map, usage and activity."},
    {"name": "Documents and tasks", "description": "Documents, tasks and task columns."},
    {"name": "Projects, folders and databases", "description": "Workspace items and records."},
    {"name": "Memory", "description": "Memory claims, links and extraction jobs."},
    {"name": "Search and recall", "description": "Full-text search and cited recall."},
    {"name": "Export", "description": "Paged export of everything in the Space."},
    {"name": "Operations", "description": "Service liveness."},
]


def create_app(
    settings: Settings | None = None,
    *,
    workspace: Workspace | None = None,
    verifier: TokenVerifier | None = None,
) -> FastAPI:
    configuration = settings or Settings(_env_file=CONFIG_FILE)
    database = keys = None
    if workspace is None and configuration.database_url:
        database, keys = Database(configuration), LocalKeys()
        verifier = TokenVerifier(configuration)
        workspace = Workspace(
            database,
            keys,
            None,
            configuration.mcp_resource_url,
            configuration.web_url,
            MemoryModels(configuration),
            configuration.recall_concurrency,
            RetrievalConfig(
                ef_search=configuration.memory_ef_search,
                iterative_scan=configuration.memory_iterative_scan,
                score_floor=configuration.memory_score_floor,
                expanding=configuration.memory_expansion,
            ),
            recall_queue_size=configuration.recall_queue_size,
            recall_queue_timeout=configuration.recall_queue_timeout,
            memory_write_concurrency=configuration.memory_write_concurrency,
            memory_write_queue_size=configuration.memory_write_queue_size,
            memory_write_queue_timeout=configuration.memory_write_queue_timeout,
        )
    if workspace is not None and verifier is None:
        raise ValueError("Resource transports require a token verifier")
    if workspace is not None and getattr(verifier, "space_tokens", None) is None:
        from space.device_auth import SpaceTokens

        verifier.space_tokens = SpaceTokens(workspace.db, verifier.issuer)
    if workspace is not None and getattr(verifier, "api_keys", None) is None:
        from space.api_keys import ApiKeys

        verifier.api_keys = ApiKeys(workspace.db, verifier.issuer)
    # Key mode answers clients that sign in through a browser; private mode needs no token.
    oauth = workspace is not None and not getattr(verifier, "private", False)
    if oauth and getattr(verifier, "oauth_tokens", None) is None:
        verifier.oauth_tokens = OAuthTokens(workspace.db, verifier.issuer)
    mcp = create_mcp(configuration, workspace, verifier)
    mcp_app = mcp.streamable_http_app()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            if database:
                database.open()
                await asyncio.to_thread(database.adopt_embedding_width)
                # The installation has one implicit owner. Creating their Private Space at
                # startup is idempotent and lets agents connect before the website opens.
                await asyncio.to_thread(workspace.bootstrap, owner_identity())
            async with mcp.session_manager.run():
                yield
        finally:
            if database:
                await workspace.aclose()
                database.close()
                keys.close()

    # The published reference lists only the routes a personal API key may call. The
    # website renders it natively; Swagger UI stays a local development aid.
    app = FastAPI(
        title="Space API",
        version=__version__,
        description=API_DESCRIPTION,
        openapi_tags=API_TAGS,
        docs_url="/docs" if configuration.environment != "production" else None,
        redoc_url=None,
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )
    app.add_middleware(HealthCheckedHostMiddleware, allowed_hosts=configuration.allowed_hosts)
    metrics = RequestMetrics()

    @app.get(
        "/health/ready",
        tags=["Operations"],
        summary="Check readiness",
        **route_options("get", "/health/ready"),
    )
    def ready():
        try:
            if workspace is None:
                raise RuntimeError("Storage unavailable")
            database_health(workspace)
            return {"status": "ready"}
        except Exception:
            return JSONResponse({"status": "unavailable"}, 503)

    @app.get("/ops/metrics", include_in_schema=False)
    def operational_metrics(request: Request):
        supplied = request.headers.get("authorization", "")
        if not configuration.ops_token or not hmac.compare_digest(
            supplied.encode(), ("Bearer " + configuration.ops_token.get_secret_value()).encode()
        ):
            return JSONResponse({"error": "Not found"}, 404)
        return {
            "requests": metrics.snapshot(),
            "database": database_health(workspace),
            "database_pool": database_pool_stats(workspace),
        }

    @app.middleware("http")
    async def private_responses(request, call_next):
        started = time.monotonic()
        # Read at most a bounded body, including chunked requests, before parsing JSON.
        size, chunks = 0, []
        async for chunk in request.stream():
            size += len(chunk)
            if size > 1500000:
                metrics.record(413, time.monotonic() - started)
                return JSONResponse(
                    {"error": "Request exceeds the supported size"},
                    413,
                    headers={"Cache-Control": "no-store"},
                )
            chunks.append(chunk)
        request._body = b"".join(chunks)
        with collect_read_timings() as timings, watch_queued_deliveries() as queued:
            try:
                response = await call_next(request)
            except Exception as error:
                # Fixed categories only: exception strings may contain private SQL,
                # provider URLs, identifiers, or credentials.
                logging.getLogger(__name__).warning(
                    "space_api_failure: %s", operation_unavailable(error)
                )
                response = JSONResponse(
                    {
                        "error": "Service temporarily unavailable. Check local provider "
                        "access and retry."
                    },
                    503,
                )
            total = f"api_total;dur={(time.monotonic() - started) * 1000:.1f}"
            response.headers["Server-Timing"] = ", ".join(filter(None, [total, timings.header()]))
        if queued and response.status_code < 400:
            response.headers[QUEUED_HEADER] = "1"
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        metrics.record(response.status_code, time.monotonic() - started)
        return response

    @app.exception_handler(Unauthenticated)
    async def authentication_error(request, error):
        return JSONResponse({"error": str(error)}, 401, headers={"WWW-Authenticate": "Bearer"})

    @app.exception_handler(CommandError)
    async def command_error(request, error):
        return JSONResponse({"error": error.message}, error.status)

    @app.exception_handler(AdmissionError)
    async def admission_error(request, error):
        return JSONResponse(
            {"error": error.message}, error.status, headers={"Retry-After": str(error.retry_after)}
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        return JSONResponse({"error": "Invalid request fields, revision, or payload size"}, 422)

    if workspace:
        app.include_router(create_routes(workspace, verifier, configuration))
    if oauth:
        app.router.routes.extend(oauth_server.routes(workspace, configuration))

    @app.get(
        "/health/live",
        tags=["Operations"],
        summary="Check liveness",
        **route_options("get", "/health/live"),
    )
    def liveness():
        """Process liveness only: does not attest database readiness."""
        return {"status": "ok", "service": "space", "version": __version__}

    app.mount("/mcp", mcp_app)
    generate_schema = app.openapi

    def openapi():
        # The generator caches its document; examples are attached to it once.
        if app.openapi_schema is None:
            add_examples(generate_schema())
        return app.openapi_schema

    app.openapi = openapi
    if configuration.api_cors_origins:
        # Credentials travel only in the Authorization header, never cookies, so a
        # browser client on another origin gains nothing without its own key.
        app.add_middleware(
            CORSMiddleware,
            allow_origins=configuration.api_cors_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "PUT", "DELETE"],
            allow_headers=["Authorization", "Content-Type"],
            max_age=600,
        )
    return app
