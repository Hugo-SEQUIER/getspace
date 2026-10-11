"""Validation of portable archives before any restore can publish content."""

import math
import re
from collections import defaultdict
from datetime import datetime
from uuid import UUID

from pydantic_core import from_json

from space import agents, brain, memory_pipeline, tasks, workspace_items
from space._vendor.memora.core.memory_entry import MemoryEntry
from space.memory import RecallMetadata
from space.workspace import SaveResource
from space.workspace_archive import FORMAT

MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 10000
MAX_ARCHIVE_DEPTH = 32
MAX_ARCHIVE_CONTAINERS = 100_000
MAX_ARCHIVE_SEPARATORS = 1_000_000
# pgvector's widest vector; archived recall vectors of any width up to it are accepted.
MAX_EMBEDDING_DIM = 16000
_STRUCTURE = re.compile(rb'["{}\[\],:]')
_STRING_END = re.compile(rb'["\\]')


def _finite_numbers(value):
    # Exponents such as 1e999 can overflow even when named NaN/Infinity are disabled.
    # The preflight already bounds traversal depth and the total number of values.
    if isinstance(value, dict):
        for child in value.values():
            _finite_numbers(child)
    elif isinstance(value, list):
        for child in value:
            _finite_numbers(child)
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Non-finite archive number")


def decode_archive(raw: bytes):
    """Bound JSON allocations before decoding; never materialize a token list.

    JSON syntax remains the decoder's responsibility. This linear scan
    counts even duplicate keys and ignored values, before they allocate objects.
    Escaped quotes/brackets inside strings cannot hide actual JSON structure.
    """
    if len(raw) > MAX_ARCHIVE_BYTES:
        raise ValueError("Archive exceeds the 100 MiB limit")
    position = depth = containers = separators = 0
    while match := _STRUCTURE.search(raw, position):
        token = match[0]
        position = match.end()
        if token == b'"':
            while end := _STRING_END.search(raw, position):
                position = end.end()
                if end[0] == b'"':
                    break
                position += 1  # Skip the escaped byte, including an escaped backslash.
            else:
                raise ValueError("Unterminated archive string")
        elif token in (b"{", b"["):
            depth += 1
            containers += 1
            if depth > MAX_ARCHIVE_DEPTH or containers > MAX_ARCHIVE_CONTAINERS:
                raise ValueError("Archive exceeds structural limits")
        elif token in (b"}", b"]"):
            depth -= 1
            if depth < 0:
                raise ValueError("Invalid archive structure")
        else:
            separators += 1
            if separators > MAX_ARCHIVE_SEPARATORS:
                raise ValueError("Archive exceeds structural limits")
    if depth:
        raise ValueError("Invalid archive structure")
    # Decode UTF-8 bytes directly using the existing Pydantic runtime: avoid an
    # additional whole-archive Unicode string (up to four times the input size).
    archive = from_json(raw, allow_inf_nan=False, cache_strings=False, allow_partial=False)
    _finite_numbers(archive)
    return archive


def _id(value):
    return str(UUID(str(value)))


def _revision(value):
    if type(value) is not int or not 1 <= value <= 2147483647:
        raise ValueError("Invalid revision")
    return value


def _agent(value):
    if value is None:
        return
    _id(value["id"])
    _revision(value["revision"])
    agents.AgentProfile.model_validate({"name": value["name"], "harness": value["harness"]})


