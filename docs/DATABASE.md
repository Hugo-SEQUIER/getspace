# Database

Space keeps everything in one PostgreSQL 17 database with the pgvector extension, in the `space` schema. This page covers the database logins and their rights, row-level security, the migrations, the tables and how to read stored data. [Operations](OPERATIONS.md) has the maintenance commands, and [Self-hosting](SELF_HOSTING.md) covers backups, restores and upgrades.

## Logins

| Login | Setting | Used by | Rights |
|---|---|---|---|
| `postgres` | `SPACE_MIGRATION_DATABASE_URL` | The installer (`python -m space.install`, the `migrate` service) and the maintenance commands | Superuser. Owns the `space` schema and every table. The API and the workers never receive this credential. |
| `space_app` | `SPACE_DATABASE_URL` | The API, and the content connection of each worker | No superuser, BYPASSRLS, role creation, database creation, replication or inherited rights, and it owns nothing. It reads and inserts rows of the application tables, updates only the columns the commands change (such as a resource's current-revision pointer or a connection's revocation time), deletes documents and tasks with their revisions and command rows, and rewrites derived rows such as the claim index and term fingerprints. Connection limit 20. |
| `space_worker` | `SPACE_JOB_DATABASE_URL` | The extraction worker's queue connection | Its only membership is the `space_job_worker` role, which reads `memory_jobs` and updates their scheduling and outcome columns. Connection limit 2. |
| `space_webhook` | `SPACE_WEBHOOK_DATABASE_URL` | The webhook worker's queue connection | Its only membership is the `space_webhook_worker` role, which reads the webhook tables and updates delivery status and attempt receipts. It cannot change a subscription's destination, secret or pause state. Connection limit 2. |

Every restricted login uses `search_path = pg_catalog, space`, a 15-second statement timeout and a 5-second lock timeout. PostgreSQL ends a transaction left idle for 30 seconds, or 45 seconds for `space_webhook`, whose delivery attempts last up to 30 seconds.

The installer runs on every start of the stack. It takes a transaction-level advisory lock, so two installers never run at once, and it marks the schema and the roles it creates with comments. It refuses to touch a `space` schema or `space_app` role that it did not create, or one that has gained privileges or role memberships. It sets each login's password to the one in its connection URL, hashing it as SCRAM-SHA-256 before sending it, and never prints credentials or driver errors. After migrating, it logs in as each login to check it.

At startup the API refuses to run if its login is not `space_app`, if that login is a superuser or can bypass row-level security, create roles or create databases, or if the configured connection budget exceeds the login's connection limit; [Development](DEVELOPMENT.md#concurrency-and-timeouts) explains the budget.

## Row-level security

Every table enables row-level security, and every table that holds Space data also forces it, so the policies apply even to the table owner unless it is a superuser. Each request transaction sets, with transaction-local `set_config` calls, the caller's issuer and subject, then the organization and Space it works in. The policies compare every row with those values; with no identity set, no row is visible. A Space ID that belongs to another organization grants nothing.

These settings are assertions made by the backend, not proof of identity: anyone with the `space_app` password can set them. Keep that password as secret as the superuser's. A few operator tables hold no Space data and enable row-level security without forcing it: `purge_cleanup`, `environment_control`, `erased_subjects` and `deletion_receipts`. The runtime may read `environment_control` and reaches the others, if at all, only through narrow `SECURITY DEFINER` functions.

The runtime runs each transaction in Psycopg pipeline mode, which batches the scope setup with the first statements. Transaction-local settings never leak between pooled connections.

## Migrations

`backend/migrations/versions/` holds the Alembic migrations, each a small Python file that applies a reviewed SQL file. The installer applies them up to the latest one. Migrations only move forward: every `downgrade()` refuses to run, so returning to an older version means restoring a backup taken with it.

