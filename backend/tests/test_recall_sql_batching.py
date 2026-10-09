"""Parameters: disposable PostgreSQL and encrypted synthetic memories.
What it does: checks scoped SQL batching without changing evidence or disclosure gates.
Output: deterministic transaction/barrier counts and authorization regression evidence.
"""

import hashlib
from collections import Counter
from contextlib import contextmanager
from dataclasses import replace
from uuid import uuid4

import pytest
from test_database_setup import database as database_fixture
from test_recall_candidates import exclude, remember
from test_workspace import change
from test_workspace import command_database as command_database_fixture
from test_workspace import commands as commands_fixture

from space import recall_candidates as candidates
from space import workspace_items
from space.workspace import CommandError, SnapshotChanged

database = database_fixture
command_database = command_database_fixture
commands = commands_fixture


def test_manifest_queues_both_complete_aggregates_before_fetching(commands):
    service, owner, space, _ = commands
    memory = remember(service, owner, space)
    project_id = uuid4()
    workspace_items.save(
        service,
        owner,
        space,
        workspace_items.SaveItem(
            command_id=uuid4(),
            item_id=project_id,
            expected_revision=0,
            kind="project",
            data={"name": "Manifest project"},
        ),
    )
    events = []

    class Cursor:
        def __init__(self, cursor):
            self.cursor = cursor

        def fetchone(self):
            events.append("fetch")
            return self.cursor.fetchone()

    class Connection:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, *args):
            events.append("execute")
            return Cursor(self.connection.execute(*args))

    with service.db.transaction(owner) as connection:
        service._scope(connection, owner, space)
        digest = service._manifest_digest(Connection(connection))
    # The manifest digest mirrors SQL md5(); it detects change and protects nothing.
    resource_hash = hashlib.md5(f"{memory['id']}:1".encode()).hexdigest()  # noqa: S324
    item_hash = hashlib.md5(f"{project_id}:1".encode()).hexdigest()  # noqa: S324
    assert digest == f"1:{resource_hash}:{item_hash}"
    assert events == ["execute", "execute", "fetch", "fetch"]


@pytest.mark.parametrize("kind", ["resource", "workspace_item"])
def test_batched_manifest_still_detects_all_scoped_content_changes(commands, kind):
    service, owner, space, _ = commands
    snapshot, _ = service._recall_state(owner, space)
    stranger = replace(owner, subject=str(uuid4()))
    foreign_space = service.bootstrap(stranger)["id"]
    service.save(stranger, foreign_space, change())
    service._authorize_model(owner, space, snapshot)
    if kind == "resource":
        service.save(owner, space, change())
    else:
        workspace_items.save(
            service,
            owner,
            space,
            workspace_items.SaveItem(
                command_id=uuid4(),
                item_id=uuid4(),
                expected_revision=0,
                kind="project",
                data={"name": "New context outside recall candidates"},
            ),
        )
    with pytest.raises(SnapshotChanged):
        service._authorize_model(owner, space, snapshot)


@pytest.mark.parametrize("route", ["lexical", "linked"])
def test_candidates_keep_evidence_and_exclusions_in_three_transactions(
    commands, monkeypatch, route
):
    service, owner, space, _ = commands
    seed = remember(service, owner, space)
    claims = [{"index": "ZX-4912", "value": "Mira owns the exact account."}]
    target = remember(service, owner, space, claims=claims, links=[seed])
    hidden = remember(service, owner, space, claims=claims, links=[seed])
    exclude(service, owner, space, hidden)
    transaction, unwrap, decrypt, bodies = (
        service.db.transaction,
        service.keys.unwrap,
        service._unpack,
        service._bodies,
    )
    counts, loaded = Counter(), []

    @contextmanager
    def counted_transaction(identity):
        counts["transactions"] += 1
        with transaction(identity) as connection:
            counts["active"] += 1
            try:
                yield connection
            finally:
                counts["active"] -= 1

    def checked_unwrap(*args):
        assert counts["active"] == 0
        return unwrap(*args)

    def checked_decrypt(*args):
        assert counts["active"] == 0
        return decrypt(*args)

    def checked_bodies(identity, space_id, items, keys, scope, **kwargs):
        assert counts["active"] == 0
        loaded.extend(item["id"] for item in items)
        return bodies(identity, space_id, items, keys, scope, **kwargs)

    monkeypatch.setattr(service.db, "transaction", counted_transaction)
    monkeypatch.setattr(service.keys, "unwrap", checked_unwrap)
    monkeypatch.setattr(service, "_unpack", checked_decrypt)
    monkeypatch.setattr(service, "_bodies", checked_bodies)
    cache = {}
    args = (
        (service, owner, space, "ZX-4912", 20)
        if route == "lexical"
        else (
            service,
            owner,
            space,
            [seed["id"]],
            "ZX-4912",
            20,
        )
    )
    recall = getattr(candidates, route)
    first = recall(*args, model_name="fixture-embedding", cached_bodies=cache)
    assert counts["transactions"] == 3
    second = recall(*args, model_name="fixture-embedding", cached_bodies=cache)
    assert counts["transactions"] == 5  # The second call reuses bodies: only two scopes.
    assert first == second and len(first) == 1
    assert first[0]["id"] == target["id"]
    assert first[0]["entry_id"] == target["memory"]["claims"][0]["id"]
    assert first[0]["body"] == claims[0]["value"] and first[0]["revision"] == 1
    assert first[0]["candidate_origin"] == route
    assert loaded == [target["id"], target["id"]]


