from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from test_archive_limits import archive
from test_database_setup import database as database_fixture
from test_memora_pipeline import fixture_embedding, source
from test_memora_workspace import claim_change
from test_restore_staging import stage
from test_workspace import command_database as command_database_fixture
from test_workspace import commands as commands_fixture
from test_workspace_archive import read_archive

from space import archive_schema, brain, restore_merge
from space.workspace import CommandError

database = database_fixture
command_database = command_database_fixture
commands = commands_fixture


def memory_entry():
    value = source()
    claim = {
        "id": str(uuid4()),
        "index": "Commute",
        "value": "Mira travels by car.",
        "memory_type": "factual",
        "created_at": value["updated_at"],
    }
    value["recall"].update(category="memory")
    value["_memory"] = {"version": 2, "claims": [claim], "forgotten": [], "entries": []}
    value["memory_index"] = [
        {"id": claim["id"], "model": "fixture-embedding", "vector": fixture_embedding("car")}
    ]
    return {"entity": "resource", "current": True, "value": value}


def linked_archive():
    origin, target = memory_entry(), memory_entry()
    target_value = target["value"]
    origin["value"]["_memory"]["claims"][0]["relationships"] = {
        "status": "matched",
        "links": [
            {
                "resource_id": target_value["id"],
                "entry_id": target_value["_memory"]["claims"][0]["id"],
                "revision": 1,
                "relation": "related",
                "confidence": 0.95,
            }
        ],
    }
    return archive(entries=[origin, target])


def relationships(value):
    return value["_memory"]["claims"][0]["relationships"]


def test_archive_accepts_legacy_and_dangling_historical_relationships():
    document = linked_archive()
    document["entries"].pop()
    before = deepcopy(document)
    groups = archive_schema.validate_archive(document)
    assert len(groups) == 1
    assert document == before
    del document["entries"][0]["value"]["_memory"]["claims"][0]["relationships"]
    assert archive_schema.validate_archive(document)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("resource_id", "not-a-uuid"),
        ("entry_id", "not-a-uuid"),
        ("revision", 0),
        ("relation", "contradicts"),
        ("confidence", 0.89),
        ("confidence", 1.01),
        ("source_text_hash", "invalid"),
        ("target_text_hash", "g" * 64),
        ("source_text_hash", "a" * 64),
    ],
)
def test_archive_rejects_invalid_relationship_links(field, value):
    document = linked_archive()
    relationships(document["entries"][0]["value"])["links"][0][field] = value
    with pytest.raises(ValueError):
        archive_schema.validate_archive(document)


def test_archive_rejects_invalid_relationship_status_and_excess_links():
    document = linked_archive()
    links = relationships(document["entries"][0]["value"])
    links["status"] = "unknown"
    with pytest.raises(ValueError):
        archive_schema.validate_archive(document)
    links["status"] = "matched"
    links["links"] *= 6
    with pytest.raises(ValueError):
        archive_schema.validate_archive(document)


def transform(document, target_offset=0):
    groups = archive_schema.validate_archive(document)
    decisions = {
        "resource:" + id_: {
            "target_id": str(uuid4()),
            "action": "copy",
            "offset": target_offset if position else 0,
            "conflict": False,
        }
        for position, (_, id_) in enumerate(groups)
    }
    result = restore_merge._transform(None, None, None, uuid4(), groups, decisions)
    return result, decisions


def test_restore_remaps_target_resource_and_revision_without_mutating_archive():
    document = linked_archive()
    before = deepcopy(document)
    restored, mappings = transform(document, target_offset=3)
    original_target = document["entries"][1]["value"]
    link = relationships(restored[0]["value"])["links"][0]
    assert link["resource_id"] == mappings["resource:" + original_target["id"]]["target_id"]
    assert link["revision"] == 4
    assert link["entry_id"] == original_target["_memory"]["claims"][0]["id"]
    assert document == before


def test_restore_drops_unmapped_targets_even_when_original_id_could_exist_live():
    document = linked_archive()
    document["entries"].pop()
    restored, _ = transform(document)
    assert relationships(restored[0]["value"]) == {"status": "no_match", "links": []}


@pytest.mark.parametrize("status", ["no_match", "disabled", "unavailable", "input_limit"])
def test_restore_preserves_empty_relationship_outcome(status):
    document = linked_archive()
    relationships(document["entries"][0]["value"]).update(status=status, links=[])
    restored, _ = transform(document)
    assert relationships(restored[0]["value"]) == {"status": status, "links": []}


