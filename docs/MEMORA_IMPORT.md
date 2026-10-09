# Importing Memora memories

Space imports Memora's full-export and memories-only JSON files through the website or a command-line tool. Both preserve claim wording through the structured memory API, keep each original record as provenance inside its memory, and regenerate embeddings with Space's configured provider.

## In the app

Open **Memory → Import memories**, choose a JSON export, and select a destination Space for every namespace. Use the full export when available to preserve its additional history. **Use current Space for all** explicitly combines the namespaces; otherwise choose separate Spaces to preserve access boundaries. Review the destination checks, then click **Import** to start. Choosing a file and reviewing it never saves memories or calls the memory provider.

The browser removes stored vector bytes before uploading the remaining records for validation. Files are limited to 25 MiB, with at most 700 KB of memory metadata after removing vectors; split larger exports. Each selected destination receives a background job. Progress is saved after every completed memory, survives closing the tab, and appears under **Recent imports in this Space**. The extraction worker processes four records per turn through the normal queue, so a large import takes many turns. Switch to each destination Space to see its progress.

If a job stops, **Resume** continues from its last checkpoint under its original identity. Completed memories remain available. Command receipts also protect a retry when a write succeeded before its response or progress checkpoint was saved. The conflict and retention limits below apply to both the website and the command-line tool. Import jobs run in the `worker` service and are started by the owner from the website; agent tokens and API keys cannot start them.

## CLI

The default command is an offline preview: it reads the export, validates every record, and reports namespace counts, destinations and errors. It does not connect to Space, read credentials, call models or write files. Instructions inside exported memories are treated as data.

## Preview

From the repository root:

```sh
uv run --directory backend --locked python -m space.import_memora \
  /absolute/path/to/memora-export-20260917 \
  --source-id my-memora-store
```

Use an absolute export path because `uv --directory backend` changes the working directory. A directory containing exactly one `memora-full-export-*.json` selects that master file. If there is no master, the importer selects exactly one `memora-memories-*.json`. When multiple exports match, choose the file explicitly. The master preserves additional metadata such as history; CSV, Markdown and vectors-only JSON are not supported.

`--source-id` is a stable name you choose for the original Memora store. Reuse it for retries and later exports from that store. Do not use the current export date or a random name. Give independent source stores different names.

## Map namespaces

Map every namespace explicitly to an existing Space UUID. Repeat `--map` for additional namespaces:

```sh
uv run --directory backend --locked python -m space.import_memora \
  /absolute/path/to/memora-full-export-20260917.json \
  --source-id my-memora-store \
  --map 'private-work=11111111-1111-4111-8111-111111111111' \
  --map 'private-personal=22222222-2222-4222-8222-222222222222'
```

The UUIDs above are placeholders. Use the exact namespace names from the preview: some full exports include a storage prefix such as `hermes_memory_`, while flattened exports omit it. The importer preserves those names literally. The CLI does not create Spaces. To intentionally combine every namespace into one existing Space, use `--space SPACE_UUID` instead of `--map`. Original namespaces remain topic labels on the memories. Topic labels do not isolate access: every authorized reader of a destination Space can read its imported memories. Use separate Spaces when namespace access should remain separate.

A preview without mappings succeeds but reports `ready: false` and the unmapped namespaces. Invalid records produce row-numbered errors and exit status 1. No records can be applied until all validation errors and missing mappings are resolved; records are never silently dropped or truncated. Preview does not check existing destination contents or guarantee remote access and provider availability.

## Apply

Set `SPACE_IMPORT_TOKEN` in the current process environment to the installation's access key, or to a read-write personal API key when every namespace maps to that key's Space. In private mode the API checks nothing, and any non-empty value works. An agent token, a model provider key or a database password does not work. The tool loads no `.env` file and never prints the token.

Add these flags to the same mapped preview command when you want to write, with your Space's public address; Caddy forwards `/v1/` to the API:

