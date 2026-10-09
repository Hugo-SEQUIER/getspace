"""Parameters: disposable PostgreSQL fixtures, never configured databases.
What it does: verifies opaque link derivation, live revision joins and tenant isolation.
Output: regression evidence for memory_link_index and migration 0022.
"""

from uuid import UUID, uuid4

import psycopg
import pytest
from test_database_setup import database as database_fixture
from test_workspace import change
from test_workspace import command_database as command_database_fixture
from test_workspace import commands as commands_fixture

from space import brain, memory_link_index
from space.security.auth import Identity

database = database_fixture
command_database = command_database_fixture
commands = commands_fixture


def memory(commands, count=1):
    """Parameters: command fixture, count. What it does: seeds claims. Output: resource/claims."""
    service, owner, space, _ = commands
    saved = service.save(owner, space, change())
    claims = [
        {
            "id": str(uuid4()),
            "entry_index": index,
            "memory_type": "factual",
            "text": f"PRIVATE CLAIM {index}",
            "cues": [],
        }
        for index in range(count)
    ]
    with service.db.transaction(owner) as connection:
        service._scope(connection, owner, space)
        brain.apply(
            connection,
            memory_id=saved["id"],
            revision=saved["revision"],
            claims=claims,
            model_name="fixture",
            vectors={c["id"]: [1.0] + [0.0] * 1535 for c in claims},
        )
    return saved, claims


def reference(target, claim, **updates):
    """Parameters: target resource/claim. What it does: builds a link. Output: encrypted shape."""
    return {
        "resource_id": target["id"],
        "entry_id": claim["id"],
        "revision": target["revision"],
        "relation": "related",
        "confidence": 0.95,
        **updates,
    }


def write(commands, source, claims):
    """Parameters: fixture/resource/claims. What it does: replaces edges. Output: none."""
    service, owner, space, _ = commands
    with service.db.transaction(owner) as connection:
        service._scope(connection, owner, space)
        memory_link_index.replace(connection, source["id"], source["revision"], claims)


def read(commands, saved, limit=50):
    """Parameters: fixture/resource/limit. What it does: reads edges. Output: navigation."""
    service, owner, space, _ = commands
    with service.db.transaction(owner) as connection:
        service._scope(connection, owner, space)
        return memory_link_index.read(connection, saved["id"], limit)


def linked(commands, count=1):
    """Parameters: fixture/count. What it does: saves matched edges. Output: source/target."""
    source, claims = memory(commands)
    target, targets = memory(commands, count)
    claims[0]["relationships"] = {
        "status": "matched",
        "links": [reference(target, c) for c in targets],
    }
    write(commands, source, claims)
    return source, claims, target, targets


def test_links_and_backlinks_are_symmetric_opaque_and_idempotent(commands):
    source, claims, target, targets = linked(commands)
    write(commands, source, claims)
    outgoing = read(commands, source)
    incoming = read(commands, target)
    assert outgoing == {
        "links": [
            {
                "entry_id": claims[0]["id"],
                "resource_id": target["id"],
                "related_entry_id": targets[0]["id"],
                "revision": 1,
                "relation": "related",
                "origin": "inferred",
            }
        ],
        "backlinks": [],
        "has_more": False,
    }
    assert incoming == {
        "links": [],
        "has_more": False,
        "backlinks": [
            {
                "entry_id": targets[0]["id"],
                "resource_id": source["id"],
                "related_entry_id": claims[0]["id"],
                "revision": 1,
                "relation": "related",
                "origin": "inferred",
            }
        ],
    }
    service, owner, space, _ = commands
    with service.db.transaction(owner) as connection:
        service._scope(connection, owner, space)
        row = connection.execute("SELECT * FROM space.memory_links").fetchone()
        assert set(row) == {
            "organization_id",
            "space_id",
            "source_memory_id",
            "source_claim_id",
            "source_revision",
            "target_memory_id",
            "target_claim_id",
            "target_revision",
            "source_text_hash",
            "target_text_hash",
            "relation",
        }


