"""Official MCP SDK adapter for protected, shared Private Space commands."""

import asyncio
from time import perf_counter
from typing import Any, Literal
from uuid import UUID

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

from space import (
    __version__,
    agents,
    database_sql,
    guide,
    resource_deletion,
    space_map,
    tasks,
    webhook_inbox,
    workspace_items,
    workspace_search,
)
from space.admission import AdmissionError, dispatch
from space.memory import (
    MAX_MEMORY_LIST,
    ChangeMemory,
    RecallRequest,
    RememberClaims,
    RememberMemory,
    SaveHandoff,
    SetMemoryLinks,
)
from space.memory_pipeline import BuildMemory
from space.model_provider import MemoryModels
from space.oauth_server import issuer_url, resource_url
from space.private_access import PrivateConnections
from space.read_timing import collect_read_timings
from space.security.auth import Identity, TokenVerifier
from space.settings import Settings
from space.workspace import CommandError, SaveResource, Workspace


def create_mcp(
    settings: Settings, workspace: Workspace | None = None, verifier: TokenVerifier | None = None
) -> FastMCP:
    hosts = [entry for host in settings.allowed_hosts for entry in (host, f"{host}:*")]
    models = getattr(workspace, "memory_models", None) or MemoryModels(settings)
    # Private mode checks nothing: agents connect without a token, and each agent
    # identity gets its own connection (see space.private_access).
    private = bool(verifier and verifier.private)
    protected = verifier is not None and not private
    server = FastMCP(
        "Space",
        instructions=guide.operating_instructions(
            models,
            resource_access=workspace is not None,
            mine_documents=settings.mine_documents,
        ),
        streamable_http_path="/",
        stateless_http=True,
        json_response=True,
        max_request_body_size=1500000,
        token_verifier=verifier if protected else None,
        # A request without a token is answered 401 with the address of this server's
        # OAuth metadata, so clients that sign in through a browser can start there (see
        # space.oauth_server). Every token's grant is bound to this MCP URL in the
        # database, which is checked instead of a token audience.
        auth=AuthSettings(
            issuer_url=issuer_url(settings),
            resource_server_url=resource_url(settings),
            validate_token_resource=False,
            required_scopes=[],
        )
        if protected
        else None,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=hosts,
            allowed_origins=[],
        ),
    )

    @server.prompt(
        name=guide.PROMPT_NAME,
        title="How to use Space memory",
        description="Operating rules for autonomous Space memory: what to save, what to "
        "refuse to save, how to recall, and the configured limits.",
    )
    def memory_guide() -> str:
        """Return the full memory guide for this Space configuration."""
        return guide.full_guide(
            models, resource_access=workspace is not None, mine_documents=settings.mine_documents
        )

    @server.resource(
        guide.RESOURCE_URI,
        name=guide.PROMPT_NAME,
        title="How to use Space memory",
        description="Operating rules for autonomous Space memory.",
        mime_type="text/markdown",
    )
    def memory_guide_resource() -> str:
        """Return the full memory guide as a readable resource."""
        return guide.full_guide(
            models, resource_access=workspace is not None, mine_documents=settings.mine_documents
        )

    @server.tool(
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        )
    )
    def server_info() -> dict[str, str | bool]:
        """Return non-sensitive build information; does not access a workspace."""
        return {
            "service": "space",
            "version": __version__,
            "resource_access": workspace is not None,
        }

    if workspace:
        connections = PrivateConnections(workspace) if private else None

        def identity(agent_id=None):
            token = get_access_token()
            request = server.get_context().request_context.request
            header = request.headers.get("x-space-agent-id") if request else None
            try:
                supplied = str(UUID(header)) if header else None
                argument = str(agent_id) if agent_id else None
                if supplied and argument and supplied != argument:
                    raise ValueError("Conflicting identity")
            except ValueError:
                raise ToolError(
                    "Supply one valid, matching Agent ID in the header or tool argument"
                ) from None
            if token is None:
                if connections is None:
                    raise ToolError("Authentication required")
                return connections.identity(supplied or argument)
            return Identity(
                token.claims["iss"],
                token.subject,
                token.expires_at,
                token.client_id,
                token.claims["space_grant_id"],
                supplied or argument,
            )

        async def command(function, *args, agent_id=None):
            try:
                # Private connections are resolved in the database, off the event loop.
                actor = await asyncio.to_thread(identity, agent_id)
                result = await dispatch(function, actor, *args)
            except (CommandError, AdmissionError) as error:
                raise ToolError(error.message) from None
            except ToolError:
                raise
            except Exception as error:
                # Provider exceptions must not disclose payloads, keys or credentials.
                from space.operational_errors import operation_unavailable

                raise ToolError(operation_unavailable(error)) from None
            return result

        # Workspace storage is a closed domain even when backed by remote stores.
        # Calls that can send content to a model provider are marked open-world.
        read = ToolAnnotations(
            readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
        )

        @server.tool(
            annotations=ToolAnnotations(
                readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
            )
        )
        async def identify_agent(
            agent_id: UUID | None = None, name: str | None = None, harness: str | None = None
        ) -> dict[str, Any]:
            """Identify this connection, then send its stable Agent ID on every content request.

            With no arguments, discover the identity already selected on the website.
            Reuse an existing agent_id for another connection. Or supply a name and
            harness (codex, claude-code, hermes, claude, chatgpt, cursor, gemini,
            pi, openclaw, prime-agent, opencode, cline, devin, factory, grok,
            perplexity, deepseek, zai, meta, minimax, kimi, other).
            Matching names and harnesses reuse one identity. Names are attribution,
            never credentials. Set X-Space-Agent-ID in client headers for automatic
            sending, or include agent_id in each tool call. Only the owner can rename
            identities or reassign an already identified connection.
            """
            if (name is None) != (harness is None) or (agent_id and name is not None):
                raise ToolError("Supply agent_id, both name and harness, or no arguments")
            try:
                profile = (
                    agents.AgentProfile(name=name, harness=harness) if name is not None else None
                )
            except ValueError:
                raise ToolError("Use a visible name and a supported harness") from None
            if connections is not None and get_access_token() is None:
                return await command(
                    lambda actor: connections.identify(actor, agent_id=agent_id, profile=profile)
                )
            return await command(
                lambda actor: agents.identify(workspace, actor, agent_id=agent_id, profile=profile)
            )

        @server.tool(annotations=read)
        async def list_agent_identities() -> dict[str, Any]:
            """List this Space's saved agent identities to reuse one when connecting."""
            return await command(lambda actor: agents.list_profiles(workspace, actor))

        @server.tool(annotations=read)
        async def workspace_info(agent_id: UUID | None = None) -> dict[str, Any]:
            """Get the Private Space, Tasks collection, and assigned agent_id.

            Send the returned agent_id with every content call, as a tool argument
            or the X-Space-Agent-ID header. If null, call identify_agent first.
            """
            return await command(workspace.info, agent_id=agent_id)

        @server.tool(annotations=read)
        async def get_space_map(
            space_id: UUID,
            request: space_map.MapRequest | None = None,
            agent_id: UUID | None = None,
        ) -> dict[str, Any]:
            """Parameters: Space and optional max_chars (4,000-20,000, default 12,000).
            What it does: returns where things belong. Sections hold projects, folders
            and databases; projects hold their folders and databases. Databases show
            fields and record_count, never records. A task database also shows tasks:
            its status and planned field names and the statuses that count as done;
            query its open rows by planned day for the user's daily work. Routing
            rules learned from memory sit on the container they govern, plus
            Space-wide global_rules. Call it at
            the start of substantive work and before creating a project, folder,
            database or document. Rules guide placement and never override the user.
            Output: the bounded map; truncated=true means descriptions or rules were
            shortened, never that a container was hidden. No model calls.
            """
            return await command(
                lambda actor: space_map.build(
                    workspace, actor, space_id, request or space_map.MapRequest()
                ),
                agent_id=agent_id,
            )

        @server.tool(annotations=read)
        async def search_workspace(
            space_id: UUID, request: workspace_search.Search, agent_id: UUID | None = None
        ) -> dict[str, Any]:
            """Search full current documents, tasks, memory, projects and database records.

            Continue using next_cursor and snapshot until next_cursor is null, even
            when a page has no matches. Each call scans at most 25 items. Restart on
            snapshot conflict. An optional project filters its documents and tasks.
            """
            return await command(
                lambda actor: workspace_search.search(workspace, actor, space_id, request),
                agent_id=agent_id,
            )

        @server.tool(annotations=read)
        async def list_workspace_items(
            space_id: UUID,
            kind: Literal["project", "database", "category", "section", "folder"],
            after: UUID | None = None,
            deleted: bool = False,
            agent_id: UUID | None = None,
        ) -> dict[str, Any]:
            """Parameters: Space, item kind, optional cursor and deleted state.
            What it does: lists projects, folders, databases, categories or sections.
            Databases are listed with their schema and record_count, never their
            records: read and change rows with query_database.
            Output: one page of items; follow next_cursor for the complete list.
            """
            return await command(
                lambda actor: workspace_items.listing(
                    workspace, actor, space_id, kind, after, deleted, records=False
                ),
                agent_id=agent_id,
            )

        @server.tool(annotations=read)
        async def get_workspace_item(
            space_id: UUID, item_id: UUID, revision: int | None = None, agent_id: UUID | None = None
        ) -> dict[str, Any]:
            """Parameters: Space, item ID and optional historical revision.
            What it does: reads a project, folder, database, category or section.
            Output: the item and its revision.
            """
            return await command(
                lambda actor: workspace_items.get(workspace, actor, space_id, item_id, revision),
                agent_id=agent_id,
            )

        @server.tool(
            annotations=ToolAnnotations(
                readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
            )
        )
        async def save_workspace_item(
            space_id: UUID, change: workspace_items.SaveItem, agent_id: UUID | None = None
        ) -> dict[str, Any]:
            """Parameters: Space, item ID, current revision, command ID, kind and data.
            What it does: saves a project, folder, database, category or section.
            Projects use name, description, status and optional section_id. Folders
            use name, description and optional section_id and project_id; a folder's
            project places it in the Space map and never moves its documents.
            New section or project assignments must name an active item in this Space.
            Categories and sections use name and description; use user-defined labels.
            Databases use name, description, fields, records, view, groupBy and optional
            project_id, section_id and tasks. tasks marks the rows as tasks: status is a
            select field ID, done lists the options that close a row, and planned is a
            date field ID for the day a row is planned for. Change records with
            query_database.
            Read the current item first and preserve field/record IDs. Limits are
            30 fields, 1,000 records and 900 KiB per snapshot. Create at revision zero.
            Output: the saved item and its new revision.
            """
            return await command(
                lambda actor: workspace_items.save(workspace, actor, space_id, change),
                agent_id=agent_id,
            )

        @server.tool(
            annotations=ToolAnnotations(
                readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False
            )
        )
        async def query_database(
            space_id: UUID,
            database_id: UUID,
            request: database_sql.Query,
            agent_id: UUID | None = None,
        ) -> dict[str, Any]:
            """Parameters: Space, database item ID and a SQL request.
            What it does: runs SQLite SQL on one table named records, with columns id, name,
            notes, created_at, updated_at and one column per field, named after the field
            (quote names with spaces). Send sql with params bound to ? placeholders, or a
            statements batch of up to 20 that succeed or fail together. SELECT reads the
            current revision. INSERT, UPDATE and DELETE need command_id and the current
            expected_revision; the whole change is validated and saved as one revision.
            Omit id on INSERT to have one assigned; id, created_at and updated_at cannot be
            updated. Checkbox columns hold 0 or 1. include_schema returns the column map.
            Output: results per statement, the revision and change counts. A retried write
            returns the committed revision with replayed=true and no rows.
            """
            return await command(
                lambda actor: database_sql.execute(
                    workspace, actor, space_id, database_id, request
                ),
                agent_id=agent_id,
            )

        @server.tool(
            annotations=ToolAnnotations(
                readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False
            )
        )
        async def set_workspace_item_deleted(
            space_id: UUID,
            item_id: UUID,
            change: workspace_items.ChangeVisibility,
            agent_id: UUID | None = None,
        ) -> dict[str, Any]:
            """Parameters: Space, item ID, current revision, command ID and deleted state.
            What it does: archives or restores the item without changing its documents.
            Output: the saved item; its history and all documents remain available.
            """
            return await command(
                lambda actor: workspace_items.visibility(
                    workspace, actor, space_id, item_id, change
                ),
                agent_id=agent_id,
            )

        @server.tool(annotations=read)
        async def list_resources(
            space_id: UUID,
            kind: Literal["document", "task"],
            after: UUID | None = None,
            agent_id: UUID | None = None,
        ) -> dict[str, Any]:
            """List up to 50 documents or tasks; use next_cursor to continue.

            Memory is never listed here: a generated memory is read with list_memories and a
            handoff through get_context with mode='resume'.
            """
            return await command(workspace.list_resources, space_id, kind, after, agent_id=agent_id)

        @server.tool(annotations=read)
        async def get_resource(
            space_id: UUID,
            resource_id: UUID,
            revision: int | None = None,
            agent_id: UUID | None = None,
        ) -> dict[str, Any]:
            """Read a document or task, optionally at an immutable historical revision."""
            return await command(
                workspace.get_resource, space_id, resource_id, revision, agent_id=agent_id
            )

        @server.tool(
            annotations=ToolAnnotations(
                readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True
            )
        )
        async def remember_claims(
            space_id: UUID, change: RememberClaims, agent_id: UUID | None = None
        ) -> dict[str, Any]:
            """Save already-separated claims verbatim: classify, embed, save.

            Use when the material is already split into claims, each with its own index,
            type and importance, or when import provenance applies. For one authored fact,
            preference, decision or user-written memory, including a single sentence, use
            remember with title and text instead. Each claim carries an index label and its
            exact wording as value. Classification runs when configured; no extraction or
            generated cues. A write fails while no memory provider is configured. A claim
            that says where something belongs may carry routes_to: up to 8 exact project,
            section, folder or database IDs from get_space_map; it then shows on those
            containers in the map. Reuse command_id unchanged on retry, and use
            extract_memory only to mine a document or conversation into claims.
            """
            return await command(workspace.remember_claims, space_id, change, agent_id=agent_id)

        @server.tool(
            annotations=ToolAnnotations(
                readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True
            )
        )
        async def extract_memory(
            space_id: UUID, change: RememberMemory | BuildMemory, agent_id: UUID | None = None
        ) -> dict[str, Any]:
            """Save an extraction job and return its ID and status immediately.

            Supply raw text/title or an existing current source revision. The worker
            uses the configured provider, and the job is refused while none is configured.
            Saved input becomes generated memory only
            when status is ready. Poll memory_job. Closing the session keeps the job;
            revoking the submitting agent grant stops unfinished work. Reuse command_id.
            """
            return await command(workspace.extract_memory, space_id, change, agent_id=agent_id)

        @server.tool(annotations=read)
        async def memory_job(
            space_id: UUID, job_id: UUID, agent_id: UUID | None = None
        ) -> dict[str, Any]:
            """Read queued, processing, ready or failed extraction status and result reference."""
            return await command(workspace.memory_job, space_id, job_id, agent_id=agent_id)

        @server.tool(
            annotations=ToolAnnotations(
                readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
            )
        )
        async def save_resource(
            space_id: UUID, change: SaveResource, agent_id: UUID | None = None
        ) -> dict[str, Any]:
            """Parameters: Space, command ID and the complete intended document or task.
            What it does: creates at revision zero or updates at the current revision.
            Send kind, title, body, status and intended project, folder, priority and
            recall metadata. Omitted body/status reset to defaults; omitted project,
            folder, priority and recall preserve existing values on updates. Explicit
            project=null or folder=null clears that association. Folders apply only to
            documents and are independent of projects. New folder assignments must name
            an active folder in this Space; unchanged archived references may remain.
            A project must name an existing project in this Space. Kind cannot change.
            Tasks cannot carry recall.category="handoff". Generated memory is never
            edited here: use remember, remember_claims, extract_memory or change_memory.
            Prefer a status from task_columns; a new status value is saved as a column
            labelled from it (add_task_column picks the label), and set_task_status moves
            a task without resending its body. Set status
            "needs_you" on a task when the work cannot continue without the person's
            decision, approval or merge: it stays on their home page until they change it.
            Only tasks take it; when a document needs review, save the document, then a
            needs_you task that names it, and never write a separate "blocked" document.
            Stay within 1 MiB.
            Output: the saved resource. Reuse command_id unchanged on retry.
            """
            return await command(workspace.save, space_id, change, agent_id=agent_id)

        @server.tool(annotations=read)
        async def task_columns(space_id: UUID, agent_id: UUID | None = None) -> dict[str, Any]:
            """Parameters: Space ID and optional agent identity.
            What it does: lists configured task status values and display labels.
            Output: column revision and ordered columns, including custom statuses.
            """
            return await command(
                lambda actor: tasks.columns(workspace, actor, space_id), agent_id=agent_id
            )

        @server.tool(
            annotations=ToolAnnotations(
                readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
            )
        )
        async def add_task_column(
            space_id: UUID, change: tasks.AddColumn, agent_id: UUID | None = None
        ) -> dict[str, Any]:
            """Parameters: Space ID, current column revision, stable value, label and command ID.
            What it does: appends a task status column without removing existing statuses.
            Output: updated columns and revision. Reuse command_id unchanged on retry.
            """
            return await command(
                lambda actor: tasks.add_column(workspace, actor, space_id, change),
                agent_id=agent_id,
            )

        @server.tool(
            annotations=ToolAnnotations(
                readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
            )
        )
        async def set_task_status(
            space_id: UUID,
            resource_id: UUID,
            change: tasks.SetStatus,
            agent_id: UUID | None = None,
        ) -> dict[str, Any]:
            """Parameters: task ID, current revision, status and command ID.
            What it does: atomically changes only task status, preserving all other content.
            Prefer a status from task_columns; a new value is saved as a column.
            Use "needs_you" when the task now waits on the person's decision, approval
            or merge; the person moves it on from their home page.
            Output: the saved task. Reuse command_id unchanged on retry; stale revisions fail.
            """
            return await command(
                lambda actor: tasks.set_status(workspace, actor, space_id, resource_id, change),
                agent_id=agent_id,
            )

        @server.tool(
            annotations=ToolAnnotations(
                readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False
            )
        )
        async def delete_task(
            space_id: UUID,
            resource_id: UUID,
            expected_revision: int,
            agent_id: UUID | None = None,
        ) -> dict[str, Any]:
            """Parameters: task ID and the revision just read.
            What it does: permanently deletes one task with every revision. Requires a
            write grant. Only tasks: documents are deleted on the website, and
            generated memory uses delete_memory. Separately saved memories remain.
            Delete only a task the user asked to remove; to close finished work, use
            set_task_status instead. Deleting an already absent task is a success.
            Output: {"deleted": true}. A stale revision fails; read the task and retry.
            """
            return await command(
                lambda actor: resource_deletion.delete_resource(
                    workspace, actor, space_id, resource_id, expected_revision
                ),
                agent_id=agent_id,
            )

        @server.tool(
            annotations=ToolAnnotations(
                readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
            )
        )
        async def pull_events(
            space_id: UUID,
            request: webhook_inbox.PullEvents | None = None,
            agent_id: UUID | None = None,
        ) -> dict[str, Any]:
            """Parameters: optional subscription_id or database_id filter and limit (1-50).
            What it does: leases the oldest waiting database change events from this
            Space's agent inbox for ten minutes, so another agent pulling at the same
            time does not receive them. The Space owner creates an inbox subscription
            under Settings → Webhooks, choosing a database and optionally one select
            transition such as Stage → Review. Each item carries a stable delivery_id
            and the event: database_id, record_id, operation, revisions, changed field
            IDs and, for a filtered subscription, the before/after values of that one
            field. Read the current record with query_database before acting; the
            event is a hint, not the record. Call ack_events with each delivery_id once
            the work is done; an unacknowledged lease returns to the queue, and after
            five pulls without acknowledgement the delivery fails and the owner can
            redeliver it. Requires a write grant held by the Space owner. Poll on a
            schedule; nothing pushes to the agent. Output: items, has_more, lease_seconds.
            """
            return await command(
                lambda actor: webhook_inbox.pull(
                    workspace, actor, space_id, request or webhook_inbox.PullEvents()
                ),
                agent_id=agent_id,
            )

        @server.tool(
            annotations=ToolAnnotations(
                readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
            )
        )
        async def ack_events(
            space_id: UUID,
            request: webhook_inbox.AcknowledgeEvents,
            agent_id: UUID | None = None,
        ) -> dict[str, Any]:
            """Parameters: delivery_ids (1-50) returned by pull_events.
            What it does: marks those inbox deliveries delivered so no agent receives
            them again, and closes their receipts in Settings → Webhooks. Acknowledge
            after the work is done, or at once when the event needs no action.
            Repeating an acknowledgement is harmless. Output: one status per ID:
            acknowledged, already_done or not_found.
            """
            return await command(
                lambda actor: webhook_inbox.acknowledge(workspace, actor, space_id, request),
                agent_id=agent_id,
            )

        @server.tool(
            annotations=ToolAnnotations(
                readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
            )
        )
        async def save_handoff(
            space_id: UUID, change: SaveHandoff, agent_id: UUID | None = None
        ) -> dict[str, Any]:
            """Save an explicit session handoff as a versioned document.

            Supply decisions, progress, unresolved_questions, next_actions and topics.
            sources must name existing resource IDs and revisions in this Space.
            Use a new command UUID per change, and reuse it unchanged on retries.
            """
            return await command(workspace.save_handoff, space_id, change, agent_id=agent_id)

        @server.tool(
            annotations=ToolAnnotations(
                readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
            )
        )
        async def get_context(
            space_id: UUID,
            request: RecallRequest,
            agent_id: UUID | None = None,
            include_timings: bool = False,
        ) -> dict[str, Any]:
            """Parameters: Space, recall request, agent identity and optional numeric timings.

            What it does: recalls cited, currently permitted context. Use mode='resume'
            for recent handoffs. mode='search' requires a query. strategy='auto' combines
            exact per-Space vector candidates and lexical sources, then assesses support
            with the configured model.
            'lexical' makes no model calls; 'prompt' adds Memora's iterative policy before
            the evidence assessment. Model calls use the configured provider.
            For a startup brief use mode='resume', compact=true: long handoffs reserve
            excerpt space for next actions, unresolved questions, decisions and progress.
            Compact resume makes no model calls; open cited revisions for complete text.
            Defaults: limit=5, max_chars=12000. Maxima: limit=10, max_chars=20000.
            Results carry verification='memory', 'handoff' or 'user_authored' and a stale
            flag. Assessed items also carry relevance=direct, historical or conflicting.
            found=false means no supported answer was found in the retrieved evidence;
            it does not prove no record exists. A note saying matches did not fit
            max_chars means retry with a larger max_chars. Pass include_related=true to
            also receive cue-linked claims in a separate, unranked `related` list.

            Output: bounded evidence. include_timings adds numeric timings_ms and
            tool_total_ms; stages can overlap and exclude earlier transport/auth work.
            Timings contain no content or identifiers. Errors still deny the response.
            """
            if not include_timings:
                return await command(workspace.context, space_id, request, agent_id=agent_id)
            started = perf_counter()
            with collect_read_timings() as timings:
                result = await command(workspace.context, space_id, request, agent_id=agent_id)
            return result | {
                "timings_ms": timings.snapshot(),
                "tool_total_ms": round((perf_counter() - started) * 1000, 1),
            }

        @server.tool(annotations=read)
        async def memory_info(space_id: UUID, agent_id: UUID | None = None) -> dict[str, Any]:
            """Show the configured memory model provider and models, without secrets."""
            return await command(workspace.memory_info, space_id, agent_id=agent_id)

        @server.tool(
            annotations=ToolAnnotations(
                readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False
            )
        )
        async def set_memory_links(
            space_id: UUID, change: SetMemoryLinks, agent_id: UUID | None = None
        ) -> dict[str, Any]:
            """Parameters: node ID, current revision, command ID and complete explicit links.

            What it does: replaces intentional node links without an AI judgment. Supply
            exact target resource IDs and relation related or expands_on; never guess a
            target from an ambiguous name. Empty links removes explicit connections only.
            Output: saved memory with links; reverse backlinks are automatic.
            """
            return await command(workspace.set_memory_links, space_id, change, agent_id=agent_id)

        @server.tool(annotations=read)
        async def get_memory_links(
            space_id: UUID,
            resource_id: UUID,
            limit: int = 50,
            agent_id: UUID | None = None,
        ) -> dict[str, Any]:
            """Parameters: Space, memory resource ID, and limit from 1 to 50.

            What it does: reads outgoing node links and reverse backlinks. Explicit
            links survive content edits; inferred links depend on assessed endpoint text.
            Deleted/excluded nodes are hidden; source-stale inferred links are hidden.
            Origin is explicit or inferred. These pointers are not proof of an answer.
            Output: links, backlinks and has_more, without model calls.
            """
            return await command(
                lambda current: workspace.memory_links(current, space_id, resource_id, limit=limit),
                agent_id=agent_id,
            )

        @server.tool(annotations=read)
        async def list_memories(
            space_id: UUID,
            query: str = "",
            after: UUID | None = None,
            limit: int = MAX_MEMORY_LIST,
            agent_id: UUID | None = None,
        ) -> dict[str, Any]:
            """Read current memory claims with their source and revision, newest first.

            Pass a query to search every memory in the Space, ranked by how rare each
            matched word is; a search returns its best matches and no cursor. Without a
            query, follow next_cursor to page through the Space. Excluded memory resources
            are also listed, with recall.excluded=true, but cannot be recalled. Stale memory
            is flagged and remains recallable unless excluded. Use change_memory to edit,
            reclassify or forget one claim.
            """
            # A bound call keeps the keyword-only page parameters out of `command`'s
            # positional contract with the admission queue.
            return await command(
                lambda current: workspace.list_memories(
                    current, space_id, query=query, after=after, limit=limit
                ),
                agent_id=agent_id,
            )

        @server.tool(
            annotations=ToolAnnotations(
                readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True
            )
        )
        async def build_memory(
            space_id: UUID, change: BuildMemory, agent_id: UUID | None = None
        ) -> dict[str, Any]:
            """Legacy synchronous extraction; prefer extract_memory for durable processing.

            This explicitly sends the chosen current source (at most 12000 characters) to
            the configured provider. Inspect memory_info first. Use an authorized saved
            source ID and revision; no chat history is imported. For rebuild, provide the
            existing memory resource_id and expected_revision. Reuse command_id on retry.
            Extracted memory is indexed immediately and needs no review.
            """
            return await command(workspace.build_memory, space_id, change, agent_id=agent_id)

        @server.tool(
            annotations=ToolAnnotations(
                readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True
            )
        )
        async def remember(
            space_id: UUID, change: RememberMemory, agent_id: UUID | None = None
        ) -> dict[str, Any]:
            """Save authored text directly: model organization, embedding and storage.

            Memory is autonomous: it is indexed immediately and is available to every
            authorized agent without human review. This sends your text to the configured
            embedding provider and the decision model when configured, without text
            generation. Wording is preserved; text over 2,000 characters is split into
            bounded claims. A write fails while no memory provider is configured.
            `importance` is 0 to 3, where 0 means undeclared; use it for
            constraints, decisions and corrections you expect to matter later. Prefer
            save_handoff for session context with decisions and
            next actions, and extract_memory to mine documents or conversations.
            Optional links connect this node to exact existing memory IDs without model approval;
            use expands_on for a detail-to-subject connection. When the user states where
            something belongs, pass routes_to with up to 8 exact container IDs from
            get_space_map. Reuse command_id unchanged on retry.
            """
            return await command(workspace.remember, space_id, change, agent_id=agent_id)

        @server.tool(
            annotations=ToolAnnotations(
                readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False
            )
        )
        async def delete_memory(
            space_id: UUID,
            resource_id: UUID,
            expected_revision: int,
            agent_id: UUID | None = None,
        ) -> dict[str, Any]:
            """Delete one generated memory record with all of its claims.

            Use this when a memory should be gone rather than corrected: forgetting its
            claims one at a time leaves an empty record behind. Every claim is journalled
            first, so a restored backup cannot resurrect it, and the record, its claims,
            its index rows and its command history are removed. Deletion is permanent.
            Read the memory with list_memories first to get its current revision. Deleting
            an already absent record is a success. To remove a single claim and keep the
            rest, use change_memory with action="forget" instead.
            """
            return await command(
                lambda actor: resource_deletion.delete_memory(
                    workspace, actor, space_id, resource_id, expected_revision
                ),
                agent_id=agent_id,
            )

        @server.tool(
            annotations=ToolAnnotations(
                readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True
            )
        )
        async def change_memory(
            space_id: UUID, change: ChangeMemory, agent_id: UUID | None = None
        ) -> dict[str, Any]:
            """Edit, reclassify or permanently remove one claim, using its current IDs.

            resource_id is the memory record and entry_id is the claim; both come from
            list_memories. action is edit, forget, classify or route. route sets routes_to,
            the containers this claim governs; an empty list clears and pins it. A forget
            removes the claim from the index immediately and is not a reversible exclusion,
            though its text
            remains in the previous immutable revision. classify pins manual assignments:
            scope, category_ids, project_ids and section_ids, each list holding at most 32
            unique IDs, and a project's active parent section is added automatically.
            classify without classification replaces the assignments using the decision model when
            configured. Never replace a user correction without their request. Read
            list_memories first to get current entry IDs and values.
            """
            return await command(workspace.change_memory, space_id, change, agent_id=agent_id)

    # SDK-generated argument models otherwise include rejected input in errors.
    # Keep this covered by the transport regression test when upgrading the SDK.
    for tool in server._tool_manager.list_tools():
        tool.fn_metadata.arg_model.model_config["hide_input_in_errors"] = True
        tool.fn_metadata.arg_model.model_rebuild(force=True)
    return server
