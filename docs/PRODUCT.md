# Product specification

## Purpose

Space gives a person a durable place for documents, tasks, structured information and agent memory. An agent can continue work using retained evidence; the person can see and correct what was remembered.

MCP is the primary interface: the owner connects an existing agent to their Space and reads and writes through it. The website is primarily a viewer for navigation, evidence, review and administration, while keeping explicit manual editing. A lightweight website assistant for retrieval and cited answers is a proposal; see [MCP](MCP.md#website-assistant).

The entry point is one person installing Space and using its Private Space. A person should get value before configuring anything else.

## Product model

| Concept | Meaning |
|---|---|
| Owner | The one person an installation belongs to. There are no user accounts. |
| Organization | The ownership and isolation boundary in storage and API contracts (`organization`). The owner's personal organization holds the Private Space. |
| Space | A bounded workspace containing sources, databases, tasks, memory and agent connections. |
| Private Space | The owner's Space, used by the owner and the agents they connect. |
| Shared Space | A Space in a team organization, shared with selected members. The backend supports it; the website hides it, because an installation has one person. See [Teams](TEAMS.md). |
| Document | A retained source with immutable revisions and readable content. |
| Task | A record in the Space's canonical Tasks collection. |
| Project | A workspace item that groups tasks and documents within a Space. |
| Database | Owner-created records with custom properties and table or board views. |
| Memory | A versioned claim with evidence, source revision, timestamps and eligibility for recall. |
| Agent | A connected client with an agent identity and a revocable connection that is read-only or read-write. |

This edition runs on one machine the owner controls; see [Self-hosting](SELF_HOSTING.md). Whoever operates that machine can read everything it stores; see [Data and privacy](PRIVACY.md).

## First useful experience

1. Install Space. At its first start the API creates the owner's Private Space and its Tasks collection.
2. Connect an existing MCP client to the installation's endpoint, with a token created on the website in key mode or without one in private mode.
3. Use the agent to save a document and create or update a task. Source content is saved before any derived processing starts.
4. Inspect those resources, task state, attribution and evidence on the website, and create or edit content there by hand.
5. Start another agent session and recover useful context with citations.
6. Read what memory holds on the website, correct an entry, or delete what should not be remembered. Memory needs no approval step.

No account, billing, team or cloud service is needed for this workflow. A model provider is optional: documents, tasks, databases, handoffs and word search work without one.

## Feature boundaries

| Area | Current capability | Later expansion |
|---|---|---|
| Documents | MCP read and write, website reader and basic manual editor, links, uploads, retained revisions | Richer blocks, comments, simultaneous editing, offline synchronization |
| Tasks | Title, status, priority, project, source links; list and board | Dependencies, recurring work, cycles, custom automation |
| Projects | Group related documents and tasks | Portfolios and advanced planning |
| Memory | Autonomous source-backed and agent-written claims, a claim browser, correction and permanent deletion, historical evidence | Deeper recall, richer consolidation |
| Agents | Direct MCP connection from an existing client, with the same commands as REST | Additional verified clients and connectors |
| Website assistant | Proposed: retrieval and cited answers within the current Space, with no write tools | Explicit assistant writing if needed |
| Databases | Blank databases or a Clients starter; custom properties, record editing, table and board views, SQL for agents | Relationships, richer saved views, formulas, templates |
| Hosting | One Docker Compose stack on one machine | Additional deployment profiles |

Custom databases are part of the personal product: the owner can organize their own data without installing a complete CRM. Projects and databases are stored with revisions, revision-checked commands and scoped MCP access, like other content. Tasks and projects keep their familiar views.

## Trust and visibility

- Completing a task changes the task record, with revision checks and attribution. It does not rewrite the document that embeds its reference.
- A document is an authoritative source; a generated memory is an interpretation of a source. Memory is live when stored and never silently overwrites structured fields.
- An agent must save useful artifacts explicitly. Connecting it does not import private historical chats.
- Recall is scoped to one selected Space. Nothing synthesizes across Spaces automatically.
- A source can be saved while indexing is pending or has failed. Documents and tasks remain usable during model outages.
- "Private" describes application access. The operator of the server and any configured model provider can still read the content they handle.

Copying or moving content between Spaces is not implemented. A later design must review the selected content, create destination-owned sources with new IDs, remap references and rebuild eligible memory, without creating references into the origin Space or carrying unrelated memory along.

## Templates

A new database can start blank or from the Clients starter on the website. Documents remain sources rather than database rows that duplicate a document body. The CRM, research and project templates of the earlier design are not implemented.

## Success measures

Space sends no telemetry, so usefulness is measured with explicit trials: first useful reuse of context, continuation across agent sessions, task and document consistency, citation support, correction effort, repeated use, latency and cost per useful workflow. Set numerical targets after a trial rather than inventing benchmarks.

## Related specifications

[Interface](INTERFACE.md) · [Architecture](ARCHITECTURE.md) · [MCP](MCP.md) · [Memory](MEMORY.md)
