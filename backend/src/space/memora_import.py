"""Offline Memora export validation and deterministic Space write planning.

Exported text is data, never an instruction. No model, database or network calls
belong here. Each memory carries its original record as import evidence.
"""

import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid5

from pydantic import ValidationError

from space.memory import (
    ImportProvenance,
    PreparedClaim,
    RecallMetadata,
    RememberClaims,
    SourceReference,
)
from space.workspace import SaveResource

MAX_EXPORT_BYTES = 25 * 1024 * 1024
MAX_RECORDS = 10_000
MAX_PROVENANCE_BYTES = 64 * 1024
MAX_JSON_DEPTH = 32
# Keep this namespace and command derivation stable across importer releases.
COMMAND_NAMESPACE = UUID("fbe7a340-53e3-494e-a439-a9b648b0325e")


class ImportProblem(ValueError):
    """A content-free error safe to display in the CLI."""


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ImportProblem("JSON contains duplicate object keys")
        value[key] = item
    return value


def _invalid_number(_):
    raise ImportProblem("JSON contains a non-finite number")


def read_export(path: Path) -> dict:
    """Read one bounded JSON file; a directory prefers its complete master export."""
    if path.is_dir():
        candidates = sorted(path.glob("memora-full-export-*.json"))
        if not candidates:
            candidates = sorted(path.glob("memora-memories-*.json"))
        if len(candidates) != 1:
            raise ImportProblem("Choose one Memora full-export or memories JSON file explicitly")
        path = candidates[0]
    try:
        if not path.is_file():
            raise ImportProblem("Export must be a regular JSON file or an export directory")
        with path.open("rb") as handle:
            raw = handle.read(MAX_EXPORT_BYTES + 1)
    except OSError:
        raise ImportProblem("Cannot read export file") from None
    return parse_export(raw)


def parse_export(raw: bytes) -> dict:
    """Validate JSON from either a local file or an authenticated app upload."""
    try:
        if len(raw) > MAX_EXPORT_BYTES:
            raise ImportProblem("Export exceeds the 25 MiB limit; split it before importing")
        value = json.loads(
            raw.decode("utf-8-sig"),
            object_pairs_hook=_unique_object,
            parse_constant=_invalid_number,
        )
        pending = [(value, 0)]
        while pending:
            item, depth = pending.pop()
            if isinstance(item, (dict, list)):
                if depth > MAX_JSON_DEPTH:
                    raise ImportProblem("JSON exceeds the supported nesting depth")
                children = item.values() if isinstance(item, dict) else item
                pending.extend((child, depth + 1) for child in children)
        # Reject overflowing numbers such as 1e999 as well as explicit NaN/Infinity.
        json.dumps(value, allow_nan=False, ensure_ascii=False).encode("utf-8")
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, ImportProblem):
            raise
        raise ImportProblem("Cannot read export as finite UTF-8 JSON") from None
    if not isinstance(value, dict) or not isinstance(value.get("records"), list):
        raise ImportProblem("Expected a Memora JSON object with count and records fields")
    if type(value.get("count")) is not int or value["count"] != len(value["records"]):
        raise ImportProblem("Export count does not match its records array")
    if not 1 <= len(value["records"]) <= MAX_RECORDS:
        raise ImportProblem("Export must contain between 1 and 10,000 records")
    return value


def _text(record, key, maximum):
    value = record.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ImportProblem(f"Invalid or missing {key}")
    return value


@dataclass(frozen=True)
class Record:
    row: int
    namespace: str
    original_id: str
    claim: PreparedClaim
    provenance: dict


def _record(raw, row):
    if not isinstance(raw, dict):
        raise ImportProblem("Record must be an object")
    namespace = _text(raw, "namespace", 100)
    original_id = _text(raw, "id", 256)
    created_at = _text(raw, "created_at", 100)
    try:
        datetime.fromisoformat(created_at)
    except ValueError:
        raise ImportProblem("Invalid created_at timestamp") from None
    metadata = raw.get("metadata", raw)
    if not isinstance(metadata, dict):
        raise ImportProblem("metadata must be an object")
    # Validate the actual wire contracts rather than duplicating claim limits here.
    try:
        RecallMetadata(topics=[namespace])
        claim = PreparedClaim.model_validate(
            {key: metadata.get(key) for key in ("index", "value", "memory_type")}
        )
    except ValidationError:
        raise ImportProblem("Namespace or claim violates Space's topic/type/text limits") from None
    # Keep history and all other source fields, but never upload the stored vector.
    provenance = {key: value for key, value in raw.items() if key != "vector_f32_b64"}
    if len(json.dumps(provenance, ensure_ascii=False).encode()) > MAX_PROVENANCE_BYTES:
        raise ImportProblem("Record metadata exceeds the 64 KiB provenance limit")
    return Record(row, namespace, original_id, claim, provenance)


