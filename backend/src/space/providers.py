"""Per-Space key material and the optional object-store interface.

The self-hosted edition has no key service and no object storage. Each Space gets a
random 32-byte key stored beside it in PostgreSQL. The key encrypts nothing: it keys
the search-term fingerprints and retry receipts, which keeps those stable per Space.
Document bodies are stored inline with their revision, so no object store is needed.
"""

import secrets
from collections.abc import Iterable
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
from typing import Protocol
from uuid import UUID, uuid4

from space.security.payloads import SpaceKey

# The value stored in space_keys.wrapping_provider for keys kept in the database as-is.
LOCAL_PROVIDER = "local"


@dataclass(frozen=True)
class ScopedKey:
    organization_id: UUID
    space_id: UUID
    key: SpaceKey = field(repr=False)

    def active_key(self, organization_id, space_id):
        if (organization_id, space_id) != (self.organization_id, self.space_id):
            raise ValueError("Key scope mismatch")
        return self.key

    def get_key(self, organization_id, space_id, key_id):
        key = self.active_key(organization_id, space_id)
        if key.key_id != key_id:
            raise ValueError("Key reference mismatch")
        return key


class KeyStore(Protocol):
    # Stored as space_keys.wrapping_key_reference beside each new key.
    reference: str

    def generate(self, organization_id: UUID, space_id: UUID) -> tuple[ScopedKey, bytes]: ...

    def unwrap(self, organization_id: UUID, space_id: UUID, row) -> ScopedKey: ...


class LocalKeys:
    """Keys kept in the database row itself. Generation needs no network call."""

    reference = "database"

    def generate(self, organization_id: UUID, space_id: UUID):
        material = secrets.token_bytes(32)
        key = ScopedKey(organization_id, space_id, SpaceKey(str(uuid4()), material))
        return key, material

    def unwrap(self, organization_id: UUID, space_id: UUID, row):
        if row["wrapping_provider"] != LOCAL_PROVIDER:
            raise ValueError("Unsupported key provider")
        return ScopedKey(
            organization_id, space_id, SpaceKey(str(row["id"]), bytes(row["wrapped_key"]))
        )

    def close(self):
        pass


_operation_keys: ContextVar[dict | None] = ContextVar("operation_keys", default=None)
MAX_OPERATION_KEYS = 8


def key_operation(function):
    """Retain a bounded set of keys only for one command, including error cleanup."""

    @wraps(function)
    def run(*args, **kwargs):
        keys = {}
        token = _operation_keys.set(keys)
        try:
            return function(*args, **kwargs)
        finally:
            keys.clear()
            _operation_keys.reset(token)

    return run


def unwrap_for_operation(store, organization_id, space_id, row):
    # The caller still authorizes each access and reads current key metadata. A changed
    # key row or reference cannot reuse an earlier key.
    keys = _operation_keys.get()
    if keys is None:
        return store.unwrap(organization_id, space_id, row)
    cache_key = (
        id(store),
        str(organization_id),
        str(space_id),
        str(row["id"]),
        row["wrapping_provider"],
        row["wrapping_key_reference"],
        bytes(row["wrapped_key"]),
    )
    if cache_key not in keys:
        key = store.unwrap(organization_id, space_id, row)
        if len(keys) >= MAX_OPERATION_KEYS:
            keys.pop(next(iter(keys)))
        keys[cache_key] = key
    return keys[cache_key]


class ObjectStore(Protocol):
    """Kept for revisions written by object-backed installations; new writes are inline."""

    def put(self, key: str, data: bytes) -> None: ...

    def get(self, key: str) -> bytes: ...

    def delete_prefix(self, prefix: str) -> int: ...

    def delete_keys(self, keys: Iterable[str]) -> int: ...


class ObjectDeletionError(RuntimeError):
    """Storage did not confirm complete cleanup; the caller must retain its work list."""
