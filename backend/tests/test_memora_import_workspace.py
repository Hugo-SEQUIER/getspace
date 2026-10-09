"""Import through the real REST routes into disposable PostgreSQL using synthetic data."""

from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from test_database_setup import database as database_fixture
from test_memora_import import export, record
from test_memora_pipeline import Models
from test_memora_workspace import ciphertexts, embeds
from test_workspace import command_database as command_database_fixture
from test_workspace import commands as commands_fixture

from space.api.rest import create_routes
from space.import_memora import SpaceClient, apply_plan
from space.memora_import import ImportProblem, plan_import
from space.settings import Settings
from space.workspace import CommandError

database = database_fixture
command_database = command_database_fixture
commands = commands_fixture


@pytest.mark.parametrize("lose_response", [False, True])
def test_real_import_preserves_provenance_indexes_encrypts_and_resumes(commands, lose_response):
    service, owner, space, _ = commands
    service.memory_models = Models()
    app = FastAPI()
    app.include_router(
        create_routes(
            service, SimpleNamespace(verify=lambda _: owner), Settings(environment="test")
        )
    )

    @app.exception_handler(CommandError)
    async def command_error(_request: Request, error: CommandError):
        return JSONResponse({"error": str(error)}, status_code=error.status)

    source_record = record()
    plan = plan_import(export(source_record), "fixture-store", space_id=space)
    with TestClient(app) as http:

        def route(request):
            nonlocal lose_response
            response = http.request(
                request.method,
                request.url.raw_path.decode(),
                content=request.content,
                headers=dict(request.headers),
            )
            if lose_response and request.url.path.endswith("/memory/claims"):
                assert response.status_code == 200, response.text
                lose_response = False
                raise httpx.ReadTimeout("Lost response after commit", request=request)
            return httpx.Response(response.status_code, json=response.json())

        client = SpaceClient(
            "https://space.test", "fixture-token", transport=httpx.MockTransport(route)
        )
        try:
            if lose_response:
                with pytest.raises(ImportProblem, match="Stopped"):
                    apply_plan(plan, client)
            assert apply_plan(plan, client)["memories_processed"] == 1
            embedded = list(embeds(service))
            assert len(embedded) == 1
            assert apply_plan(plan, client)["memories_processed"] == 1
            assert embeds(service) == embedded

            memories = service.list_memories(owner, space)["items"]
            assert len(memories) == 1
            assert memories[0]["import_origin"] == "memora"
            saved = service.get_resource(owner, space, UUID(memories[0]["id"]))
            assert saved["memory"]["claims"][0]["value"] == source_record["metadata"]["value"]
            assert saved["recall"]["topics"] == [source_record["namespace"]]
            assert saved["recall"]["sources"] == []
            provenance = saved["memory"]["provenance"]
            assert provenance["source_id"] == "fixture-store"
            assert provenance["record"] == {
                k: v for k, v in source_record.items() if k != "vector_f32_b64"
            }
            assert service.list_resources(owner, space, "document")["items"] == []
            assert any(b"Mira" in value for value in ciphertexts(service, owner, space))
            with service.db.transaction(owner) as connection:
                service._scope(connection, owner, space)
                assert (
                    connection.execute("SELECT count(*) AS n FROM space.memory_claims").fetchone()[
                        "n"
                    ]
                    == 1
                )
                assert (
                    connection.execute("SELECT count(*) AS n FROM space.resources").fetchone()["n"]
                    == 1
                )

            source_record["metadata"]["value"] = "Changed text under the same original ID."
            with pytest.raises(ImportProblem, match="409"):
                apply_plan(
                    plan_import(export(source_record), "fixture-store", space_id=space), client
                )
            assert embeds(service) == embedded
        finally:
            client.close()
