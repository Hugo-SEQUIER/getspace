"""Response shapes of the public API, for the published reference only.

Routes keep returning their existing dictionaries; these models document them and are
never used to filter a response. tests/test_api_contract.py validates real responses
against the schemas generated from these models, so a field added to a response without
being added here fails the build. Closed models reject unknown fields in that check;
open ones (documented as such) carry content whose keys depend on the caller's data.
"""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Open(BaseModel):
    model_config = ConfigDict(extra="allow")


class Error(Closed):
    """Every error response has this body."""

    model_config = ConfigDict(
        extra="forbid", json_schema_extra={"examples": [{"error": "This API key is read-only."}]}
    )

    error: str = Field(description="A human-readable explanation of what went wrong.")


class Deleted(Closed):
    deleted: Literal[True]


# Shared building blocks


class AgentRef(Open):
    """The agent identity an agent wrote under. Open: identities may carry more details."""

    id: UUID
    revision: int
    name: str
    harness: str = Field(description="The agent's software, such as claude, codex or cursor.")


class SourceRef(Closed):
    resource_id: UUID
    revision: int = Field(ge=1)


class Recall(Closed):
    category: Literal["source", "handoff", "memory"] = Field(
        description="source for documents and tasks, handoff for session handoffs, memory "
        "for memory records."
    )
    topics: list[str]
    sources: list[SourceRef] = Field(description="Exact source revisions this item cites.")
    excluded: bool = Field(description="True when the item is kept out of recall.")


class TaskColumn(Closed):
    value: str = Field(description="The status value stored on tasks, such as in_progress.")
    label: str


# Documents and tasks


class ResourceSummary(Closed):
    id: UUID
    kind: Literal["document", "task"]
    revision: int = Field(ge=1, description="Send this as expected_revision to change it.")
    title: str
    status: str = Field(description="A task column value. Documents report todo.")
    updated_at: datetime
    author: str = Field(
        description='"you", "agent", or a teammate\'s display name in a team Space.'
    )
    author_client_id: UUID | None = Field(
        description="The agent connection that wrote this revision, if an agent did."
    )
    author_agent: AgentRef | None
    project: UUID | None
    folder: UUID | None
    priority: Literal["High", "Medium", "Low"] | None
    recall: Recall


class ExplicitLink(Closed):
    resource_id: UUID
    relation: Literal["related", "expands_on"]


class Classification(Closed):
    scope: Literal["global", "project", "unassigned"]
    category_ids: list[UUID]
    project_ids: list[UUID]
    section_ids: list[UUID]
    method: str = Field(description="How the assignment was made, such as manual or typesafe.")
    reason: str


class Creator(Closed):
    kind: Literal["human", "agent"]
    basis: str = Field(description="How the creator is known, such as captured or assigned.")
    agent: AgentRef | None


class Relationships(Closed):
    status: str
    links: list[dict[str, Any]]


class Claim(Closed):
    id: UUID = Field(description="The claim's entry_id, used to edit or forget it.")
    index: str = Field(description="A short label for the claim.")
    value: str = Field(description="The claim's exact text.")
    memory_type: Literal["factual", "procedural"]
    importance: int = Field(ge=0, le=3)
    edited: bool
    created_at: datetime
    classification: Classification
    creator: Creator
    relationships: Relationships | None = None
    generated_value: str | None = Field(
        default=None, description="The original wording, present once a claim was edited."
    )
    routes_to: list[UUID] | None = Field(
        default=None, description="Containers this claim governs, from the Space map."
    )
    source: str | None = Field(
        default=None,
        description="In memory listings: the cited source resource ID, or the author kind.",
    )


class MemoryRecord(Closed):
    claims: list[Claim]
    version: int
    links: list[ExplicitLink]


class Resource(ResourceSummary):
    body: str = Field(description="Markdown text.")
    memory: MemoryRecord | None = Field(
        default=None, description="Present when the resource is a memory record."
    )


class MemoryResource(ResourceSummary):
    body: str
    memory: MemoryRecord


class ResourcePage(Closed):
    items: list[ResourceSummary]
    next_cursor: UUID | None = Field(
        description="Pass as after for the next page; null at the end."
    )


class TaskColumns(Closed):
    revision: int = Field(description="Send as expected_revision when adding a column.")
    columns: list[TaskColumn]


# Space


class SpaceMapHint(Closed):
    tool: str


class SpaceInfo(Closed):
    id: UUID
    organization_id: UUID
    kind: Literal["personal", "team"]
    role: Literal["owner", "admin", "member", "viewer"]
    visibility: str
    is_default: bool
    name: str
    tasks_collection_id: UUID
    mcp_url: str = Field(description="The agent (MCP) endpoint. The REST API shares its origin.")
    agent_id: UUID | None = Field(description="Always null for an API key.")
    space_map: SpaceMapHint