@dataclass(frozen=True)
class PlannedRecord:
    record: Record
    space_id: UUID
    # Reconstruct old source receipts for retry compatibility; never write this document.
    source: SaveResource
    memory_command_id: UUID

    def memory(self, source: SourceReference | None = None) -> RememberClaims:
        return RememberClaims(
            command_id=self.memory_command_id,
            title=self.record.claim.index,
            claims=[self.record.claim],
            topics=[self.record.namespace],
            source=source,
            provenance=None
            if source
            else ImportProvenance(
                source_id=json.loads(self.source.body)["source_id"],
                record=self.record.provenance,
            ),
        )


@dataclass(frozen=True)
class Plan:
    source_id: str
    total: int
    records: tuple[Record, ...]
    writes: tuple[PlannedRecord, ...]
    destinations: dict[str, UUID]
    errors: tuple[dict, ...]

    @property
    def unmapped(self):
        return sorted({record.namespace for record in self.records} - self.destinations.keys())

    @property
    def ready(self):
        return not self.errors and not self.unmapped and len(self.writes) == self.total

    def summary(self):
        counts = Counter(record.namespace for record in self.records)
        return {
            "mode": "preview",
            "source_id": self.source_id,
            "records": self.total,
            "valid_records": len(self.records),
            "ready": self.ready,
            "namespaces": [
                {
                    "namespace": namespace,
                    "records": count,
                    "space_id": str(self.destinations[namespace])
                    if namespace in self.destinations
                    else None,
                    "topic": namespace,
                }
                for namespace, count in sorted(counts.items())
            ],
            "unmapped_namespaces": self.unmapped,
            "errors": list(self.errors),
            "source_documents": 0,
            "memory_claims": len(self.records),
            "embeddings": "regenerated by Space; supplied vectors are not uploaded",
            "notes": [
                "Offline preview: target access, existing imports and providers are not checked.",
                "Original metadata is kept inside each memory; no documents are created.",
                "Namespaces mapped to one Space share access; topic labels do not isolate data.",
                "Use the same export format on retries; namespace spellings are preserved exactly.",
                "Links/history remain provenance; this does not restore Memora's memory graph.",
                "Retry deduplication lasts only while the server retains the original commands.",
            ],
        }


def plan_import(export: dict, source_id: str, destinations=None, *, space_id=None) -> Plan:
    """Validate every record before preparing any writes. Invalid records block the run."""
    if (
        not source_id.strip()
        or source_id != source_id.strip()
        or len(source_id) > 200
        or any(c in source_id for c in "\r\n")
    ):
        raise ImportProblem("Use a stable source ID of 1–200 characters without line breaks")
    if destinations and space_id:
        raise ImportProblem("Choose either namespace mappings or a single destination Space")
    records, errors, seen = [], [], set()
    for row, raw in enumerate(export["records"], 1):
        try:
            record = _record(raw, row)
            identity = record.namespace, record.original_id
            if identity in seen:
                raise ImportProblem("Duplicate record ID within the same namespace")
            seen.add(identity)
            records.append(record)
        except ImportProblem as exc:
            errors.append({"row": row, "reason": str(exc)})
    namespaces = {record.namespace for record in records}
    mapping = dict(destinations or {})
    if space_id:
        mapping = dict.fromkeys(namespaces, space_id)
    if mapping.keys() - namespaces:
        raise ImportProblem("A namespace mapping does not match any valid exported namespace")
    writes = []
    for record in records:
        if record.namespace not in mapping:
            continue
        target = UUID(str(mapping[record.namespace]))
        mapping[record.namespace] = target
        # Exclude file path, order, timestamps and contents: changing a previously
        # imported record must conflict, not silently create a second memory.
        identity = json.dumps([source_id, str(target), record.namespace, record.original_id])
        command = uuid5(COMMAND_NAMESPACE, identity)
        body = json.dumps(
            {
                "format": "space.memora-source.v1",
                "source_id": source_id,
                "record": record.provenance,
            },
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        source = SaveResource(
            command_id=uuid5(command, "source"),
            expected_revision=0,
            kind="document",
            title=("Memora source: " + record.claim.index)[:300],
            body=body,
            recall=RecallMetadata(topics=[record.namespace]),
        )
        writes.append(PlannedRecord(record, target, source, uuid5(command, "memory")))
    return Plan(
        source_id, len(export["records"]), tuple(records), tuple(writes), mapping, tuple(errors)
    )
