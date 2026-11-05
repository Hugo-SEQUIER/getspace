"""Compare the file rows with the files in the folder, and name what does not agree.

Two disagreements matter, and they are not equally serious:

- a row whose `object_key` names a file the folder does not have. Its bytes are gone,
  which is data loss: the command lists those parts and exits 1.
- a file in the folder no row names. It is an object left by an upload that failed after
  writing its bytes, or by an earlier run that stopped between the two. It costs disk
  space and nothing else; `--delete-orphans` removes those files after printing the report.

Run it in a Compose installation with:

    docker compose run --rm migrate python -m space.files.reconcile
    docker compose run --rm migrate python -m space.files.reconcile --delete-orphans

It connects with the migration credential, as the other maintenance commands do, because
it reads the parts of every Space at once.
"""

from __future__ import annotations

import argparse
import os
import stat
import sys

from space.database_setup import InstallSettings, connect
from space.files import size_text
from space.local_store import LocalObjectStore, is_key
from space.settings import CONFIG_FILE, Settings


def stored_keys(objects) -> tuple[set[str], int, list[str]]:
    """The keys of the files in the folder, their total size, and the paths that are no key.

    Parameters: the store.
    What it does: walks the folder without following a link, and skips the temporary
    names the store writes while it moves an object into place, and any other hidden
    file. Everything else is read as a key: three folders of one UUID each and a file
    named by a part number.
    Output: (the keys found, their bytes, the paths the store would not have written).
    """
    keys, total, unexpected = set(), 0, []
    root = objects.root
    if not root.is_dir():
        return keys, total, unexpected
    for directory, folders, files in os.walk(root, followlinks=False):
        folders[:] = [name for name in folders if not name.startswith(".")]
        for name in files:
            if name.startswith("."):
                continue
            path = os.path.join(directory, name)
            key = os.path.relpath(path, root).replace(os.sep, "/")
            try:
                info = os.lstat(path)
            except OSError:
                unexpected.append(key)
                continue
            if not stat.S_ISREG(info.st_mode) or not is_key(key):
                unexpected.append(key)
                continue
            keys.add(key)
            total += info.st_size
    return keys, total, sorted(unexpected)


def scan(connection, objects):
    """Read the rows and the folder, and put what disagrees into three lists.

    Parameters: an open connection with the migration credential and the store.
    What it does: compares the object keys the rows name with the files the folder holds,
    and counts the parts still keeping their bytes in the database.
    Output: a dict with the named keys, the orphans, the missing keys and the inline count.
    """
    rows = connection.execute(
        "SELECT organization_id,space_id,file_id,part,object_key FROM space.file_parts "
        "WHERE object_key IS NOT NULL ORDER BY organization_id,space_id,file_id,part"
    ).fetchall()
    named = {row[4] for row in rows}
    present, total, unexpected = stored_keys(objects)
    inline = connection.execute(
        "SELECT count(*) FROM space.file_parts WHERE content IS NOT NULL"
    ).fetchone()[0]
    return {
        "rows": len(rows),
        "named": named,
        "present": present,
        "bytes": total,
        "orphans": sorted(present - named),
        "missing": [row[4] for row in rows if row[4] not in present],
        "unexpected": unexpected,
        "inline": int(inline),
    }


def report(found, objects, write=print) -> int:
    """Print the report. Output: 0, or 1 when a row names a file that is gone."""
    write("Space file check")
    write(f"Folder: {objects.root} ({len(found['present'])} objects, {size_text(found['bytes'])})")
    write(f"Rows: {found['rows']} file parts name an object")
    write(f"Orphans: {len(found['orphans'])} object files no row names")
    for key in found["orphans"]:
        write(f"  {key}")
    if found["unexpected"]:
        write(f"Unrecognised: {len(found['unexpected'])} files that are not objects of this store")
        for key in found["unexpected"]:
            write(f"  {key}")
    if found["missing"]:
        write(f"Missing: {len(found['missing'])} rows name an object that is not in the folder")
        for key in found["missing"]:
            write(f"  {key}")
        write(
            "These parts cannot be read any more. Restore the file folder from a backup "
            "taken with this database, or delete the documents that hold them."
        )
    if found["inline"]:
        write(
            f"Still in the database: {found['inline']} file parts keep their bytes in their "
            "row; run python -m space.files.move"
        )
    return 1 if found["missing"] else 0


def run(connection, objects, *, delete_orphans=False, write=print) -> int:
    """Print the report and, when asked, remove the orphans.

    Parameters: the connection, the store, whether to delete the orphans, and the writer.
    What it does: scans, prints what disagrees, then removes the orphan files through the
    store, which is idempotent and refuses a key it did not build. A missing object is
    never touched: nothing can bring it back from this folder.
    Output: an exit status, 1 when a row names an object that is not there.
    """
    found = scan(connection, objects)
    status = report(found, objects, write)
    if delete_orphans and found["orphans"]:
        removed = objects.delete_keys(found["orphans"])
        write(f"Removed {removed} orphan object(s).")
    return status


def main(argv=None) -> int:
    """Run the check from the command line. Output: an exit status, 1 on data loss."""
    parser = argparse.ArgumentParser(
        description="Compare the file rows with the files in the folder."
    )
    parser.add_argument(
        "--delete-orphans",
        action="store_true",
        help="remove the objects no row names, after printing the report",
    )
    args = parser.parse_args(argv)
    settings = InstallSettings()
    if not settings.migration_database_url:
        print("Set SPACE_MIGRATION_DATABASE_URL first", file=sys.stderr)
        return 2
    objects = LocalObjectStore(Settings(_env_file=CONFIG_FILE).file_dir)
    connection = connect(settings)
    try:
        return run(connection, objects, delete_orphans=args.delete_orphans)
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
