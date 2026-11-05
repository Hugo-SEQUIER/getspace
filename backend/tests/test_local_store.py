"""The folder-backed object store, against a temporary directory."""

import os
import stat
from pathlib import Path
from uuid import uuid4

import pytest

from space.local_store import (
    LocalObjectStore,
    ObjectKeyError,
    ObjectStoreError,
    file_prefix,
    part_key,
)
from space.providers import ObjectMissing
from space.settings import Settings


def stored_files(store):
    """Every file below the store's root, as paths relative to it."""
    if not store.root.exists():
        return []
    return sorted(
        str(path.relative_to(store.root)) for path in store.root.rglob("*") if path.is_file()
    )


def test_put_writes_one_private_object_and_leaves_no_temporary_file(tmp_path):
    store = LocalObjectStore(tmp_path / "files")
    key = part_key(uuid4(), uuid4(), uuid4(), 0)
    store.put(key, b"%PDF-1.7 first bytes")
    path = store.root.joinpath(*key.split("/"))
    assert path.read_bytes() == b"%PDF-1.7 first bytes"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    # Created on the first write, never wider than the owner.
    assert stat.S_IMODE(store.root.stat().st_mode) == 0o700
    assert stored_files(store) == ["/".join(key.split("/"))]
    assert [entry.name for entry in path.parent.iterdir()] == ["0"]
    # Replacing an object renames over it: the reader sees the new bytes whole, no debris.
    store.put(key, b"second")
    assert store.get(key) == b"second"
    assert [entry.name for entry in path.parent.iterdir()] == ["0"]


@pytest.mark.parametrize(
    "key",
    [
        "../../etc/passwd",
        "/etc/passwd",
        "",
        f"{uuid4()}/{uuid4()}/{uuid4()}",
        f"{uuid4()}/{uuid4()}/{uuid4()}/0/extra",
        f"{uuid4()}/{uuid4()}/{uuid4()}/250",
        f"{uuid4()}/{uuid4()}/{uuid4()}/01",
        f"{uuid4()}/{uuid4()}/{uuid4()}/+1",
        f"{uuid4()}/{uuid4()}/{uuid4()}/x",
        f"{uuid4().hex}/{uuid4()}/{uuid4()}/0",
        f"{str(uuid4()).upper()}/{uuid4()}/{uuid4()}/0",
        f"{{{uuid4()}}}/{uuid4()}/{uuid4()}/0",
    ],
)
def test_a_key_from_anywhere_else_is_refused(tmp_path, key):
    store = LocalObjectStore(tmp_path / "files")
    with pytest.raises(ObjectKeyError):
        store.put(key, b"x")
    with pytest.raises(ObjectKeyError):
        store.get(key)
    with pytest.raises(ObjectKeyError):
        store.delete_keys([key])
    assert stored_files(store) == []


@pytest.mark.parametrize(
    "prefix",
    [
        "",
        "/",
        "../",
        f"{uuid4()}/nope/",
        f"{uuid4()}/{uuid4()}/{uuid4()}/{uuid4()}/",
        f"{uuid4()}/{uuid4()}/..",
        f"{uuid4()}/{uuid4()}/{uuid4()}s/",
    ],
)
def test_a_prefix_from_anywhere_else_is_refused(tmp_path, prefix):
    store = LocalObjectStore(tmp_path / "files")
    with pytest.raises(ObjectKeyError):
        store.delete_prefix(prefix)


def test_a_missing_object_raises_and_a_read_creates_no_folder(tmp_path):
    store = LocalObjectStore(tmp_path / "files")
    key = part_key(uuid4(), uuid4(), uuid4(), 0)
    with pytest.raises(ObjectMissing):
        store.get(key)
    assert not store.root.exists()
    # Both deletions are idempotent and count nothing.
    assert store.delete_keys([key]) == 0
    assert store.delete_prefix(file_prefix(*key.split("/")[:3])) == 0