def commit_archive(service, owner, space, document, choices=None):
    upload = stage(service, owner, space, document)
    review = restore_merge.review(
        service, owner, space, upload.id, restore_merge.Review(choices=choices or {})
    )
    assert not review["issues"]
    restore_merge.commit(
        service,
        owner,
        space,
        upload.id,
        restore_merge.Commit(snapshot=review["snapshot"], choices=choices or {}),
    )
    return {item["id"]: item["target_id"] for item in review["items"]}


@pytest.mark.parametrize("reverse", [False, True])
def test_restore_indexes_links_after_all_targets_and_exports_them(commands, reverse):
    service, owner, space, _ = commands
    document = linked_archive()
    origin, target = [entry["value"] for entry in document["entries"]]
    if reverse:
        document["entries"].reverse()
    commit_archive(service, owner, space, document)
    with service.db.transaction(owner) as connection:
        service._scope(connection, owner, space)
        rows = connection.execute("SELECT * FROM space.memory_links").fetchall()
    assert len(rows) == 1
    exported = read_archive(service, owner, space)
    exported_origin = next(
        e["value"] for e in exported["entries"] if e["value"]["id"] == origin["id"]
    )
    assert relationships(exported_origin) == relationships(origin)
    service.change_memory(
        owner, space, claim_change(target, target["_memory"]["claims"][0]["id"], "forget")
    )
    with service.db.transaction(owner) as connection:
        service._scope(connection, owner, space)
        assert not connection.execute("SELECT * FROM space.memory_links").fetchall()
    after_forget = read_archive(service, owner, space)
    assert archive_schema.validate_archive(after_forget)
    mappings = commit_archive(
        service,
        owner,
        space,
        after_forget,
        {"resource:" + entry["value"]["id"]: "copy" for entry in after_forget["entries"]},
    )
    copied = service.get_resource(owner, space, UUID(mappings[origin["id"]]), _internal=True)
    assert relationships(copied) == {"status": "no_match", "links": []}
    with service.db.transaction(owner) as connection:
        service._scope(connection, owner, space)
        assert not connection.execute("SELECT * FROM space.memory_links").fetchall()


@pytest.mark.parametrize("target_action", ["copy", "replace"])
def test_restore_copy_links_only_to_mapped_current_target(commands, target_action):
    service, owner, space, _ = commands
    document = with_hashes(linked_archive())
    origin, target = [entry["value"] for entry in document["entries"]]
    commit_archive(service, owner, space, document)
    mappings = commit_archive(
        service,
        owner,
        space,
        document,
        {"resource:" + origin["id"]: "copy", "resource:" + target["id"]: target_action},
    )
    copied = service.get_resource(owner, space, UUID(mappings[origin["id"]]), _internal=True)
    link = relationships(copied)["links"][0]
    assert link["resource_id"] == mappings[target["id"]]
    assert link["revision"] == (2 if target_action == "replace" else 1)
    with service.db.transaction(owner) as connection:
        service._scope(connection, owner, space)
        rows = connection.execute(
            "SELECT target_memory_id,target_revision FROM space.memory_links "
            "WHERE source_memory_id=%s",
            (copied["id"],),
        ).fetchall()
    assert [(str(row["target_memory_id"]), row["target_revision"]) for row in rows] == [
        (link["resource_id"], link["revision"])
    ]
    document["entries"].pop()
    detached = commit_archive(service, owner, space, document, {"resource:" + origin["id"]: "copy"})
    detached_copy = service.get_resource(owner, space, UUID(detached[origin["id"]]), _internal=True)
    assert relationships(detached_copy) == {"status": "no_match", "links": []}
    with service.db.transaction(owner) as connection:
        service._scope(connection, owner, space)
        assert not connection.execute(
            "SELECT * FROM space.memory_links WHERE source_memory_id=%s", (detached_copy["id"],)
        ).fetchall()


