"""Parameters: disposable PostgreSQL fixtures.
What it does: tests explicit node edges independently from claim lifetimes.
Output: real database lifecycle, transaction and isolation evidence.
"""

from uuid import UUID, uuid4

import psycopg
import pytest
from test_database_setup import database as database_fixture
from test_memory_link_index import hash_linked, memory, read, reindex
from test_workspace import command_database as command_database_fixture
from test_workspace import commands as commands_fixture

from space import brain, memory_link_index
from space.security.auth import Identity

database = database_fixture
command_database = command_database_fixture
commands = commands_fixture


def node(commands, count=1):
    """Parameters: fixture/count. What it does: marks a memory node. Output: node/claims."""
    saved, claims = memory(commands, count)
    service, owner, space, _ = commands
    with service.db.transaction(owner) as connection:
        service._scope(connection, owner, space)
        connection.execute(
            "UPDATE space.resources SET memory_kind='memory' WHERE id=%s", (saved["id"],)
        )
    return saved, claims


def write_nodes(commands, source, links, claims=()):
    """Parameters: fixtures and links. What it does: syncs both indexes. Output: none."""
    service, owner, space, _ = commands
    with service.db.transaction(owner) as connection:
        service._scope(connection, owner, space)
        memory_link_index.replace(connection, source["id"], source["revision"], claims, links=links)


def test_explicit_nodes_have_no_claim_ids_and_both_relations(commands):
    source, _ = node(commands, 0)
    target, _ = node(commands, 0)
    links = [{"resource_id": target["id"], "relation": r} for r in ("related", "expands_on")]
    write_nodes(commands, source, links + links)
    outgoing = read(commands, source)["links"]
    incoming = read(commands, target)["backlinks"]
    assert len(outgoing) == len(incoming) == 2
    assert {item["relation"] for item in outgoing} == {"related", "expands_on"}
    for entries, other in ((outgoing, target), (incoming, source)):
        for item in entries:
            assert item == {
                "entry_id": None,
                "resource_id": other["id"],
                "related_entry_id": None,
                "revision": 1,
                "relation": item["relation"],
                "origin": "explicit",
            }


@pytest.mark.parametrize("side", ["source", "target"])
def test_explicit_links_survive_claim_rebuild_edit_and_forget(commands, side):
    source, claims = node(commands)
    target, targets = node(commands)
    write_nodes(commands, source, [{"resource_id": target["id"], "relation": "expands_on"}])
    saved, values = (source, claims) if side == "source" else (target, targets)
    current = reindex(commands, saved, [{**values[0], "text": "New claim text"}], advance=True)
    reindex(commands, current, values, model="new-model")
    service, owner, space, _ = commands
    with service.db.transaction(owner) as connection:
        service._scope(connection, owner, space)
        brain.forget(connection, saved["id"])
    outgoing, incoming = read(commands, source)["links"], read(commands, target)["backlinks"]
    assert len(outgoing) == len(incoming) == 1
    assert outgoing[0]["revision"] == (2 if side == "target" else 1)
    assert incoming[0]["revision"] == (2 if side == "source" else 1)


def test_explicit_and_inferred_edges_coexist_and_share_limit(commands):
    source, claims, target, _ = hash_linked(commands)
    service, owner, space, _ = commands
    with service.db.transaction(owner) as connection:
        service._scope(connection, owner, space)
        connection.execute(
            "UPDATE space.resources SET memory_kind='memory' WHERE id=ANY(%s::uuid[])",
            ([source["id"], target["id"]],),
        )
    links = [{"resource_id": target["id"], "relation": "related"}]
    write_nodes(commands, source, links, claims)
    for saved, direction in ((source, "links"), (target, "backlinks")):
        page = read(commands, saved, 1)
        assert [item["origin"] for item in page[direction]] == ["explicit"]
        assert page["has_more"] is False
    with service.db.transaction(owner) as connection:
        service._scope(connection, owner, space)
        assert (
            connection.execute("SELECT count(*) AS n FROM space.memory_links").fetchone()["n"] == 1
        )
    write_nodes(commands, source, [], claims)
    assert [item["origin"] for item in read(commands, source)["links"]] == ["inferred"]


