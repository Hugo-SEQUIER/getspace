"""Reviewable archive merges, retaining history and publishing in one transaction."""

import hashlib
import json
from copy import deepcopy
from dataclasses import replace
from typing import Literal
from uuid import UUID, uuid5

from pydantic import Field

from space import agents, brain, memory_pipeline, restore_staging, workspace_items
from space.memory import Input
from space.model_provider import ModelError
from space.workspace import CommandError, encode
from space.workspace_search import require_snapshot


class Review(Input):
    choices: dict[str, Literal["keep", "copy", "replace"]] = Field(
        default_factory=dict, max_length=10000
    )


class Commit(Review):
    snapshot: int = Field(ge=0, strict=True)


def _name(value):
    return value.get("title", value.get("name", "Untitled"))


def _current(workspace, identity, space_id, entity, id_):
    if entity == "resource":
        return workspace.get_resource(identity, space_id, UUID(id_), _internal=True)
    return workspace_items.get(workspace, identity, space_id, UUID(id_))


def plan(workspace, identity, space_id, restore_id, groups, choices):
    identity = replace(identity, selected_space_id=space_id)
    snapshot = require_snapshot(workspace, identity, space_id, None)
    with workspace.db.transaction(identity) as connection:
        workspace._scope(connection, identity, space_id, human=True)
        rows = connection.execute(
            "SELECT 'resource' AS entity,id,current_revision AS revision,kind FROM space.resources "
            "UNION ALL SELECT 'item',id,current_revision,kind FROM space.workspace_items"
        ).fetchall()
    existing = {(row["entity"], str(row["id"])): row for row in rows}
    profiles = agents.list_profiles(workspace, identity)["items"]
    profile_ids = {p["id"]: p for p in profiles}
    names = {(p["name"].casefold(), p["harness"]): p for p in profiles}
    decisions, issues = {}, []
    for (entity, id_), versions in groups.items():
        value = versions[-1]["value"]
        key = entity + ":" + id_
        old = existing.get((entity, id_))
        if entity == "agent":
            duplicate = names.get((value["name"].casefold(), value["harness"])) or profile_ids.get(
                id_
            )
            action, target, offset = (
                ("keep", duplicate["id"], 0) if duplicate else ("create", id_, 0)
            )
            if not duplicate:
                names[(value["name"].casefold(), value["harness"])] = value
        else:
            action = choices.get(key, "keep") if old else "create"
            target = str(uuid5(restore_id, key)) if action == "copy" else id_
            offset = old["revision"] if old and action == "replace" else 0
            target_kind = (
                "task" if old and old["kind"] == "record" else old["kind"] if old else None
            )
            if action == "replace" and target_kind != value["kind"]:
                issues.append(
                    f"{_name(value)} has a different item type. Keep it or restore a copy."
                )
            if action == "copy" and (entity, target) in existing:
                issues.append(
                    f"A restore copy of {_name(value)} already exists. Start a new review."
                )
        decisions[key] = {
            "key": key,
            "entity": entity,
            "id": id_,
            "target_id": target,
            "title": _name(value),
            "action": action,
            "conflict": bool(old),
            "existing_revision": old["revision"] if old else None,
            "incoming_revisions": len(versions),
            "offset": offset,
        }
    if set(choices) - {key for key, decision in decisions.items() if decision["conflict"]}:
        raise CommandError("A restore choice does not match this archive and Space", 422)
    if (
        len(profiles)
        + sum(d["entity"] == "agent" and d["action"] == "create" for d in decisions.values())
        > agents.MAX_AGENTS
    ):
        issues.append("This merge would exceed the Space's agent identity limit.")
    for (entity, id_), versions in groups.items():
        if decisions[entity + ":" + id_]["action"] == "keep":
            continue
        for entry in versions:
            value = entry["value"]
            references = []
            if entity == "resource" and value.get("folder"):
                references.append((value["folder"], "folder"))
            if entity == "item" and value["kind"] in {"folder", "database"}:
                if value.get("section_id"):
                    references.append((value["section_id"], "section"))
                if value.get("project_id"):
                    references.append((value["project_id"], "project"))
            for ref, kind in references:
                mapping = decisions["item:" + ref]
                if mapping["action"] == "keep" and existing[("item", ref)]["kind"] != kind:
                    issues.append(
                        f"Restore {mapping['title']} as a copy or replacement "
                        f"to preserve its {kind} reference."
                    )
    # Keeping a changed source must not silently attach imported memory to different evidence.
    checked = set()
    for (entity, id_), versions in groups.items():
        if entity != "resource" or decisions[entity + ":" + id_]["action"] == "keep":
            continue
        for entry in versions:
            for ref in entry["value"]["recall"]["sources"]:
                source = decisions["resource:" + ref["resource_id"]]
                marker = (ref["resource_id"], ref["revision"])
                if source["action"] != "keep" or marker in checked:
                    continue
                checked.add(marker)
                try:
                    current = workspace.get_resource(identity, space_id, UUID(marker[0]), marker[1])
                    original = groups[("resource", marker[0])][marker[1] - 1]["value"]
                    same = all(current.get(k) == original.get(k) for k in ("kind", "title", "body"))
                except CommandError as error:
                    if error.status != 404:
                        raise
                    same = False
                if not same:
                    issues.append(
                        f"Restore {source['title']} as a copy or replacement "
                        "to preserve source evidence."
                    )
    require_snapshot(workspace, identity, space_id, snapshot)
    return {
        "snapshot": snapshot,
        "items": list(decisions.values()),
        "issues": list(dict.fromkeys(issues)),
    }


