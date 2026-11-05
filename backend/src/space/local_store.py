"""Uploaded file bytes kept on the server's own disk.

A Space's original files live in a folder beside the installation, not in PostgreSQL:
`space.file_parts.object_key` names the file and this store reads and writes it. The
folder is as trusted as the database volume and the bytes are not encrypted, because a
self-hosted installation already treats its disk that way. Keeping parts out of the
database stops one large upload from bloating PostgreSQL, its write-ahead log and its
dumps.

A key is built here and nowhere else: `{organization_id}/{space_id}/{file_id}/{part}`
with canonical UUIDs and a part number from 0 to 249. `put` writes a temporary file in
the object's own directory, fsyncs it and renames it into place, so a reader sees either
the previous object or the whole new one. Every segment is checked again before use, and
a resolved path that leaves the root is refused, so a symlink cannot widen what this
store touches.
"""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path
from uuid import UUID

from space.providers import ObjectDeletionError, ObjectMissing

# The part numbers `space.file_parts` allows; the store accepts nothing wider.
MAX_PARTS = 250
ROOT_MODE = 0o700
OBJECT_MODE = 0o600
# Objects are written under a hidden name in the same directory, then renamed.
TEMPORARY_PREFIX = ".part-"


class ObjectKeyError(ValueError):
    """A key or prefix this store did not build itself. Nothing reaches the disk."""


class ObjectStoreError(RuntimeError):
    """The file folder cannot be used; the message names SPACE_FILE_DIR."""


def part_key(organization_id, space_id, file_id, part: int) -> str:
    """The one key shape a file part has.

    Parameters: the Space's organization and ID, the file's ID and the part number.
    What it does: joins them in the order the store accepts.
    Output: the object key, as `space.file_parts.object_key` stores it.
    """
    return f"{organization_id}/{space_id}/{file_id}/{part}"


def file_prefix(organization_id, space_id, file_id) -> str:
    """The prefix that covers every part of one file, for a prefix deletion."""
    return f"{organization_id}/{space_id}/{file_id}/"


def space_prefix(organization_id, space_id) -> str:
    """The prefix that covers every object of one Space."""
    return f"{organization_id}/{space_id}/"


def _is_uuid_segment(value: str) -> bool:
    """True only for the canonical lowercase form the store itself writes."""
    try:
        return str(UUID(value)) == value
    except ValueError:
        return False


def _is_part_segment(value: str) -> bool:
    """True only for a plain decimal part number, so `01` and `+1` never reach a path."""
    return (
        value.isascii() and value.isdigit() and str(int(value)) == value and int(value) < MAX_PARTS
    )