def test_explicit_edges_skip_self_nonmemory_missing_and_stale_source(commands):
    source, _ = node(commands)
    target, _ = node(commands)
    document, _ = memory(commands)
    good = {"resource_id": target["id"], "relation": "related"}
    links = [
        good,
        {**good, "resource_id": source["id"]},
        {**good, "resource_id": document["id"]},
        {**good, "resource_id": str(uuid4())},
        {**good, "resource_id": "bad-uuid"},
        {**good, "relation": "invented"},
    ]
    write_nodes(commands, source, links)
    assert len(read(commands, source)["links"]) == 1
    write_nodes(commands, {**source, "revision": 2}, [good])
    assert read(commands, source)["links"] == []


def test_explicit_replacement_rolls_back_with_inferred_index(commands):
    source, _ = node(commands)
    target, _ = node(commands)
    write_nodes(commands, source, [{"resource_id": target["id"], "relation": "related"}])
    service, owner, space, _ = commands
    with pytest.raises(RuntimeError, match="abort"):
        with service.db.transaction(owner) as connection:
            service._scope(connection, owner, space)
            memory_link_index.replace(connection, source["id"], 1, [], links=[])
            raise RuntimeError("abort")
    assert len(read(commands, source)["links"]) == 1


def test_explicit_nodes_have_strong_rls_and_composite_foreign_keys(commands):
    source, _ = node(commands)
    target, _ = node(commands)
    write_nodes(commands, source, [{"resource_id": target["id"], "relation": "related"}])
    service, owner, space, _ = commands
    other = Identity("https://identity.invalid", str(uuid4()), 4102444800)
    other_space = UUID(service.bootstrap(other)["id"])
    other_commands = (service, other, other_space, commands[3])
    foreign, _ = node(other_commands)
    write_nodes(other_commands, foreign, [{"resource_id": target["id"], "relation": "related"}])
    assert read(other_commands, foreign)["links"] == []
    with service.db.transaction(other) as connection:
        scope = service._scope(connection, other, other_space)
        assert read(other_commands, source)["links"] == []
        assert (
            connection.execute("SELECT count(*) AS n FROM space.memory_node_links").fetchone()["n"]
            == 0
        )
    sql = "INSERT INTO space.memory_node_links VALUES (%s,%s,%s,%s,'related')"
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with service.db.transaction(other) as connection:
            service._scope(connection, other, other_space)
            connection.execute(
                sql,
                (
                    scope["organization_id"],
                    other_space,
                    foreign["id"],
                    target["id"],
                ),
            )
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with service.db.transaction(owner) as connection:
            service._scope(connection, owner, space)
            connection.execute(
                sql,
                (
                    scope["organization_id"],
                    other_space,
                    source["id"],
                    target["id"],
                ),
            )


@pytest.mark.parametrize("side", ["source", "target"])
def test_whole_node_delete_cascades_explicit_edges(commands, side):
    source, _ = node(commands)
    target, _ = node(commands)
    write_nodes(commands, source, [{"resource_id": target["id"], "relation": "related"}])
    service, owner, space, _ = commands
    saved = source if side == "source" else target
    with service.db.transaction(owner) as connection:
        service._scope(connection, owner, space)
        connection.execute("SELECT set_config('space.quota_deletion','on',true)")
        brain.forget(connection, saved["id"])
        connection.execute(
            "DELETE FROM space.events WHERE command_id IN "
            "(SELECT id FROM space.commands WHERE resource_id=%s)",
            (saved["id"],),
        )
        connection.execute("DELETE FROM space.commands WHERE resource_id=%s", (saved["id"],))
        connection.execute(
            "DELETE FROM space.resource_revisions WHERE resource_id=%s", (saved["id"],)
        )
        connection.execute("DELETE FROM space.resources WHERE id=%s", (saved["id"],))
    assert read(commands, source)["links"] == []
    assert read(commands, target)["backlinks"] == []
    write_nodes(commands, source, [{"resource_id": target["id"], "relation": "related"}])
    assert read(commands, source)["links"] == []


def test_node_link_cap_and_mixed_origin_limit_are_applied_before_return(commands):
    source, claims, target, _ = hash_linked(commands)
    service, owner, space, _ = commands
    with service.db.transaction(owner) as connection:
        service._scope(connection, owner, space)
        connection.execute(
            "UPDATE space.resources SET memory_kind='memory' WHERE id=ANY(%s::uuid[])",
            ([source["id"], target["id"]],),
        )
    others = [node(commands, 0)[0] for _ in range(21)]
    write_nodes(
        commands,
        source,
        [{"resource_id": other["id"], "relation": "related"} for other in others],
        claims,
    )
    page = read(commands, source)
    assert len(page["links"]) == 21
    assert sum(item["origin"] == "explicit" for item in page["links"]) == 20
    assert sum(item["origin"] == "inferred" for item in page["links"]) == 1
    assert read(commands, source, 20)["has_more"] is True
    assert read(commands, source, 21)["has_more"] is False
    assert read(commands, others[-1])["backlinks"] == []


