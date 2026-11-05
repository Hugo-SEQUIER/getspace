#!/usr/bin/env bash
# Back up the whole installation, which is two files with one timestamp:
#   backups/space-<UTC time>.dump           every document, task, database, memory and setting
#   backups/space-<UTC time>.files.tar.gz   the folder holding the bytes of uploaded files
# Both are needed. The database keeps the path of each file part, not its bytes, so a dump
# alone restores documents whose files are missing. Copy both off the server, with .env: a
# backup on the same disk is not one.
set -euo pipefail
cd "$(dirname "$0")/.."

mkdir -p backups
chmod 700 backups
umask 077
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
dump="backups/space-$stamp.dump"
archive="backups/space-$stamp.files.tar.gz"

# How many files the folder holds, so an empty one is said plainly rather than guessed at
# from the size of the archive.
sizes="$(docker compose run --rm --no-deps -T --entrypoint sh migrate -c \
  "find /var/lib/space/files -type f ! -name '.*' -printf '%s\n' 2>/dev/null" |
  awk '{n++; b += $1} END {print n + 0 " " b + 0}')"
count="${sizes%% *}"
bytes="${sizes##* }"

docker compose exec -T db pg_dump -U postgres -d space --format=custom >"$dump.partial"
mv "$dump.partial" "$dump"

# The archive is read through the same image and volume, as the user that owns the folder.
# The store renames each part into place, so a part written while this runs is archived
# either as its previous bytes or whole, never half written.
docker compose run --rm --no-deps -T --entrypoint tar migrate \
  -czf - -C /var/lib/space/files . >"$archive.partial"
mv "$archive.partial" "$archive"

echo "Backup written to $dump ($(du -h "$dump" | cut -f1)) and $archive ($(du -h "$archive" | cut -f1))."
if [ "$count" -eq 0 ]; then
  echo "The file folder is empty: no document here keeps an uploaded file, so the archive holds no files."
else
  echo "The file archive holds $count files, $bytes bytes. Copy both files and .env off this server."
fi