@pytest.mark.parametrize("status", [None, "no_match", "unavailable", "disabled", "input_limit"])
def test_replace_clears_outgoing_links_when_relationships_are_absent_or_not_matched(
    commands, status
):
    source, claims, target, _ = linked(commands)
    if status is None:
        claims[0].pop("relationships")
    else:
        claims[0]["relationships"]["status"] = status
    write(commands, source, claims)
    assert read(commands, source)["links"] == []
    assert read(commands, target)["backlinks"] == []


def test_replace_ignores_missing_stale_malformed_and_duplicate_references(commands):
    source, claims, target, targets = linked(commands)
    good = reference(target, targets[0])
    for bad in (
        {"resource_id": str(uuid4())},
        {"entry_id": str(uuid4())},
        {"revision": 2},
        {"resource_id": "not-a-uuid"},
        {"revision": 0},
        {"revision": True},
        {"confidence": 0.89},
        {"confidence": float("nan")},
        {"relation": "invented"},
    ):
        claims[0]["relationships"]["links"] = [good | bad, good, good]
        write(commands, source, claims)
        assert len(read(commands, source)["links"]) == 1
    claims[0]["id"] = str(uuid4())
    write(commands, source, claims)
    assert read(commands, source)["links"] == []


def test_max_five_links_and_bounded_reads_in_both_directions(commands):
    source, _, target, _ = linked(commands, count=6)
    assert len(read(commands, source)["links"]) == 5
    for focal, direction in ((source, "links"), (target, "backlinks")):
        page = read(commands, focal, 2)
        assert len(page[direction]) == 2 and page["has_more"] is True
        assert read(commands, focal, 2) == page
        assert read(commands, focal, 5)["has_more"] is False


@pytest.mark.parametrize("side", ["source", "target"])
def test_forgetting_or_editing_either_claim_cascades_links(commands, side):
    source, claims, target, targets = linked(commands)
    saved, original = (source, claims) if side == "source" else (target, targets)
    service, owner, space, _ = commands
    changed = [{**original[0], "text": "Changed exact text"}]
    with service.db.transaction(owner) as connection:
        service._scope(connection, owner, space)
        brain.apply(
            connection,
            memory_id=saved["id"],
            revision=1,
            claims=changed,
            model_name="fixture",
            vectors={changed[0]["id"]: [1.0] + [0.0] * 1535},
        )
        assert (
            connection.execute("SELECT count(*) AS n FROM space.memory_links").fetchone()["n"] == 0
        )
    assert read(commands, source)["links"] == []
    write(commands, source, claims)
    assert len(read(commands, source)["links"]) == 1
    with service.db.transaction(owner) as connection:
        service._scope(connection, owner, space)
        brain.forget(connection, saved["id"])
        assert (
            connection.execute("SELECT count(*) AS n FROM space.memory_links").fetchone()["n"] == 0
        )
    assert read(commands, target)["backlinks"] == []


@pytest.mark.parametrize("side", ["source", "target"])
def test_revision_changes_hide_old_links_and_block_reinsertion(commands, side):
    source, claims, target, _ = linked(commands)
    saved = source if side == "source" else target
    service, owner, space, _ = commands
    # A real resource revision advances while its old claim index remains behind.
    service.save(
        owner,
        space,
        change().model_copy(
            update={
                "resource_id": UUID(saved["id"]),
                "expected_revision": 1,
                "body": "New revision",
            }
        ),
    )
    assert read(commands, source)["links"] == []
    assert read(commands, target)["backlinks"] == []
    write(commands, source, claims)
    assert read(commands, source)["links"] == []


def test_claim_only_revision_change_also_hides_links(commands):
    source, _, target, _ = linked(commands)
    service, owner, space, _ = commands
    with service.db.transaction(owner) as connection:
        service._scope(connection, owner, space)
        connection.execute(
            "UPDATE space.memory_claims SET revision=2 WHERE memory_id=%s", (target["id"],)
        )
    assert read(commands, source)["links"] == []
    assert read(commands, target)["backlinks"] == []


