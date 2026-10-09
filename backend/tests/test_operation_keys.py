from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

import pytest

from space.providers import MAX_OPERATION_KEYS, key_operation, unwrap_for_operation


def row(**changes):
    return {
        "id": "key",
        "wrapping_provider": "fixture",
        "wrapping_key_reference": "key-a",
        "wrapped_key": b"encrypted",
        **changes,
    }


def store():
    calls = []

    def unwrap(org, space, envelope):
        value = object()
        calls.append((org, space, dict(envelope), value))
        return value

    return SimpleNamespace(unwrap=unwrap), calls


def test_keys_are_reused_only_within_one_operation_and_errors_clear_them():
    keys, calls = store()

    @key_operation
    def operation(fail=False):
        first = unwrap_for_operation(keys, "org", "space", row())
        assert unwrap_for_operation(keys, "org", "space", row()) is first
        if fail:
            raise ValueError("fixture")
        return first

    assert operation() is not operation()
    with pytest.raises(ValueError):
        operation(fail=True)
    operation()
    assert len(calls) == 4
    unwrap_for_operation(keys, "org", "space", row())
    unwrap_for_operation(keys, "org", "space", row())
    assert len(calls) == 6  # No cache survives the command, including its error path.


def test_scope_store_and_every_envelope_field_are_part_of_the_key():
    keys, calls = store()
    other, other_calls = store()

    @key_operation
    def operation():
        original = unwrap_for_operation(keys, "org", "space", row())
        for field, value in [
            ("id", "new"),
            ("wrapping_provider", "different"),
            ("wrapping_key_reference", "key-b"),
            ("wrapped_key", b"rotated"),
        ]:
            assert unwrap_for_operation(keys, "org", "space", row(**{field: value})) is not original
        assert unwrap_for_operation(keys, "another-org", "space", row()) is not original
        assert unwrap_for_operation(keys, "org", "another-space", row()) is not original
        assert unwrap_for_operation(other, "org", "space", row()) is not original

    operation()
    assert len(calls) == 7 and len(other_calls) == 1


def test_cache_is_bounded_and_nested_operations_restore_the_parent():
    keys, calls = store()

    @key_operation
    def inner():
        return unwrap_for_operation(keys, "org", "space", row())

    @key_operation
    def outer():
        first = unwrap_for_operation(keys, "org", "space", row())
        assert inner() is not first
        assert unwrap_for_operation(keys, "org", "space", row()) is first
        for n in range(MAX_OPERATION_KEYS):
            unwrap_for_operation(keys, "org", "space", row(id=f"key-{n}"))
        assert unwrap_for_operation(keys, "org", "space", row()) is not first

    outer()
    assert len(calls) == MAX_OPERATION_KEYS + 3


def test_concurrent_operations_do_not_share_unwrapped_keys():
    keys, calls = store()
    barrier = Barrier(2)

    @key_operation
    def operation():
        first = unwrap_for_operation(keys, "org", "space", row())
        barrier.wait(timeout=5)
        assert unwrap_for_operation(keys, "org", "space", row()) is first
        return first

    with ThreadPoolExecutor(max_workers=2) as executor:
        a = executor.submit(operation)
        b = executor.submit(operation)
        assert a.result() is not b.result()
    assert len(calls) == 2