@pytest.mark.parametrize("direction", ["links", "backlinks"])
@pytest.mark.parametrize("origin", ["explicit", "inferred"])
@pytest.mark.parametrize("filter_name", ["visible_ids", "fresh_ids"])
def test_visibility_filters_endpoints_before_limits(commands, direction, origin, filter_name):
    focal, focal_claims = node(commands)
    others = [node(commands) for _ in range(3)]
    service, owner, space, _ = commands
    for other, other_claims in others:
        source, claims, target, targets = (
            (focal, focal_claims, other, other_claims)
            if direction == "links"
            else (other, other_claims, focal, focal_claims)
        )
        if origin == "explicit":
            links = [{"resource_id": target["id"], "relation": "related"}]
            if direction == "links":
                links = [{"resource_id": saved["id"], "relation": "related"} for saved, _ in others]
            write_nodes(commands, source, links)
        else:
            links = [
                {
                    "resource_id": target["id"],
                    "entry_id": targets[0]["id"],
                    "revision": 1,
                    "relation": "related",
                    "confidence": 0.95,
                }
            ]
            if direction == "links":
                links = [
                    {
                        "resource_id": saved["id"],
                        "entry_id": values[0]["id"],
                        "revision": 1,
                        "relation": "related",
                        "confidence": 0.95,
                    }
                    for saved, values in others
                ]
            claims[0]["relationships"] = {"status": "matched", "links": links}
            write_nodes(commands, source, [], claims)
    unfiltered = read(commands, focal)[direction]
    assert len(unfiltered) == 3
    last_id = unfiltered[-1]["resource_id"]
    with service.db.transaction(owner) as connection:
        service._scope(connection, owner, space)
        page = memory_link_index.read(
            connection,
            focal["id"],
            1,
            **{filter_name: {focal["id"], last_id}},
        )
        if origin == "explicit" and filter_name == "fresh_ids":
            assert len(page[direction]) == 1 and page["has_more"] is True
        else:
            assert [item["resource_id"] for item in page[direction]] == [last_id]
            assert page["has_more"] is False
            # Both endpoints, including the focal memory, must be permitted.
            assert (
                memory_link_index.read(
                    connection,
                    focal["id"],
                    1,
                    **{filter_name: {last_id}},
                )[direction]
                == []
            )
            assert (
                memory_link_index.read(
                    connection,
                    focal["id"],
                    1,
                    **{filter_name: []},
                )[direction]
                == []
            )


def test_neighbors_union_focal_and_both_origins_without_unrelated_nodes(commands):
    source, _, target, _ = hash_linked(commands)
    service, owner, space, _ = commands
    explicit, _ = node(commands, 0)
    unrelated, _ = node(commands, 0)
    with service.db.transaction(owner) as connection:
        service._scope(connection, owner, space)
        connection.execute(
            "UPDATE space.resources SET memory_kind='memory' WHERE id=%s", (target["id"],)
        )
    write_nodes(commands, explicit, [{"resource_id": target["id"], "relation": "related"}])
    with service.db.transaction(owner) as connection:
        service._scope(connection, owner, space)
        assert memory_link_index.neighbor_ids(connection, target["id"]) == sorted(
            [
                source["id"],
                target["id"],
                explicit["id"],
            ]
        )
        assert memory_link_index.neighbor_ids(connection, source["id"]) == sorted(
            [
                source["id"],
                target["id"],
            ]
        )
        assert memory_link_index.neighbor_ids(connection, explicit["id"]) == sorted(
            [
                explicit["id"],
                target["id"],
            ]
        )
        assert memory_link_index.neighbor_ids(connection, unrelated["id"]) == [unrelated["id"]]
        assert memory_link_index.neighbor_ids(connection, uuid4()) == []
    other = Identity("https://identity.invalid", str(uuid4()), 4102444800)
    other_space = UUID(service.bootstrap(other)["id"])
    with service.db.transaction(other) as connection:
        service._scope(connection, other, other_space)
        assert memory_link_index.neighbor_ids(connection, target["id"]) == []
