#!/usr/bin/env bash
# Upgrade this installation to the latest release, or to the release named:
#   scripts/upgrade.sh
#   scripts/upgrade.sh v0.4.0
#   scripts/upgrade.sh --build     # build the images here instead of downloading them
# It downloads the release's images, backs up the database, switches this folder to the
# release and restarts Space. If the download fails, nothing changes. Database migrations
# run on start and only move forward, so it refuses to go back to an older release; restore
# a backup taken with that release instead.
set -euo pipefail

# Everything runs inside main, which bash reads completely before running it: switching
# this folder to another release replaces this file while it runs.
main() {
  local build=0 target=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --build) build=1 ;;
      -h | --help) sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
      -*) fail "Unknown option: $1" ;;
      *) target="$1" ;;
    esac
    shift
  done

  cd "$(dirname "$0")/.."
  [ -f .env ] || fail "No .env here. Install Space first: see docs/SELF_HOSTING.md."
  [ -d .git ] || fail "This folder is not a git checkout, so it cannot switch releases."

  local changed
  changed="$(git status --porcelain --untracked-files=no)"
  if [ -n "$changed" ]; then
    printf '%s\n' "$changed" >&2
    fail "These files were changed locally. Undo the changes, or move them to compose.override.yaml, which upgrades leave alone."
  fi

  git fetch --quiet --tags origin
  if [ -z "$target" ]; then
    target="$(git tag -l 'v*' | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | sort -V | tail -n 1)"
    [ -n "$target" ] || fail "No release found."
  fi
  git rev-parse --quiet --verify "refs/tags/$target" >/dev/null || fail "There is no release $target."

  local current
  current="$(sed -n 's/^SPACE_VERSION=//p' .env | tail -n 1)"
  current="${current:-latest}"
  if [ "$current" = "$target" ]; then
    echo "Already on $target."
    exit 0
  fi
  if [ "$current" != latest ] && [ "$(printf '%s\n%s\n' "$current" "$target" | sort -V | tail -n 1)" = "$current" ]; then
    fail "$target is older than $current. To go back, restore a backup taken with $target (scripts/restore.sh)."
  fi

  echo "Upgrading from $current to $target."
  if [ "$build" -eq 0 ]; then
    # The value in the environment wins over .env, so this fetches the new images while
    # the installation still runs the old ones.
    SPACE_VERSION="$target" docker compose pull --quiet ||
      fail "Could not download the images of $target; nothing was changed. Try again later, or build them here with: scripts/upgrade.sh --build $target"
  fi
  scripts/backup.sh
  git -c advice.detachedHead=false checkout --quiet "$target"
  set_version "$target"
  if [ "$build" -eq 1 ]; then
    docker compose up -d --wait --remove-orphans --build
  else
    docker compose up -d --wait --remove-orphans
  fi
  echo "Space is now on $target."
}

fail() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

# Rewrite SPACE_VERSION in place, keeping .env's owner and permissions.
set_version() {
  local next
  next="$(mktemp)"
  if grep -q '^SPACE_VERSION=' .env; then
    sed "s/^SPACE_VERSION=.*/SPACE_VERSION=$1/" .env >"$next"
  else
    { cat .env; printf '\n# The released version to run; scripts/upgrade.sh changes it.\nSPACE_VERSION=%s\n' "$1"; } >"$next"
  fi
  cat "$next" >.env
  rm -f "$next"
}

main "$@"
exit
