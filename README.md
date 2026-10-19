# Space

Space is a workspace your AI agents share with you: documents, tasks, databases and long-term memory that agents read and write over [MCP](https://modelcontextprotocol.io), with a website where you see, correct and edit the same work. Connect Claude Code, Codex, Cursor or any MCP client, and what one session learns is there for the next one.

This repository is the open-source, self-hosted edition. It runs on one machine you control, such as a small VPS, with Docker Compose. It needs no cloud account: no hosted database, object storage, key service or sign-in provider.

## What you get

- **Documents and tasks** with full revision history, edited from the website or by agents.
- **Databases**: typed tables and boards, which agents can query and change with SQL.
- **Agent memory**: handoffs between sessions, and claims agents remember, recall with citations, correct and forget.
- **Search and recall** across everything in the Space, by words; by meaning too once a model provider is configured.
- **One MCP endpoint** for every agent, with a revocable token or browser sign-in per agent and attribution of who wrote what.
- **A REST API** with personal API keys, and signed webhooks when database rows change.
- **Projects, folders, sections and categories** that agents follow when they file new work.

## How it is built

| Part | What it runs |
|---|---|
| `caddy` | The only published service. Serves HTTPS with automatic certificates and routes requests. |
| `web` | The website (TanStack Start on Node.js). |
| `api` | The REST API and the MCP endpoint (Python, FastAPI). |
| `worker` | Background memory extraction. |
| `webhooks` | Signed webhook delivery. |
| `db` | PostgreSQL 17 with pgvector. Every document, task, memory and setting lives here. |

Content is stored as readable JSON in PostgreSQL. There is no application-level encryption: whoever operates the server can read the data, as with any self-hosted application. Back up the database and protect the server accordingly.

## Install

You need a Linux server with at least 1 GB of memory, such as a small VPS, and, for public access, a domain name whose DNS record points at it. On the server, run:

```sh
curl -fsSL https://raw.githubusercontent.com/Hugo-SEQUIER/getspace/main/scripts/install.sh | sudo bash
```

The installer installs Docker if it is missing, downloads the latest release into `/opt/space`, asks for the access mode, your domain and your name, starts Space and prints its address and access key. To do the same steps by hand:

```sh
git clone https://github.com/Hugo-SEQUIER/getspace.git
cd getspace
git checkout "$(git tag -l 'v*' | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | sort -V | tail -n 1)"   # latest release
scripts/setup.sh                 # asks for the access mode and your domain, writes .env
docker compose up -d             # downloads the released images and starts everything
```

Both offer two access modes:

- **Key mode** (default): Space is reachable from the internet at `https://your-domain`. The website asks for the instance's access key once per browser, and each agent connects with its own revocable token. Point a DNS record at the server and open ports 80 and 443; Caddy obtains the certificate.
- **Private mode**: no access check at all. Space listens on `127.0.0.1:8080` and you reach it through an SSH tunnel or a VPN such as Tailscale. Never expose this mode publicly.

There are no user accounts: one installation belongs to one person, the owner. [Self-hosting](docs/SELF_HOSTING.md) covers both modes, every setting, upgrades, backups and troubleshooting.

Moving from the hosted Space? Export your Space there and import it here: [Moving from the hosted Space](docs/MOVING_FROM_HOSTED.md).

## Connect an agent

Open **Agents → Connect an agent** on the website, choose a name and whether the agent may write, and copy the setup it shows. For Claude Code:

```sh
claude mcp add --transport http space https://your-domain/mcp/ \
  --header "Authorization: Bearer spc_..."
```

Hosted assistants such as claude.ai and ChatGPT sign in instead: add `https://your-domain/mcp/` to the app as a custom connector, and approve it when it opens Space in your browser.

In private mode agents need no token: `claude mcp add --transport http space http://127.0.0.1:8080/mcp/`. [Connecting agents](docs/CONNECTING_AGENTS.md) has setups for Codex, Cursor and other clients, and the optional [agent skills](skills/README.md) teach an agent when to recall, remember and hand off.

## Memory with a model provider

Documents, tasks, databases, handoffs and word search work without any model. Automatic memory (extracting claims from text, embeddings and semantic recall) needs an OpenAI-compatible endpoint or Ollama; set `SPACE_MEMORY_PROVIDER` and the related settings in `.env`. Only the text an agent asks Space to process is sent to that provider. A decision model on OpenRouter's Decisions API can also assess recall evidence, organize memories against your categories and projects, and link related memories; each of those is off until you turn it on. See [memory](docs/MEMORY.md).

## Back up and upgrade

```sh
scripts/backup.sh                                  # writes backups/space-<time>.dump
scripts/upgrade.sh                                 # backs up, then moves to the latest release
scripts/restore.sh backups/space-<time>.dump       # replace this installation's data
```

Copy backups off the server: a backup on the same disk is not a backup.

## Documentation

| Document | What it covers |
|---|---|
| [Self-hosting](docs/SELF_HOSTING.md) | Install, access modes, configuration, upgrades, backups, security and troubleshooting. |
| [Moving from the hosted Space](docs/MOVING_FROM_HOSTED.md) | Exporting a hosted Space and importing it into your own installation. |
| [Connecting agents](docs/CONNECTING_AGENTS.md) | Tokens, client setups, agent identities and the device-code flow. |
| [Architecture](docs/ARCHITECTURE.md) | Processes, storage, access control and background work. |
| [Development](docs/DEVELOPMENT.md) | Running from source, tests and checks. |
| [MCP](docs/MCP.md) | The agent interface and its tools. |
| [Memory](docs/MEMORY.md) | Handoffs, claims, recall and the claim index. |
| [Webhooks](docs/WEBHOOKS.md) | Signed notifications when database rows change. |
| [Glossary](docs/GLOSSARY.md) | Every term the system uses. |
| [API reference](docs/openapi.json) | The REST API a personal API key can call (OpenAPI). |

## License

Space is free software under the [GNU Affero General Public License v3.0](LICENSE). You may use, modify and self-host it for any purpose. If you run a modified version for other people over a network, you must offer them its source code. The vendored Memora runtime keeps its MIT license; see [NOTICE](NOTICE) and [THIRD-PARTY.md](THIRD-PARTY.md).

Contributions are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) and accept the [Contributor License Agreement](CLA.md) before your first pull request.
