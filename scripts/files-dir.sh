#!/usr/bin/env bash
# Create the folder Space keeps uploaded file bytes in, with the owner the backend runs as.
#
# The folder is the files-data volume, mounted at /var/lib/space/files. A new volume copies
# the owner of that directory from the image, so this is only needed for a volume created
# by an older release, or one made by hand with `docker volume create`. The backend runs as
# uid 10001, so the folder must belong to it and be readable by nobody else.
#
# It runs a one-off container of the migrate service as root, because only root can change a
# volume's owner, and it leaves the database alone (--no-deps).
set -euo pipefail
cd "$(dirname "$0")/.."

docker compose run --rm --no-deps --user 0:0 --entrypoint sh migrate -c '
  set -e
  mkdir -p /var/lib/space/files
  chown 10001:10001 /var/lib/space/files
  chmod 700 /var/lib/space/files
'
