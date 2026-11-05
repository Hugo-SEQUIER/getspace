#!/usr/bin/env bash
# Replace this installation's data with a backup made by scripts/backup.sh.
#
# A backup is two files with one timestamp, and both are required:
#   backups/space-<time>.dump           the database
#   backups/space-<time>.files.tar.gz   the folder holding the bytes of uploaded files
# Name the dump; the script finds the archive beside it. It refuses rather than restore half
# a backup, because a database restored without its files lists documents that cannot open.
# The database logins keep this installation's passwords from .env.
set -euo pipefail
cd "$(dirname "$0")/.."

file="${1:-}"
if [ -z "$file" ]; then
  echo "Usage: scripts/restore.sh backups/space-<time>.dump" >&2
  exit 2
fi
files="${file%.dump}.files.tar.gz"
missing=""
[ -f "$file" ] || missing="database dump $file"
[ -f "$files" ] || missing="${missing:+$missing and }file archive $files"
if [ -n "$missing" ]; then
  echo "Cannot restore: this backup is missing its $missing." >&2
  echo "A backup is the dump and the file archive together, both written by scripts/backup.sh." >&2
  echo "Restoring one half would bring back documents whose files are not there." >&2
  exit 2
fi

if [ "${2:-}" != "--yes" ]; then
  echo "This replaces every document, task, database, memory and uploaded file here with the backup."
  read -r -p "Type restore to continue: " answer
  [ "$answer" = restore ] || { echo "Cancelled."; exit 1; }
fi

docker compose stop web api worker webhooks
docker compose up -d --wait db
# The installer creates the logins the backup's grants refer to.
docker compose run --rm migrate
docker compose exec -T db pg_restore -U postgres -d space --clean --if-exists --single-transaction <"$file"
# Upgrade a backup taken on an older version, then start everything again.
docker compose run --rm migrate
# The file folder goes back before the API starts, so no request can read a database row
# that names a file the folder does not have yet. The folder is emptied first: it may hold
# files written after the backup, which the restored rows know nothing about.
scripts/files-dir.sh
docker compose run --rm -T --no-deps --entrypoint sh migrate -c '
  set -e
  find /var/lib/space/files -mindepth 1 -maxdepth 1 -exec rm -rf {} +
  tar -xzf - -C /var/lib/space/files
' <"$files"
docker compose up -d
echo "Restored $file and $files."
