# Moving from the hosted Space

The hosted Space and this self-hosted edition share the same archive format (`space.archive.v1`). You can export a Space from the hosted service and import it into your own installation, with its documents, tasks, projects, databases, memories and their full revision history.

## What moves and what does not

| Moves | Does not move |
|---|---|
| Documents, tasks and their revision history | Agent connections and tokens: reconnect each agent |
| Projects, folders, sections and categories | Personal API keys: create new ones |
| Databases with their records and revisions | Webhooks: create them again |
| Memories, handoffs and their recall vectors | Sign-ins from claude.ai, ChatGPT and other apps: add the connector again |
| The trash | Your plan or usage on the hosted service |
| Named agent identities, so authorship stays attributed | |

One export holds one Space. If you have several Spaces on the hosted service, export each one and import them one after the other; they all land in the single Space of your installation.

## Steps

1. **Install Space** on your server and open it once, so the empty Space exists: see [Self-hosting](SELF_HOSTING.md). If you want memories to keep working with meaning-based search right away, set up the model provider before importing; see [Memories](#memories) below.
2. **Export from the hosted Space.** Open **Settings → Data** and choose the complete Space export. Your browser downloads one JSON file. It contains everything readable, so keep it somewhere private and delete it when you are done.
3. **Import into your installation.** Open **Settings → Data** on your own Space, choose **Restore an archive**, and select the file. Space uploads and checks the whole file before changing anything.
4. **Review and apply.** In a new installation every item is new, so there is nothing to decide: apply the restore. If you import into a Space that already holds work, the review lets you keep the existing item, add the archived one as a separate copy, or make the archived one current for each item that exists in both.
5. **Reconnect your agents** from the **Agents** page, using your own server's address, and remove the hosted Space connector from your agents and apps. Each agent keeps its name and history: when you choose that identity while connecting it, or when it identifies itself with the same name and harness, it reuses the imported identity.
6. **Recreate** any personal API keys and webhooks you used.

When everything looks right on your own server, you can delete the Space on the hosted service.

## Memories

Every memory carries recall vectors: numbers that an embedding model computed from its text, which meaning-based search compares. The hosted Space computes them with OpenAI's `text-embedding-3-small`, 1,536 numbers per claim, and the export includes them.

- **Same embedding model (recommended).** Set the model provider in `.env` with `SPACE_MEMORY_EMBEDDING_MODEL=text-embedding-3-small`, served by OpenAI (`SPACE_MEMORY_PROVIDER_URL=https://api.openai.com/v1`, or `SPACE_MEMORY_EMBEDDING_URL` if your chat models come from another service), and keep the default `SPACE_MEMORY_EMBEDDING_DIMENSIONS=1536`. Imported memories then work exactly as before, with no extra cost.
- **No model provider.** The import works. Memories stay readable, editable and found by word search and `resume`, but meaning-based recall and tools such as `remember` need a provider.
- **Another embedding model with 1,536 numbers.** The import works. Imported memories are found by word search, but meaning-based search skips them until each one is written again, which re-embeds it. `memory_info` reports how many claims still use another model.
- **An embedding model of another size**, such as `nomic-embed-text` (768) with Ollama. The installation refuses the archive, because its memory index has a fixed size that differs from the export's. Import with the default size of 1,536 instead.

See [memory](MEMORY.md) for the provider settings.

## Limits

The import accepts files up to 100 MiB and 10,000 archived revisions, and an upload expires after 24 hours. [Search, export and merge restore](SPACE_ARCHIVES.md) describes the merge review in full.