class RestoreWorkspace:
    def __init__(self, resources):
        self.resources = {(v["id"], v["revision"]): deepcopy(v) for v in resources}
        self.db = self
        self.requested = []

    @contextmanager
    def transaction(self, identity):
        yield self

    def _scope(self, *args, **kwargs):
        pass

    def execute(self, query, parameters):
        (id_,) = parameters
        value = self.get_resource(None, None, UUID(id_))
        return SimpleNamespace(
            fetchall=lambda: [
                {
                    "claim_id": row["id"],
                    "embedding_model": row["model"],
                    "embedding": str(row["vector"]),
                }
                for row in value["memory_index"]
            ]
        )

    def get_resource(self, identity, space, id_, revision=None, **kwargs):
        self.requested.append((str(id_), revision))
        candidates = [
            v
            for (resource_id, rev), v in self.resources.items()
            if resource_id == str(id_) and (revision is None or rev == revision)
        ]
        if not candidates:
            raise CommandError("Missing", 404)
        return deepcopy(max(candidates, key=lambda v: v["revision"]))


def with_hashes(document):
    origin, target = [entry["value"] for entry in document["entries"]]
    link = relationships(origin)["links"][0]
    link["source_text_hash"] = brain.text_hash(origin["_memory"]["claims"][0]["value"]).hex()
    link["target_text_hash"] = brain.text_hash(target["_memory"]["claims"][0]["value"]).hex()
    return document


def merge_live(document, live, actions=None):
    groups = archive_schema.validate_archive(document)
    workspace = RestoreWorkspace(live)
    actions = actions or {}
    decisions = {}
    for entity, id_ in groups:
        previous = [v for v in live if v["id"] == id_]
        action = actions.get(id_, "replace" if previous else "create")
        decisions[entity + ":" + id_] = {
            "action": action,
            "target_id": str(uuid4()) if action == "copy" else id_,
            "offset": max(v["revision"] for v in previous) if action == "replace" else 0,
            "conflict": bool(previous),
        }
    return restore_merge._transform(workspace, None, uuid4(), uuid4(), groups, decisions), decisions


@pytest.mark.parametrize("hashed", [False, True])
def test_restore_live_revision_two_is_not_offset_to_unjudged_revision_four(hashed):
    document = linked_archive()
    if hashed:
        with_hashes(document)
    live_origin, live_target = deepcopy([e["value"] for e in document["entries"]])
    live_origin["revision"] = live_target["revision"] = 2
    relationships(live_origin)["links"][0]["revision"] = 2
    restored, _ = merge_live(document, [live_origin, live_target])
    assert restored[1]["value"]["revision"] == 3
    links = relationships(restored[0]["value"])["links"]
    if hashed:
        assert links[0]["revision"] == 3
    else:
        assert links == []


@pytest.mark.parametrize("hashed", [False, True])
@pytest.mark.parametrize("action", ["copy", "replace"])
@pytest.mark.parametrize("reverse", [False, True])
def test_restore_archive_judgment_never_binds_to_retained_human_edit(hashed, action, reverse):
    document = linked_archive()
    if hashed:
        with_hashes(document)
    original, target = [e["value"] for e in document["entries"]]
    live_target = deepcopy(target)
    live_target["revision"] = 2
    live_target["_memory"]["claims"][0].update(value="HUMAN EDIT", edited=True)
    if reverse:
        document["entries"].reverse()
    restored, mappings = merge_live(document, [live_target], {target["id"]: action})
    source = next(e["value"] for e in restored if e["value"]["id"] == original["id"])
    restored_target = next(
        e["value"]
        for e in restored
        if e["value"]["id"] == mappings["resource:" + target["id"]]["target_id"]
    )
    assert restored_target["_memory"]["claims"][0]["value"] == "HUMAN EDIT"
    assert relationships(source) == {"status": "no_match", "links": []}


@pytest.mark.parametrize("change", ["value", "index", "forgotten", None])
def test_restore_live_hash_judgment_requires_unchanged_target_text_and_title(change):
    document = with_hashes(linked_archive())
    live_origin, live_target = deepcopy([e["value"] for e in document["entries"]])
    archive_target = document["entries"][1]["value"]
    if change in {"value", "index"}:
        archive_target["_memory"]["claims"][0][change] = "Different"
    elif change == "forgotten":
        archive_target["_memory"]["forgotten"] = [archive_target["_memory"]["claims"][0]["id"]]
        archive_target["_memory"]["claims"] = []
        archive_target["memory_index"] = []
    restored, _ = merge_live(document, [live_origin, live_target])
    links = relationships(restored[0]["value"])["links"]
    assert bool(links) == (change is None)
    if links:
        assert links[0]["revision"] == 2


