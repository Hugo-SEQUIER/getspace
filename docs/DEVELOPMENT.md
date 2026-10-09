# Development

This page explains how to run Space from source and which checks a change must pass. [Architecture](ARCHITECTURE.md) describes the code, and [CONTRIBUTING.md](../CONTRIBUTING.md) the rules for a pull request. To run Space for real use, follow [Self-hosting](SELF_HOSTING.md) instead.

## Prerequisites

- Python 3.13 and [uv](https://docs.astral.sh/uv/). `backend/uv.lock` pins every Python dependency and tool, including the formatter.
- Node.js 22.12 or newer and below 27, with npm. `apps/web/package-lock.json` pins the website's dependencies.
- Docker, for the local database and the database tests.

Install the dependencies of both parts from the repository root:

```sh
make setup
```

This runs `uv sync --directory backend --locked --group dev` and `npm --prefix apps/web ci`. Use the project's environments rather than installing packages yourself: `uv run --directory backend --locked ...` for Python and `npm --prefix apps/web ...` for the website.

## A local database

Run PostgreSQL with pgvector in a container that listens only on the loopback address:

```sh
docker run -d --name space-dev-db -p 127.0.0.1:5432:5432 \
  -e POSTGRES_PASSWORD=change-me-postgres -e POSTGRES_DB=space \
  pgvector/pgvector:0.8.2-pg17
```

The backend reads its settings from `backend/.env`, which git ignores; variables set in the environment take precedence over the file. A minimal development file:

```dotenv
SPACE_MIGRATION_DATABASE_URL=postgresql://postgres:change-me-postgres@127.0.0.1:5432/space
SPACE_DATABASE_URL=postgresql://space_app:change-me-app@127.0.0.1:5432/space
SPACE_JOB_DATABASE_URL=postgresql://space_worker:change-me-worker@127.0.0.1:5432/space
SPACE_WEBHOOK_DATABASE_URL=postgresql://space_webhook:change-me-webhook@127.0.0.1:5432/space
SPACE_DATABASE_SSLMODE=disable
SPACE_ACCESS_MODE=private
SPACE_WEB_URL=http://127.0.0.1:3000
```

The migration URL is the superuser of the container. The three other URLs name the restricted logins and the passwords they should have; the installer creates the logins, or resets them to these passwords, so choose any values and percent-encode special characters. `SPACE_ACCESS_MODE=private` turns every access check off, which is convenient on your own machine. To develop key mode, set `SPACE_ACCESS_MODE=key` and `SPACE_ACCESS_KEY` to at least 32 characters, in this file and in the website's settings below.

Then create the logins and apply the migrations:

```sh
make install
```

It prints one `PASS` line. Run it again after pulling new migrations; it is safe to repeat. To use a model provider in development, add the `SPACE_MEMORY_*` settings described in [Self-hosting](SELF_HOSTING.md#configuration) to `backend/.env`, and set `SPACE_MEMORY_EMBEDDING_DIMENSIONS` before `make install` if the embedding model does not produce 1,536 dimensions.

## Run

Start each process in its own terminal:

```sh
make backend          # REST under http://127.0.0.1:8000/v1, MCP at http://127.0.0.1:8000/mcp/
make web              # the website at http://127.0.0.1:3000
make memory-worker    # optional: memory extraction jobs
make webhook-worker   # optional: webhook delivery
```

`make backend` runs Uvicorn on `127.0.0.1` with `BACKEND_PORT` (default 8000) and `BACKEND_WORKERS` (default 1). At startup it creates the owner's Private Space. In development the interactive API documentation is served at `http://127.0.0.1:8000/docs`; production hides it.

`make web` starts the Vite development server on `WEB_PORT` (default 3000). The website reads its settings from `apps/web/.env.local`, which git ignores:

```dotenv
SPACE_ACCESS_MODE=private
SPACE_API_URL=http://127.0.0.1:8000
SPACE_WEB_URL=http://127.0.0.1:3000
# Key mode only, the same value as in backend/.env:
# SPACE_ACCESS_KEY=...
# Optional, the name the website shows:
# SPACE_OWNER_NAME=Ada
```

The website and the backend must use the same access mode and access key. `SPACE_API_URL` defaults to `http://127.0.0.1:<BACKEND_PORT>`. Make exports `SPACE_WEB_URL=http://127.0.0.1:<WEB_PORT>` to both processes, so pass the same `WEB_PORT` to `make backend` and `make web` if you change it: the backend uses that origin for citation links and for the device-code page. Use the same host name everywhere, `127.0.0.1` or `localhost`, not a mix.

To try the production build of the website:

```sh
npm --prefix apps/web run build
npm --prefix apps/web start -- --port 3101
```

The production server loads `apps/web/.env.local` without replacing variables already set in its environment.

### Connecting a local agent

The MCP endpoint defaults to `http://127.0.0.1:<BACKEND_PORT>/mcp/` (`SPACE_MCP_RESOURCE_URL`). Agent connections are bound to that address, so connect local agents to it exactly, for example in private mode:

```sh
claude mcp add --transport http space-dev http://127.0.0.1:8000/mcp/
```

The API answers only to the host names in `SPACE_ALLOWED_HOSTS`, which defaults to `localhost`, `127.0.0.1` and `[::1]`. [Connecting agents](CONNECTING_AGENTS.md) covers tokens and agent identities.

## Checks

```sh
make check
make check-database
```

`make check` runs the whole gate: `ruff check` and `ruff format --check` on the backend, the backend tests, then the website's unit tests, production build and type check. Format with `uv run --directory backend --locked ruff format`, never with another Ruff version.

Database tests are skipped unless `SPACE_RUN_DATABASE_TESTS=1`. `make check-database` sets it and runs the whole backend suite, starting disposable `pgvector/pgvector:pg17` containers bound to the loopback address with temporary storage. They never touch the database in `backend/.env`. The tests do not pull images, so run `docker pull pgvector/pgvector:pg17` once first. `backend/tests/test_selfhosted.py` covers a whole installation end to end: the installer, the access key, website-issued agent tokens, readable storage, unlimited usage, private mode and the workers' idle checks.

To run a subset, call pytest directly, for example `SPACE_RUN_DATABASE_TESTS=1 uv run --directory backend --locked pytest tests/test_webhooks.py`.

### Website tests

```sh
npm --prefix apps/web test            # unit tests (tests/*.test.mjs)
npm --prefix apps/web run test:e2e    # browser tests (tests/**/*.e2e.ts)
```

The browser tests use the [e2e](https://e2e.tester.army/docs) runner. `test:e2e` starts the Vite development server on a free port and answers every `/api` request inside the browser with synthetic data, so it needs no backend; set `APP_URL` to test a server you started yourself. From `apps/web`, `npx e2e run tests/<name>.e2e.ts` runs one file. Steps written with `screen` and `expect` need no model key. Reports and failure screenshots go to `apps/web/.e2e/`.

`npm --prefix apps/web run test:e2e:stack` runs a second suite against the production website build, the real backend and a disposable PostgreSQL, with nothing faked in the browser. It needs Docker with `pgvector/pgvector:pg17`, the backend environment from `make setup`, and `npm --prefix apps/web run build` first. [E2E coverage](E2E_COVERAGE.md) lists which surface each suite checks.

### Other commands

| Command | What it does |
|---|---|
| `make api-reference` | Regenerates `docs/openapi.json` from the running application's schema. Run it after changing a public route or schema; a test fails while the committed file is stale. |
| `make check-memory-models` | Runs both extraction builders and recall against the configured provider on synthetic text, without writing to Space. It costs provider usage. |
| `make benchmark-memory` | Times lexical ranking on synthetic data in process, with no provider or database. |
| `make evaluate-retrieval` | Measures retrieval strategies on the synthetic corpus in `backend/evaluations/retrieval/`, in a disposable database. The first run embeds the corpus with the configured provider and caches the vectors in `backend/evaluations/retrieval/embeddings.json`. |

## Concurrency and timeouts

REST and MCP share two admission lanes in each API process. Recall (`get_context` and `POST /v1/spaces/{space_id}/context`) runs 12 requests at a time with 128 waiting. Synchronous memory writes (`remember`, `remember_claims`, `build_memory`, `change_memory`, memory links and creator corrections) share a second lane with 2 running and 32 waiting, so a slow extraction never occupies recall's slots. A request waits at most 5 seconds for a slot; a full queue or an expired wait returns 503 with `Retry-After: 1` over REST, or a busy error over MCP. Waiting requests hold no thread.

| Setting | Default |
|---|---|
| `SPACE_RECALL_CONCURRENCY`, `SPACE_RECALL_QUEUE_SIZE`, `SPACE_RECALL_QUEUE_TIMEOUT` | 12, 128, 5 seconds |
| `SPACE_MEMORY_WRITE_CONCURRENCY`, `SPACE_MEMORY_WRITE_QUEUE_SIZE`, `SPACE_MEMORY_WRITE_QUEUE_TIMEOUT` | 2, 32, 5 seconds |
| `SPACE_DATABASE_POOL_SIZE` | 4 connections per API process |

A queue size or timeout of zero rejects a request as soon as every slot of that lane is busy. The queues live in memory, one set per process: `BACKEND_WORKERS=4` starts four processes with four sets of lanes and four pools, and nothing coordinates them. Once synchronous work has started it cannot be cancelled, and it keeps its slot until it really finishes, even if the client disconnects. Retry a write with the same command ID and payload, because an earlier attempt may already have committed. These defaults are starting values, not a measured capacity.

At startup the API compares its connection budget, `(SPACE_DATABASE_POOL_SIZE × SPACE_DATABASE_API_PROCESSES + SPACE_DATABASE_WORKER_PROCESSES + SPACE_DATABASE_WEBHOOK_PROCESSES + SPACE_DATABASE_ADDITIONAL_CONNECTIONS) × SPACE_DATABASE_REPLICAS × SPACE_DATABASE_DEPLOYMENT_OVERLAP`, with the connection limit of the `space_app` login, and refuses to start if the budget does not fit. The installer sets that limit to 20 (`SPACE_DATABASE_CONNECTION_LIMIT`) and gives each worker login 2 connections. `SPACE_DATABASE_API_PROCESSES` defaults to `BACKEND_WORKERS`.

The database itself bounds every login: statements stop after 15 seconds and lock waits after 5 seconds, and a transaction left idle is ended after 30 seconds for `space_app` and the extraction worker, and after 45 seconds for the webhook worker. Model calls therefore always run outside database transactions.

The website forwards browser requests to the API through its `/api` proxy with a 45-second deadline, or 210 seconds for memory builds and writes, recall and restore commits (`apps/web/lib/request-timeouts.ts`). A write that times out is not resubmitted automatically: the website says the outcome is uncertain. The browser keeps reads in an in-memory TanStack Query cache (`apps/web/lib/read-cache.ts`) that treats a response as fresh for 30 seconds.
