# Search, export and merge restore

Projects and custom databases are stored by the backend with revision checks, like documents and tasks. Moving a project or database to the trash preserves its saved revisions and contents; restore it from the trash section on its index page.

Whole-Space search reads complete current content from the server through bounded pages, including document text and custom database record values. It reports progress and fails visibly if the Space changes during a search. It does not silently limit results to the browser's cache.

## Export

In Settings → Data, choose the complete Space export. It includes every retained resource, project and database revision, the trash, named agent identities and the current memory index. Agent connections and credentials are excluded. The download is a ZIP holding one `space.archive.v1` JSON file with readable private content; keep it in a location you control. The browser compresses each page as it arrives, so the export is limited only by ZIP's 4 GiB file size; a larger export reports an error instead of presenting a partial file as complete. Personal API keys can page through the same export with `POST /v1/spaces/{space_id}/export`.

## Merge restore

1. Select a Space archive in Settings → Data: the downloaded ZIP, or the JSON file inside it. The website uploads it in staging chunks and validates the complete archive before showing the merge review.
2. Review each existing item. **Keep existing** leaves it unchanged. **Restore separate copy** creates a separate item with remapped project and source references. **Use archive as latest** appends the archive's retained revisions after the existing history and makes its archived head current.
3. Resolve the source and type issues listed by the review. An imported memory cannot quietly reference different source evidence under the same ID. Existing memory edits and forget markers remain authoritative; incompatible memory providers or models require keeping or rebuilding the memory before merging. When the archive's memory index has another width than this installation's, for example an export from an installation with another embedding model, restoring embeds those memories again with this installation's model. That needs a memory model provider and makes one embedding request per batch of 32 claims; without a provider the restore stops before changing anything.
4. Apply the reviewed merge. If another session changed the Space after the review, review again. All visible database changes publish in one transaction. A timeout can be recovered through the staged restore's status and retry controls.

Uploads are limited to 200 MiB of archive JSON and 10,000 archived revisions, so a larger export can be downloaded but not restored through the website, and they expire after 24 hours. Before decoding, a restore also limits the JSON to 32 nested containers, 500,000 objects or arrays and 5,000,000 structural separators (commas and colons outside strings). Databases account for most of a large archive, because every retained database revision holds all of its records. Restoring needs memory: a 100 MiB archive peaks at about 700 MiB in the API process. A restore accepts archives from the hosted edition, and drops source references to resources that were permanently deleted before the export, since the archive cannot contain them. Archives must use UTF-8 and contain only supported envelope fields. Each API process handles one archive review or publication at a time; concurrent requests receive a retryable busy response. Existing agent identities are reused where possible. Restoring an archive never reconnects an agent or restores revoked permissions: reconnect agents on the Agents page when needed.

This merge works inside one Space and keeps its history. It differs from restoring a database backup with `scripts/restore.sh`, which replaces the whole installation; see [Self-hosting](SELF_HOSTING.md#restoring).
