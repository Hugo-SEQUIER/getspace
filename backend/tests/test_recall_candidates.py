"""Parameters: pure fixtures and opt-in disposable PostgreSQL.
What it does: exercises bounded lexical and explicit-link claim candidates.
Output: regression evidence without live databases or provider calls.
"""

from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from test_database_setup import database as database_fixture
from test_memora_pipeline import Models, fixture_embedding
from test_workspace import change
from test_workspace import command_database as command_database_fixture
from test_workspace import commands as commands_fixture

from space import brain
from space import recall_candidates as candidates
from space.memory import RecallMetadata, RememberClaims
from space.terms import query_fingerprints, term_key

command_database = command_database_fixture
commands = commands_fixture
database = database_fixture


def view(resource, entry, **values):
    return {"id": resource, "entry_id": entry, "title": "Title", "body": "Body", **values}


def remember(service, owner, space, *, title="Memory", claims=None, links=()):
    service.memory_models = Models()
    return service.remember_claims(
        owner,
        space,
        RememberClaims(
            command_id=uuid4(),
            title=title,
            claims=claims or [{"index": "Detail", "value": "Ordinary background."}],
            links=[{"resource_id": target["id"], "relation": "related"} for target in links],
        ),
    )


def exclude(service, owner, space, resource):
    internal = service.get_resource(owner, space, UUID(resource["id"]), _internal=True)
    service._save(
        owner,
        space,
        change().model_copy(
            update={
                "resource_id": UUID(resource["id"]),
                "expected_revision": resource["revision"],
                "title": internal["title"],
                "body": internal["body"],
                "recall": RecallMetadata.model_validate({**internal["recall"], "excluded": True}),
            }
        ),
        memory=internal["_memory"],
    )


def edge(service, owner, space, source, target):
    with service.db.transaction(owner) as connection:
        scope = service._scope(connection, owner, space)
        connection.execute(
            "INSERT INTO space.memory_node_links "
            "(organization_id,space_id,source_memory_id,target_memory_id,relation) "
            "VALUES (%s,%s,%s,%s,'related')",
            (scope["organization_id"], space, source["id"], target["id"]),
        )


def test_merge_deduplicates_composite_keys_and_keeps_only_vector_scores():
    first = view("a", "1", score=0.12)
    hybrid = view("a", "2", score=-0.2)
    lex = view("b", "1", score=900)
    result = candidates.merge([first, first, hybrid], [lex, hybrid, lex], 3)
    assert result[0] == hybrid | {"candidate_origin": "hybrid"}
    by_id = {(item["id"], item["entry_id"]): item for item in result}
    assert by_id[("a", "1")]["score"] == 0.12
    assert by_id[("a", "2")]["score"] == -0.2
    assert "score" not in by_id[("b", "1")]
    assert by_id[("b", "1")]["candidate_origin"] == "lexical"
    assert first["score"] == 0.12 and lex["score"] == 900
    assert len(candidates.merge([first, hybrid], [lex], 1)) == 1


def test_merge_empty_lexical_preserves_vector_order_not_id_or_cosine_order():
    records = [view("z", "2", score=0.1), view("a", "1", score=0.9)]
    assert candidates.merge(records, [], 4) == [
        record | {"candidate_origin": "vector"} for record in records
    ]
    assert candidates.merge(records, records, 0) == []
    assert candidates.merge([], [], 2) == []


def test_claim_ranking_matches_index_and_value_with_stable_composite_ties():
    records = [
        view("b", "2", title="Mira", body="ZX-4912"),
        view("a", "2", title="Mira", body="ZX-4912"),
        view("a", "1", title="Mira", body="ZX-4912"),
        view("0", "0", title="Unrelated", body="Weather"),
    ]
    assert [(v["id"], v["entry_id"]) for v in candidates._rank(records, "Mira ZX-4912")] == [
        ("a", "1"),
        ("a", "2"),
        ("b", "2"),
    ]
    assert candidates._rank(records, "nonmatching") == []


def test_empty_requests_do_not_read_keys_or_database():
    workspace = object()
    assert candidates.lexical(workspace, None, None, "", 5) == []
    assert candidates.lexical(workspace, None, None, "code", 0) == []
    assert candidates.linked(workspace, None, None, [], "code", 5) == []
    assert candidates.linked(workspace, None, None, ["seed"], "code", 0) == []


