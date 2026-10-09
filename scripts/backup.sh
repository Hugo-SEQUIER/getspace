#!/usr/bin/env bash
# Back up the whole Space database to backups/space-<UTC time>.dump.
# Every document, task, database, memory and setting lives in PostgreSQL, so this one
# file is a complete backup. Copy it off the server; a backup on the same disk is not one.
set -euo pipefail
cd "$(dirname "$0")/.."

mkdir -p backups
chmod 700 backups
file="backups/space-$(date -u +%Y%m%dT%H%M%SZ).dump"
umask 077
docker compose exec -T db pg_dump -U postgres -d space --format=custom >"$file.partial"
mv "$file.partial" "$file"
echo "Backup written to $file ($(du -h "$file" | cut -f1))."