def review(workspace, identity, space_id, restore_id, request: Review):
    with restore_staging.load(workspace, identity, space_id, restore_id) as loaded:
        _, _, row, _, groups = loaded
        if row["state"] != "staged":
            raise CommandError("This restore is already publishing. Check its status.", 409)
        return plan(workspace, identity, space_id, restore_id, groups, request.choices)


def status(workspace, identity, space_id, restore_id):
    scope, key, row, _ = restore_staging.session(workspace, identity, space_id, restore_id)
    result = (
        workspace._unpack(key, scope, restore_id, 1, "restore_result", row["encrypted_result"])
        if row["encrypted_result"]
        else None
    )
    return {"id": str(restore_id), "state": row["state"], "result": result}


def _transform(workspace, identity, space_id, restore_id, groups, decisions):
    """Prepare the complete replacement set before any uploads or visible writes."""
    transformed = []
    live_relationships = {}
    live_explicit = set()
    for (entity, id_), versions in groups.items():
        decision = decisions[entity + ":" + id_]
        if decision["action"] == "keep":
            continue
        previous = None
        if entity == "resource" and decision["conflict"]:
            previous = _current(workspace, identity, space_id, entity, id_)
        for entry in versions:
            value = deepcopy(entry["value"])
            value["id"] = decision["target_id"]
            value["revision"] = value["revision"] + decision["offset"] if entity != "agent" else 1
            if author := value.get("author_agent"):
                if mapping := decisions.get("agent:" + author["id"]):
                    author["id"] = mapping["target_id"]
            for reference in ("section_id", "project_id"):
                if entity == "item" and value.get(reference):
                    value[reference] = decisions["item:" + value[reference]]["target_id"]
            if entity == "resource":
                for claim in value.get("_memory", {}).get("claims", []):
                    if creator := (claim.get("creator") or {}).get("agent"):
                        if mapping := decisions.get("agent:" + creator["id"]):
                            creator["id"] = mapping["target_id"]
                    if routing := claim.get("routing"):
                        routing["target_ids"] = [
                            decisions["item:" + ref]["target_id"]
                            for ref in routing.get("target_ids", [])
                        ]
                    classification = claim.get("classification", {})
                    for kind in ("category", "project", "section"):
                        field = kind + "_ids"
                        if field in classification:
                            classification[field] = [
                                decisions["item:" + ref]["target_id"]
                                for ref in classification[field]
                            ]
                if value.get("project"):
                    value["project"] = decisions["item:" + value["project"]]["target_id"]
                if value.get("folder"):
                    value["folder"] = decisions["item:" + value["folder"]]["target_id"]
                for source in value["recall"]["sources"]:
                    mapping = decisions["resource:" + source["resource_id"]]
                    source["resource_id"] = mapping["target_id"]
                    source["revision"] += mapping["offset"]
                # Existing edits and forget markers remain authoritative in active recall.
                if (
                    entry["current"]
                    and value.get("_memory")
                    and previous
                    and previous.get("_memory")
                ):
                    remembered = value["_memory"]
                    if any(
                        c.get("edited") for c in previous["_memory"]["claims"]
                    ) and remembered.get("provider_fingerprint") != previous["_memory"].get(
                        "provider_fingerprint"
                    ):
                        raise CommandError(
                            "Memory providers differ. Keep the current memory or rebuild it "
                            "before merging.",
                            409,
                        )
                    memory_pipeline.retain_edits(remembered, previous["_memory"])
                    before = {c["id"]: c for c in previous["_memory"]["claims"]}
                    for claim in remembered["claims"]:
                        prior = before.get(claim["id"], {})
                        if (
                            prior.get("value") == claim["value"]
                            and "relationships" in prior
                            and claim.get("relationships") == prior["relationships"]
                        ):
                            marker = (value["id"], value["revision"], claim["id"])
                            live_relationships[marker] = prior
                    if "links" in previous["_memory"]:
                        remembered["links"] = deepcopy(previous["_memory"]["links"])
                        live_explicit.add((value["id"], value["revision"]))
                    remembered["forgotten"] = sorted(
                        set(remembered["forgotten"])
                        | set(entry["value"]["_memory"].get("forgotten", []))
                    )
                    remembered["claims"] = [
                        c for c in remembered["claims"] if c["id"] not in remembered["forgotten"]
                    ]
                    with workspace.db.transaction(identity) as connection:
                        workspace._scope(connection, identity, space_id, human=True)
                        index = connection.execute(
                            "SELECT claim_id,embedding::text,embedding_model FROM "
                            "space.memory_claims WHERE memory_id=%s",
                            (id_,),
                        ).fetchall()
                    before = {c["id"]: c for c in previous["_memory"]["claims"]}
                    vectors = {c["id"]: c for c in value["memory_index"]}
                    existing_vectors = {
                        str(c["claim_id"]): {
                            "id": str(c["claim_id"]),
                            "model": c["embedding_model"],
                            "vector": json.loads(c["embedding"]),
                        }
                        for c in index
                    }
                    for claim in remembered["claims"]:
                        if before.get(claim["id"], {}).get("edited"):
                            if claim["id"] not in existing_vectors:
                                raise CommandError(
                                    "Current memory has no recall index. Rebuild it before "
                                    "restoring.",
                                    409,
                                )
                            vectors[claim["id"]] = existing_vectors[claim["id"]]
                    value["memory_index"] = [vectors[c["id"]] for c in remembered["claims"]]
                    if len({c["model"] for c in value["memory_index"]}) > 1:
                        raise CommandError(
                            "Memory models differ. Keep the existing memory or rebuild it "
                            "before merging.",
                            409,
                        )
                    value["body"] = memory_pipeline.memory_body(remembered)
            transformed.append({**entry, "value": value, "action": decision["action"]})
    _restore_links(
        workspace,
        identity,
        space_id,
        groups,
        decisions,
        transformed,
        live_relationships,
        live_explicit,
    )
    return transformed