def _memory(value):
    from space.memory import (
        ExplicitMemoryLink,
        ImportProvenance,
        MemoryClassification,
        MemoryRelationships,
    )
    from space.memory_creator import MemoryCreator

    if value.get("provenance") is not None:
        ImportProvenance.model_validate(value["provenance"])
    fingerprint = value.get("provider_fingerprint")
    if fingerprint is not None and (not isinstance(fingerprint, str) or len(fingerprint) > 200):
        raise ValueError("Invalid memory provider fingerprint")
    if value.get("version") not in (1, 2):
        raise ValueError("Unsupported memory version")
    links = value.get("links", [])
    if not isinstance(links, list) or len(links) > 20:
        raise ValueError("Invalid explicit memory links")
    explicit = [ExplicitMemoryLink.model_validate(link) for link in links]
    if len({link.resource_id for link in explicit}) != len(explicit):
        raise ValueError("Explicit links must name distinct memory nodes")
    claims = value["claims"]
    if not isinstance(claims, list) or len(claims) > memory_pipeline.MAX_CLAIMS:
        raise ValueError("Too many memory claims")
    ids, indices = set(), set()
    for claim in claims:
        if claim.get("creator") is not None:
            MemoryCreator.model_validate(claim["creator"])
        if claim.get("classification") is not None:
            MemoryClassification.model_validate(claim["classification"])
        if claim.get("relationships") is not None:
            MemoryRelationships.model_validate(claim["relationships"])
        id_ = _id(claim["id"])
        if id_ in ids or claim["index"] in indices:
            raise ValueError("Duplicate memory claims")
        ids.add(id_)
        indices.add(claim["index"])
        if not isinstance(claim["index"], str) or not 1 <= len(claim["index"]) <= 300:
            raise ValueError("Invalid memory index")
        if not isinstance(claim["value"], str) or not 1 <= len(claim["value"]) <= 2000:
            raise ValueError("Invalid memory value")
        if claim.get("memory_type") not in {"factual", "procedural", "episodic"}:
            raise ValueError("Invalid memory type")
        if type(claim.get("importance", 0)) is not int or not 0 <= claim.get("importance", 0) <= 3:
            raise ValueError("Invalid importance")
        if type(claim.get("edited", False)) is not bool:
            raise ValueError("Invalid edited flag")
    forgotten = value.get("forgotten", [])
    if not isinstance(forgotten, list) or len(forgotten) > 10000:
        raise ValueError("Invalid forgotten claim history")
    if ids.intersection(_id(id_) for id_ in forgotten):
        raise ValueError("Forgotten claims cannot remain active")
    entries = memory_pipeline.stored_entries(value)
    if not isinstance(entries, list) or len(entries) > memory_pipeline.MAX_ENTRIES:
        raise ValueError("Invalid cue graph")
    for entry in entries:
        MemoryEntry.model_validate(entry)
    brain._desired(memory_pipeline.index_rows(value))


