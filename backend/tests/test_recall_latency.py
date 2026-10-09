"""Parameters: disposable PostgreSQL and synthetic encrypted memory fixtures.
What it does: verifies request-local reuse, phase timings and unchanged disclosure checks.
Output: work-count and authorization regression evidence, not hosted latency claims.
"""

from collections import Counter
from contextlib import contextmanager
from uuid import UUID, uuid4

import pytest
from test_database_setup import database as database_fixture
from test_memora_pipeline import Models
from test_workspace import command_database as command_database_fixture
from test_workspace import commands as commands_fixture
from test_workspace import delegated

from space.memory import RecallRequest, RememberClaims
from space.model_provider import MemoryModels
from space.read_timing import collect_read_timings
from space.settings import Settings
from space.workspace import CommandError

database = database_fixture
command_database = command_database_fixture
commands = commands_fixture


@pytest.fixture
def memories(commands):
    """Parameters: workspace fixture. What it does: saves claims. Output: recall inputs."""
    service, owner, space, _ = commands
    service.memory_models = Models()
    saved = [
        service.remember_claims(
            owner,
            space,
            RememberClaims(
                command_id=uuid4(),
                title=f"Mira commute {i}",
                claims=[{"index": "Mira commute", "value": f"Mira travels by car on route {i}."}],
            ),
        )
        for i in range(3)
    ]
    return service, owner, space, saved


def test_recall_reuses_keys_and_bodies_but_not_across_requests(memories, monkeypatch):
    """Parameters: memories/spies. What it does: measures two recalls. Output: bounded reuse."""
    service, owner, space, saved = memories
    unwrap, decrypt = service.keys.unwrap, service._unpack
    counts, bodies = Counter(), Counter()

    def counted_unwrap(*args):
        """Parameters: key call. What it does: counts unwraps. Output: real key."""
        counts["unwrap"] += 1
        return unwrap(*args)

    def counted_decrypt(key, scope, id_, revision, purpose, value):
        """Parameters: payload. What it does: counts body reads. Output: real decrypted data."""
        if purpose == "source":
            bodies[(str(id_), revision)] += 1
        return decrypt(key, scope, id_, revision, purpose, value)

    monkeypatch.setattr(service.keys, "unwrap", counted_unwrap)
    monkeypatch.setattr(service, "_unpack", counted_decrypt)
    request = RecallRequest(query="Mira commute car")
    with collect_read_timings() as timings:
        first = service.context(owner, space, request)
    assert first["found"]
    assert counts["unwrap"] == 1
    assert bodies == Counter({(s["id"], 1): 1 for s in saved})
    assert {
        "recall_total",
        "recall_sources",
        "recall_vector",
        "recall_lexical",
        "recall_context",
        "recall_assessment",
        "recall_auth",
        "space_key",
        "decode",
        "db_pool",
        "db_transaction",
    } <= set(timings.durations)
    second = service.context(owner, space, request)
    assert first == second
    assert counts["unwrap"] == 2
    assert bodies == Counter({(s["id"], 1): 2 for s in saved})


def test_reuse_matches_uncached_candidate_response(memories, monkeypatch):
    """Parameters: same fixture/queries. What it does: compares reuse off/on. Output: equality."""
    service, owner, space, _ = memories
    bodies, key = service._bodies, service._unwrap_key
    uncached_counts, cached_counts = Counter(), Counter()
    decrypt, unwrap = service._unpack, service.keys.unwrap
    counts = uncached_counts

    def counted_decrypt(*args):
        """Parameters: payload. What it does: counts sources. Output: original decrypt."""
        if args[4] == "source":
            counts["body_decrypts"] += 1
        return decrypt(*args)

    def counted_unwrap(*args):
        """Parameters: key. What it does: counts key work. Output: original key."""
        counts["key_unwraps"] += 1
        return unwrap(*args)

    monkeypatch.setattr(service, "_unpack", counted_decrypt)
    monkeypatch.setattr(service.keys, "unwrap", counted_unwrap)
    monkeypatch.setattr(service, "_bodies", lambda *a, **kw: bodies(*a))
    monkeypatch.setattr(service, "_unwrap_key", service.keys.unwrap)
    request = RecallRequest(query="Mira commute car")
    baseline = service.context(owner, space, request)
    monkeypatch.setattr(service, "_bodies", bodies)
    monkeypatch.setattr(service, "_unwrap_key", key)
    counts = cached_counts
    optimized = service.context(owner, space, request)
    assert baseline == optimized
    assert uncached_counts == {"body_decrypts": 6, "key_unwraps": 4}
    assert cached_counts == {"body_decrypts": 3, "key_unwraps": 1}


def test_revocation_on_cached_hydration_still_denies_release(memories, monkeypatch):
    """Parameters: delegated caller. What it does: revokes during cache hit. Output: denial."""
    service, owner, space, _ = memories
    reader = delegated(service, owner)
    bodies = service._bodies
    revoked = False

    def revoke_on_reuse(*args, **kwargs):
        """Parameters: hydration call. What it does: revokes on reuse. Output: cached bodies."""
        nonlocal revoked
        if kwargs.get("cached_bodies") and not revoked:
            service.revoke(owner, UUID(reader.client_id))
            revoked = True
        return bodies(*args, **kwargs)

    monkeypatch.setattr(service, "_bodies", revoke_on_reuse)
    with pytest.raises(CommandError) as error:
        service.context(reader, space, RecallRequest(query="Mira commute car"))
    assert revoked and error.value.status == 403
    # A failed request must not leave keys or bodies available to a later caller.
    assert service.context(owner, space, RecallRequest(query="Mira commute car"))["found"]


def test_recall_combines_disclosure_authorization_and_quota_transaction(commands, monkeypatch):
    """Parameters: real scoped DB and synthetic provider settings.
    What it does: invokes recall pre-disclosure hook and post-provider recheck separately.
    Output: one transaction for auth+charge; revocation remains an error.
    """
    service, owner, space, _ = commands
    service.memory_models = MemoryModels(
        Settings(
            memory_provider="openai_compatible",
            memory_provider_url="https://example.invalid/v1",
            memory_api_key="synthetic",
            memory_text_model="fixture",
            memory_embedding_model="fixture",
        )
    )
    snapshot, _ = service._recall_state(owner, space)
    transaction = service.db.transaction
    counts = Counter()

    @contextmanager
    def counted_transaction(identity):
        """Parameters: identity. What it does: counts transactions. Output: real connection."""
        counts["transactions"] += 1
        with transaction(identity) as connection:
            yield connection

    monkeypatch.setattr(service.db, "transaction", counted_transaction)
    model = service._model_session(
        owner,
        space,
        lambda: service._authorize_model(owner, space, snapshot),
        recall_snapshot=snapshot,
    )
    model.prepare_request()
    assert counts["transactions"] == 1
    model.authorize()
    assert counts["transactions"] == 2
    with transaction(owner) as connection:
        service._scope(connection, owner, space)
        assert (
            connection.execute("SELECT model_requests FROM space.usage_limits").fetchone()[
                "model_requests"
            ]
            == 1
        )
    reader = delegated(service, owner)
    denied = service._model_session(
        reader,
        space,
        lambda: service._authorize_model(reader, space, snapshot),
        recall_snapshot=snapshot,
    )
    service.revoke(owner, UUID(reader.client_id))
    with pytest.raises(CommandError):
        denied.prepare_request()
    with pytest.raises(CommandError):
        denied.authorize()
