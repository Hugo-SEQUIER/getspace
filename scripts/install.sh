#!/usr/bin/env bash
# Install Space on a Linux server in one command: Docker if it is missing, the latest
# release, the settings, then start it.
#
#   curl -fsSL https://raw.githubusercontent.com/Hugo-SEQUIER/getspace/main/scripts/install.sh | sudo bash
#
# It asks for the access mode, the domain name and your name. To answer on the command
# line instead, pass scripts/setup.sh's options after `bash -s --`:
#
#   curl -fsSL .../scripts/install.sh | sudo bash -s -- --mode key --domain space.example.com --owner "Ada"
#
# SPACE_DIR chooses the installation folder (default /opt/space) and SPACE_VERSION a release
# other than the latest, for example SPACE_VERSION=v0.4.0.
set -euo pipefail

# Everything runs inside main, which bash reads completely before running it: a partial
# download of this script never runs half a command.
main() {
  local repo="${SPACE_REPO:-https://github.com/Hugo-SEQUIER/getspace.git}"
  local dir="${SPACE_DIR:-/opt/space}"
  local version="${SPACE_VERSION:-}"

  [ "$(uname -s)" = Linux ] ||
    fail "This installer is for Linux servers. On macOS or Windows, follow docs/SELF_HOSTING.md."
  [ "$(id -u)" -eq 0 ] ||
    fail "Run it as root, for example: curl -fsSL <installer address> | sudo bash"

  if [ -f "$dir/.env" ]; then
    echo "Space is already installed in $dir."
    echo "To upgrade it: cd $dir && scripts/upgrade.sh"
    exit 0
  fi

  check_memory
  command -v git >/dev/null 2>&1 || { step "Installing git"; install_packages git; }
  command -v curl >/dev/null 2>&1 || { step "Installing curl"; install_packages curl; }
  if ! command -v docker >/dev/null 2>&1; then
    step "Installing Docker"
    curl -fsSL https://get.docker.com | sh
  fi
  systemctl enable --now docker >/dev/null 2>&1 || true
  docker compose version >/dev/null 2>&1 ||
    fail "Docker is installed without the Compose plugin. Install docker-compose-plugin, then run this again."

  if [ -z "$version" ]; then
    version="$(latest_release "$repo")"
    [ -n "$version" ] || fail "No release of Space was found at $repo."
  fi

  step "Downloading Space $version into $dir"
  if [ ! -d "$dir/.git" ]; then
    git clone --quiet "$repo" "$dir"
  fi
  cd "$dir"
  git fetch --quiet --tags origin
  git -c advice.detachedHead=false checkout --quiet "$version"

  step "Configuring"
  if [ -t 0 ] || ! { : </dev/tty; } 2>/dev/null; then
    SPACE_SETUP_QUIET=1 SPACE_VERSION="$version" scripts/setup.sh "$@"
  else
    # Piped from curl, standard input is this script: ask the questions on the terminal.
    SPACE_SETUP_QUIET=1 SPACE_VERSION="$version" scripts/setup.sh "$@" </dev/tty
  fi

  step "Downloading the images"
  if ! docker compose pull --quiet; then
    echo "The released images could not be downloaded; building them here instead (several minutes)."
    docker compose build
  fi

  step "Starting Space"
  docker compose up -d --wait

  summary
}

step() {
  printf '\n==> %s\n' "$*"
}

fail() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

install_packages() {
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "$@"
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y -q "$@"
  elif command -v yum >/dev/null 2>&1; then
    yum install -y -q "$@"
  else
    fail "Install $* yourself, then run this again."
  fi
}

# The running stack needs about 300 MB; the operating system and Docker need the rest.
check_memory() {
  local kb
  kb="$(awk '/^MemTotal:/ {print $2}' /proc/meminfo 2>/dev/null || echo 0)"
  if [ "${kb:-0}" -gt 0 ] && [ "$kb" -lt 900000 ]; then
    echo "Warning: this server has less than 1 GB of memory. Space may run out of memory." >&2
  fi
}

# The highest vX.Y.Z tag; prereleases such as v1.0.0-rc.1 are skipped.
latest_release() {
  git ls-remote --tags --refs "$1" 'v*' 2>/dev/null |
    sed 's#.*refs/tags/##' |
    grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' |
    sort -V |
    tail -n 1
}

setting() {
  sed -n "s/^$1=//p" .env | tail -n 1 | tr -d '"'
}

summary() {
  local url mode key site
  url="$(setting SPACE_PUBLIC_URL)"
  mode="$(setting SPACE_ACCESS_MODE)"
  key="$(setting SPACE_ACCESS_KEY)"
  site="$(setting CADDY_SITE_ADDRESS)"

  printf '\nSpace is running.\n\n  Open:  %s\n' "$url"
  if [ "$mode" = key ]; then
    printf '  Access key (also in %s/.env):\n\n    %s\n' "$(pwd)" "$key"
  fi
  if [ "$site" != ":80" ]; then
    cat <<NEXT

  The HTTPS certificate is issued once the domain's DNS record points at this server and
  ports 80 and 443 are open. Until then the address does not load; nothing needs restarting.
NEXT
  elif [ "$(setting SPACE_BIND_ADDRESS)" = 127.0.0.1 ]; then
    cat <<NEXT

  Space listens on this server only. From your computer, tunnel to it first:
    ssh -L ${url##*:}:127.0.0.1:${url##*:} you@this-server
NEXT
  fi
  cat <<NEXT

  Next: connect your agents from the Agents page, and set up nightly backups
  (docs/SELF_HOSTING.md#backups). Upgrade later with:  cd $(pwd) && scripts/upgrade.sh
NEXT
}

main "$@"
exit