def test_a_symlink_out_of_the_root_is_never_followed(tmp_path):
    store = LocalObjectStore(tmp_path / "files")
    organization, space, file = uuid4(), uuid4(), uuid4()
    key = part_key(organization, space, file, 0)
    store.put(key, b"inside the root")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_bytes(b"private")
    link = store.root.joinpath(*key.split("/"))
    link.unlink()
    link.symlink_to(outside / "secret")
    # A file that resolves outside the root is refused, not read.
    with pytest.raises(ObjectKeyError):
        store.get(key)
    assert (outside / "secret").read_bytes() == b"private"
    # A whole file folder that is a symlink is unlinked, never walked into.
    link.unlink()
    directory = link.parent
    directory.rmdir()
    directory.symlink_to(outside)
    assert store.delete_prefix(file_prefix(organization, space, file)) == 1
    assert (outside / "secret").read_bytes() == b"private"
    assert not directory.exists()


def test_a_put_through_a_linked_folder_is_refused(tmp_path):
    store = LocalObjectStore(tmp_path / "files")
    store.put(part_key(uuid4(), uuid4(), uuid4(), 0), b"inside the root")
    outside = tmp_path / "outside"
    outside.mkdir()
    organization = uuid4()
    (store.root / str(organization)).symlink_to(outside)
    with pytest.raises(ObjectKeyError):
        store.put(part_key(organization, uuid4(), uuid4(), 0), b"escaped")
    assert list(outside.iterdir()) == []


def test_delete_prefix_counts_the_objects_and_prunes_empty_folders(tmp_path):
    store = LocalObjectStore(tmp_path / "files")
    organization, space, file = uuid4(), uuid4(), uuid4()
    for part in range(3):
        store.put(part_key(organization, space, file, part), b"x" * (part + 1))
    kept_file = uuid4()
    store.put(part_key(organization, space, kept_file, 0), b"keep")
    assert store.delete_prefix(file_prefix(organization, space, file)) == 3
    assert not (store.root / str(organization) / str(space) / str(file)).exists()
    assert store.get(part_key(organization, space, kept_file, 0)) == b"keep"
    assert store.delete_prefix(file_prefix(organization, space, file)) == 0
    # A Space prefix removes the rest and its own folder, and the root stays.
    assert store.delete_prefix(f"{organization}/{space}/") == 1
    assert store.root.is_dir() and list(store.root.iterdir()) == []


def test_delete_keys_is_idempotent_and_counts_one_object_once(tmp_path):
    store = LocalObjectStore(tmp_path / "files")
    organization, space = uuid4(), uuid4()
    first, second = (
        part_key(organization, space, uuid4(), 0),
        part_key(organization, space, uuid4(), 0),
    )
    store.put(first, b"a")
    store.put(second, b"b")
    assert store.delete_keys([first, first, second]) == 2
    assert store.delete_keys([first]) == 0
    assert stored_files(store) == []
    assert list(store.root.iterdir()) == []


def test_an_unusable_folder_fails_on_write_and_names_the_setting(tmp_path):
    blocker = tmp_path / "files"
    blocker.write_bytes(b"a file where the folder should be")
    store = LocalObjectStore(blocker)
    with pytest.raises(ObjectStoreError, match="SPACE_FILE_DIR"):
        store.put(part_key(uuid4(), uuid4(), uuid4(), 0), b"x")


def test_the_file_folder_setting_defaults_and_follows_the_environment(monkeypatch, tmp_path):
    monkeypatch.delenv("SPACE_FILE_DIR", raising=False)
    assert Settings(environment="test").file_dir == Path("data/files")
    monkeypatch.setenv("SPACE_FILE_DIR", str(tmp_path))
    assert Settings(environment="test").file_dir == Path(tmp_path)
    # A relative setting resolves against the working directory when the store reads it.
    monkeypatch.setenv("SPACE_FILE_DIR", "var/files")
    store = LocalObjectStore(Settings(environment="test").file_dir)
    assert store.root == Path(os.getcwd()).joinpath("var/files")


def test_a_relative_root_resolves_once_at_construction(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    store = LocalObjectStore("data/files")
    store.put(part_key(uuid4(), uuid4(), uuid4(), 0), b"x")
    monkeypatch.chdir(tmp_path.parent)
    assert store.root == (tmp_path / "data" / "files").resolve()
    store.put(part_key(uuid4(), uuid4(), uuid4(), 0), b"y")
    assert len(stored_files(store)) == 2
