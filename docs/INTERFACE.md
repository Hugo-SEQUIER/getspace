# Interface specification

How the website should behave. Some sections describe behaviour that is not built yet; they say so.

## Direction

Combine the space and flexibility of a document canvas with compact, responsive task views and keyboard navigation. Notion and Linear are interaction references, not requirements to reproduce their branding or full feature sets. Space's distinctive element is inspectable memory and evidence connected to the work.

The primary work interface is the owner's external MCP agent. Design the website primarily for viewing, finding, inspecting, and correcting that work, with manual create/edit actions available. A rich writing environment is not the first activation requirement. Provide a clear connection entry point showing the MCP endpoint, supported setup, selected Space, and requested capabilities. See [MCP](MCP.md).

## Application shell

```text
Space navigation       Document, task list, or record          Optional context
--------------------   ------------------------------------   ---------------------
Current Space          Breadcrumb and local actions            Linked documents
Private / Shared       Writing canvas or compact rows          Related tasks
                       Current item remains the focus           Memory suggestions
Documents                                                     Source evidence
Tasks                                                         Access scope
Memory
Agents

Projects / saved views when useful
Settings
```

The selected Space is an explicit access boundary. An installation has one owner, so the website shows the owner's Private Space and hides the Space picker and the shared Spaces the backend supports; see [Teams](TEAMS.md).

Hide unused product concepts: an empty Private Space does not show CRM, pipeline navigation, team invitations, or enterprise administration. Installation details live in settings unless they affect the owner's current choice.

## Databases

Custom databases are first-class navigation in the personal workspace. A user can choose **New database** from Documents or Databases, start blank or with a Clients schema, name it, add typed properties, and create/edit records. Table and board views use the same records; any select property can group the board. A record has a stable identity and editable notes. Property renaming preserves its identity and values; removing an option that is still used requires resolving those records first.

The first frontend supports text, number, select, date, email, phone, URL, and checkbox properties. The acquisition starter provides contact, email, stage, value, and next follow-up properties without imposing currency or a fixed CRM data model. Further relationships and saved views will extend this foundation. The website and MCP create and edit databases and their records through the same workspace-item commands.

Database creation also offers **Import a file**. Show the supported extensions and limits, let users choose an Excel worksheet and record-name column, suggest editable field types, preview rows, and validate all records before creating. Preserve text identifiers, zero and false values. A failed import creates no partial database.

## Home attention

The home page answers "what needs me?" before "what did I touch last?". Two blocks sit above the recent pages, each hidden when empty:

- **Needs you** lists every task or document whose status is `needs_you`, newest first, with the author, project and age. It clears only when the owner changes the status.
- **New since you looked** lists items whose latest revision was written by an agent or a personal API key and not opened since, with a count per project and a "Mark all as seen" action. Opening an item on the website records its current revision as seen, across devices. Generated memory never appears here.

The Home link in the sidebar carries the combined count. Seen marks are website state: the `resource_views` table stores one row per resource with the revision last opened, nothing else. Agents and API keys cannot read or write them.

## Documents

A document opens in a calm reading view with headings, paragraphs, lists, links, and references to tasks or other resources, with an explicit Edit action. The initial manual editor supports a bounded Markdown feature set. Selection of an editor library is still open; persistence must preserve the canonical Markdown and reference syntax irrespective of that selection.

The editor can render a link of the form `[Task title](space://resource/<task-uuid>)` as a live task block when the target is a permitted task in the same Space. The checkbox/status control updates the task through the record command. The serialized document contains a reference, not another authoritative task status. Native document saves resolve reference links transactionally; imported text does not gain trusted links merely by resembling this syntax.

Supported editing actions must round-trip through the persisted format. Unsupported import features are retained in the original source and shown with a clear limitation; they must not silently disappear on save. Full document CRDT collaboration, arbitrary embeds, and filesystem synchronization are deferred.

Save feedback distinguishes `saving`, `saved`, `conflict`, and `save failed` from derived processing states `queued`, `running`, `ready`, and `failed`. A parsing failure never turns a successful source save into an apparent data-loss event. Poll the ingestion-run status returned by the source write.

The current frontend includes a separately labelled content-import flow for PDF, DOCX, Markdown and text. It previews editable text before the user saves and explicitly states that images, layout and original PDF/DOCX files are not retained. Scanned PDFs need OCR. The complete original-source ingestion and retention contract above remains pending; this first content importer must not claim to implement it.

For PNG, JPEG, WebP and GIF files, Upload creates an image-only document with an editable title and image description. Add image in the document editor, or paste an image into Content, inserts an image at the selection. Images are processed on the device: files are limited to 10 MB and 40 million pixels, metadata is removed, animated images become still frames, and the image is resized to at most 2048 pixels on its longest side and compressed to fit. Each encoded image is limited to 300,000 characters, within the existing 700,000-character document body limit. The document body stores self-contained Markdown data URLs, so images follow document revisions, deletion and export without separate media storage. The writing field uses short, temporary image references that expand back to embedded data on preview and save. Remote images, SVG and executable destinations are not rendered. Images do not add OCR or image understanding; a description supplies readable context. Upload preserves the active folder and optional project filter.

Project detail offers New document and an empty Documents state with the same action. The existing editor preselects the current project without assigning a folder. Saving opens the new document and refreshes the project's related documents. View-only Spaces do not offer this creation action.

