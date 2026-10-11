# Self-hosting

Space runs as one Docker Compose stack on a machine you control. This guide covers installing it, the two access modes, every setting, upgrades, backups and troubleshooting.

## Requirements

- A Linux server with Docker Engine 24 or newer and the Compose plugin (`docker compose version`); the installer adds them if they are missing. macOS and Windows with Docker Desktop work for trying it out.
- At least 1 GB of memory and 10 GB of disk. The running stack uses about 300 MB. Building the images from source instead of downloading them needs 2 GB.
- For key mode: a domain or subdomain whose DNS `A` (or `AAAA`) record points at the server, and ports 80 and 443 open to the internet. Caddy uses port 80 to obtain the HTTPS certificate from Let's Encrypt.

## Install

### With the installer

On a fresh Linux server, as root or with `sudo`:

```sh
curl -fsSL https://raw.githubusercontent.com/Hugo-SEQUIER/getspace/main/scripts/install.sh | sudo bash
```

It installs git and Docker if they are missing, downloads the latest release into `/opt/space`, runs `scripts/setup.sh` (described below), downloads the images, starts Space and prints its address and, in key mode, the access key. It changes no firewall rules: in key mode, open ports 80 and 443 yourself if the server has a firewall. To answer the setup questions on the command line, pass the options of `scripts/setup.sh` after `bash -s --`:

```sh
curl -fsSL https://raw.githubusercontent.com/Hugo-SEQUIER/getspace/main/scripts/install.sh \
  | sudo bash -s -- --mode key --domain space.example.com --owner "Ada"
```

`SPACE_DIR=/srv/space` installs somewhere else, and `SPACE_VERSION=v0.4.0` installs a release other than the latest. Run against a folder that already holds an installation, the installer changes nothing and points to `scripts/upgrade.sh`.

### By hand

```sh
git clone https://github.com/Hugo-SEQUIER/getspace.git
cd getspace
git checkout "$(git tag -l 'v*' | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | sort -V | tail -n 1)"
scripts/setup.sh
docker compose up -d
```

`scripts/setup.sh` asks two questions, the access mode and the address, and writes `.env` with freshly generated passwords and, in key mode, the access key. It never overwrites an existing `.env` unless you pass `--force`, which discards its secrets. You can also answer on the command line:

```sh
scripts/setup.sh --mode key --domain space.example.com --owner "Ada"
scripts/setup.sh --mode private --url http://127.0.0.1:8080
scripts/setup.sh --mode private --url http://100.101.102.103:8080 --bind 100.101.102.103
```

`docker compose up -d` downloads the two released images named by `SPACE_VERSION` (written by `scripts/setup.sh`: the release you checked out, or `latest`). To build them from your checkout instead, for example after changing the code, run `docker compose up -d --build`; that takes a few minutes. The `migrate` service then creates the database logins and schema and exits, the API creates your Private Space, and the website becomes available. Check with `docker compose ps`: every service except `migrate` should be running, and `migrate` should have exited with code 0.

## Access modes

Space has no user accounts. One installation belongs to one person, the owner, and the access mode decides how the installation is protected.

### Key mode

The default, for a server on the internet.

