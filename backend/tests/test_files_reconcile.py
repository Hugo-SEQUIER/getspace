"""Checking that the file rows and the file folder agree, in their own database."""

import time
from uuid import UUID, uuid4

import pytest
from test_database_setup import database as database_fixture
from test_document_files import commit, parts, read, stored_objects, stored_parts, upload
from test_workspace import TestKeys
from test_workspace import command_database as command_database_fixture

from space import document_files, local_store
from space.files import reconcile as files_reconcile
from space.local_store import LocalObjectStore, ObjectKeyError
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
    """Leave no rows behind: the next test has its own folder, and the check is global."""
    yield
    _, _, space, connect = commands
    with connect() as connection:
        connection.execute("DELETE FROM space.file_parts WHERE space_id=%s", (space,))


def stored_file(service, owner, space, raw):
    """Commit a document that keeps `raw` as its original file."""
    request = commit(upload(service, owner, space, raw), raw)
    return document_files.commit(service, owner, space, request)


def add_row(connection, space, key, raw=b"gone bytes"):
    """Record a part whose object the folder does not have, and return its file ID."""
    file_id = uuid4()
    connection.execute(
        "INSERT INTO space.file_parts(organization_id,space_id,file_id,part,object_key,"
        "fingerprint,plain_bytes,content_bytes) "
        "SELECT organization_id,id,%s,0,%s,%s,%s,%s FROM space.spaces WHERE id=%s",
        (file_id, key, document_files.fingerprint(file_id, 0, raw), len(raw), len(raw), space),
    )
    return file_id


def messages():
    lines = []
    return lines, lines.append


def test_reconcile_names_an_orphan_and_a_missing_object_and_fails_for_the_missing(commands):
    service, owner, space, connect = commands
    kept = stored_file(service, owner, space, b"kept bytes")
    scope, _ = service._key(owner, space)
    orphan = local_store.part_key(scope["organization_id"], space, uuid4(), 0)
    missing = local_store.part_key(scope["organization_id"], space, uuid4(), 0)
    service.objects.put(orphan, b"nobody names this")
    with connect() as connection:
        broken = add_row(connection, space, missing)

    with connect() as connection:
        found = files_reconcile.scan(connection, service.objects)
    assert found["orphans"] == [orphan]
    assert found["missing"] == [missing]
    assert found["named"] == {missing, stored_parts(connect, space)[0][4]}
    assert sorted(found["present"]) == stored_objects(service)

    lines, write = messages()
    with connect() as connection:
        status = files_reconcile.run(connection, service.objects, write=write)
    assert status == 1, "a row whose object is gone is data loss"
    assert any(orphan in line for line in lines)
    assert any(missing in line for line in lines)
    assert any("cannot be read any more" in line for line in lines)
    assert any("Orphans: 1 object files no row names" in line for line in lines)

    # Removing the orphans takes the unclaimed object and leaves every named one alone.
    with connect() as connection:
        status = files_reconcile.run(connection, service.objects, delete_orphans=True, write=write)
    assert status == 1
    assert any("Removed 1 orphan object" in line for line in lines)
    assert not (service.objects.root / orphan).exists()
    assert read(service, owner, space, kept) == b"kept bytes"

    # Once the broken row is gone the two halves agree again.
    with connect() as connection:
        connection.execute("DELETE FROM space.file_parts WHERE file_id=%s", (broken,))
    lines, write = messages()
    with connect() as connection:
        assert files_reconcile.run(connection, service.objects, write=write) == 0
    assert "Orphans: 0 object files no row names" in lines
    assert not any(line.startswith("Missing:") for line in lines)


def test_reconcile_says_which_parts_the_move_has_not_reached(commands):
    service, owner, space, connect = commands
    raw = b"still inline"
    saved = stored_file(service, owner, space, raw)
    file_id = UUID(saved["file"]["id"])
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
    lines, write = messages()
    with connect() as connection:
        assert files_reconcile.run(connection, service.objects, write=write) == 0
    assert any("space.files.move" in line and "1 file parts" in line for line in lines)
    assert read(service, owner, space, saved) == raw


def test_a_temporary_write_and_a_stranger_file_are_not_objects(commands):
    service, _, _, _ = commands
    root = service.objects.root
    root.mkdir(parents=True, exist_ok=True)
    (root / ".part-abc").write_bytes(b"half written")
    (root / "notes.txt").write_bytes(b"an operator's file")
    keys, total, unexpected = files_reconcile.stored_keys(service.objects)
    assert (keys, total) == (set(), 0)
    assert unexpected == ["notes.txt"]
    # The store refuses a key it did not build, so nothing here can be mistaken for one.
    assert not local_store.is_key("notes.txt")
    with pytest.raises(ObjectKeyError):
        service.objects.delete_keys(["notes.txt"])