class ReadVersion(Closed):
    version: str = Field(
        description="Opaque. It changes whenever content, your role or visibility changes."
    )
    versions: dict[str, str] | None = Field(
        default=None,
        description=(
            "Opaque, one per kind of content (document, task, memory, project, folder, "
            "database, category, section, columns). Each changes only when that kind "
            "changes, or when your role or visibility changes."
        ),
    )


class Usage(Closed):
    retained_bytes: int
    items: int
    storage_limit_bytes: int | None = Field(description="Null when the Space has no limit.")
    item_limit: int | None = Field(description="Null when the Space has no limit.")
    model_requests: int = Field(description="Model requests used this calendar month (UTC).")
    model_request_limit: int | None = Field(description="Null when the Space has no limit.")
    resets_at: datetime | None
    plan: None = Field(description="Always null: a self-hosted installation has no plans.")


class Measures(Closed):
    document_writes: int
    documents_created: int
    memories_created: int
    memory_writes: int
    retrievals: int
    with_results: int
    empty: int
    errors: int
    p50_ms: float | None
    p95_ms: float | None


class Summary(Measures):
    reused_memories: int


class AgentActivity(Measures):
    agent_id: UUID | None
    actor_type: str
    last_active_at: datetime


class StrategyActivity(Measures):
    strategy: str


class ActivityDay(Closed):
    date: str = Field(description="A UTC calendar date, YYYY-MM-DD.")
    available: bool = Field(description="False before activity tracking started.")
    document_writes: int | None
    memories_created: int | None
    retrievals: int | None


class RecentEvent(Closed):
    id: UUID
    created_at: datetime
    agent_id: UUID | None
    actor_type: str
    kind: str
    resource_id: UUID | None
    new_items: int
    item_count: int
    outcome: str
    duration_ms: float | None
    resource_exists: bool


class Totals(Closed):
    documents: int
    tasks: int
    memories: int
    document_lines: int
    measured_documents: int


class Period(Closed):
    period_start: datetime
    summary: Summary
    agents: list[AgentActivity]
    strategies: list[StrategyActivity]


class Overview(Closed):
    space_id: UUID
    days: Literal[7, 30, 90]
    as_of: datetime
    period_start: datetime
    tracking_started_at: datetime
    totals: Totals
    summary: Summary
    activity: list[ActivityDay]
    agents: list[AgentActivity]
    strategies: list[StrategyActivity]
    recent: list[RecentEvent]
    usage: Usage
    periods: dict[str, Period] | None = Field(
        default=None, description="With all_periods=true: the 7, 30 and 90 day windows."
    )


class Rule(Closed):
    """A routing rule: a memory claim that says what belongs in a container."""

    text: str
    entry_id: UUID
    memory_id: UUID
    revision: int
    origin: Literal["explicit", "inferred"]
    stale: bool


class MapField(Closed):
    name: str
    type: str


class MapDatabase(Closed):
    id: UUID
    name: str
    description: str
    records: int
    fields: list[MapField] | None = Field(
        default=None, description="Omitted when the map is shortened to fit max_chars."
    )
    view: str
    group_by: str | None
    rules: list[Rule]


class MapFolder(Closed):
    id: UUID
    name: str
    description: str
    rules: list[Rule]


class MapProject(Closed):
    id: UUID
    name: str
    description: str
    status: str
    rules: list[Rule]
    folders: list[MapFolder]
    databases: list[MapDatabase]


class MapSection(Closed):
    id: UUID
    name: str
    description: str
    rules: list[Rule]
    projects: list[MapProject]
    folders: list[MapFolder]
    databases: list[MapDatabase]


class Unsectioned(Closed):
    projects: list[MapProject]
    folders: list[MapFolder]
    databases: list[MapDatabase]


class MapCategory(Closed):
    id: UUID
    name: str
    description: str


class MapSpace(Closed):
    id: UUID
    name: str


class SpaceMap(Closed):
    space: MapSpace
    global_rules: list[Rule]
    sections: list[MapSection]
    unsectioned: Unsectioned
    categories: list[MapCategory]
    task_statuses: list[TaskColumn]
    truncated: bool = Field(description="True when the map was shortened to fit max_chars.")


# Projects, folders and databases


class DatabaseField(Closed):
    id: str
    name: str
    type: str = Field(description="text, number, select, checkbox, date and similar.")
    options: list[str] = Field(default_factory=list, description="Choices for select fields.")


class DatabaseRecord(Closed):
    id: str
    name: str
    notes: str
    createdAt: datetime | None = None
    updatedAt: datetime | None = None
    values: dict[str, Any] = Field(description="Field values keyed by field id.")


class TaskTracking(Closed):
    status: str = Field(description="ID of the select field holding each row's status.")
    done: list[str] = Field(description="Status options that close a row.")
    planned: str = Field(description="ID of the date field holding the day a row is planned for.")