class LocalObjectStore:
    """The ObjectStore protocol over one folder on the server's disk.

    Parameters: the folder, from `settings.file_dir`. A relative folder resolves against
    the process's working directory, which is the backend directory.
    What it does: keeps each object at `{organization_id}/{space_id}/{file_id}/{part}`
    below that folder, creates the folder 0700 on the first write, and refuses any key
    that is not exactly that shape.
    """

    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        self._root_ready = False

    # Keys are parsed here, so a caller cannot describe a path: no `..`, no separator
    # beyond the three joins, no absolute name, no canonicalization surprise.
    def _key_segments(self, key) -> list[str]:
        segments = key.split("/") if isinstance(key, str) else []
        if (
            len(segments) != 4
            or not all(_is_uuid_segment(segment) for segment in segments[:3])
            or not _is_part_segment(segments[3])
        ):
            raise ObjectKeyError(
                "An object key is an organization ID, a Space ID, a file ID and a part number"
            )
        return segments

    def _prefix_segments(self, prefix) -> list[str]:
        text = prefix[:-1] if isinstance(prefix, str) and prefix.endswith("/") else prefix
        segments = text.split("/") if isinstance(text, str) else []
        if not 1 <= len(segments) <= 3 or not all(_is_uuid_segment(item) for item in segments):
            raise ObjectKeyError("An object prefix is one to three UUIDs")
        return segments

    def _path(self, key) -> Path:
        path = self.root.joinpath(*self._key_segments(key))
        # The segments cannot escape on their own; the check keeps that true if the key
        # shape ever changes.
        if not path.is_relative_to(self.root):
            raise ObjectKeyError("The object path leaves SPACE_FILE_DIR")
        return path

    def _unavailable(self, error: OSError) -> ObjectStoreError:
        return ObjectStoreError(
            f"Cannot use SPACE_FILE_DIR ({self.root}): {error.strerror or error}. "
            "Create the folder and let the API and worker users write to it."
        )

    def _ensure_root(self) -> Path:
        """Create the root 0700 the first time a write needs it, never at boot."""
        if self._root_ready:
            return self.root
        try:
            if self.root.is_dir():
                self._root_ready = True
                return self.root
            self.root.mkdir(parents=True, mode=ROOT_MODE)
            # mkdir applies the umask, so set the intended mode explicitly.
            os.chmod(self.root, ROOT_MODE)
        except OSError as error:
            raise self._unavailable(error) from None
        self._root_ready = True
        return self.root

    def _object_directory(self, path: Path) -> None:
        """Create the folders up to an object, refusing a link or a file on the way.

        A link could name a directory outside the root, where object bytes must never
        land, so each existing segment is checked before the next one is created.
        """
        current = self.root
        for name in path.parent.relative_to(self.root).parts:
            current = current / name
            try:
                info = current.lstat()
            except FileNotFoundError:
                try:
                    current.mkdir(mode=ROOT_MODE)
                except FileExistsError:
                    # Another writer created it between the check and the call.
                    info = current.lstat()
                except OSError as error:
                    raise self._unavailable(error) from None
                else:
                    continue
            if stat.S_ISLNK(info.st_mode):
                raise ObjectKeyError("The object folder is a link out of SPACE_FILE_DIR")
            if not stat.S_ISDIR(info.st_mode):
                raise ObjectStoreError(
                    f"Cannot use SPACE_FILE_DIR ({self.root}): {current} is not a folder."
                )

    def put(self, key: str, data: bytes) -> None:
        """Parameters: an object key and the bytes to keep under it.
        What it does: writes a temporary file in the object's directory, fsyncs it,
        renames it into place and syncs the directory, so a crash leaves either the
        previous object or the complete new one.
        Output: none.
        """
        path = self._path(key)
        self._ensure_root()
        self._object_directory(path)
        try:
            descriptor, temporary = tempfile.mkstemp(prefix=TEMPORARY_PREFIX, dir=path.parent)
        except OSError as error:
            raise self._unavailable(error) from None
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, OBJECT_MODE)
            os.replace(temporary, path)
            self._sync_directory(path.parent)
        except OSError as error:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise self._unavailable(error) from None

    @staticmethod
    def _sync_directory(directory: Path) -> None:
        """Persist the rename itself; a filesystem that cannot sync a folder is tolerated."""
        try:
            handle = os.open(directory, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(handle)
        finally:
            os.close(handle)

    def get(self, key: str) -> bytes:
        """Parameters: an object key.
        What it does: reads the bytes, refusing a path that resolves outside the root,
        as a symlink there would.
        Output: the stored bytes, or `ObjectMissing` when nothing is stored under the key.
        """
        path = self._path(key)
        try:
            target = path.resolve(strict=True)
        except (OSError, RuntimeError):
            raise ObjectMissing(key) from None
        if not target.is_relative_to(self.root):
            raise ObjectKeyError("The object path leaves SPACE_FILE_DIR")
        try:
            return target.read_bytes()
        except FileNotFoundError:
            raise ObjectMissing(key) from None
        except OSError as error:
            raise self._unavailable(error) from None

    def delete_keys(self, keys) -> int:
        """Parameters: object keys to remove, which need not exist.
        What it does: unlinks each object it built the key for and removes the folders it
        emptied. A symlink is unlinked itself, never followed.
        Output: the number of files removed.
        """
        removed = 0
        for key in keys:
            path = self._path(key)
            try:
                info = path.lstat()
            except FileNotFoundError:
                continue
            except OSError as error:
                raise self._unavailable(error) from None
            if not (stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)):
                # A key names one object; a directory at that path is not one.
                continue
            try:
                path.unlink()
            except FileNotFoundError:
                continue
            except OSError as error:
                raise ObjectDeletionError(str(error)) from None
            removed += 1
            self._prune(path.parent)
        return removed

    def delete_prefix(self, prefix: str) -> int:
        """Parameters: an organization, Space or file prefix.
        What it does: removes every object below it without descending into a symlinked
        directory, then removes the folders it emptied, stopping at the root.
        Output: the number of files and links removed. Absent folders remove nothing.
        """
        directory = self.root.joinpath(*self._prefix_segments(prefix))
        try:
            info = directory.lstat()
        except FileNotFoundError:
            return 0
        except OSError as error:
            raise self._unavailable(error) from None
        if stat.S_ISLNK(info.st_mode):
            # The prefix itself is a link: remove the link, never what it points at.
            try:
                directory.unlink()
            except FileNotFoundError:
                return 0
            except OSError as error:
                raise ObjectDeletionError(str(error)) from None
            self._prune(directory.parent)
            return 1
        if not stat.S_ISDIR(info.st_mode):
            return 0
        removed = self._remove_tree(directory)
        self._prune(directory.parent)
        return removed

    def _remove_tree(self, directory: Path) -> int:
        """Remove one folder tree without following links. Symlinks count as files."""
        removed = 0
        try:
            entries = list(os.scandir(directory))
        except FileNotFoundError:
            return 0
        except OSError as error:
            raise ObjectDeletionError(str(error)) from None
        for entry in entries:
            try:
                is_directory = entry.is_dir(follow_symlinks=False)
            except OSError:
                is_directory = False
            if is_directory:
                removed += self._remove_tree(Path(entry.path))
                continue
            try:
                os.unlink(entry.path)
            except FileNotFoundError:
                continue
            except OSError as error:
                raise ObjectDeletionError(str(error)) from None
            removed += 1
        try:
            os.rmdir(directory)
        except OSError:
            # Another writer added an object, or the folder is already gone.
            pass
        return removed

    def _prune(self, directory: Path) -> None:
        """Remove the folders a deletion emptied, up to but never including the root."""
        while directory != self.root and directory.is_relative_to(self.root):
            try:
                directory.rmdir()
            except OSError:
                return
            directory = directory.parent
