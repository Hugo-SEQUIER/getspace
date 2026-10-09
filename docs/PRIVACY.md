# Data and privacy

A self-hosted installation keeps its data on the machine that runs it, under the control of whoever operates that machine. This page lists what Space stores, who can read it, what leaves the server and when, and how data is deleted. The [security notes](SELF_HOSTING.md#security-notes) in the self-hosting guide cover protecting the server.

## What is stored, and where

Almost everything lives in the PostgreSQL database, in the `db-data` Docker volume:

- **Content.** Documents, tasks, projects, folders, sections, categories, databases and their records, handoffs and memories, each with every revision.
- **The memory index.** For each current memory claim: its embedding, SHA-256 hashes of the claim text and of its cue labels, its type, declared importance, embedding model, source reference and identifiers.
- **Search fingerprints.** Keyed fingerprints of every word in each current revision, used to find candidates for recall.
- **Agents and access.** Agent identities (a name and a harness), agent connections and whether they may write, SHA-256 digests of agent tokens and API keys, API key names and prefixes, and webhook subscriptions with their receiver URLs and signing secrets.
- **Records of activity.** A command and event row for every write (who wrote which revision of which item, and when, without the content), the Overview's activity log (timestamps, operation types, counts, outcomes, durations and identifiers, without queries or text), and webhook delivery receipts.
- **Work in progress.** The text submitted to `extract_memory` stays with its job, and an archive being restored is staged in chunks until it is applied or cancelled.

Outside the database:

- `.env` holds the access key, the database passwords and any model provider key.
- Caddy's volumes hold its HTTPS certificates.
- Container logs, described below.
- In the browser, the website keeps its session cookie and display preferences, and it may cache content you have read in the browser's storage. Clearing the site's data in the browser removes them.

## Who can read it

Space does not encrypt content in the application. Documents, tasks and memories are stored as readable JSON, and anyone who can read the database can read all of them: the operator of the server, anyone with a shell on it or access to its disk, and anyone who obtains a backup. Encrypt the disk and the backups if that matters to you.

The per-Space key in `space_keys` is stored in the same database. It makes search fingerprints and retry fingerprints stable within a Space; it protects nothing from someone who can read the database.

Inside the application, access depends on the mode. In key mode the website and the REST API require the access key, agents need their own tokens, and personal API keys reach one Space each; tokens and keys are stored only as digests. In private mode there is no access check at all, and anyone who can reach the address controls everything.

## What leaves the server

By default nothing does: with `SPACE_MEMORY_PROVIDER=disabled` no text is sent to any model, and Space sends no telemetry. Data leaves the server only for the services you configure.

**A model provider**, when `SPACE_MEMORY_PROVIDER` is `openai_compatible` or `ollama`. Space calls it only while handling a request from an agent or the website, or a job such a request queued:

- When memory is written or edited (`remember`, `remember_claims`, `change_memory`, a Memora import), the text of each claim is sent to be embedded.
- When an agent asks for extraction (`extract_memory`, `build_memory`), the source text, at most 12,000 characters, is sent to the text model, and the resulting claims are embedded.
- During recall with the `semantic`, `prompt` or `auto` strategy, the query is embedded. For evidence assessment, up to 50 candidate claims and passages of at most 2,000 characters each are sent to the assessing model, with the titles of their sources and the names of the projects, categories and sections they belong to. The `prompt` strategy also lets the text model expand the query over candidate claims. The `lexical` strategy and `mode="resume"` call no model.

Chat requests to an OpenAI-compatible endpoint ask the provider not to store them (`store: false`); whether it keeps anything is up to that provider. An Ollama server on the same machine or network keeps the text local. Space never sends the access key, agent tokens, API keys or database credentials to a provider.

**TypeSafe**, an optional external service that is off by default. It needs `SPACE_TYPESAFE_API_KEY` together with one of `SPACE_MEMORY_RELEVANCE_PROVIDER=typesafe`, `SPACE_MEMORY_CLASSIFICATION_PROVIDER=typesafe` or `SPACE_MEMORY_RELATIONSHIP_PROVIDER=typesafe`; `compose.yaml` passes none of them, so enabling it means adding them to the `api` and `worker` services. When enabled, it receives candidate passages during recall, each new or edited claim with the names and descriptions of your categories, projects and sections for classification, or a new claim with up to five similar stored claims to judge whether they are related. When classification is on and the catalog is too large to send whole, the names and descriptions of its categories, projects, sections, folders and databases are also embedded by the model provider.

**Webhook receivers** you configure. A notification carries identifiers, revision numbers and the IDs of changed fields; a subscription filtered on one select field also carries that field's old and new value. It never contains whole records, names or notes.

**Caddy** obtains its HTTPS certificate from a public certificate authority such as Let's Encrypt, which learns your domain name.

Agents connected to Space receive whatever they read through MCP. What an agent and its model provider keep is outside Space's control.

## Logs

The containers log request outcomes and fixed error categories, never document content, queries, tokens or credentials. The API writes no access log. Docker keeps at most 30 MB of logs per container (three files of 10 MB).

## Deleting data

- **Documents and tasks.** Deleting one, on the website or through the REST API, removes it with every revision and its command records. Agents can delete tasks but not documents. Editing a document keeps its earlier revisions, so text removed in an edit stays in the history until the document itself is deleted.
- **Memory.** `change_memory` with `action="forget"` removes one claim from the current memory and deletes its row in the memory index, embedding and hashes included, so recall stops returning it; earlier revisions of that memory still contain the claim's text. Deleting a whole memory (`delete_memory`, or on the website) removes it with every revision, its index rows and its command records.
- **Projects, folders, sections, categories and databases.** Removing one moves it to the trash, where it keeps its revisions and can be restored. Nothing deletes it permanently except resetting the Space.
- **Agent identities** are retired rather than erased: their names stay attached to the revisions they wrote.
- **Logs of activity.** `python -m space.retention` removes command and event rows older than 180 days and Overview activity older than 90 days, and `python -m space.webhook_retention` removes finished webhook deliveries older than 30 days. Neither runs on its own; see [Operations](OPERATIONS.md#retention).
- **Work in progress.** Extraction job inputs and abandoned restore uploads are not removed by any retention command. They stay until the Space is reset.
- **A whole Space.** `python -m space.purge` resets a Space's content; `docker compose down -v` deletes the whole installation's data. See [Operations](OPERATIONS.md#resetting-a-space).

## Backups

`scripts/backup.sh` writes a complete, unencrypted copy of the database: content, history, the memory index, token digests, webhook signing secrets and settings. Restoring a backup brings back everything it contained, including items deleted after it was taken. Deleted data therefore survives in older backups until you delete those files. Keep backups and `.env` as private as the server, and copy them somewhere off the machine.