def test_preselection_uses_keyed_query_and_fixed_bounds(monkeypatch):
    executed = []

    @contextmanager
    def transaction(identity):
        yield SimpleNamespace(
            execute=lambda sql, args: (
                executed.append((sql, args)) or SimpleNamespace(fetchall=lambda: [])
            )
        )

    workspace = SimpleNamespace(
        db=SimpleNamespace(transaction=transaction),
        _scope=lambda *args: None,
        _key=lambda *args: (None, SimpleNamespace(key=SimpleNamespace(material=b"a" * 32))),
        _items_for=lambda *args, **kwargs: ([], {}, None),
    )
    assert candidates.lexical(workspace, None, None, "Mira ZX-4912", 1000) == []
    sql, args = executed[0]
    assert sql == candidates.LEXICAL_SQL
    assert args[1] == args[2] == query_fingerprints("Mira ZX-4912", term_key(b"a" * 32))
    assert args[-1] == 32
    assert "Mira" not in sql and "ZX-4912" not in sql
    assert all(isinstance(term, bytes) for term in args[1])
    selected = []

    def items(identity, space_id, ids, **kwargs):
        selected.append(ids)
        return ([{"id": id_, "revision": 1} for id_ in ids], {}, None)

    workspace._items_for = items
    monkeypatch.setattr(candidates, "_hydrate", lambda *args, **kwargs: [])
    assert candidates.linked(workspace, None, None, list("abcdef"), "code", 1000) == []
    assert selected == [list("abcde")]
    assert executed[-1][0] == candidates.LINKED_SQL
    assert executed[-1][1][-1] == 25


def test_lexical_finds_exact_claim_outside_vector_pool_and_keeps_metadata(commands):
    service, owner, space, _ = commands
    travel = remember(service, owner, space, claims=[{"index": "Route", "value": "By car."}])
    exact = remember(
        service,
        owner,
        space,
        title="Unsearchable title",
        claims=[
            {"index": "Dr Valerian", "value": "Account ZX-4912 belongs to this doctor."},
            {"index": "Weather", "value": "Heavy rain."},
        ],
    )
    with service.db.transaction(owner) as connection:
        service._scope(connection, owner, space)
        hits = brain.search(connection, fixture_embedding("car"), "fixture-embedding", 1)
    assert str(hits[0]["memory_id"]) == travel["id"]
    found = candidates.lexical(service, owner, space, "Dr Valerian ZX-4912", 4)
    assert len(found) == 1 and found[0]["id"] == exact["id"]
    assert found[0]["title"] == "Dr Valerian"
    assert found[0]["body"] == exact["memory"]["claims"][0]["value"]
    assert found[0]["revision"] == exact["revision"] and "recall" in found[0]
    assert found[0]["candidate_origin"] == "lexical" and "score" not in found[0]
    assert candidates.lexical(service, owner, space, "Unsearchable title", 4) == []


def test_lexical_rejects_foreign_excluded_forgotten_wrong_model_and_old_revisions(
    commands,
    monkeypatch,
):
    service, owner, space, _ = commands
    claims = [{"index": "ZX-4912", "value": "Private detail."}]
    saved = [remember(service, owner, space, claims=claims) for _ in range(6)]
    keep, excluded, forgotten, old_claim, old_terms, wrong_model = saved
    exclude(service, owner, space, excluded)
    stranger = replace(owner, subject=str(uuid4()))
    foreign_space = service.bootstrap(stranger)["id"]
    foreign = remember(service, stranger, foreign_space, claims=claims)
    with service.db.transaction(owner) as connection:
        service._scope(connection, owner, space)
        connection.execute("DELETE FROM space.memory_claims WHERE memory_id=%s", (forgotten["id"],))
        connection.execute(
            "UPDATE space.memory_claims SET revision=revision+1 WHERE memory_id=%s",
            (old_claim["id"],),
        )
        connection.execute(
            "UPDATE space.resource_terms SET revision=revision+1 WHERE resource_id=%s",
            (old_terms["id"],),
        )
        connection.execute(
            "UPDATE space.memory_claims SET embedding_model='old-model' WHERE memory_id=%s",
            (wrong_model["id"],),
        )
    loaded = []
    original = service._bodies

    def bodies(identity, space_id, items, keys, scope, **kwargs):
        loaded.extend(item["id"] for item in items)
        return original(identity, space_id, items, keys, scope, **kwargs)

    monkeypatch.setattr(service, "_bodies", bodies)
    found = candidates.lexical(service, owner, space, "ZX-4912", 20, model_name="fixture-embedding")
    assert [item["id"] for item in found] == [keep["id"]]
    assert loaded == [keep["id"]]
    assert foreign["id"] not in loaded
    found = candidates.lexical(service, owner, space, "ZX-4912", 20)
    assert {item["id"] for item in found} == {keep["id"], wrong_model["id"]}