```sh
  --api-url https://space.example.com \
  --apply
```

Only `--apply` enables network access and writes. `--dry-run` explicitly selects the default offline behavior and cannot be combined with `--apply`. Public addresses require HTTPS. Plain HTTP is accepted only for loopback and private-network hosts, as elsewhere in the edition, so a private-mode installation works through its SSH tunnel (`--api-url http://localhost:8080`) or on its VPN address. The client does not follow redirects or use environment proxy settings.

Before writing, the importer checks that a memory provider is enabled in each mapped Space. The server verifies authorization and any usage limits on every request. Each record is processed with one `remember_claims` command containing the original index, text, memory type and import metadata. It creates one memory and no source document. Claims become available immediately after the memory command succeeds.

The memory's provenance preserves the original record, including its namespace, ID, timestamps, metadata and history when present. Stored vector bytes are omitted. Space assigns new resource and claim IDs and import-time timestamps; original timestamps remain provenance, not backdated Space revisions. Export-level machine paths and the export timestamp are not stored. Existing links, cue fields and history are retained as provenance rather than reconstructed as a Memora graph.

The existing structured write path preserves claim wording. It sends claim text for fresh embeddings, and may also classify it using the destination's configured classification provider. The supplied vectors are not uploaded or reused. Check the destination's memory-provider configuration and the provider's costs before applying a large import.

## Retries and conflicts

Command IDs are deterministic for the source ID, destination Space, original namespace and original record ID. They do not depend on file location, record order or content. Repeating the same operation as the owner, whether with the access key or an API key, reuses the server's original result. A lost response after a successful write can therefore be retried without an additional resource or embedding call while the command remains in the server's history.

On failure or interruption, rerun with the same source ID, mapping and unmodified export. Completed records remain saved. There is no whole-import transaction or rollback. The command-line tool has no local checkpoint file: it stops on the first remote failure and reports how many records completed; it does not automatically retry provider failures. The website instead saves progress in its background job and offers Resume for stopped imports.

Retries also recognize completed imports from the older importer that created source documents. They reuse the existing memory without creating another document or calling the model again. If only the old source command committed, retry creates the missing memory with embedded provenance. Existing source documents and their references are preserved.

If an already imported record changes, the existing command ID conflicts with its changed payload and the importer stops. This is an append importer, not a synchronization or overwrite tool. Do not change the source ID to work around a conflict: that creates another import identity. Switching between full and flattened exports can conflict because the provenance differs, or create another import identity if their namespace spellings differ. Keep the same export representation for retries.

Retry deduplication depends on Space retaining the original command records. `python -m space.retention` removes them after 180 days by default, and the operator can choose another window; see [Operations](OPERATIONS.md#retention). After command history is pruned, rerunning can create duplicates. Check the destination before repeating an old import; permanent deduplication across retention, resets or restores is not provided. Mapping a namespace to another Space explicitly creates another destination copy.

## Limits and validation

The importer accepts UTF-8 JSON exports up to 25 MiB with 1–10,000 records and a matching integer `count`. It rejects duplicate JSON keys, excessive nesting, non-finite numbers, invalid Unicode, duplicate IDs within a namespace, malformed timestamps and unsupported claim types. Each record must have a namespace that fits Space's topic rules, a nonempty ID, an original creation timestamp, an index of at most 300 characters and claim text of at most 2,000 characters. Provenance is limited to 64 KiB per record. Records with the same ID in different namespaces remain distinct.

## Verification

The importer tests use synthetic exports and mock HTTP responses, including a response lost after commit, partial failures, conflicting payloads and offline previews. Optional integration tests run the actual REST routes and write path against a disposable local PostgreSQL with a fake model provider; no personal exports or external services are used:

```sh
uv run --directory backend --locked pytest tests/test_memora_import.py
SPACE_RUN_DATABASE_TESTS=1 uv run --directory backend --locked pytest tests/test_memora_import_workspace.py
```
