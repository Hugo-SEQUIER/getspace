#!/usr/bin/env bash
# Write .env for a self-hosted Space: the access mode, the public address and fresh secrets.
set -euo pipefail

cd "$(dirname "$0")/.."

usage() {
  cat <<'USAGE'
Usage: scripts/setup.sh [--mode key|private] [--domain NAME | --url URL] [options]

  --mode key       Default. Anyone who reaches the address must enter the access key,
                   and agents connect with tokens created on the website. Needs a domain
                   name pointing at this server, for HTTPS.
  --mode private   No access check at all. Space listens on 127.0.0.1:8080 only; reach
                   it through an SSH tunnel or a VPN. Never expose this mode publicly.
  --domain NAME    Domain name for key mode, for example space.example.com. Caddy
                   obtains its HTTPS certificate.
  --url URL        A plain-HTTP address on a private network instead of a domain, for
                   example http://127.0.0.1:8080 (the private-mode default) or
                   http://100.64.0.7:8080. Key mode accepts one too, for a LAN or VPN.
  --bind ADDRESS   Interface to listen on with --url (default: 127.0.0.1), for
                   example your Tailscale address 100.x.y.z.
  --owner NAME     Name shown in the website (default: Owner).
  --output FILE    Write somewhere other than .env, for example to compare settings.
  --force          Replace an existing file. Its secrets are lost.
USAGE
}

mode="" domain="" url="" bind="" owner="" output=".env" force=0
while [ $# -gt 0 ]; do
  case "$1" in
    --mode) mode="${2:-}"; shift 2 ;;
    --domain) domain="${2:-}"; shift 2 ;;
    --url) url="${2:-}"; shift 2 ;;
    --bind) bind="${2:-}"; shift 2 ;;
    --owner) owner="${2:-}"; shift 2 ;;
    --output) output="${2:-}"; shift 2 ;;
    --force) force=1; shift ;;
    -h | --help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [ -z "$output" ]; then
  echo "--output needs a file name." >&2
  exit 2
fi
if [ -e "$output" ] && [ "$force" -ne 1 ]; then
  echo "$output already exists. Keep it, or rerun with --force to replace it (its secrets are lost)." >&2
  exit 1
fi

ask() {
  local answer=""
  if [ -t 0 ]; then
    read -r -p "$1 [$2]: " answer || true
  fi
  printf '%s' "${answer:-$2}"
}

# 32 random bytes as hex: safe in URLs, shells and .env files.
secret() {
  od -An -N32 -tx1 /dev/urandom | tr -d ' \n'
}

if [ -z "$mode" ]; then
  mode="$(ask "Access mode: key (protected, reachable from the internet) or private (localhost or VPN, unprotected)" key)"
fi
case "$mode" in
  key | private) ;;
  *) echo "The mode must be key or private." >&2; exit 2 ;;
esac

if [ "$mode" = key ] && [ -z "$url" ]; then
  if [ -z "$domain" ]; then
    domain="$(ask "Domain name pointing at this server" "")"
  fi
  domain="${domain#https://}"
  domain="${domain%%/*}"
  if ! printf '%s' "$domain" | grep -Eq '^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)+$'; then
    echo "Key mode needs a domain name such as space.example.com, for its HTTPS certificate." >&2
    exit 2
  fi
  host="$domain"
  public_url="https://$domain"
  site_address="$domain"
  bind_address="0.0.0.0"
  http_port=80
  https_port=443
  access_key="$(secret)"
else
  url="${url:-http://127.0.0.1:8080}"
  if ! printf '%s' "$url" | grep -Eq '^http://[^/:]+(:[0-9]+)?/?$'; then
    echo "Use a plain-HTTP address such as http://127.0.0.1:8080 or http://100.64.0.1:8080." >&2
    exit 2
  fi
  url="${url%/}"
  hostport="${url#http://}"
  host="${hostport%%:*}"
  http_port=80
  if [ "$hostport" != "$host" ]; then
    http_port="${hostport##*:}"
  fi
  public_url="$url"
  site_address=":80"
  bind_address="${bind:-127.0.0.1}"
  https_port=8443
  access_key=""
  if [ "$mode" = key ]; then
    access_key="$(secret)"
  fi
fi

if [ -z "$owner" ]; then
  owner="$(ask "Your name, shown in the website" Owner)"