def test_link_replace_rolls_back_with_its_callers_transaction(commands):
    source, claims, _, _ = linked(commands)
    service, owner, space, _ = commands
    with pytest.raises(RuntimeError, match="abort"):
        with service.db.transaction(owner) as connection:
            service._scope(connection, owner, space)
            memory_link_index.replace(connection, source["id"], 1, [])
            raise RuntimeError("abort")
    assert read(commands, source)["links"][0]["entry_id"] == claims[0]["id"]


def test_rls_and_composite_foreign_keys_reject_cross_tenant_edges(commands):
    source, claims, target, targets = linked(commands)
    service, owner, space, _ = commands
    other = Identity("https://identity.invalid", str(uuid4()), 4102444800)
    other_space = UUID(service.bootstrap(other)["id"])
    other_commands = (service, other, other_space, commands[3])
    foreign, foreign_claims = memory(other_commands)
    with service.db.transaction(other) as connection:
        scope = service._scope(connection, other, other_space)
        assert memory_link_index.read(connection, source["id"])["links"] == []
        assert (
            connection.execute("SELECT count(*) AS n FROM space.memory_links").fetchone()["n"] == 0
        )
    foreign_claims[0]["relationships"] = {
        "status": "matched",
        "links": [reference(target, targets[0])],
    }
    write(other_commands, foreign, foreign_claims)
    assert read(other_commands, foreign)["links"] == []
    insert = (
        "INSERT INTO space.memory_links (organization_id,space_id,source_memory_id,"
        "source_claim_id,source_revision,target_memory_id,target_claim_id,target_revision) "
        "VALUES (%s,%s,%s,%s,1,%s,%s,1)"
    )
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with service.db.transaction(other) as connection:
            service._scope(connection, other, other_space)
            connection.execute(
                insert,
                (
                    scope["organization_id"],
                    other_space,
                    foreign["id"],
                    foreign_claims[0]["id"],
                    target["id"],
                    targets[0]["id"],
                ),
            )
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with service.db.transaction(owner) as connection:
            service._scope(connection, owner, space)
            connection.execute(
                insert,
                (
                    scope["organization_id"],
                    other_space,
                    source["id"],
                    claims[0]["id"],
                    target["id"],
                    targets[0]["id"],
                ),
            )
    assert len(read(commands, source)["links"]) == 1


def hash_linked(commands, *, relation="related"):
    """Parameters: fixture/relation. What it does: adds content pins. Output: linked fixtures."""
    source, claims, target, targets = linked(commands)
    claims[0]["relationships"]["links"] = [
        reference(
            target,
            targets[0],
            relation=relation,
            source_text_hash=brain.text_hash(claims[0]["text"]).hex(),
            target_text_hash=brain.text_hash(targets[0]["text"]).hex(),
        )
    ]
    write(commands, source, claims)
    return source, claims, target, targets


def reindex(commands, saved, claims, *, model="fixture", advance=False):
    """Parameters: fixtures, index intent. What it does: reconciles. Output: current revision."""
    service, owner, space, _ = commands
    revision = saved["revision"] + int(advance)
    with service.db.transaction(owner) as connection:
        service._scope(connection, owner, space)
        if advance:
            connection.execute(
                "INSERT INTO space.resource_revisions (organization_id,space_id,resource_id,"
                "revision,key_id,encrypted_metadata,encrypted_record,object_key,"
                "author_principal_id,content_bytes) SELECT organization_id,space_id,resource_id,"
                "%s,key_id,encrypted_metadata,encrypted_record,object_key,author_principal_id,"
                "content_bytes FROM space.resource_revisions WHERE resource_id=%s AND revision=%s",
                (revision, saved["id"], saved["revision"]),
            )
            connection.execute(
                "UPDATE space.resources SET current_revision=%s WHERE id=%s",
                (revision, saved["id"]),
            )
        brain.apply(
            connection,
            memory_id=saved["id"],
            revision=revision,
            claims=claims,
            model_name=model,
            vectors={c["id"]: [1.0] + [0.0] * 1535 for c in claims},
        )
    return {**saved, "revision": revision}