def test_sql_resource_caps_bound_metadata_and_body_hydration(commands, monkeypatch):
    service, owner, space, _ = commands
    seed = remember(service, owner, space)
    for index in range(36):
        remember(
            service,
            owner,
            space,
            claims=[
                {"index": f"shared {index}", "value": "Shared code ZX-4912."},
            ],
            links=[seed],
        )
    seen = {"metadata": 0, "body": 0}
    decrypt = service._unpack
    bodies = service._bodies

    def counting(key, scope, resource_id, revision, purpose, value):
        if purpose == "metadata":
            seen["metadata"] += 1
        return decrypt(key, scope, resource_id, revision, purpose, value)

    def hydrate(identity, space_id, items, keys, scope, **kwargs):
        seen["body"] += len(items)
        return bodies(identity, space_id, items, keys, scope, **kwargs)

    monkeypatch.setattr(service, "_unpack", counting)
    monkeypatch.setattr(service, "_bodies", hydrate)
    found = candidates.lexical(service, owner, space, "ZX-4912", 1000)
    assert len(found) == 32
    assert seen == {"metadata": 32, "body": 32}
    seen.update(metadata=0, body=0)
    found = candidates.linked(service, owner, space, [seed["id"]], "no match", 1000)
    assert len(found) == 25
    assert seen == {"metadata": 26, "body": 25}
    assert all("score" not in item for item in found)


def test_links_are_bidirectional_explicit_one_hop_and_keep_nonlexical_claims(commands):
    service, owner, space, _ = commands
    seed = remember(service, owner, space)
    outgoing = remember(
        service,
        owner,
        space,
        claims=[
            {"index": "Preference", "value": "I love quiet places."},
            {"index": "ZX-4912", "value": "Exact match."},
        ],
    )
    incoming = remember(service, owner, space, links=[seed])
    two_hop = remember(service, owner, space, links=[outgoing])
    inferred = remember(service, owner, space)
    edge(service, owner, space, seed, outgoing)
    edge(service, owner, space, outgoing, seed)
    with service.db.transaction(owner) as connection:
        scope = service._scope(connection, owner, space)
        connection.execute(
            "INSERT INTO space.memory_links "
            "(organization_id,space_id,source_memory_id,source_claim_id,source_revision,"
            "target_memory_id,target_claim_id,target_revision) "
            "VALUES (%s,%s,%s,%s,1,%s,%s,1)",
            (
                scope["organization_id"],
                space,
                seed["id"],
                seed["memory"]["claims"][0]["id"],
                inferred["id"],
                inferred["memory"]["claims"][0]["id"],
            ),
        )
        connection.execute("UPDATE space.memory_claims SET cue_hashes=ARRAY[%s::bytea]", (b"cue",))
    found = candidates.linked(service, owner, space, [seed["id"], seed["id"]], "ZX-4912", 20)
    assert len(found) == 3
    assert found[0]["title"] == "ZX-4912"
    assert {item["id"] for item in found} == {outgoing["id"], incoming["id"]}
    assert all(item["linked_from"] == [seed["id"]] for item in found)
    assert two_hop["id"] not in {item["id"] for item in found}
    assert candidates.linked(service, owner, space, [inferred["id"]], "", 20) == []
    no_seed_endpoints = candidates.linked(
        service,
        owner,
        space,
        [seed["id"], outgoing["id"]],
        "",
        20,
    )
    assert {item["id"] for item in no_seed_endpoints} == {incoming["id"], two_hop["id"]}