## Tasks

The task list uses compact rows with title, status, project, due date, and assignee. A simple board groups the same records by status. Opening a task exposes description, source document links, relationships, and revision/attribution detail where relevant.

Drag task cards between columns to change status, with visible drop targets, keyboard controls and a status picker on each card. Moving or opening a task keeps the Board view; adding within a column preselects its status. Scroll surfaces suppress rubber-band overscroll while preserving normal vertical and horizontal scrolling.

Default statuses are To do, In progress, and Done. A fourth built-in status, Needs you, is always accepted but never stored as a column: the board always shows it before Done, so it keeps one shape. A task saved with a status that is not yet a column gets one, labelled from its value (`in_review` becomes In review), and a task database row whose status names no column gets its own column on the board. An agent sets it when it cannot continue without the person's decision, approval or merge, and the item stays on the home page until the person changes it. Priority, assignee, and due date are optional. An unknown due date stays empty. User-facing dates use the user's timezone; the backend persists unambiguous timestamps.

The Tasks view resolves the collection with `system_role=tasks`, then uses the same record query/update commands as every Base. Template field keys map to tenant-local field UUIDs. There is no separate task store maintained by the editor.

Creating a task from a document is one composed domain command: authorize both writes; create the task; save a new document revision containing its reference; append both events/audit records; commit the database work atomically. If the document revision is stale, neither logical change commits. The composed endpoint is required before this UI action is exposed; it is not included in the current core API slice. Until then, create a task first and explicitly insert its reference with a normal document save.

Completing a task in either location updates the same resource revision and invalidates both read models. An optimistic update rolls back on conflict and offers a reread; it never silently replays a stale action against changed state. Historical document views label embedded tasks as historical and use the target revision retained by the link rather than presenting today's task status as past truth.

## Context and memory

The context panel is optional and preserves room for the current document or task. It can show bounded related records, source excerpts, memory suggestions, and the current access scope. Expensive memory loading does not block ordinary page rendering.

A memory entry shows its text, origin, precise source revision, relevant excerpt, memory type, edited state and time uncertainty. Correct and delete are explicit commands, and deletion is permanent. Show conflicts rather than blending incompatible values into a confident statement. Correcting text clears an obsolete structured claim unless a validated replacement is supplied.

The Memory view is a brain browser. It discloses the configured processor and the index size, builds memory from a selected current source revision, recalls a topic through the shared context command, and lists every memory with its claims, type, source revision and edited state. A claim can be corrected or permanently deleted; deletion removes it from the index and asks the user to confirm first. There is no review queue, no promotion step and no separate exclusion state, so do not add one without a product decision.

Source excerpts in context are part of the token budget. A citation opens the retained authorized revision. An unavailable or revoked source gets a safe unavailable state; its title and snippet are not leaked through a stale cache.

## Agents and sharing

The normal agent connection is a direct MCP connection with a token created on the website, without a custom agent application. ChatGPT, Claude, Codex, Claude Code, Prime-Agent, and Hermes are intended clients; show client-specific instructions only after verifying their actual compatibility. Clients that accept remote servers only through an OAuth sign-in, such as claude.ai, add the MCP address as a custom connector and are approved on the website's consent page at `/connect/authorize`.

An agent entry shows which Space it can access, allowed operations, delegation owner, and connection status. Read context and write resources are distinct permissions, and memory writes and deletions use the same write grant as resources. Sharing and administration stay separate, and the initial connection flow does not grant them by default.

A Private Space admits its owner and the agents they connect. Surface agent and model destinations when the owner makes a disclosure decision, rather than burying them in installation settings.

## Website retrieval assistant

Not built yet. Offer a lightweight question-and-answer entry point for owners who do not connect an external agent. Initially it retrieves authorized Space content, answers with citations, and lets the user open the evidence. It has no write tools. Manual website editing and explicitly granted external-agent writes remain available; those capabilities do not automatically transfer to the website assistant.

Show the selected Space, configured model destination, and unavailable/insufficient-evidence states. Keep answers transient unless a later explicit save feature is designed; asking a question must not silently create memory. Reuse the same retrieval, evidence, and authorization services as MCP. Assistant writing is a later optional capability.

## Navigation, responsiveness, and accessibility

- Provide command navigation and keyboard shortcuts with discoverable equivalents; keyboard support must not depend on a mouse-only hover control.
- Preserve focus after edits, validation errors, and list reordering. Native controls have accessible names and visible focus indicators.
- Use semantic headings, screen-reader status announcements, and status labels that do not depend on color alone.
- On narrower screens, turn the context panel into an explicit secondary view and compress navigation. Keep document editing and task actions usable without clipped controls.
- Honor system appearance and reduced-motion preferences. Use restrained accents for selection, progress, and edited state.
- Preserve scroll and selection when switching between a document and a linked task. Avoid opening a new unrelated page for every relationship.

## Screen-level release checks

The personal workflow must run without any team setup; source saves must distinguish ingestion failure; embedded and listed tasks must stay consistent; stale edits must be recoverable; source evidence must be inspectable; and permission changes must invalidate already-loaded content before its next authorized release. [E2E coverage](E2E_COVERAGE.md) lists the browser checks that exist.
