#!/usr/bin/env bash
# Replace this installation's data with a backup made by scripts/backup.sh.
# The database logins keep this installation's passwords from .env.
set -euo pipefail
cd "$(dirname "$0")/.."

file="${1:-}"
if [ -z "$file" ] || [ ! -f "$file" ]; then
  echo "Usage: scripts/restore.sh backups/space-<time>.dump" >&2
  exit 2
fi
if [ "${2:-}" != "--yes" ]; then
  echo "This replaces every document, task, database and memory here with the backup."
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
docker compose up -d
echo "Restored $file."