fi
owner="$(printf '%s' "$owner" | tr -d '\r\n"')"

# The released images to run: this checkout's version tag, or the latest release.
version="${SPACE_VERSION:-$(git describe --tags --exact-match 2>/dev/null || true)}"
version="${version:-latest}"

umask 077
cat >"$output" <<ENV
# Written by scripts/setup.sh on $(date -u +%Y-%m-%d). Keep this file private: it holds every
# secret of this installation. See docs/SELF_HOSTING.md for each setting.

# key: the website asks for SPACE_ACCESS_KEY once per browser, agents use tokens.
# private: no access check; keep the instance on localhost or a VPN.
SPACE_ACCESS_MODE=$mode
SPACE_ACCESS_KEY=$access_key

# How people and agents reach Space. Caddy serves CADDY_SITE_ADDRESS: a domain name
# gets an automatic HTTPS certificate, ":80" serves plain HTTP.
SPACE_PUBLIC_URL=$public_url
SPACE_ALLOWED_HOSTS=["$host","localhost","127.0.0.1","api"]
CADDY_SITE_ADDRESS=$site_address
SPACE_BIND_ADDRESS=$bind_address
SPACE_HTTP_PORT=$http_port
SPACE_HTTPS_PORT=$https_port
SPACE_OWNER_NAME="$owner"

# The released version to run. scripts/upgrade.sh changes it; "latest" follows new releases
# whenever the images are pulled again.
SPACE_VERSION=$version

# Database passwords. The installer applies them on every start.
POSTGRES_PASSWORD=$(secret)
SPACE_APP_DB_PASSWORD=$(secret)
SPACE_WORKER_DB_PASSWORD=$(secret)
SPACE_WEBHOOK_DB_PASSWORD=$(secret)

# Optional model provider for automatic memory: extraction, embeddings and semantic
# recall. Documents, tasks, databases and word search work without one.
# OpenAI-compatible example:
#   SPACE_MEMORY_PROVIDER=openai_compatible
#   SPACE_MEMORY_PROVIDER_URL=https://api.openai.com/v1
#   SPACE_MEMORY_API_KEY=sk-...
#   SPACE_MEMORY_TEXT_MODEL=gpt-5-mini
#   SPACE_MEMORY_EMBEDDING_MODEL=text-embedding-3-small
# Local Ollama example (add an ollama service in compose.override.yaml or run it on the host):
#   SPACE_MEMORY_PROVIDER=ollama
#   SPACE_MEMORY_PROVIDER_URL=http://ollama:11434
#   SPACE_MEMORY_TEXT_MODEL=qwen3:8b
#   SPACE_MEMORY_EMBEDDING_MODEL=nomic-embed-text
#   SPACE_MEMORY_EMBEDDING_DIMENSIONS=768
SPACE_MEMORY_PROVIDER=disabled
SPACE_MEMORY_PROVIDER_URL=
SPACE_MEMORY_API_KEY=
SPACE_MEMORY_TEXT_MODEL=
SPACE_MEMORY_EMBEDDING_MODEL=
# The embedding model's output size. Choose it before the first memory is stored.
SPACE_MEMORY_EMBEDDING_DIMENSIONS=1536
ENV

echo "Wrote $output (readable by you only)."
# scripts/install.sh starts Space itself and prints its own summary.
if [ -n "${SPACE_SETUP_QUIET:-}" ]; then
  exit 0
fi
echo
if [ "$mode" = key ] && [ -n "$domain" ]; then
  cat <<NEXT
Next:
  1. Point a DNS A (or AAAA) record for $domain at this server and open ports 80 and 443.
  2. Start Space:  docker compose up -d
  3. Open $public_url and unlock it with this access key (also in .env):

       $access_key

  Then connect your agents from the Agents page.
NEXT
elif [ "$mode" = key ]; then
  cat <<NEXT
Next:
  1. Start Space:  docker compose up -d
  2. Open $public_url and unlock it with this access key (also in .env):

       $access_key

  Plain HTTP sends the access key unencrypted: use this only on a private network.
NEXT
else
  cat <<NEXT
Next:
  1. Start Space:  docker compose up -d
  2. Open $public_url
     From another computer, tunnel to it first:  ssh -L $http_port:$bind_address:$http_port you@this-server

  Private mode has no access check. Keep port $http_port off the public internet.
NEXT
fi