| Migrations | What they add |
|---|---|
| `0001`, `0002` | Principals, organizations, Spaces, Space keys, the Tasks collection, resources and revisions, commands and events, connection grants |
| `0003` to `0006` | The pgvector claim index, declared importance, keyed term fingerprints, the readable memory marker on resources |
| `0007` | An operator cleanup table and the maintenance policies of the schema owner |
| `0008`, `0010` | Extraction jobs, the job worker's capability role, fair scheduling between Spaces |
| `0009` | An activity timestamp on connection grants |
| `0011`, `0012` | Agent identities; workspace items (projects and databases) with their revisions |
| `0013`, `0014` | Staging for archive restores; per-Space usage counters and limits |
| `0015` | Environment fencing and erasure records, unused by a self-hosted installation |
| `0016` | Organizations, members, invitations and shared Spaces |
| `0017`, `0018` | Memory categories and sections; Memora imports as jobs |
| `0019` | The Overview's activity log and line counts |
| `0020`, `0021` | Deleting agent identities, documents and tasks |
| `0022`, `0024` | Links between memories, explicit and inferred |
| `0023` | Row-level security on internal tables |
| `0025`, `0026` | Editable task columns; document folders |
| `0027` | Webhooks and the webhook worker's capability role |
| `0028`, `0029` | Cached catalog embeddings for classification; routing rules |
| `0032`, `0033`, `0034` | Device-code agent connections; personal API keys; which revision the owner last opened |
| `0035_selfhosted_limits` | Makes every Space unlimited by default |
| `0036_oauth_server` | Sign-in for apps: registered clients, authorization requests and tokens |
| `0037_change_revisions` | Records which kinds of content changed at each content revision, so the website reloads only the lists whose kind changed |
| `0038_export_snapshots` | Stamps each revision with the content revision it committed under, and keeps the recall vectors of deleted claims for a day, so an export reads the Space as it was at its first page |
| `0039_profile_images` | The owner's own profile picture, chosen on the website, readable only by its person and purged with the Space |

Migration `0003` installs the `vector` extension in the `space` schema, because the restricted logins resolve types through `search_path = pg_catalog, space`; it stops if `vector` is already installed in another schema. The claim index is created with 1,536 dimensions. When `SPACE_MEMORY_EMBEDDING_DIMENSIONS` asks for another width (16 to 2000), the installer rebuilds the column and its index while the table is empty, and refuses to change the width once embeddings are stored.

## Tables

| Group | Tables |
|---|---|
| Identity and access | `principals`, `organizations`, `spaces`, `space_keys`, `oauth_grants` (agent connection grants, a historical name), `device_authorizations` and `device_code_attempts` (device-code connections, stored as SHA-256 digests), `oauth_clients`, `oauth_requests` and `oauth_tokens` (sign-in for apps; secrets, codes and tokens as digests), `api_keys` (digests), `agent_identities`, `organization_members`, `space_members`, `team_invitations` |
| Content | `collections`, `resources`, `resource_revisions` (documents, tasks, handoffs and memories), `workspace_items` and `workspace_item_revisions` (projects, folders, sections, categories and databases), `resource_views` |
| Writes | `commands` and `events` (one row each per write, for idempotent retries), `workspace_item_commands` |
| Search and memory | `resource_terms` (keyed term fingerprints), `memory_claims` (the claim index), `memory_links`, `memory_node_links`, `memory_routes`, `workspace_item_vectors` |
| Background work | `memory_jobs`, `memory_job_schedule`, `webhook_subscriptions`, `webhook_events`, `webhook_deliveries`, `webhook_attempts`, `webhook_commands` |
| Restore | `restore_sessions`, `restore_chunks`, and `restore_objects`, which tracked object-storage cleanup and stays empty here |
| Usage and activity | `usage_limits`, `activity_events` |
| Unused in this edition | `environment_control`, `deletion_receipts`, `purge_cleanup`, `erased_subjects` |

Every Space-owned row carries its `organization_id` and `space_id`, and composite foreign keys bind each reference to its own organization and Space. A resource's current-revision pointer is a deferred constraint, so a revision and its pointer commit together.

## Reading stored data

Open a shell as the superuser, which bypasses row-level security:

```sh
docker compose exec db psql -U postgres -d space
```

Payloads are canonical UTF-8 JSON in `bytea` columns that keep their historical `encrypted_*` names: `encrypted_metadata` and `encrypted_record` on resource revisions, `encrypted_payload` on workspace item revisions, and others. Convert one to `jsonb` to read it:

```sql
SELECT r.id, r.kind, convert_from(v.encrypted_metadata, 'UTF8')::jsonb ->> 'title' AS title
FROM space.resources r
JOIN space.resource_revisions v
  ON (v.organization_id, v.space_id, v.resource_id, v.revision)
   = (r.organization_id, r.space_id, r.id, r.current_revision);
```

`SELECT organization_id, id FROM space.spaces;` lists the Spaces with the organization each belongs to, which the maintenance commands take as arguments. Change data through the website, the API or MCP rather than SQL, so revisions, term fingerprints, the claim index and the usage counters stay consistent.
