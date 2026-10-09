import json
from uuid import uuid4

import pytest

from space.providers import LOCAL_PROVIDER, LocalKeys
from space.security.payloads import (
    MAX_PAYLOAD_BYTES,
    PayloadUnavailable,
    SpaceKey,
    decode_payload,
    encode_payload,
)


def test_payloads_are_readable_canonical_json():
    value = {"title": "Plan", "body": "Café menu", "status": "todo"}
    stored = encode_payload(value)
    assert (
        stored
        == json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    )
    assert b"Caf\xc3\xa9 menu" in stored
    assert decode_payload(stored) == value
    assert decode_payload(memoryview(stored)) == value


def test_oversized_and_malformed_payloads_are_refused_without_echoing_them():
    with pytest.raises(ValueError, match="supported size"):
        encode_payload({"body": "x" * MAX_PAYLOAD_BYTES})
    for value in (b"{not json", b"\xff\xfe", b"x" * (MAX_PAYLOAD_BYTES + 4096)):
        with pytest.raises(PayloadUnavailable) as error:
            decode_payload(value)
        assert "not json" not in str(error.value)


def test_space_keys_need_an_identity_and_256_bits():
    with pytest.raises(ValueError):
        SpaceKey("", b"k" * 32)
    with pytest.raises(ValueError):
        SpaceKey("key", b"short")
    assert "material" not in repr(SpaceKey("key", b"k" * 32)).split("key_id")[0]


def test_local_keys_round_trip_through_their_database_row():
    store, organization, space = LocalKeys(), uuid4(), uuid4()
    key, stored = store.generate(organization, space)
    assert stored == key.key.material and len(stored) == 32
    row = {
        "id": key.key.key_id,
        "wrapping_provider": LOCAL_PROVIDER,
        "wrapping_key_reference": store.reference,
        "wrapped_key": stored,
    }
    assert store.unwrap(organization, space, row) == key
    with pytest.raises(ValueError, match="scope"):
        store.unwrap(organization, space, row).active_key(organization, uuid4())
    with pytest.raises(ValueError, match="provider"):
        store.unwrap(organization, space, {**row, "wrapping_provider": "external"})
