"""Stored workspace payloads: readable JSON, bounded in size.

The self-hosted edition keeps workspace content as plain UTF-8 JSON in PostgreSQL. The
operator owns the server and its database, so there is no application encryption and
no key service. Columns keep their historical `encrypted_*` names, but they hold
readable JSON; read one in SQL with `convert_from(column, 'UTF8')::jsonb`.
"""

import json
from dataclasses import dataclass, field

MAX_PAYLOAD_BYTES = 1024 * 1024
# A stored payload is the JSON itself, with room for the largest accepted value.
MAX_STORED_BYTES = MAX_PAYLOAD_BYTES + 2048


class PayloadUnavailable(Exception):
    """The requested payload cannot be read. Never include its contents."""


@dataclass(frozen=True)
class SpaceKey:
    """Per-Space secret material for keyed fingerprints (search terms, retry receipts).

    It no longer protects content: it only keeps fingerprints stable within one Space.
    """

    key_id: str
    material: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if not self.key_id or len(self.key_id) > 200 or len(self.material) != 32:
            raise ValueError("Expected an identified 256-bit Space key")


def encode_payload(value) -> bytes:
    """Serialize one stored value as canonical UTF-8 JSON, refusing oversized values."""
    data = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    if len(data) > MAX_PAYLOAD_BYTES:
        raise ValueError("Payload exceeds the supported size")
    return data


def decode_payload(value: bytes | bytearray | memoryview):
    """Parse a stored value. Raises PayloadUnavailable instead of echoing bad content."""
    try:
        data = bytes(value)
        if len(data) > MAX_STORED_BYTES:
            raise ValueError("Payload exceeds the supported size")
        return json.loads(data)
    except (ValueError, TypeError, UnicodeError) as exc:
        raise PayloadUnavailable("Payload unavailable") from exc