def test_link_exclusions_no_source_fanout_and_current_model_filter(commands, monkeypatch):
    service, owner, space, _ = commands
    source = remember(service, owner, space)
    visible = remember(service, owner, space, links=[source])
    excluded = remember(service, owner, space, links=[source])
    forgotten = remember(service, owner, space, links=[source])
    wrong_model = remember(service, owner, space, links=[source])
    exclude(service, owner, space, excluded)
    with service.db.transaction(owner) as connection:
        service._scope(connection, owner, space)
        connection.execute("DELETE FROM space.memory_claims WHERE memory_id=%s", (forgotten["id"],))
        connection.execute(
            "UPDATE space.memory_claims SET embedding_model='old-model' WHERE memory_id=%s",
            (wrong_model["id"],),
        )
    found = candidates.linked(
        service,
        owner,
        space,
        [source["id"]],
        "",
        20,
        model_name="fixture-embedding",
    )
    assert [item["id"] for item in found] == [visible["id"]]
    found = candidates.linked(service, owner, space, [source["id"]], "", 20)
    assert {item["id"] for item in found} == {visible["id"], wrong_model["id"]}
    exclude(service, owner, space, source)

    def no_hydration(*args, **kwargs):
        pytest.fail("An excluded seed must not query or hydrate any linked candidates")

    monkeypatch.setattr(candidates, "_hydrate", no_hydration)
    assert candidates.linked(service, owner, space, [source["id"]], "", 20) == []


def test_hydration_rejects_changed_head_before_reading_any_body():
    workspace = SimpleNamespace(
        _item_rows=lambda *args, **kwargs: ([{"resource_id": "resource", "revision": 2}], {}),
    )
    selection = candidates._selected_rows(
        workspace,
        None,
        None,
        None,
        [{"resource_id": "resource", "revision": 1}],
        model_name=None,
    )
    assert candidates._hydrate(workspace, None, None, selection, None) == []


def test_lexical_rarity_weighting_is_scoped_to_current_memory_terms(commands):
    service, owner, space, _ = commands
    rare = remember(service, owner, space, claims=[{"index": "Rare", "value": "ZX-4912"}])
    for _ in range(4):
        remember(service, owner, space, claims=[{"index": "Common", "value": "policy enterprise"}])
    for _ in range(5):
        service.save(
            owner,
            space,
            change().model_copy(update={"title": "ZX-4912", "body": "ZX-4912"}),
        )
    _, scoped = service._key(owner, space)
    terms = query_fingerprints("ZX-4912 policy enterprise", term_key(scoped.key.material))
    with service.db.transaction(owner) as connection:
        service._scope(connection, owner, space)
        rows = connection.execute(
            candidates.LEXICAL_SQL,
            (space, terms, terms, None, None, 1),
        ).fetchall()
    assert [str(row["resource_id"]) for row in rows] == [rare["id"]]


def test_mixed_claim_models_filter_each_claim_not_only_the_resource(commands):
    service, owner, space, _ = commands
    source = remember(service, owner, space)
    target = remember(
        service,
        owner,
        space,
        claims=[
            {"index": "ZX-4912 first", "value": "First value."},
            {"index": "ZX-4912 second", "value": "Second value."},
        ],
        links=[source],
    )
    first, second = target["memory"]["claims"]
    with service.db.transaction(owner) as connection:
        service._scope(connection, owner, space)
        connection.execute(
            "UPDATE space.memory_claims SET embedding_model='old-model' WHERE claim_id=%s",
            (second["id"],),
        )
    lexical = candidates.lexical(
        service,
        owner,
        space,
        "ZX-4912",
        20,
        model_name="fixture-embedding",
    )
    linked = candidates.linked(
        service,
        owner,
        space,
        [source["id"]],
        "",
        20,
        model_name="fixture-embedding",
    )
    assert [item["entry_id"] for item in lexical] == [first["id"]]
    assert [item["entry_id"] for item in linked] == [first["id"]]
    assert len(candidates.lexical(service, owner, space, "ZX-4912", 20)) == 2
    assert len(candidates.linked(service, owner, space, [source["id"]], "", 20)) == 2


def test_link_seeds_are_scoped_and_current_index_is_required_at_both_endpoints(commands):
    service, owner, space, _ = commands
    source = remember(service, owner, space)
    target = remember(service, owner, space, links=[source])
    stranger = replace(owner, subject=str(uuid4()))
    foreign_space = service.bootstrap(stranger)["id"]
    foreign = remember(service, stranger, foreign_space)
    remember(service, stranger, foreign_space, links=[foreign])
    assert candidates.linked(service, owner, space, [foreign["id"]], "", 20) == []
    with service.db.transaction(owner) as connection:
        service._scope(connection, owner, space)
        connection.execute(
            "UPDATE space.memory_claims SET revision=revision+1 WHERE memory_id=%s",
            (target["id"],),
        )
    assert candidates.linked(service, owner, space, [source["id"]], "", 20) == []
    assert candidates.linked(service, owner, space, [target["id"]], "", 20) == []
