"""The published API reference matches what the API returns.

Every operation in /openapi.json is called with a real read-write API key against real
PostgreSQL, and each response body is validated against the schema documented for its
status code. A new public route without a documented response, or a response that
grows an undocumented field, fails here.
"""

import json
import os
from uuid import UUID, uuid4

import jsonschema
from fastapi.testclient import TestClient
from test_auth import signing as signing_fixture
from test_database_setup import database as database_fixture
from test_memory_write_efficiency import provider
from test_workspace import command_database as command_database_fixture
from test_workspace import commands as commands_fixture

from space.app import create_app
from space.settings import Settings

database = database_fixture
command_database = command_database_fixture
commands = commands_fixture
signing = signing_fixture


class Recorder:
    """Calls the API and remembers each response under its documented operation."""

    def __init__(self, http, headers):
        self.http, self.headers, self.calls = http, headers, []

    def __call__(self, method, template, *, path=None, status=200, headers=None, **kwargs):
        response = self.http.request(
            method, path or template, headers=headers or self.headers, **kwargs
        )
        assert response.status_code == status, (method, template, response.text)
        self.calls.append(
            (method.lower(), template, response.status_code, response.json(), kwargs.get("json"))
        )
        return response.json()


def exercise(call, space):
    """Parameters: a recorder and the key's Space. What it does: walks every public
    operation in a realistic order. Output: none; the recorder keeps the responses."""
    s = f"/v1/spaces/{space}"
    t = "/v1/spaces/{space_id}"
    call("get", "/health/live")
    call("get", "/health/ready")
    call("get", "/v1/workspace")
    call("get", t + "/read-version", path=s + "/read-version")
    call("get", t + "/usage", path=s + "/usage")
    call("get", t + "/overview", path=s + "/overview")
    call("get", t + "/map", path=s + "/map")
    call("get", t + "/memory/provider", path=s + "/memory/provider")

    document = call(
        "post",
        t + "/resources",
        path=s + "/resources",
        json={
            "command_id": str(uuid4()),
            "expected_revision": 0,
            "kind": "document",
            "title": "Atlas launch plan",
            "body": "Atlas launches in November with PostgreSQL.",
        },
    )
    task = call(
        "post",
        t + "/resources",
        path=s + "/resources",
        json={
            "command_id": str(uuid4()),
            "expected_revision": 0,
            "kind": "task",
            "title": "Write the Atlas changelog",
            "body": "Summarize what changed.",
            "priority": "High",
        },
    )
    call("get", t + "/resources", path=s + "/resources", params={"kind": "document"})
    call("get", t + "/resources", path=s + "/resources", params={"kind": "task"})
    call(
        "get",
        t + "/resources/{resource_id}",
        path=f"{s}/resources/{document['id']}",
    )
    columns = call("get", t + "/tasks/columns", path=s + "/tasks/columns")
    columns = call(
        "post",
        t + "/tasks/columns",
        path=s + "/tasks/columns",
        json={
            "value": "review",
            "label": "Review",
            "command_id": str(uuid4()),
            "expected_revision": columns["revision"],
        },
    )
    task = call(
        "post",
        t + "/tasks/{resource_id}/status",
        path=f"{s}/tasks/{task['id']}/status",
        json={
            "command_id": str(uuid4()),
            "expected_revision": task["revision"],
            "status": "review",
        },
    )
    call(
        "post",
        t + "/handoffs",
        path=s + "/handoffs",
        json={
            "command_id": str(uuid4()),
            "title": "Atlas session",
            "summary": "Planned the launch.",
            "decisions": ["Use PostgreSQL"],
            "next_actions": ["Write the changelog"],
        },
    )
    call("post", t + "/search", path=s + "/search", json={"query": "Atlas"})
    page = call("post", t + "/export", path=s + "/export", json={})
    if page.get("next_cursor"):
        call(
            "post",
            t + "/export",
            path=s + "/export",
            json={"after": page["next_cursor"], "snapshot": page["snapshot"]},
        )

    project = call(
        "post",
        t + "/items",
        path=s + "/items",
        json={
            "command_id": str(uuid4()),
            "item_id": str(uuid4()),
            "expected_revision": 0,
            "kind": "project",
            "data": {"name": "Atlas", "description": "The Atlas launch."},
        },
    )
    leads = call(
        "post",
        t + "/items",
        path=s + "/items",
        json={
            "command_id": str(uuid4()),
            "item_id": str(uuid4()),
            "expected_revision": 0,
            "kind": "database",
            "data": {
                "name": "Leads",
                "fields": [
                    {"id": "company", "name": "Company", "type": "text"},
                    {"id": "value", "name": "Value", "type": "number"},
                ],
                "records": [
                    {
                        "id": "r1",
                        "name": "Acme",
                        "notes": "",
                        "updatedAt": "2026-09-01T00:00:00Z",
                        "values": {"company": "Acme Ltd", "value": 100},
                    }
                ],
            },
        },
    )
    call("get", t + "/items", path=s + "/items", params={"kind": "project"})
    call("get", t + "/items/{item_id}", path=f"{s}/items/{project['id']}")
    query = t + "/databases/{database_id}/query"
    call(
        "post",
        query,
        path=f"{s}/databases/{leads['id']}/query",
        json={
            "sql": "SELECT name, Value FROM records WHERE Value > ?",
            "params": [10],
            "include_schema": True,
        },
    )
    call(
        "post",
        query,
        path=f"{s}/databases/{leads['id']}/query",
        json={
            "sql": "UPDATE records SET Value = ? WHERE name = ?",
            "params": [150, "Acme"],
            "command_id": str(uuid4()),
            "expected_revision": leads["revision"],
        },
    )

    memory = call(
        "post",
        t + "/memory/remember",
        path=s + "/memory/remember",
        json={
            "command_id": str(uuid4()),
            "title": "Atlas database",
            "text": "Atlas uses PostgreSQL.",
            "importance": 2,
        },
    )
    claims = call(
        "post",
        t + "/memory/claims",
        path=s + "/memory/claims",
        json={
            "command_id": str(uuid4()),
            "title": "Atlas launch facts",
            "claims": [
                {"index": "Launch month", "value": "Atlas launches in November."},
                {"index": "Owner", "value": "Sam owns the Atlas launch.", "importance": 1},
            ],
        },
    )
    call("get", t + "/memories", path=s + "/memories")
    call("get", t + "/memories", path=s + "/memories", params={"query": "Atlas"})
    call("get", t + "/memories/{resource_id}", path=f"{s}/memories/{memory['id']}")
    linked = call(
        "put",
        t + "/memories/{resource_id}/links",
        path=f"{s}/memories/{memory['id']}/links",
        json={
            "command_id": str(uuid4()),
            "resource_id": memory["id"],
            "expected_revision": memory["revision"],
            "links": [{"resource_id": claims["id"], "relation": "related"}],
        },
    )
    call(
        "get",
        t + "/memories/{resource_id}/links",
        path=f"{s}/memories/{memory['id']}/links",
    )
    changed = call(
        "post",
        t + "/memory/change",
        path=s + "/memory/change",
        json={
            "command_id": str(uuid4()),
            "resource_id": claims["id"],
            "expected_revision": claims["revision"],
            "entry_id": claims["memory"]["claims"][0]["id"],
            "action": "edit",
            "value": "Atlas launches in early November.",
        },
    )
    call(
        "post",
        t + "/memory/creator",
        path=s + "/memory/creator",
        json={
            "command_id": str(uuid4()),
            "resource_id": changed["id"],
            "expected_revision": changed["revision"],
            "entry_ids": [changed["memory"]["claims"][1]["id"]],
            "creator_agent_id": None,
        },
    )
    call(
        "post",
        t + "/context",
        path=s + "/context",
        json={"query": "Which database does Atlas use?", "strategy": "lexical"},
    )
    call("post", t + "/context", path=s + "/context", json={"mode": "resume", "compact": True})
    job = call(
        "post",
        t + "/memory/jobs",
        path=s + "/memory/jobs",
        status=202,
        json={"command_id": str(uuid4()), "title": "Atlas notes", "text": "Atlas ships soon."},
    )
    call("get", t + "/memory/jobs", path=s + "/memory/jobs")
    call("get", t + "/memory/jobs/{job_id}", path=f"{s}/memory/jobs/{job['id']}")
    # Synchronous extraction needs a text model; a stale source shows its error shape.
    call(
        "post",
        t + "/memory/build",
        path=s + "/memory/build",
        status=409,
        json={
            "command_id": str(uuid4()),
            "source": {"resource_id": document["id"], "revision": document["revision"] + 5},
        },
    )
    call(
        "delete",
        t + "/memories/{resource_id}",
        path=f"{s}/memories/{linked['id']}",
        params={"expected_revision": linked["revision"]},
    )
    call(
        "delete",
        t + "/resources/{resource_id}",
        path=f"{s}/resources/{document['id']}",
        params={"expected_revision": document["revision"]},
    )
    # A second pass over the summaries once the Space holds structure and activity.
    section = call(
        "post",
        t + "/items",
        path=s + "/items",
        json={
            "command_id": str(uuid4()),
            "item_id": str(uuid4()),
            "expected_revision": 0,
            "kind": "section",
            "data": {"name": "Work"},
        },
    )
    for kind, data in (
        ("category", {"name": "Engineering"}),
        ("folder", {"name": "Specs"}),
        ("project", {"name": "Beacon", "section_id": section["id"]}),
    ):
        call(
            "post",
            t + "/items",
            path=s + "/items",
            json={
                "command_id": str(uuid4()),
                "item_id": str(uuid4()),
                "expected_revision": 0,
                "kind": kind,
                "data": data,
            },
        )
    for kind in ("section", "category", "folder", "database"):
        call("get", t + "/items", path=s + "/items", params={"kind": kind})
    sourced = call(
        "post",
        t + "/memory/remember",
        path=s + "/memory/remember",
        json={
            "command_id": str(uuid4()),
            "title": "Atlas launch window",
            "text": "Atlas launches in November.",
            "source": {"resource_id": task["id"], "revision": task["revision"]},
            "links": [{"resource_id": claims["id"], "relation": "expands_on"}],
        },
    )
    call("get", t + "/memories/{resource_id}", path=f"{s}/memories/{sourced['id']}")
    call(
        "get",
        t + "/memories/{resource_id}/links",
        path=f"{s}/memories/{claims['id']}/links",
    )
    call(
        "post",
        t + "/context",
        path=s + "/context",
        json={
            "query": "When does Atlas launch?",
            "strategy": "lexical",
            "include_related": True,
        },
    )
    call("get", t + "/map", path=s + "/map")
    call("get", t + "/overview", path=s + "/overview", params={"all_periods": True})
    call("get", t + "/memories", path=s + "/memories")
    call(
        "get",
        t + "/resources/{resource_id}",
        path=f"{s}/resources/{task['id']}",
        params={"revision": 1},
    )
    # Error shapes, all of which share one documented body.
    call("get", "/v1/workspace", status=401, headers={"Authorization": "Bearer spk_unknown"})
    call(
        "get",
        t + "/resources/{resource_id}",
        path=f"{s}/resources/{uuid4()}",
        status=404,
    )
    call("post", t + "/search", path=s + "/search", status=422, json={"query": 5})