def validate_archive(raw):
    """Return checked entries grouped by entity and ID; reject partial histories/references."""
    archive = raw
    if not isinstance(archive, dict) or set(archive) - {
        "format",
        "source_space",
        "exported_at",
        "snapshot",
        "entries",
        "task_columns",
    }:
        raise ValueError("Invalid archive fields")
    if archive.get("format") != FORMAT:
        raise ValueError("Choose a supported Space archive")
    tasks.archive_columns(archive.get("task_columns", []))
    _id(archive["source_space"])
    datetime.fromisoformat(archive["exported_at"].replace("Z", "+00:00"))
    entries = archive["entries"]
    if not isinstance(entries, list) or len(entries) > MAX_ARCHIVE_ENTRIES:
        raise ValueError("Archive exceeds the 10,000 revision limit")
    groups = defaultdict(list)
    for entry in entries:
        entity, value = entry["entity"], entry["value"]
        if entity not in {"agent", "resource", "item"} or type(entry["current"]) is not bool:
            raise ValueError("Invalid archive entry")
        id_, revision = _id(value["id"]), _revision(value["revision"])
        # Only ID normalization needs a copy. Nested content stays read-only here;
        # the merge transformer owns its copies when remapping references.
        value = {**value, "id": id_}
        entry = {**entry, "value": value}
        if entity == "agent":
            _agent(value)
            if not entry["current"]:
                raise ValueError("Agent archives contain the current identity only")
        elif entity == "item":
            workspace_items.SaveItem.model_validate(
                {
                    "command_id": id_,
                    "item_id": id_,
                    "expected_revision": revision - 1,
                    "kind": value["kind"],
                    "data": {
                        key: item
                        for key, item in value.items()
                        if key
                        not in {
                            "id",
                            "kind",
                            "revision",
                            "deleted",
                            "updated_at",
                            "author_agent",
                        }
                    },
                }
            )
            if type(value["deleted"]) is not bool:
                raise ValueError("Invalid trash state")
            _agent(value.get("author_agent"))
        else:
            SaveResource.model_validate(
                {
                    "command_id": id_,
                    "resource_id": id_,
                    "expected_revision": revision,
                    **{key: value[key] for key in ("kind", "title", "body", "status", "recall")},
                    "project": value.get("project"),
                    "folder": value.get("folder"),
                    "priority": value.get("priority"),
                }
            )
            _agent(value.get("author_agent"))
            memory = value.get("_memory")
            if (value["recall"]["category"] == "memory") != bool(memory):
                raise ValueError("Memory content and category disagree")
            if memory:
                _memory(memory)
                if entry["current"]:
                    index = value.get("memory_index", [])
                    if {row["id"] for row in index} != {c["id"] for c in memory["claims"]}:
                        raise ValueError("Memory archive is missing recall vectors")
                    if (
                        len(index) != len(memory["claims"])
                        or len({row["model"] for row in index}) > 1
                    ):
                        raise ValueError("Invalid memory index")
                    # Another installation may use an embedding model of another size;
                    # restore indexes such memories again with this installation's model.
                    if len({len(row["vector"]) for row in index}) > 1:
                        raise ValueError("Invalid memory index")
                    for row in index:
                        if not isinstance(row["model"], str) or not 1 <= len(row["model"]) <= 200:
                            raise ValueError("Invalid embedding model")
                        if not 1 <= len(row["vector"]) <= MAX_EMBEDDING_DIM or any(
                            type(n) not in (int, float) or not math.isfinite(n) or abs(n) > 1e30
                            for n in row["vector"]
                        ):
                            raise ValueError("Invalid recall vector")
        if entity != "agent":
            timestamp = datetime.fromisoformat(value["updated_at"].replace("Z", "+00:00"))
            if timestamp.tzinfo is None:
                raise ValueError("Content timestamps require a timezone")
        groups[(entity, id_)].append(entry)
    for (entity, id_), versions in groups.items():
        versions.sort(key=lambda entry: entry["value"]["revision"])
        if entity == "agent":
            if len(versions) != 1:
                raise ValueError("Duplicate agent identity")
            continue
        if [entry["value"]["revision"] for entry in versions] != list(range(1, len(versions) + 1)):
            raise ValueError("Archive has missing or duplicate revisions")
        if [entry["current"] for entry in versions] != [False] * (len(versions) - 1) + [True]:
            raise ValueError("Archive must identify the latest revision")
        if len({entry["value"]["kind"] for entry in versions}) != 1:
            raise ValueError("Item kind cannot change across revisions")
        for entry in versions:
            value = entry["value"]
            references = []
            if entity == "item" and value.get("section_id"):
                references.append((value["section_id"], "section"))
            if entity == "item" and value.get("project_id"):
                references.append((value["project_id"], "project"))
            for claim in value.get("_memory", {}).get("claims", []):
                for kind in ("category", "project", "section"):
                    references.extend(
                        (ref, kind)
                        for ref in claim.get("classification", {}).get(kind + "_ids", [])
                    )
            for ref, kind in references:
                linked = groups.get(("item", _id(ref)))
                if not linked or linked[-1]["value"]["kind"] != kind:
                    raise ValueError("Archive is missing a classification item")
            for claim in value.get("_memory", {}).get("claims", []):
                for ref in (claim.get("routing") or {}).get("target_ids", []):
                    linked = groups.get(("item", _id(ref)))
                    if not linked or linked[-1]["value"]["kind"] not in (
                        "project",
                        "section",
                        "folder",
                        "database",
                    ):
                        raise ValueError("Archive is missing a routing target")
        if entity == "resource":
            for entry in versions:
                value = entry["value"]
                if project := value.get("project"):
                    linked = groups.get(("item", _id(project)))
                    if not linked or linked[-1]["value"]["kind"] != "project":
                        raise ValueError("Archive is missing a linked project")
                if folder := value.get("folder"):
                    linked = groups.get(("item", _id(folder)))
                    if not linked or linked[-1]["value"]["kind"] != "folder":
                        raise ValueError("Archive is missing a linked folder")
                for source in RecallMetadata.model_validate(value["recall"]).sources:
                    linked = groups.get(("resource", str(source.resource_id)))
                    if (
                        source.resource_id == UUID(id_)
                        or not linked
                        or source.revision > len(linked)
                    ):
                        raise ValueError("Archive is missing a referenced source revision")
    return groups