@pytest.mark.parametrize("side", ["source", "target"])
@pytest.mark.parametrize("remodel", [False, True])
def test_hash_pins_survive_metadata_and_unchanged_reembedding(commands, side, remodel):
    source, claims, target, targets = hash_linked(commands, relation="expands_on")
    saved, values = (source, claims) if side == "source" else (target, targets)
    current = reindex(
        commands,
        saved,
        [{**c, "importance": 3, "cues": ["new cue"]} for c in values],
        model="new-model" if remodel else "fixture",
        advance=True,
    )
    outgoing = read(commands, source)["links"]
    incoming = read(commands, target)["backlinks"]
    assert len(outgoing) == len(incoming) == 1
    assert outgoing[0]["relation"] == "expands_on"
    assert outgoing[0]["revision"] == (2 if side == "target" else 1)
    assert incoming[0]["revision"] == (2 if side == "source" else 1)
    # Historical encrypted references are still valid only because both text hashes match.
    write(commands, current if side == "source" else source, claims)
    assert len(read(commands, source)["links"]) == 1


@pytest.mark.parametrize("side", ["source", "target"])
@pytest.mark.parametrize("operation", ["edit", "forget", "remove"])
def test_hash_links_pruned_on_text_change_or_forget_cannot_reinsert(commands, side, operation):
    source, claims, target, targets = hash_linked(commands)
    saved, values = (source, claims) if side == "source" else (target, targets)
    service, owner, space, _ = commands
    if operation == "forget":
        with service.db.transaction(owner) as connection:
            service._scope(connection, owner, space)
            brain.forget(connection, saved["id"])
    else:
        changed = [] if operation == "remove" else [{**values[0], "text": "Edited text"}]
        reindex(commands, saved, changed)
    assert read(commands, source)["links"] == []
    assert read(commands, target)["backlinks"] == []
    write(commands, source, claims)
    assert read(commands, source)["links"] == []
    with service.db.transaction(owner) as connection:
        service._scope(connection, owner, space)
        assert (
            connection.execute("SELECT count(*) AS n FROM space.memory_links").fetchone()["n"] == 0
        )


@pytest.mark.parametrize("side", ["source", "target"])
def test_legacy_edges_require_target_revision_and_current_source_replacement(commands, side):
    source, claims, target, targets = linked(commands)
    saved, values = (source, claims) if side == "source" else (target, targets)
    current = reindex(commands, saved, values, advance=True)
    assert read(commands, source)["links"] == []
    assert read(commands, target)["backlinks"] == []
    write(commands, current if side == "source" else source, claims)
    assert len(read(commands, source)["links"]) == (1 if side == "source" else 0)


@pytest.mark.parametrize("bad_hash", ["not-a-hash", "00" * 31, "gg" * 32, 1, None])
def test_partial_or_malformed_hashes_are_not_legacy_links(commands, bad_hash):
    source, claims, _, _ = hash_linked(commands)
    claims[0]["relationships"]["links"][0]["target_text_hash"] = bad_hash
    write(commands, source, claims)
    assert read(commands, source)["links"] == []


@pytest.mark.parametrize("selection", ["exact", "approximate"])
def test_search_excludes_focal_node_before_limit(commands, selection):
    source, _ = memory(commands, count=6)
    target, targets = memory(commands)
    service, owner, space, _ = commands
    with service.db.transaction(owner) as connection:
        service._scope(connection, owner, space)
        rows = brain.search(
            connection,
            [1.0] + [0.0] * 1535,
            "fixture",
            1,
            config=brain.RetrievalConfig(selection=selection),
            exclude_memory_id=source["id"],
        )
    assert len(rows) == 1
    assert str(rows[0]["memory_id"]) == target["id"]
    assert str(rows[0]["claim_id"]) == targets[0]["id"]


@pytest.mark.parametrize("side", ["source", "target"])
def test_read_rechecks_live_text_hash_even_without_reconciliation(commands, side):
    source, _, target, _ = hash_linked(commands)
    service, owner, space, _ = commands
    saved = source if side == "source" else target
    with service.db.transaction(owner) as connection:
        service._scope(connection, owner, space)
        connection.execute(
            "UPDATE space.memory_claims SET text_hash=%s WHERE memory_id=%s",
            (brain.text_hash("Different indexed content"), saved["id"]),
        )
    assert read(commands, source)["links"] == []
    assert read(commands, target)["backlinks"] == []