@pytest.mark.parametrize("route", ["lexical", "linked"])
@pytest.mark.parametrize("change_index", ["revision", "model", "forgotten"])
def test_batched_claim_membership_is_current_after_metadata_read(
    commands,
    monkeypatch,
    route,
    change_index,
):
    service, owner, space, _ = commands
    seed = remember(service, owner, space)
    target = remember(
        service,
        owner,
        space,
        claims=[
            {"index": "ZX-4912", "value": "Current indexed evidence only."},
        ],
        links=[seed],
    )
    item_rows, bodies = service._item_rows, service._bodies
    changed, loaded = False, []

    def change_after_metadata(connection, scope, space_id, ids, **kwargs):
        nonlocal changed
        result = item_rows(connection, scope, space_id, ids, **kwargs)
        if target["id"] in ids and not changed:
            changed = True
            statement = {
                "revision": "UPDATE space.memory_claims SET revision=revision+1 WHERE memory_id=%s",
                "model": "UPDATE space.memory_claims SET embedding_model='old' WHERE memory_id=%s",
                "forgotten": "DELETE FROM space.memory_claims WHERE memory_id=%s",
            }[change_index]
            connection.execute(statement, (target["id"],))
        return result

    def checked_bodies(identity, space_id, items, keys, scope, **kwargs):
        loaded.extend(item["id"] for item in items)
        return bodies(identity, space_id, items, keys, scope, **kwargs)

    monkeypatch.setattr(service, "_item_rows", change_after_metadata)
    monkeypatch.setattr(service, "_bodies", checked_bodies)
    args = (
        (service, owner, space, "ZX-4912", 20)
        if route == "lexical"
        else (
            service,
            owner,
            space,
            [seed["id"]],
            "ZX-4912",
            20,
        )
    )
    assert getattr(candidates, route)(*args, model_name="fixture-embedding") == []
    assert changed and loaded == []


@pytest.mark.parametrize("route", ["lexical", "linked"])
def test_batched_shortlist_rejects_a_head_changed_before_metadata(commands, monkeypatch, route):
    service, owner, space, _ = commands
    seed = remember(service, owner, space)
    target = remember(
        service,
        owner,
        space,
        claims=[
            {"index": "ZX-4912", "value": "Superseded evidence."},
        ],
        links=[seed],
    )
    item_rows, bodies = service._item_rows, service._bodies
    changed, loaded = False, []

    def change_before_metadata(connection, scope, space_id, ids, **kwargs):
        nonlocal changed
        if target["id"] in ids and not changed:
            changed = True
            exclude(service, owner, space, target)
        return item_rows(connection, scope, space_id, ids, **kwargs)

    def checked_bodies(identity, space_id, items, keys, scope, **kwargs):
        loaded.extend(item["id"] for item in items)
        return bodies(identity, space_id, items, keys, scope, **kwargs)

    monkeypatch.setattr(service, "_item_rows", change_before_metadata)
    monkeypatch.setattr(service, "_bodies", checked_bodies)
    args = (
        (service, owner, space, "ZX-4912", 20)
        if route == "lexical"
        else (
            service,
            owner,
            space,
            [seed["id"]],
            "ZX-4912",
            20,
        )
    )
    assert getattr(candidates, route)(*args, model_name="fixture-embedding") == []
    assert changed and loaded == []


@pytest.mark.parametrize("route", ["lexical", "linked"])
def test_batched_candidates_deny_another_callers_space(commands, route):
    service, owner, space, _ = commands
    target = remember(service, owner, space, claims=[{"index": "ZX-4912", "value": "Private."}])
    stranger = replace(owner, subject=str(uuid4()))
    service.bootstrap(stranger)
    args = (
        (service, stranger, space, "ZX-4912", 20)
        if route == "lexical"
        else (
            service,
            stranger,
            space,
            [target["id"]],
            "ZX-4912",
            20,
        )
    )
    with pytest.raises(CommandError) as error:
        getattr(candidates, route)(*args, model_name="fixture-embedding")
    assert error.value.status == 404
