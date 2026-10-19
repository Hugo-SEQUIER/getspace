# Operations

[Self-hosting](SELF_HOSTING.md) covers day-to-day running: logs, upgrades, backups, restores, changing the access key and moving to a new domain. This page adds health checks, the maintenance commands, usage limits, what a restore brings back, and what Space does not clean up on its own. [Database](DATABASE.md) describes the logins and tables the commands work on.

## Running maintenance commands

The maintenance commands connect with the superuser URL, `SPACE_MIGRATION_DATABASE_URL`, because they work across Spaces, outside the row-level scope of the API's login. In the compose stack, run them in a one-off container of the `migrate` service, which has that URL and the backend image:

```sh
docker compose run --rm migrate python -m space.retention
```

From a source checkout, run `uv run --directory backend --locked python -m space.retention`; the command reads the URL from `backend/.env`. Commands that take a Space need its organization ID and Space ID:

```sh
docker compose exec db psql -U postgres -d space -c "SELECT organization_id, id FROM space.spaces"
```

## Health

| Check | Answer |
|---|---|
| `GET /health/live` | `{"status": "ok", "service": "space", "version": "..."}` while the API process runs. The compose health check uses it. |
| `GET /health/ready` | 200 with `{"status": "ready"}` when the API can query the database, otherwise 503 with `{"status": "unavailable"}`. |

Caddy forwards both, so `curl https://your-domain/health/ready` works from anywhere. For the background queues, ask the database directly:

```sh
docker compose exec db psql -U postgres -d space -c "SELECT space.operational_health()"
```

It returns queued and processing extraction jobs, failed jobs in the last 24 hours, the age in seconds of the oldest queued job, jobs whose lease expired, expired restore uploads, and the number of Spaces at a usage limit. The worker logs an attempt that times out or fails; `docker compose logs worker` shows them.

The API also serves `/ops/metrics` when `SPACE_OPS_TOKEN`, at least 32 characters, is set in its environment. It returns per-process response counts and latency buckets, the same database aggregates and connection-pool counters, never content, queries or identifiers, to a request carrying `Authorization: Bearer <token>`; without the token it answers 404. `compose.yaml` does not pass this setting and Caddy does not forward `/ops/`, so add the setting to the `api` service and query it from inside the compose network if you want it.

## Retention

Two tables grow with every write, and two more with activity. Nothing prunes them automatically; run the retention commands from cron or by hand.

| Command | What it removes |
|---|---|
| `python -m space.retention` | Command and event rows older than 180 days (`--days` changes the window), then Overview activity older than 90 days, then the recall vectors of deleted memory claims older than one day, which exports in progress may still read. It works in committed batches of 10,000 rows (`--batch-size`) until nothing is left, and prints the counts. |
| `python -m space.webhook_retention` | Delivered or failed webhook deliveries older than 30 days with their attempts, events no delivery still references, command receipts older than 30 days, and subscriptions removed more than 30 days ago. Each run removes at most one batch of 1,000 rows per table (`--batch-size`, up to 10,000); run it again while it reports removals. |

A command row is what makes a retry safe: within the window, repeating a command ID returns the original result. After its row is pruned, a retry of that command is applied again. Imports of Memora exports rely on the same rows to skip records they already imported.

A weekly schedule is enough for most installations, for example in the crontab of the user that runs Docker:

```cron
30 3 * * 0 cd /srv/getspace && docker compose run --rm -T migrate python -m space.retention
45 3 * * 0 cd /srv/getspace && docker compose run --rm -T migrate python -m space.webhook_retention
```

### What is not cleaned up

No command removes these yet; they stay until the Space is reset:

- the input of finished extraction jobs, in `space.memory_jobs`;
- the chunks of restore uploads that were never applied or cancelled, in `space.restore_chunks`, which are ignored after they expire at 24 hours;
- projects, folders, sections, categories and databases in the trash, with their revisions.

## Usage limits

Each Space still counts its retained bytes, its items (documents, tasks, memories and workspace items such as projects and databases) and its model requests in the current UTC month, and `GET /v1/spaces/{space_id}/usage` reports them. Migration `0035_selfhosted_limits` removed the limits: every limit is stored at its maximum value, which the API reports as `null`. To protect a small server or a provider budget, set a limit for one Space as the superuser:

```sql
UPDATE space.usage_limits
SET storage_limit_bytes = 1073741824, item_limit = 20000, model_request_limit = 5000
WHERE space_id = '<space-uuid>';
```

Storage counts every retained revision. Each outbound model request counts before it is sent, including requests that then fail. At a limit, writes that add content or call a model fail with 429 and nothing partial is saved; reading, export, deletion and forgetting a memory still work. Restore a limit to "none" with the column's maximum value: 9223372036854775807 for storage, 2147483647 for items and model requests.

## After restoring a backup

`scripts/restore.sh` brings the database back to the moment of the backup, as [Self-hosting](SELF_HOSTING.md#restoring) describes. Plan for the consequences:

- Agent connections and API keys return to their state at that moment. Tokens and keys created later no longer work, and any revoked later work again: revoke them again on the website.
- Content written after the backup is gone, and content deleted after it is back.
- Extraction jobs and webhook deliveries that were pending in the backup run again. A webhook receiver can therefore get a notification it already handled, and must deduplicate by event ID, as it must for any retry.

## Resetting a Space

`python -m space.purge ORG SPACE` removes the content of one Space in a single transaction. Run it with `--dry-run` first: it prints how many rows each table would lose and writes nothing.

```sh
docker compose run --rm migrate python -m space.purge ORG SPACE --dry-run
docker compose run --rm migrate python -m space.purge ORG SPACE
```

A reset deletes the Space's documents, tasks, memories, workspace items, search and claim indexes, jobs, webhooks, restore uploads, activity, usage counters, agent identities, agent connections and API keys. It keeps the Space itself, its key, its organization and the Tasks collection, so the website and newly connected agents find an empty Space. Every connected agent must reconnect.

To remove the installation's data entirely, delete the database volume instead: `docker compose down -v` removes every container and volume, and the next `docker compose up -d` starts with an empty Space.

A reset does not touch backups: deleted content remains in every backup taken before it.