- The website shows an unlock page and asks for the access key (`SPACE_ACCESS_KEY` in `.env`) once per browser. It then keeps a signed session cookie for 30 days. Changing the key logs every browser out.
- Each agent connects with its own token, created on the website under **Agents**. A token can be read-only or read-write, and revoking it disconnects only that agent.
- Apps that sign in through the browser, such as claude.ai and ChatGPT, need only the MCP address: add it as a custom connector, and approve the app when it opens Space. See [connecting agents](CONNECTING_AGENTS.md#key-mode-apps-that-sign-in-through-the-browser). Hosted apps connect from their own servers, so this needs a public HTTPS address.
- Personal API keys for your own scripts are created under **Settings → API keys**. The access key itself also works as a bearer token for the REST API, but not for MCP.
- Wrong unlock attempts are rate-limited per client address.

Keep the access key secret and long; `scripts/setup.sh` generates 64 random hexadecimal characters. Anyone who has it controls the whole installation.

Key mode can also run on a private network without a domain: `scripts/setup.sh --mode key --url http://100.64.0.7:8080 --bind 100.64.0.7` serves plain HTTP on that address and still asks for the access key. Plain HTTP sends the key unencrypted, so use this only on a LAN or VPN you trust.

### Private mode

No access check at all, for a machine only you can reach.

- The website opens directly. Agents connect without a token and identify themselves by name; each agent identity still gets its own connection and attribution.
- `scripts/setup.sh` binds Caddy to `127.0.0.1:8080`, so only the server itself can connect. Reach it from your computer with an SSH tunnel, `ssh -L 8080:127.0.0.1:8080 you@server`, then open `http://127.0.0.1:8080`.
- With a VPN such as Tailscale or WireGuard, bind to the VPN address instead (`--bind 100.x.y.z`) and use that address in `--url`. Plain HTTP is accepted only for loopback, private-network and VPN addresses.

Never publish a private-mode installation on a public address: anyone who reaches it can read and delete everything.

## Configuration

All settings live in `.env` next to `compose.yaml`. After changing one, apply it with `docker compose up -d`.

| Setting | Meaning |
|---|---|
| `SPACE_ACCESS_MODE` | `key` or `private`, as described above. |
| `SPACE_ACCESS_KEY` | Key mode only: the access key, at least 32 characters. |
| `SPACE_PUBLIC_URL` | The address people and agents use, without a trailing slash, for example `https://space.example.com`. The MCP endpoint is this address plus `/mcp/`. |
| `SPACE_ALLOWED_HOSTS` | JSON list of host names the API answers to. Keep your public host name, `localhost`, `127.0.0.1` and `api` in it. |
| `CADDY_SITE_ADDRESS` | What Caddy serves: a domain name (automatic HTTPS) or `:80` (plain HTTP). |
| `SPACE_BIND_ADDRESS` | Interface the published ports listen on: `0.0.0.0` for all, `127.0.0.1` for the server only, or a VPN address. |
| `SPACE_HTTP_PORT`, `SPACE_HTTPS_PORT` | Published ports for HTTP and HTTPS. |
| `SPACE_OWNER_NAME` | The name the website shows for you. |
| `SPACE_VERSION` | The released images to run: a release tag such as `v0.4.0`, or `latest`. `scripts/upgrade.sh` changes it. |
| `POSTGRES_PASSWORD` | The database superuser password, used only by the `migrate` service. |
| `SPACE_APP_DB_PASSWORD`, `SPACE_WORKER_DB_PASSWORD`, `SPACE_WEBHOOK_DB_PASSWORD` | Passwords of the three restricted database logins used by the API and the two workers. The installer applies them on every start, so you can change them in `.env`. |
| `SPACE_MEMORY_PROVIDER` | `disabled` (default), `openai_compatible` or `ollama`. |
| `SPACE_MEMORY_PROVIDER_URL` | Base URL of the provider, for example `https://api.openai.com/v1` or `http://ollama:11434`. |
| `SPACE_MEMORY_API_KEY` | The provider's API key, if it needs one. |
| `SPACE_MEMORY_TEXT_MODEL`, `SPACE_MEMORY_EMBEDDING_MODEL` | Required with a provider. |
| `SPACE_MEMORY_EMBEDDING_DIMENSIONS` | The embedding model's output size: 1536 (default) for `text-embedding-3-small`, 768 for `nomic-embed-text`, 1024 for `mxbai-embed-large` or `bge-m3`. Up to 2000. Set it before the first memory is stored; see below. |
| `SPACE_MEMORY_RELEVANCE_MODEL` | Optional model that assesses recall evidence; defaults to the text model. |
| `SPACE_MEMORY_EMBEDDING_URL`, `SPACE_MEMORY_EMBEDDING_API_KEY` | Optional separate endpoint and key for embeddings. |
| `SPACE_MEMORY_REASONING_EFFORT`, `SPACE_MEMORY_RELEVANCE_REASONING_EFFORT` | `none`, `low`, `medium` or `high`, only for models that accept a reasoning effort. |

The PostgreSQL superuser password is used only when the database volume is first created. To change it later, change it inside PostgreSQL as well (`ALTER ROLE postgres PASSWORD ...`) before editing `.env`.

### Without a model provider

Documents, tasks, databases, handoffs, word search, lexical recall, webhooks and the API all work with `SPACE_MEMORY_PROVIDER=disabled`. Tools that need a model, such as `remember`, `extract_memory` and semantic recall, report that no provider is configured.

### With a model provider

Space sends a provider only the text an agent explicitly asks it to process: claims to embed, sources to extract, and candidate passages to assess during recall. Nothing is sent in the background on its own. Plain HTTP is allowed only for providers on localhost or a private network, such as an Ollama container on the same compose network.

The memory index stores every embedding with one fixed size. On each start the installer sizes the index to `SPACE_MEMORY_EMBEDDING_DIMENSIONS` while it is still empty; once memories exist it refuses another size, because vectors of different sizes cannot be compared. Pick the embedding model before storing memories. For a fully local setup, run Ollama next to Space, pull a chat model and an embedding model, and set `SPACE_MEMORY_PROVIDER=ollama` with the matching size.

## Operating Space

### Logs and status

```sh
docker compose ps
docker compose logs -f --tail=100 api
```

Logs contain request statuses and error categories, never document content or credentials. Each container's logs are capped at 30 MB.

### Upgrades

```sh
scripts/upgrade.sh            # the latest release
scripts/upgrade.sh v0.4.0     # or a specific one
```

The script downloads the release's images, takes a backup with `scripts/backup.sh`, switches the folder to the release, sets `SPACE_VERSION` in `.env` and restarts Space. If the images cannot be downloaded, it stops before changing anything. `scripts/upgrade.sh --build` builds the images on the server instead, which needs 2 GB of memory. The `migrate` service applies new database migrations before the API starts. Migrations only move forward, so the script refuses an older release; to go back, restore a backup taken with that version. [Releases](https://github.com/Hugo-SEQUIER/getspace/releases) lists what each version changes.

The script refuses to run if files of the checkout were changed locally, since switching releases would overwrite them. Put your own additions, such as an Ollama service, in `compose.override.yaml` next to `compose.yaml`: Docker Compose reads it automatically and upgrades leave it alone.

### Backups

```sh
scripts/backup.sh
```

This writes `backups/space-<UTC time>.dump`, a PostgreSQL archive of the whole installation: every document, task, database, memory, agent connection and setting. There is nothing else to back up besides `.env`. Copy both somewhere off the server, and keep `.env` as private as the backup, since each contains secrets. A `cron` entry such as `0 3 * * * cd /opt/space && scripts/backup.sh` takes a nightly backup; prune old files yourself.

### Restoring

```sh
scripts/restore.sh backups/space-20261009T030000Z.dump
```

This stops the website, the API and the workers, replaces the database contents with the backup, upgrades it to the current version if it is older, and starts everything again. The database logins keep the passwords from the current `.env`, so you can restore a backup onto a fresh installation with new secrets.

### Changing the access key

Edit `SPACE_ACCESS_KEY` in `.env` (at least 32 characters), then run `docker compose up -d`. Every browser has to unlock again. Agent tokens and API keys are unaffected; revoke them on the website if they may have leaked.

### Moving to a new domain

Change `SPACE_PUBLIC_URL`, `SPACE_ALLOWED_HOSTS` and `CADDY_SITE_ADDRESS`, then run `docker compose up -d`. Your data is unaffected, but agents connected with the old MCP address must be reconnected, and apps that signed in must sign in again with the new one: each connection is bound to the MCP address it was created for.

### Database access

Content is stored as readable JSON. To inspect it:

```sh
docker compose exec db psql -U postgres -d space
```

Columns whose names start with `encrypted_` keep their historical names but hold plain UTF-8 JSON; read one with `convert_from(encrypted_record, 'UTF8')::jsonb`. Change data through the website, the API or MCP rather than SQL, so revisions, search terms and the claim index stay consistent.

## Security notes

- **No application encryption.** Whoever can read the server's disk, its backups or the database can read every document and memory. Encrypt the disk and the backups if that matters to you.
- **One owner.** There are no accounts or roles to separate people. Share an installation only with people you would give the access key to, or give others API keys and agent tokens with read-only access.
- **Agents are delegates.** A read-write agent token can create, change and delete content. Prefer read-only tokens for agents that only need to look things up, and revoke tokens you no longer use.
- **App sign-in is open to registration.** Any app can register itself and start a sign-in, as hosted assistants require, but nothing is granted until you approve it on the website. The consent page shows where it returns you; allow access only to a connection you started.
- **Webhook signing secrets** and API key metadata are stored in the database. API keys, agent tokens and OAuth tokens themselves are stored only as SHA-256 digests.
- **Updates.** Keep the host's packages, Docker and Space itself up to date.
- **Exposure.** Only Caddy publishes ports. The database, API and website are reachable only inside the compose network.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `migrate` exits with a non-zero code | Run `docker compose logs migrate`. A password mismatch means `POSTGRES_PASSWORD` changed after the database volume was created. |
| Caddy cannot obtain a certificate | The DNS record does not point at the server yet, or port 80 or 443 is blocked by a firewall. |
| The website shows "Service unavailable" | The API is still starting or failing: `docker compose logs api`. |
| An agent gets 401 Unauthorized | The token was revoked or mistyped, or the agent sends no `Authorization` header. Create a new connection on the Agents page. |
| claude.ai, ChatGPT or another hosted app cannot connect | It reaches Space from its own servers. Use key mode on a public HTTPS address, and give the app exactly `SPACE_PUBLIC_URL` plus `/mcp/`. |
| The consent page says the request expired | Sign-in requests expire after ten minutes. Start the connection again from the app. |
| An agent is told to identify itself | It must call `identify_agent` once, then send `X-Space-Agent-ID` (or `agent_id`) with every request. See [connecting agents](CONNECTING_AGENTS.md). |
| Memory tools report no provider | Set `SPACE_MEMORY_PROVIDER` and its models in `.env`, then run `docker compose up -d`. |
| "Service URLs require HTTPS" in the API log | `SPACE_PUBLIC_URL` uses plain HTTP on a public host name. Use HTTPS, or a private-network address in private mode. |
| The website refuses changes from this address (403) | Open Space at exactly `SPACE_PUBLIC_URL`. The website accepts changes only from that origin, so `http://localhost:8080` and `http://127.0.0.1:8080` are different addresses to it. |