def test_every_public_operation_matches_its_published_schema(commands, signing, monkeypatch):
    service = commands[0]
    verifier, token = signing
    browser = {"Authorization": "Bearer " + token()}
    settings = Settings(environment="test", allowed_hosts=["testserver"])
    service.memory_models, _ = provider(monkeypatch)
    with TestClient(create_app(settings, workspace=service, verifier=verifier)) as http:
        space = UUID(http.post("/v1/workspace", headers=browser).json()["id"])
        key = http.post(
            f"/v1/spaces/{space}/api-keys",
            headers=browser,
            json={"name": "Contract test", "can_write": True},
        ).json()["token"]
        call = Recorder(http, {"Authorization": "Bearer " + key})
        exercise(call, space)
        schema = http.get("/openapi.json").json()

    if os.getenv("SPACE_API_EXAMPLES") == "write":
        write_examples(call.calls)

    published = {(m, p) for p, ops in schema["paths"].items() for m in ops}
    exercised = {(m, p) for m, p, *_ in call.calls}
    assert published - exercised == set(), "Every published operation needs a contract call"
    for method, path, status, body, _ in call.calls:
        responses = schema["paths"][path][method]["responses"]
        documented = responses.get(str(status))
        assert documented, f"{method.upper()} {path} does not document {status}"
        definition = documented["content"]["application/json"]["schema"]
        assert definition, f"{method.upper()} {path} has no {status} schema"
        jsonschema.validate(
            body,
            {**definition, "components": schema["components"]},
            cls=jsonschema.Draft202012Validator,
        )


