"""Exercise the real persistence path with synthetic providers and disposable PostgreSQL."""

import json
from collections import Counter
from contextlib import contextmanager
from uuid import uuid4

import httpx
import pytest
from test_database_setup import database as database_fixture
from test_memora_jobs import request as import_request
from test_memory_classification import response
from test_memory_classification_workspace import item
from test_memory_jobs import queue as queue_fixture
from test_workspace import change
from test_workspace import command_database as command_database_fixture
from test_workspace import commands as commands_fixture

from space import jobs, memora_jobs, quotas, reclassify_jobs
from space.memory import RememberClaims, SourceReference
from space.model_provider import MemoryModels
from space.settings import Settings
from space.workspace import CommandError

database = database_fixture
command_database = command_database_fixture
commands = commands_fixture
queue = queue_fixture


def instrument(service, monkeypatch):
    counts = Counter()
    transaction = service.db.transaction

    class Connection:
        def __init__(self, wrapped):
            self.wrapped = wrapped

        def execute(self, query, *args, **kwargs):
            counts["queries"] += 1
            if "SELECT k.* FROM space.space_keys" in str(query):
                counts["key_queries"] += 1
            return self.wrapped.execute(query, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(self.wrapped, name)

    @contextmanager
    def measured(identity):
        counts["transactions"] += 1
        with transaction(identity) as connection:
            yield Connection(connection)

    monkeypatch.setattr(service.db, "transaction", measured)
    for target, method, label in [
        (service.keys, "unwrap", "unwraps"),
        (service.objects, "get", "object_reads"),
    ]:
        original = getattr(target, method)

        def counted(*args, _original=original, _label=label, **kwargs):
            counts[_label] += 1
            return _original(*args, **kwargs)

        monkeypatch.setattr(target, method, counted)
    return counts


def provider(monkeypatch, *, match=False):
    requests = []

    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if "questions" in payload:
            scores = {
                f"i{n}": 0.99
                for n, entry in enumerate(payload["state"]["catalog"])
                if match and entry["kind"] in {"project", "category"}
            }
            return httpx.Response(200, json=response(payload, scores))
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": i, "embedding": [1.0] + [0.0] * 1535}
                    for i, _ in enumerate(payload["input"])
                ]
            },
        )

    client = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: client(transport=httpx.MockTransport(handler), **kw)
    )
    return MemoryModels(
        Settings(
            _env_file=None,
            environment="test",
            memory_provider="openai_compatible",
            memory_provider_url="https://models.invalid/v1",
            memory_api_key="fixture-key",
            memory_text_model="fixture",
            memory_embedding_model="fixture-embed",
            memory_classification_provider="typesafe",
            typesafe_api_key="fixture-key",
        )
    ), requests


@pytest.mark.parametrize("catalog_size", [0, 24])
def test_prepared_write_efficiency(commands, monkeypatch, catalog_size):
    service, owner, space, _ = commands
    source = service.save(owner, space, change())
    for n in range(catalog_size):
        item(service, owner, space, "category", f"Category {n}")
    service.memory_models, requests = provider(monkeypatch)
    # The one-time reclassification catch-up has its own budget in test_reclassify_jobs.
    monkeypatch.setattr(reclassify_jobs, "maybe_enqueue", lambda *args: None)
    counts = instrument(service, monkeypatch)
    request = RememberClaims(
        command_id=uuid4(),
        title="Commute",
        source=SourceReference(resource_id=source["id"], revision=source["revision"]),
        claims=[{"index": "Commute", "value": "Mira travels by car."}],
    )
    saved = service.remember_claims(owner, space, request)
    assert saved["memory"]["claims"][0]["value"] == "Mira travels by car."
    assert len(requests) == 2
    print(json.dumps({"catalog_size": catalog_size, **counts}))
    assert counts["object_reads"] == 0, "finished claims only need source revision metadata"
    assert counts["unwraps"] == 1, "one command must unwrap each unchanged key only once"
    assert counts["key_queries"] <= 3, "catalog size must not multiply key lookups"
    assert counts["transactions"] <= 12
    before = len(requests)
    assert service.remember_claims(owner, space, request) == saved
    assert len(requests) == before, "an identical retry must not repeat paid model calls"
    assert counts["unwraps"] == 2, "the next operation must acquire its own key"
    assert quotas.usage(service, owner, space)["model_requests"] == 2


def test_import_record_uses_one_memory_receipt_lookup(commands, queue, monkeypatch):
    service, owner, space, _ = commands
    service.memory_models, requests = provider(monkeypatch)
    change = import_request()
    memora_jobs.start(service, owner, space, change)
    job = jobs.claim(queue)
    counts = instrument(service, monkeypatch)
    retry = service._memory_retry
    receipts = []

    def checked(*args):
        receipts.append(args[2])
        return retry(*args)

    monkeypatch.setattr(service, "_memory_retry", checked)
    saved = jobs.process(service, queue, job)
    assert saved and receipts == ["remember_claims"]
    assert len(requests) == 2
    assert counts["object_reads"] == 0
    print(json.dumps({"import_record": True, **counts}))
    assert service.memory_job(owner, space, change.command_id)["status"] == "ready"


def test_incoming_model_quota_is_checked_before_provider_disclosure(commands, monkeypatch):
    service, owner, space, connect = commands
    service.memory_models, requests = provider(monkeypatch)
    quotas.consume_model_request(service, owner, space)
    with connect() as connection:
        connection.execute(
            "UPDATE space.usage_limits SET model_request_limit=1 WHERE space_id=%s", (space,)
        )
    request = RememberClaims(
        command_id=uuid4(),
        title="Commute",
        claims=[{"index": "Commute", "value": "Mira travels by car."}],
    )
    with pytest.raises(CommandError) as error:
        service.remember_claims(owner, space, request)
    assert error.value.status == 429
    assert requests == []
    assert not service.list_memories(owner, space)["items"]
