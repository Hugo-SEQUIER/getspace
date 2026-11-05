"""Move the file bytes a v0.5.0 installation kept in PostgreSQL into the file folder.

Before `0044_file_objects`, a file part held its own bytes in `space.file_parts.content`.
This command writes every such part to the store the API uses, checks the bytes against
the part's fingerprint, and only then clears `content` and records the object key, in
committed batches. A part whose bytes do not match their fingerprint is left exactly as
it was and reported, because the database copy is then the only copy there is.

The bytes reach the disk before the row stops naming them, so an interrupted run leaves
objects `space.files.reconcile` can sweep, never a part with nothing behind it. A second
run finds nothing to do.

It connects with the migration credential, as the other maintenance commands do, because
it works across every Space at once. Run it in a Compose installation with:

    docker compose run --rm migrate python -m space.files.move --dry-run
    docker compose run --rm migrate python -m space.files.move

From a source checkout, `uv run --directory backend --locked python -m space.files.move`
reads the URLs from `backend/.env`. `scripts/upgrade.sh` runs it after the migrations.
"""

from __future__ import annotations

import argparse
import sys

from space import document_files, local_store
from space.database_setup import InstallSettings, connect
from space.files import size_text
from space.local_store import LocalObjectStore
from space.settings import CONFIG_FILE, Settings

# A batch is one transaction: a few hundred parts of at most 1 MiB, so a crash costs one
# batch of work and the lock it holds touches only those rows.
BATCH_SIZE = 250


def pending(connection) -> tuple[int, int]:
    """The parts still holding their bytes, and how many bytes that is."""
    row = connection.execute(
        "SELECT count(*), coalesce(sum(content_bytes),0) FROM space.file_parts "
        "WHERE content IS NOT NULL"
    ).fetchone()
    return int(row[0]), int(row[1])


def move(connection, objects, *, batch_size=BATCH_SIZE, dry_run=False, write=print):
    """Move every part whose bytes are still in its row into the object store.

    Parameters: an open connection with the migration credential, the store, the number
    of rows one transaction covers, whether to only report, and where to write progress.
    What it does: reads a batch of parts under a row lock, writes each part's bytes to the
    store under the key its row will name, checks them against the stored fingerprint, and
    clears `content` in the same transaction that recorded the key. A part whose bytes do
    not match is skipped and left alone.
    Output: a dict of the rows and bytes found, the parts moved and the parts refused.
    """
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    rows_pending, bytes_pending = pending(connection)
    counts = {
        "rows": rows_pending,
        "bytes": bytes_pending,
        "moved": 0,
        "refused": 0,
        "dry_run": dry_run,
    }
    if not rows_pending:
        return counts
    boundary = None
    while True:
        # The ctid boundary walks the table once: a part that is refused keeps its
        # `content`, so it would otherwise be read again by the next batch.
        with connection.transaction():
            rows = connection.execute(
                "SELECT ctid,organization_id,space_id,file_id,part,content,fingerprint "
                "FROM space.file_parts WHERE content IS NOT NULL "
                "AND ctid > coalesce(%s::tid,'(0,0)'::tid) ORDER BY ctid LIMIT %s FOR UPDATE",
                (boundary, batch_size),
            ).fetchall()
            for row in rows:
                boundary = row[0]
                raw = bytes(row[5])
                key = local_store.part_key(row[1], row[2], row[3], row[4])
                if document_files.fingerprint(row[3], row[4], raw) != bytes(row[6]):
                    counts["refused"] += 1
                    write(
                        f"Refused {key}: the bytes do not match the part's fingerprint, so "
                        "the database copy was kept. Check this part before running again."
                    )
                    continue
                counts["moved"] += 1
                if dry_run:
                    continue
                # The bytes go first: a crash between the two leaves an object no row
                # names, which `python -m space.files.reconcile` reports and can remove.
                objects.put(key, raw)
                connection.execute(
                    "UPDATE space.file_parts SET object_key=%s,content=NULL "
                    "WHERE file_id=%s AND part=%s",
                    (key, row[3], row[4]),
                )
        if len(rows) < batch_size:
            return counts


def report(counts, folder, write=print) -> int:
    """Print what a run did, and say plainly when the database still holds bytes."""
    if counts["dry_run"]:
        if not counts["rows"]:
            write(f"Nothing to move: every file part already names an object in {folder}.")
        else:
            write(
                f"Would move {counts['rows']} file parts ({size_text(counts['bytes'])}) "
                f"from the database into {folder}. Nothing was changed."
            )
    elif not counts["rows"]:
        write(f"Nothing to move: every file part already names an object in {folder}.")
    else:
        write(
            f"Moved {counts['moved']} of {counts['rows']} file parts "
            f"({size_text(counts['bytes'])}) into {folder}."
        )
    if counts["refused"]:
        write(
            f"{counts['refused']} part(s) were refused and left in the database; "
            "their bytes do not match their fingerprint."
        )
        return 1
    return 0


def main(argv=None) -> int:
    """Run the move from the command line. Output: an exit status, 1 when a part is refused."""
    parser = argparse.ArgumentParser(
        description="Move file bytes kept in PostgreSQL into the server's file folder."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print how many parts and bytes would move, and change nothing",
    )
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    args = parser.parse_args(argv)
    settings = InstallSettings()
    if not settings.migration_database_url:
        print("Set SPACE_MIGRATION_DATABASE_URL first", file=sys.stderr)
        return 2
    objects = LocalObjectStore(Settings(_env_file=CONFIG_FILE).file_dir)
    print("Space file move")
    print(f"Folder: {objects.root}")
    connection = connect(settings)
    try:
        counts = move(connection, objects, batch_size=args.batch_size, dry_run=args.dry_run)
    finally:
        connection.close()
    return report(counts, objects.root)


if __name__ == "__main__":
    raise SystemExit(main())