def test_restore_hash_judgment_rejects_changed_source_title():
    document = with_hashes(linked_archive())
    live_origin, live_target = deepcopy([e["value"] for e in document["entries"]])
    document["entries"][0]["value"]["_memory"]["claims"][0]["index"] = "New title"
    restored, _ = merge_live(document, [live_origin, live_target])
    assert relationships(restored[0]["value"])["links"] == []


@pytest.mark.parametrize(
    "links",
    [
        None,
        {},
        [{}],
        [{"resource_id": "bad", "relation": "related"}],
        [{"resource_id": str(uuid4()), "relation": "contradicts"}],
        [{"resource_id": str(uuid4()), "relation": "related", "revision": 1}],
        [{"resource_id": str(uuid4()), "relation": "related"}] * 21,
    ],
)
def test_archive_rejects_invalid_explicit_node_links(links):
    document = linked_archive()
    document["entries"][0]["value"]["_memory"]["links"] = links
    with pytest.raises(ValueError):
        archive_schema.validate_archive(document)


@pytest.mark.parametrize("relation", ["related", "expands_on"])
def test_restore_explicit_links_remap_nodes_not_claim_text(relation):
    document = linked_archive()
    origin, target = [e["value"] for e in document["entries"]]
    origin["_memory"]["links"] = [
        {"resource_id": target["id"], "relation": relation},
        {"resource_id": str(uuid4()), "relation": "related"},
    ]
    live_target = deepcopy(target)
    live_target["_memory"]["claims"][0].update(value="HUMAN EDIT", edited=True)
    restored, mappings = merge_live(document, [live_target], {target["id"]: "copy"})
    assert restored[0]["value"]["_memory"]["links"] == [
        {"resource_id": mappings["resource:" + target["id"]]["target_id"], "relation": relation}
    ]
    assert relationships(restored[0]["value"])["links"] == []


@pytest.mark.parametrize("removed", [False, True])
def test_restore_live_explicit_links_are_authoritative_including_empty_list(removed):
    document = linked_archive()
    origin, target = [e["value"] for e in document["entries"]]
    origin["_memory"]["links"] = [{"resource_id": target["id"], "relation": "related"}]
    live_origin = deepcopy(origin)
    live_origin["_memory"]["links"] = (
        [] if removed else [{"resource_id": str(uuid4()), "relation": "expands_on"}]
    )
    restored, _ = merge_live(document, [live_origin])
    assert restored[0]["value"]["_memory"]["links"] == live_origin["_memory"]["links"]


@pytest.mark.parametrize("reverse", [False, True])
def test_restore_indexes_explicit_links_after_all_nodes_exist(commands, reverse):
    service, owner, space, _ = commands
    document = linked_archive()
    origin, target = [e["value"] for e in document["entries"]]
    origin["_memory"]["links"] = [{"resource_id": target["id"], "relation": "expands_on"}]
    if reverse:
        document["entries"].reverse()
    commit_archive(service, owner, space, document)
    exported = read_archive(service, owner, space)
    current = next(e["value"] for e in exported["entries"] if e["value"]["id"] == origin["id"])
    assert current["_memory"]["links"] == origin["_memory"]["links"]
    with service.db.transaction(owner) as connection:
        service._scope(connection, owner, space)
        rows = connection.execute(
            "SELECT * FROM space.memory_node_links WHERE relation='expands_on'"
        ).fetchall()
    assert len(rows) == 1


def test_archive_rejects_duplicate_explicit_target_nodes():
    document = linked_archive()
    origin, target = [e["value"] for e in document["entries"]]
    origin["_memory"]["links"] = [
        {"resource_id": target["id"], "relation": relation}
        for relation in ("related", "expands_on")
    ]
    with pytest.raises(ValueError, match="distinct"):
        archive_schema.validate_archive(document)


def test_restore_explicit_link_defaults_to_related():
    document = linked_archive()
    origin, target = [e["value"] for e in document["entries"]]
    origin["_memory"]["links"] = [{"resource_id": target["id"]}]
    restored, mappings = transform(document)
    assert restored[0]["value"]["_memory"]["links"] == [
        {"resource_id": mappings["resource:" + target["id"]]["target_id"], "relation": "related"}
    ]