def _restore_links(
    workspace,
    identity,
    space_id,
    groups,
    decisions,
    entries,
    live_relationships,
    live_explicit,
):
    """Parameters: archive, merge decisions, prepared entries and live claim origins.

    What it does: remaps explicit links and checks inferred judgments against their
    original content after all retained edits and tombstones have been applied.
    Output: updates only the prepared entries, never the archive or live records.
    """
    prepared = {
        (e["value"]["id"], e["value"]["revision"]): e["value"]
        for e in entries
        if e["entity"] == "resource"
    }
    heads = {
        e["value"]["id"]: e["value"] for e in entries if e["entity"] == "resource" and e["current"]
    }
    live = {}

    def live_resource(id_, revision=None):
        """Parameters: scoped resource ID and optional revision.

        What it does: caches authorized live reads, treating missing resources as absent.
        Output: a resource or None.
        """
        key = (id_, revision)
        if key not in live:
            try:
                live[key] = workspace.get_resource(
                    identity, space_id, UUID(id_), revision, _internal=True
                )
            except CommandError as error:
                if error.status != 404:
                    raise
                live[key] = None
        return live[key]

    def claim_in(resource, id_):
        """Parameters: resource and claim ID.

        What it does: finds an active claim in the given resource revision.
        Output: the claim or None.
        """
        memory = (resource or {}).get("_memory", {})
        if id_ in memory.get("forgotten", []):
            return None
        return next((c for c in memory.get("claims", []) if c["id"] == id_), None)

    def same_text(left, right):
        """Parameters: original and restored claims.

        What it does: compares values and the titles used by the judge when available.
        Output: whether the original judgment still describes the restored text.
        """
        return bool(
            left
            and right
            and left["value"] == right["value"]
            and ("index" not in left or left["index"] == right.get("index"))
        )

    originals = {
        (
            decisions["resource:" + id_]["target_id"],
            e["value"]["revision"] + decisions["resource:" + id_]["offset"],
        ): e["value"]
        for (entity, id_), versions in groups.items()
        if entity == "resource"
        for e in versions
    }
    for entry in entries:
        if entry["entity"] != "resource":
            continue
        value = entry["value"]
        memory = value.get("_memory", {})
        if "links" in memory:
            explicit = {}
            for link in memory["links"]:
                mapping = decisions.get("resource:" + str(UUID(link["resource_id"])))
                target_id = (
                    mapping["target_id"]
                    if mapping
                    else (
                        str(UUID(link["resource_id"]))
                        if (value["id"], value["revision"]) in live_explicit
                        else None
                    )
                )
                if target_id and target_id != value["id"]:
                    mapped = {"resource_id": target_id, "relation": link.get("relation", "related")}
                    explicit[target_id] = mapped
            if len(explicit) > 20:
                raise CommandError("Merged memory exceeds the explicit link limit of 20", 409)
            memory["links"] = list(explicit.values())
        original = originals[(value["id"], value["revision"])]
        for claim in memory.get("claims", []):
            relationships = claim.get("relationships")
            if not relationships:
                continue
            origin = live_relationships.get((value["id"], value["revision"], claim["id"]))
            source = origin if origin is not None else claim_in(original, claim["id"])
            kept = []
            for link in relationships.get("links", []):
                target_id = str(UUID(link["resource_id"]))
                mapping = decisions.get("resource:" + target_id)
                if not mapping or mapping["target_id"] == value["id"]:
                    continue
                hashed = bool(link.get("source_text_hash") and link.get("target_text_hash"))
                if origin is not None:
                    # A live revision belongs to live history, not to the imported history.
                    if not hashed and mapping["action"] != "keep":
                        continue
                    judged_resource = live_resource(target_id, link["revision"])
                    revision = link["revision"]
                else:
                    history = groups.get(("resource", target_id), [])
                    judged_resource = next(
                        (e["value"] for e in history if e["value"]["revision"] == link["revision"]),
                        None,
                    )
                    revision = link["revision"] + mapping["offset"]
                judged = claim_in(judged_resource, link["entry_id"])
                if mapping["action"] == "keep":
                    target = live_resource(target_id, None if hashed else revision)
                elif hashed:
                    target = heads.get(mapping["target_id"])
                else:
                    target = prepared.get((mapping["target_id"], revision))
                actual = claim_in(target, link["entry_id"])
                if not same_text(source, claim) or not same_text(judged, actual):
                    continue
                if hashed:
                    if (
                        brain.text_hash(claim["value"]).hex() != link["source_text_hash"]
                        or brain.text_hash(actual["value"]).hex() != link["target_text_hash"]
                    ):
                        continue
                    revision = target["revision"]
                kept.append({**link, "resource_id": mapping["target_id"], "revision": revision})
            if relationships.get("links") and not kept:
                relationships["status"] = "no_match"
            relationships["links"] = kept