class WorkspaceItem(Closed):
    """A section, project, folder, category or database. Kind-specific fields are optional."""

    id: UUID
    kind: Literal["section", "project", "folder", "category", "database"]
    revision: int
    deleted: bool
    updated_at: datetime
    author_agent: AgentRef | None
    name: str
    description: str
    section_id: UUID | None = Field(default=None, description="Projects: the parent section.")
    project_id: UUID | None = Field(default=None, description="Folders and databases.")
    status: str | None = Field(default=None, description="Projects: a task column value.")
    fields: list[DatabaseField] | None = Field(default=None, description="Databases.")
    records: list[DatabaseRecord] | None = Field(default=None, description="Databases.")
    view: str | None = Field(default=None, description="Databases: table or board.")
    groupBy: str | None = Field(default=None, description="Databases: the board's field.")
    tasks: TaskTracking | None = Field(
        default=None, description="Databases: present when the rows are tracked as tasks."
    )


class ItemPage(Closed):
    items: list[WorkspaceItem]
    next_cursor: UUID | None


class DatabaseVersion(Closed):
    id: UUID
    revision: int


class StatementResult(Closed):
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool = Field(description="True when more rows matched than max_rows.")


class Changes(Closed):
    inserted: int
    updated: int
    deleted: int


class SchemaColumn(Closed):
    column: str = Field(description="The SQL column name to use in queries.")
    field_id: str
    type: str
    options: list[str]


class QueryResult(Closed):
    database: DatabaseVersion
    results: list[StatementResult] = Field(description="One result per statement.")
    changes: Changes | None = Field(description="Row counts for a write; null for a read.")
    schema_: list[SchemaColumn] | None = Field(
        default=None, alias="schema", description="With include_schema=true."
    )


# Memory


class ProviderIndex(Closed):
    claims: int
    memories: int
    other_model: int = Field(description="Claims indexed by a different embedding model.")
    engine: str


class ProviderInfo(Open):
    """The Space's memory provider. Open: providers report different settings."""

    enabled: bool
    provider: str
    index: ProviderIndex | None = None


class ImportOrigin(Open):
    """Where an imported memory came from. Open: depends on the import format."""


class MemorySummary(ResourceSummary):
    stale: bool = Field(description="True when a cited source has changed since.")
    import_origin: ImportOrigin | None = None
    claims: list[Claim]


class MemoryPage(Closed):
    items: list[MemorySummary]
    next_cursor: UUID | None
    provider: ProviderInfo


class MemoryLink(Closed):
    entry_id: UUID | None
    resource_id: UUID
    related_entry_id: UUID | None
    revision: int
    relation: str
    origin: Literal["explicit", "inferred"]


class MemoryLinks(Closed):
    links: list[MemoryLink]
    backlinks: list[MemoryLink]
    has_more: bool


class JobResult(Closed):
    resource_id: UUID
    revision: int


class Job(Closed):
    id: UUID
    status: Literal["queued", "processing", "ready", "failed"]
    attempts: int
    source_saved: Literal[True]
    recall_ready: bool = Field(description="True once the extracted memory can be recalled.")
    result: JobResult | None
    error_code: str | None


class JobList(Closed):
    items: list[Job]


# Search and recall


class SearchItem(Closed):
    id: UUID
    kind: str = Field(description="document, task, memory, project, database and so on.")
    title: str
    description: str = Field(description="A short excerpt.")
    status: str | None = None
    updated_at: datetime | None = None


class SearchPage(Closed):
    items: list[SearchItem]
    snapshot: int = Field(description="Send with after on following pages.")
    scanned: int = Field(description="Items scanned for this page (at most 25).")
    next_cursor: str | None = Field(description="Continue until null, even after empty pages.")


class RecallItem(Open):
    """One cited result. Open: assessed results add relevance and evidence details."""

    id: UUID
    revision: int
    kind: str
    title: str
    verification: Literal["memory", "handoff", "user_authored"]
    stale: bool
    url: str = Field(description="A link to this exact revision in the Space website.")
    excerpt: str
    truncated: bool


class RecallResult(Open):
    """Cited recall. Open: optional related, linked and timing details."""

    engine: str
    mode: Literal["search", "resume"]
    found: bool = Field(
        description="False means no supported answer was found, not that none exists."
    )
    items: list[RecallItem]
    has_more: bool
    guidance: str


# Export


class ExportEntry(Closed):
    entity: str = Field(description="What value holds, such as resource or item.")
    current: bool = Field(description="False for an earlier revision kept as history.")
    value: dict[str, Any]


class ExportPage(Closed):
    format: str
    source_space: UUID
    snapshot: int
    exported_at: datetime
    task_columns: list[TaskColumn] | None = Field(default=None, description="First page only.")
    entries: list[ExportEntry]
    next_cursor: str | None


# Operations


class Live(Closed):
    status: Literal["ok"]
    service: str
    version: str


class Ready(Closed):
    status: Literal["ready"]
