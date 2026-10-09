"""Read round trips, response fidelity and access checks against disposable PostgreSQL."""

from threading import Barrier
from uuid import UUID, uuid4

import pytest
from test_database_setup import database as database_fixture
from test_memora_pipeline import Models
from test_memory_classification_workspace import item
from test_memory_write_efficiency import instrument
from test_workspace import change, delegated
from test_workspace import command_database as command_database_fixture
from test_workspace import commands as commands_fixture

from space import workspace, workspace_items
from space.memory import RememberClaims, SourceReference
from space.workspace import CommandError

database = database_fixture
command_database = command_database_fixture
commands = commands_fixture


def remember(service, owner, space, source=None):
    service.memory_models = Models()
    return service.remember_claims(
        owner,
        space,
        RememberClaims(
            command_id=uuid4(),
            title="Commute",
            source=SourceReference(resource_id=source["id"], revision=source["revision"])
            if source
            else None,
            claims=[{"index": "Commute", "value": "Mira travels by car."}],
        ),
    )


def test_section_reads_do_not_reopen_a_transaction_to_load_keys(commands, monkeypatch):
    service, owner, space, _ = commands
    counts = instrument(service, monkeypatch)
    assert workspace_items.listing(service, owner, space, "section")["items"] == []
    assert counts["transactions"] == 1
    assert counts["unwraps"] == 0
    saved = item(service, owner, space, "section", "Work")
    counts.clear()
    assert workspace_items.listing(service, owner, space, "section")["items"] == [saved]
    assert counts["transactions"] == 2
    assert counts["unwraps"] == 1
    counts.clear()
    assert workspace_items.get(service, owner, space, UUID(saved["id"])) == saved
    assert counts["transactions"] == 2


def test_section_revocation_during_key_unwrap_still_denies_disclosure(commands, monkeypatch):
    service, owner, space, _ = commands
    item(service, owner, space, "section", "Work")
    reader = delegated(service, owner)
    original = service.keys.unwrap

    def revoke(*args):
        key = original(*args)
        service.revoke(owner, UUID(reader.client_id))
        return key

    monkeypatch.setattr(service.keys, "unwrap", revoke)
    with pytest.raises(CommandError) as error:
        workspace_items.listing(service, reader, space, "section")
    assert error.value.status == 403


@pytest.mark.parametrize("with_source", [False, True])
def test_memory_page_keeps_claims_and_index_with_fewer_transactions(
    commands, monkeypatch, with_source
):
    service, owner, space, _ = commands
    source = service.save(owner, space, change()) if with_source else None
    saved = remember(service, owner, space, source)
    counts = instrument(service, monkeypatch)
    page = service.list_memories(owner, space)
    assert page["items"][0]["id"] == saved["id"]
    assert page["items"][0]["claims"][0]["value"] == "Mira travels by car."
    assert page["items"][0]["stale"] is False
    assert page["provider"]["index"]["claims"] == 1
    assert page["provider"]["index"]["memories"] == 1
    assert page["next_cursor"] is None
    assert counts["transactions"] == (3 if with_source else 2)
    assert counts["unwraps"] == 1
    assert counts["object_reads"] == 0
    counts.clear()
    assert service.memory_info(owner, space) == page["provider"]
    assert counts["transactions"] == 1


def test_legacy_memory_objects_load_concurrently_and_preserve_page_order(commands, monkeypatch):
    service, owner, space, _ = commands
    saved = [remember(service, owner, space) for _ in range(4)]
    expected = service.list_memories(owner, space)
    with service.db.transaction(owner) as connection:
        service._scope(connection, owner, space)
        rows = connection.execute(
            "SELECT resource_id,encrypted_record FROM space.resource_revisions "
            "WHERE resource_id=ANY(%s::uuid[])",
            ([entry["id"] for entry in saved],),
        ).fetchall()
    for row in rows:
        service.objects.put(str(row["resource_id"]), bytes(row["encrypted_record"]))
    # Project real encrypted rows as the legacy object-backed layout without modifying
    # immutable revisions or weakening database constraints in the fixture.
    monkeypatch.setattr(
        workspace,
        "MEMORY_PAGE_SQL",
        workspace.MEMORY_PAGE_SQL.replace(
            "v.encrypted_record, v.object_key",
            "NULL::bytea AS encrypted_record, r.id::text AS object_key",
        ),
    )
    read = service.objects.get
    together = Barrier(4, timeout=5)

    def concurrent_read(key):
        together.wait()
        return read(key)

    monkeypatch.setattr(service.objects, "get", concurrent_read)
    assert service.list_memories(owner, space) == expected


@pytest.mark.parametrize("mutation", ["revoke", "source"])
def test_memory_list_keeps_final_access_and_snapshot_checks(commands, monkeypatch, mutation):
    service, owner, space, _ = commands
    source = service.save(owner, space, change())
    remember(service, owner, space, source)
    reader = delegated(service, owner)
    original = service._summaries

    def changed(*args, **kwargs):
        summaries = original(*args, **kwargs)
        if mutation == "revoke":
            service.revoke(owner, UUID(reader.client_id))
        else:
            service.save(
                owner,
                space,
                change().model_copy(
                    update={
                        "resource_id": UUID(source["id"]),
                        "expected_revision": 1,
                    }
                ),
            )
        return summaries

    monkeypatch.setattr(service, "_summaries", changed)
    with pytest.raises(CommandError) as error:
        service.list_memories(reader, space)
    assert error.value.status == (403 if mutation == "revoke" else 409)
