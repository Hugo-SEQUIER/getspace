import json
import tracemalloc
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest

from space import archive_schema as schema
from space import restore_merge, restore_staging
from space.admission import AdmissionError


def archive(**changes):
    return {
        "format": "space.archive.v1",
        "source_space": str(uuid4()),
        "exported_at": "2026-09-16T00:00:00Z",
        "snapshot": 0,
        "entries": [],
        **changes,
    }


@pytest.mark.parametrize(
    "raw",
    [
        b'{"padding":[' + b"[]," * 100_000 + b"[]]}",
        b'{"padding":[' + b"0," * 1_000_000 + b"0]}",
        b"[" * 33 + b"]" * 33,
        # Duplicate keys still allocate while decoding, even though only one survives.
        b"{" + b'"x":[],' * 100_000 + b'"x":[]}',
    ],
)
def test_complexity_is_rejected_before_json_allocates(raw, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Over-budget JSON must never reach the object decoder")

    monkeypatch.setattr(schema, "from_json", forbidden)
    with pytest.raises(ValueError, match="structural limits"):
        schema.decode_archive(raw)


def test_object_fanout_has_bounded_preflight_memory():
    raw = b'{"padding":[' + b"[]," * 100_000 + b"[]]}"
    tracemalloc.start()
    try:
        with pytest.raises(ValueError, match="structural limits"):
            schema.decode_archive(raw)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 2 * 1024 * 1024


@pytest.mark.parametrize("ensure_ascii", [False, True])
def test_strings_escapes_unicode_and_structural_characters_are_preserved(ensure_ascii):
    value = archive()
    value["entries"] = [{"text": 'Escapes: \\" [ { , : ] } and "quotes" \\ 한국어 😀' * 1000}]
    raw = json.dumps(value, ensure_ascii=ensure_ascii).encode()
    assert schema.decode_archive(raw) == value


@pytest.mark.parametrize(
    "raw", [b'{"x":NaN}', b'{"x":Infinity}', b'{"x":-Infinity}', b'{"x":1e999}', b"[-1e999]"]
)
def test_nonfinite_constants_are_rejected(raw):
    with pytest.raises(ValueError):
        schema.decode_archive(raw)


@pytest.mark.parametrize("encoding", ["utf-16", "utf-32"])
def test_alternate_encodings_cannot_bypass_byte_preflight(encoding):
    with pytest.raises(ValueError):
        schema.decode_archive(json.dumps(archive()).encode(encoding))


def test_unknown_envelope_fields_are_rejected_and_valid_input_is_not_mutated():
    with pytest.raises(ValueError, match="archive fields"):
        schema.validate_archive(archive(padding=[]))
    original = archive(
        entries=[
            {
                "entity": "agent",
                "current": True,
                "value": {
                    "id": str(uuid4()).upper(),
                    "revision": 1,
                    "name": "Partner",
                    "harness": "codex",
                },
            }
        ]
    )
    before = json.dumps(original)
    groups = schema.validate_archive(original)
    assert json.dumps(original) == before
    assert next(iter(groups.values()))[0]["value"]["id"].islower()


def test_byte_limit_is_checked_before_decoding(monkeypatch):
    monkeypatch.setattr(schema, "MAX_ARCHIVE_BYTES", 10)
    with pytest.raises(ValueError, match="100 MiB"):
        schema.decode_archive(b" " * 11)


@pytest.mark.parametrize("phase", ["review", "publication"])
def test_restore_admission_spans_review_and_publication(monkeypatch, phase):
    from space import restore_publish

    entered, finish = Event(), Event()
    loads = []

    def load(*args):
        loads.append(args)
        return None, None, {"state": "staged"}, archive(), {}

    def hold(*args, **kwargs):
        entered.set()
        assert finish.wait(5)
        return {"items": [], "issues": [], "snapshot": 0}

    monkeypatch.setattr(restore_staging, "_load", load)
    monkeypatch.setattr(
        restore_merge,
        "plan",
        hold if phase == "review" else lambda *a: {"items": [], "issues": [], "snapshot": 0},
    )
    monkeypatch.setattr(restore_merge, "status", lambda *a: {"state": "staged"})
    monkeypatch.setattr(restore_merge, "_transform", lambda *a: [])
    monkeypatch.setattr(restore_publish, "publish", hold)
    with ThreadPoolExecutor(max_workers=1) as executor:
        first = executor.submit(
            restore_merge.review if phase == "review" else restore_merge.commit,
            object(),
            None,
            uuid4(),
            uuid4(),
            restore_merge.Review() if phase == "review" else restore_merge.Commit(snapshot=0),
        )
        try:
            assert entered.wait(5)
            for operation, body in (
                (restore_merge.review, restore_merge.Review()),
                (restore_merge.commit, restore_merge.Commit(snapshot=0)),
            ):
                with pytest.raises(AdmissionError) as error:
                    operation(object(), None, uuid4(), uuid4(), body)
                assert error.value.status == 503
            assert len(loads) == 1
        finally:
            finish.set()
        assert first.result()["snapshot"] == 0
    assert restore_merge.review(None, None, None, None, restore_merge.Review())["snapshot"] == 0


@pytest.mark.parametrize("during_load", [True, False])
def test_admission_is_released_after_validation_or_consumer_failure(monkeypatch, during_load):
    def load(*args):
        if during_load:
            raise ValueError("fixture failure")
        return ()

    monkeypatch.setattr(restore_staging, "_load", load)
    with (
        pytest.raises(ValueError, match="fixture failure"),
        restore_staging.load(None, None, None, None),
    ):
        raise ValueError("fixture failure")
    monkeypatch.setattr(restore_staging, "_load", lambda *a: ())
    with restore_staging.load(None, None, None, None) as result:
        assert result == ()
