#!/usr/bin/env bash
# Copy the agent skills from the space-skills repository into skills/.
# space-skills is the only place the skills are edited; skills/ is a copy, and
# skills/.source records the commit it came from.
#
#   scripts/sync-skills.sh [ref]    copy the given branch, tag or commit (default: main)
#   scripts/sync-skills.sh --check  fail if skills/ differs from the recorded commit
#
# SPACE_SKILLS_REPO overrides the source, for example a local checkout.
set -euo pipefail
cd "$(dirname "$0")/.."

repo="${SPACE_SKILLS_REPO:-https://github.com/Hugo-SEQUIER/space-skills.git}"
ref="${1:-main}"
check=false
if [[ "$ref" == "--check" ]]; then
  check=true
  ref="$(sed -n 's/^commit: //p' skills/.source 2>/dev/null || true)"
  [[ -n "$ref" ]] || { echo "skills/.source has no commit; run scripts/sync-skills.sh first." >&2; exit 1; }
fi

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
git -C "$work" init --quiet
git -C "$work" fetch --quiet --depth 1 "$repo" "$ref"
commit="$(git -C "$work" rev-parse FETCH_HEAD)"
mkdir "$work/skills"
git -C "$work" archive FETCH_HEAD | tar -x -C "$work/skills"
rm -rf "$work/skills/.github" "$work/skills/.gitignore" "$work/skills/.gitattributes"

if $check; then
  if ! diff -r --exclude=.source "$work/skills" skills; then
    echo "skills/ differs from space-skills $commit. Edit the skills in space-skills, then run scripts/sync-skills.sh." >&2
    exit 1
  fi
  echo "skills/ matches space-skills $commit."
  exit 0
fi

rsync -a --delete --exclude=.source "$work/skills/" skills/
printf 'repository: %s\ncommit: %s\n' "${repo%.git}" "$commit" >skills/.source
echo "skills/ now matches space-skills $commit."