LOCAL_URLS = {
    "http://127.0.0.1:8000": "https://api.example.com",
    "http://127.0.0.1:3000": "https://app.example.com",
    "https://models.invalid": "https://models.example.com",
}


def write_examples(calls):
    """Parameters: recorded calls. What it does: keeps the last successful request and
    response per operation, shortened and with local URLs replaced, as the reference's
    examples. Output: none; writes space/api/examples.json."""
    from space.api.reference_docs import EXAMPLES

    def shorten(value):
        if isinstance(value, list):
            return [shorten(item) for item in value[:2]]
        if isinstance(value, dict):
            return {key: shorten(item) for key, item in value.items()}
        if isinstance(value, str):
            for local, public in LOCAL_URLS.items():
                value = value.replace(local, public)
            return value
        return value

    examples = {}
    for method, path, status, body, request in calls:
        operation = f"{method} {path}"
        if status < 300:
            examples[operation] = {
                "request": shorten(request) if request is not None else None,
                "response": shorten(body),
            }
        elif request is not None and operation not in examples:
            # Still show how to call it, even when only an error could be captured.
            examples[operation] = {"request": shorten(request), "response": None}
    EXAMPLES.write_text(json.dumps(examples, indent=1, sort_keys=True) + "\n")


# Synchronous extraction needs a real text model, so its success is never captured.
WITHOUT_RESPONSE_EXAMPLE = {("post", "/v1/spaces/{space_id}/memory/build")}


def test_documented_examples_match_their_schemas():
    from space.api.reference import published_schema

    schema = published_schema()
    missing = set()
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            media = [
                response["content"]["application/json"]
                for response in operation["responses"].values()
                if "example" in response.get("content", {}).get("application/json", {})
            ]
            if not media:
                missing.add((method, path))
            for documented in media:
                jsonschema.validate(
                    documented["example"],
                    {**documented["schema"], "components": schema["components"]},
                    cls=jsonschema.Draft202012Validator,
                )
    assert missing == WITHOUT_RESPONSE_EXAMPLE, "Every operation needs a response example"


def test_committed_reference_snapshot_is_current():
    from space.api.reference import SNAPSHOT, published_schema, render

    assert SNAPSHOT.read_text() == render(published_schema()), (
        "docs/openapi.json is stale. Run: python -m space.api.reference"
    )
