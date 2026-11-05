"""Moving v0.5.0 file parts onto the folder: the bytes, the fingerprint and a second run."""

import time
from uuid import UUID, uuid4

import pytest
from test_database_setup import database as database_fixture
from test_document_files import commit, parts, read, stored_objects, stored_parts, upload
from test_workspace import TestKeys
from test_workspace import command_database as command_database_fixture

from space import document_files
from space.files import move as files_move
from space.files import size_text
from space.local_store import LocalObjectStore
from space.security.auth import Identity
from space.workspace import Workspace

database = database_fixture
command_database = command_database_fixture

RESOURCE_URL = "http://127.0.0.1:8000/mcp/"


@pytest.fixture
def commands(command_database, database, tmp_path):
    """A Workspace whose file parts go to a folder in the test's temporary directory."""
    service = Workspace(
        command_database,
        TestKeys(),
        LocalObjectStore(tmp_path / "files"),
        RESOURCE_URL,
        memory_models=None,
    )
    identity = Identity("https://identity.invalid", str(uuid4()), int(time.time()) + 600)
    space = UUID(service.bootstrap(identity)["id"])
    return service, identity, space, database[0]


@pytest.fixture(autouse=True)
def clean(commands):
    """Leave no rows behind: the move works across every Space, so a run must start clear."""
    yield
    _, _, space, connect = commands
    with connect() as connection:
        connection.execute("DELETE FROM space.file_parts WHERE space_id=%s", (space,))


def stored_file(service, owner, space, raw):
    """Commit a document that keeps `raw` as its original file."""
    request = commit(upload(service, owner, space, raw), raw)
    return document_files.commit(service, owner, space, request)


def legacy(service, connect, space, file_id, raw):
    """Make a file look as it did before the move: bytes inline, no key, nothing on disk.

    Parameters: the workspace, the connection fixture, the Space, the file's ID and its bytes.
    What it does: writes each part's bytes back into its row and removes every object the
    upload left, which is exactly the state `python -m space.files.move` has to repair.
    Output: none.
    """
    with connect() as connection:
        for number, chunk in enumerate(parts(raw)):
            connection.execute(
                "UPDATE space.file_parts SET content=%s,object_key=NULL "
                "WHERE file_id=%s AND part=%s",
                (chunk, file_id, number),
            )
    for path in service.objects.root.rglob("*"):
        if path.is_file():
            path.unlink()


def messages():
    lines = []
    return lines, lines.append


def test_a_part_stored_in_the_database_moves_to_the_folder_and_reads_back(commands):
    service, owner, space, connect = commands
    raw = bytes(range(256)) * 9000  # Three parts, the last one partial.
    saved = stored_file(service, owner, space, raw)
    file_id = UUID(saved["file"]["id"])
    legacy(service, connect, space, file_id, raw)
    # Before the move the row's own bytes answer, which is why the command must verify them.
    assert stored_objects(service) == []
    assert read(service, owner, space, saved) == raw

    lines, write = messages()
    with connect() as connection:
        counts = files_move.move(connection, service.objects, write=write)
    assert counts == {
        "rows": 3,
        "bytes": len(raw),
        "moved": 3,
        "refused": 0,
        "dry_run": False,
    }
    assert lines == []
    report, write_report = messages()
    assert files_move.report(counts, service.objects.root, write_report) == 0
    assert any("Moved 3 of 3" in line for line in report)
    rows = stored_parts(connect, space)
    assert all(row[4] and row[5] is None for row in rows), "every part names an object now"
    assert stored_objects(service) == sorted(row[4] for row in rows)
    assert read(service, owner, space, saved) == raw


def test_a_part_whose_bytes_do_not_match_its_fingerprint_is_refused_and_left_alone(commands):
    service, owner, space, connect = commands
    raw = b"%PDF-1.7 correct bytes"
    saved = stored_file(service, owner, space, raw)
    file_id = UUID(saved["file"]["id"])
    legacy(service, connect, space, file_id, raw)
    # Same length, different bytes: only the fingerprint can tell them apart.
    damaged = b"%PDF-1.7 changed bytes"
    assert len(damaged) == len(raw)
    with connect() as connection:
        connection.execute(
            "UPDATE space.file_parts SET content=%s WHERE file_id=%s AND part=0",
            (damaged, file_id),
        )

    lines, write = messages()
    with connect() as connection:
        counts = files_move.move(connection, service.objects, write=write)
    assert counts["moved"] == 0 and counts["refused"] == 1
    assert any("Refused" in line and "fingerprint" in line for line in lines)
    assert files_move.report(counts, service.objects.root, write) == 1
    # Nothing moved, nothing written, and the database copy is still there to be read.
    rows = stored_parts(connect, space)
    assert [(row[4], row[5]) for row in rows] == [(None, damaged)]
    assert stored_objects(service) == []
    assert read(service, owner, space, saved) == damaged


def test_a_second_run_changes_nothing_and_a_dry_run_only_reports(commands):
    service, owner, space, connect = commands
    raw = b"x" * (document_files.PART_BYTES + 7)
    saved = stored_file(service, owner, space, raw)
    legacy(service, connect, space, UUID(saved["file"]["id"]), raw)

    with connect() as connection:
        dry = files_move.move(connection, service.objects, dry_run=True)
    assert (dry["rows"], dry["bytes"], dry["moved"]) == (2, len(raw), 2)
    assert dry["dry_run"] is True
    assert stored_objects(service) == []
    assert all(row[4] is None for row in stored_parts(connect, space))
    lines, write = messages()
    assert files_move.report(dry, service.objects.root, write) == 0
    assert lines == [
        f"Would move 2 file parts ({size_text(len(raw))}) from the database into "
        f"{service.objects.root}. Nothing was changed."
    ]

    with connect() as connection:
        first = files_move.move(connection, service.objects)
    assert first["moved"] == 2
    after = stored_objects(service)
    lines, write = messages()
    with connect() as connection:
        second = files_move.move(connection, service.objects, write=write)
    assert second == {"rows": 0, "bytes": 0, "moved": 0, "refused": 0, "dry_run": False}
    assert files_move.report(second, service.objects.root, write) == 0
    assert lines == [
        f"Nothing to move: every file part already names an object in {service.objects.root}."
    ]
    assert stored_objects(service) == after
    assert read(service, owner, space, saved) == raw


def test_the_move_takes_several_batches_without_missing_or_repeating_a_part(commands):
    service, owner, space, connect = commands
    raw = b"y" * (document_files.PART_BYTES + 40)
    saved = stored_file(service, owner, space, raw)
    legacy(service, connect, space, UUID(saved["file"]["id"]), raw)
    with connect() as connection:
        counts = files_move.move(connection, service.objects, batch_size=1)
    assert (counts["moved"], counts["refused"]) == (2, 0)
    assert read(service, owner, space, saved) == raw
    assert len(stored_objects(service)) == 2