def _reindex(workspace, identity, space_id, groups, decisions, snapshot):
    """Embed restored memories again when their vectors have another width.

    The memory index has one fixed width, so an archive from an installation whose
    embedding model has another size cannot keep its vectors. This installation's model
    embeds those claims instead. Embedding runs outside every transaction, before
    publication, so a failed model call changes nothing.
    """
    values = [
        versions[-1]["value"]
        for (entity, id_), versions in groups.items()
        if entity == "resource"
        and decisions["resource:" + id_]["action"] != "keep"
        and any(
            len(row["vector"]) != brain.EMBEDDING_DIM
            for row in versions[-1]["value"].get("memory_index", [])
        )
    ]
    if not values:
        return
    if not workspace.memory_models.enabled:
        raise CommandError(
            "This archive's memories were indexed by an embedding model of another size. "
            "Configure a memory model provider, then restore again so Space can index them "
            "with it.",
            409,
        )
    rows = [memory_pipeline.index_rows(value["_memory"]) for value in values]
    session = workspace._model_session(
        identity, space_id, lambda: require_snapshot(workspace, identity, space_id, snapshot)
    )
    try:
        vectors = iter(session.embed([row["text"] for claims in rows for row in claims]))
    except ModelError as error:
        raise CommandError(str(error), 503) from None
    model = workspace.memory_models.embedding_model
    for value, claims in zip(values, rows, strict=True):
        value["memory_index"] = [
            {"id": row["id"], "model": model, "vector": next(vectors)} for row in claims
        ]


def commit(workspace, identity, space_id, restore_id, request: Commit):
    fingerprint = hashlib.sha256(encode(request.model_dump(mode="json"))).hexdigest()
    receipt = status(workspace, identity, space_id, restore_id)
    if receipt["state"] == "committed":
        if receipt["result"]["request_fingerprint"] != fingerprint:
            raise CommandError("This restore was already committed with different choices", 409)
        return receipt
    with restore_staging.load(workspace, identity, space_id, restore_id) as loaded:
        scope, key, _, archive, groups = loaded
        reviewed = plan(workspace, identity, space_id, restore_id, groups, request.choices)
        if reviewed["snapshot"] != request.snapshot:
            raise CommandError("Space changed since review. Review the merge again.", 409)
        if reviewed["issues"]:
            raise CommandError("Resolve the listed source or type conflicts before restoring.", 409)
        decisions = {item["key"]: item for item in reviewed["items"]}
        _reindex(workspace, identity, space_id, groups, decisions, request.snapshot)
        prepared = _transform(workspace, identity, space_id, restore_id, groups, decisions)
        from space.restore_publish import publish

        return publish(
            workspace,
            identity,
            space_id,
            restore_id,
            scope,
            key,
            prepared,
            request.snapshot,
            archive["source_space"],
            fingerprint,
            task_columns=archive.get("task_columns", []),
        )
