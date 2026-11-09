"""Parameters: scoped Systems commands from MCP and the website.
What it does: stores systems (recurring activities with a goal, a health status and a
playbook) and their lab entries (hypotheses and lessons under test) as packed workspace
items. A lab entry reaches recall only when someone promotes it to a verdict: promotion
writes a memory carrying the verdict, and demotion keeps that memory out of recall while
both histories stay.
Readings are the dated numbers a sensor script records for a system, each under a metric
(`goal` unless named). They are rows of `space.system_readings`, one per system, date and
metric, without revision history: a second write for that date and metric replaces the row,
so the owner archive carries one entry per stored reading rather than a revision per write.
Output: systems with their lab statistics and latest reading, lab entries, their revision
history, verdicts and readings.
"""

from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID, uuid5

from pydantic import Field, field_validator, model_validator

from space import agents, workspace_items
from space.memory import Input, RememberMemory, SetMemoryRecall

SYSTEM_STATUSES = ("healthy", "needs_attention", "broken", "not_measured", "paused")
CADENCES = ("weekly", "per_cohort", "monthly", "quarterly")
LAB_STATUSES = ("testing", "validated", "invalidated", "retired")
EVIDENCE_LABELS = {
    "untested": "Untested idea",
    "suggestive": "Suggestive",
    "measured": "Measured",
    "owner_call": "Owner's call",
}
DOMAINS = (
    "Copy & hooks",
    "Social",
    "Outreach & sales",
    "SEO/GEO",
    "Product & positioning",
    "Dev & engineering",
    "Agents & automation",
    "Video",
    "Ops & business",
)
VERDICT_LABELS = {"validated": "works", "invalidated": "does_not_work"}
# Only these evidence levels may become a verdict; the rest stay in the lab.
VERDICT_EVIDENCE = ("measured", "owner_call")
REUSE_WINDOW = timedelta(days=30)
# The structured fields list_verdicts repeats in each system summary.
SUMMARY_FIELDS = (
    "area",
    "project_id",
    "controllable_inputs",
    "stop_conditions",
    "outcome_delay",
    "machine",
    "skills",
)
# One read of the area unpacks every active system and lab entry.
MAX_ITEMS = 5000
MAX_HISTORY = 200
MAX_USES = 500
MAX_MACHINE = 50
MAX_SKILLS = 30
KINDS = ("system", "lab_entry")
# A list call returns at most this many readings.
MAX_READINGS = 1000
# A Space keeps at most this many readings, fifteen systems with six metrics a day for three
# years. Each one is an entry of the owner archive, which restore bounds by this number too.
MAX_SPACE_READINGS = 100_000
# One record_system_readings call writes at most this many readings of one system.
MAX_BATCH_READINGS = 20
# The metric a reading counts toward when none is named; the summaries report this one.
GOAL_METRIC = "goal"
METRIC_PATTERN = r"^[a-z0-9][a-z0-9_.-]*$"

Domain = Literal[DOMAINS]
Evidence = Literal[tuple(EVIDENCE_LABELS)]


def _text(maximum):
    return Field(default="", max_length=maximum)


class MachineJob(Input):
    """A scheduled job that runs part of the system, such as a cron job."""

    job_id: str = Field(min_length=1, max_length=100)
    label: str = _text(200)

    @field_validator("label", mode="before")
    @classmethod
    def no_label(cls, value):
        return "" if value is None else value

    @field_validator("job_id")
    @classmethod
    def named_job(cls, value):
        if not value.strip():
            raise ValueError("A job ID is required")
        return value.strip()


class SystemFields(Input):
    """Every system field but its name, each with an empty default, so a system saved
    before a field existed stays valid. A null resets a field to its default."""

    description: str = _text(10000)
    status: Literal[SYSTEM_STATUSES] = "not_measured"
    goal_metric: str = _text(2000)
    goal_target: str = _text(500)
    current_value: str = _text(2000)
    counter_metric: str = _text(2000)
    source_of_truth: str = _text(2000)
    review_cadence: Literal[CADENCES] | None = None
    last_review: date | None = None
    next_decision: str = _text(2000)
    # The system's playbook: a document in this Space.
    playbook_id: UUID | None = None
    # The area of the business it serves, such as Growth or Sales.
    area: str = _text(200)
    # The project in this Space the system belongs to.
    project_id: UUID | None = None
    controllable_inputs: str = _text(2000)
    # When to stop or pause the system.
    stop_conditions: str = _text(2000)
    # How long an input takes to show in the goal metric, such as "2 to 4 weeks".
    outcome_delay: str = _text(200)
    # The scheduled jobs that run it.
    machine: list[MachineJob] = Field(default_factory=list, max_length=MAX_MACHINE)
    # The names of the agent skills it uses.
    skills: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(
        default_factory=list, max_length=MAX_SKILLS
    )

    @model_validator(mode="before")
    @classmethod
    def null_resets(cls, data):
        if not isinstance(data, dict):
            return data
        fields = cls.model_fields
        return {
            key: (
                fields[key].get_default(call_default_factory=True)
                if value is None
                and key in fields
                and not fields[key].is_required()
                and fields[key].get_default(call_default_factory=True) is not None
                else value
            )
            for key, value in data.items()
        }

    @field_validator("skills")
    @classmethod
    def named_skills(cls, value):
        names = [name.strip() for name in value]
        if not all(names):
            raise ValueError("A skill needs a name")
        return list(dict.fromkeys(names))

    # The subclasses below define the name; a None name is never valid.
    @field_validator("name", check_fields=False)
    @classmethod
    def visible_name(cls, value):
        if value is None or not value.strip():
            raise ValueError("A name is required")
        return value.strip()


def system_defaults():
    """Every system field but the name at its default, for a system stored before it."""
    return {
        key: field.get_default(call_default_factory=True)
        for key, field in SystemFields.model_fields.items()
    }


class SystemData(SystemFields):
    """A stored system: a name and the fields above."""

    name: str = Field(min_length=1, max_length=200)


class SystemChange(SystemFields):
    """What a create or update sends. An update keeps every field it leaves out; a null or
    empty value clears that field. The name can be left out of an update, never cleared."""

    name: str | None = Field(default=None, min_length=1, max_length=200)


class Use(Input):
    used_in: str = Field(min_length=1, max_length=500)
    at: date
    # The agent that recorded the use; None when the person did.
    by: str | None = Field(default=None, max_length=200)


class Verdict(Input):
    memory_id: UUID
    label: Literal["works", "does_not_work"]
    promoted_at: datetime
    promoted_by: str | None = Field(default=None, max_length=200)


class LabEntryInput(Input):
    """What a person or agent writes; status, uses and the verdict change only through
    promote, demote and record_use."""

    # The hypothesis or lesson in one sentence; short enough to title its verdict memory.
    name: str = Field(min_length=1, max_length=280)
    system_id: UUID
    domain: Domain
    evidence: Evidence = "untested"
    why: str = _text(4000)
    apply: str = _text(4000)
    applies_to: str = _text(1000)
    source: str = _text(1000)
    captured: date | None = None
    # What changed in this revision; the revisions with their notes are the entry's log.
    note: str = _text(2000)

    @field_validator("name")
    @classmethod
    def visible_name(cls, value):
        if not value.strip():
            raise ValueError("A hypothesis or lesson is required")
        return value.strip()


class LabEntryData(LabEntryInput):
    status: Literal[LAB_STATUSES] = "testing"
    uses: list[Use] = Field(default_factory=list, max_length=MAX_USES)
    verdict: Verdict | None = None
    # Times the entry went back to testing after a verdict or retirement.
    retries: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def consistent(self):
        live = self.status in VERDICT_LABELS
        if live != (self.verdict is not None):
            raise ValueError("Only a validated or invalidated entry carries a verdict")
        if live and self.verdict.label != VERDICT_LABELS[self.status]:
            raise ValueError("The verdict label must match the status")
        return self


class SaveSystem(Input):
    command_id: UUID
    system_id: UUID | None = None
    expected_revision: int = Field(ge=0, strict=True)
    data: SystemChange

    @field_validator("data", mode="before")
    @classmethod
    def sent_fields(cls, value):
        # A full SystemData keeps only the fields it was given, so an update built from one
        # still leaves the others as stored.
        if isinstance(value, SystemFields) and not isinstance(value, SystemChange):
            return value.model_dump(exclude_unset=True)
        return value

    @model_validator(mode="after")
    def create_or_update(self):
        if (self.system_id is None) != (self.expected_revision == 0):
            raise ValueError("Create with revision 0; updates require the system ID")
        if self.system_id is None and self.data.name is None:
            raise ValueError("A name is required")
        return self


class SaveLabEntry(Input):
    command_id: UUID
    entry_id: UUID | None = None
    expected_revision: int = Field(ge=0, strict=True)
    data: LabEntryInput

    @model_validator(mode="after")
    def create_or_update(self):
        if (self.entry_id is None) != (self.expected_revision == 0):
            raise ValueError("Create with revision 0; updates require the entry ID")
        return self


class Promote(Input):
    command_id: UUID
    expected_revision: int = Field(ge=1, strict=True)
    verdict: Literal["validated", "invalidated"]
    note: str = _text(2000)


class Demote(Input):
    command_id: UUID
    expected_revision: int = Field(ge=1, strict=True)
    status: Literal["testing", "retired"]
    note: str = _text(2000)


class RecordUse(Input):
    command_id: UUID
    expected_revision: int = Field(ge=1, strict=True)
    used_in: str = Field(min_length=1, max_length=500)


class VerdictQuery(Input):
    system_id: UUID | None = None
    domain: Domain | None = None


class ReadingFields(Input):
    """One dated number: what a sensor measured, never an opinion."""

    date: date
    # A JSON number; a string, a boolean, NaN or infinity is refused.
    value: float = Field(strict=True, allow_inf_nan=False)
    unit: str = _text(40)
    note: str = _text(500)
    # Where the number came from, such as Search Console or Gmail.
    source: str = Field(min_length=1, max_length=200)
    # What the number measures: `goal` for the system's goal metric, or a short slug such as
    # `replies` or `counter.unsubscribes` for an input or the counter-metric.
    metric: str = Field(default=GOAL_METRIC, min_length=1, max_length=64, pattern=METRIC_PATTERN)

    @field_validator("metric", mode="before")
    @classmethod
    def default_metric(cls, value):
        return GOAL_METRIC if value is None else value

    @field_validator("unit", "note")
    @classmethod
    def trimmed(cls, value):
        return value.strip()

    @field_validator("source")
    @classmethod
    def named_source(cls, value):
        if not value.strip():
            raise ValueError("Name where the number came from")
        return value.strip()


class Reading(ReadingFields):
    # The agent that recorded it; None when the person did.
    recorded_by: str | None = Field(default=None, max_length=200)


class ArchivedReading(Reading):
    """One reading as the owner archive carries it."""

    system_id: UUID
    recorded_at: datetime


class RecordReading(ReadingFields):
    command_id: UUID


class RecordReadings(Input):
    """Several readings of one system in one call, such as a sensor's whole daily run."""

    command_id: UUID
    readings: list[ReadingFields] = Field(min_length=1, max_length=MAX_BATCH_READINGS)

    @model_validator(mode="after")
    def one_per_date_and_metric(self):
        keys = [(reading.date, reading.metric) for reading in self.readings]
        if len(set(keys)) != len(keys):
            raise ValueError("A call records one reading per date and metric")
        return self


class ReadingQuery(Input):
    since: date | None = None
    # Only this metric's readings; None lists every metric.
    metric: str | None = Field(default=None, min_length=1, max_length=64, pattern=METRIC_PATTERN)
    limit: int = Field(default=100, ge=1, le=MAX_READINGS)


def _error(message, status=409):
    from space.workspace import CommandError

    return CommandError(message, status)


def _items(workspace, identity, space_id, kinds=KINDS):
    """Parameters: Space and item kinds.
    What it does: reads and unpacks every active item of those kinds in one transaction.
    Output: decoded items.
    """
    with workspace.db.transaction(identity) as connection:
        scope = workspace._scope(connection, identity, space_id)
        rows = connection.execute(
            workspace_items.ROW + "WHERE i.kind=ANY(%s) AND NOT i.deleted "
            "AND v.revision=i.current_revision ORDER BY i.id LIMIT %s",
            (list(kinds), MAX_ITEMS + 1),
        ).fetchall()
        key_rows = workspace_items.item_keys(workspace, connection, scope, rows)
    if len(rows) > MAX_ITEMS:
        raise _error(f"The Systems area holds at most {MAX_ITEMS:,} systems and entries", 413)
    if not rows:
        return []
    return workspace_items.decode(workspace, identity, space_id, scope, rows, key_rows)


def _entry(item):
    """A decoded lab entry with its last use."""
    uses = item.get("uses", [])
    return item | {"last_used": max((use["at"] for use in uses), default=None)}


def reused(entry):
    """Whether a live verdict was used within the reuse window after its promotion."""
    verdict = entry.get("verdict")
    if not verdict:
        return False
    start = datetime.fromisoformat(verdict["promoted_at"]).date()
    return any(
        start <= date.fromisoformat(use["at"]) <= start + REUSE_WINDOW for use in entry["uses"]
    )


def stats(entries):
    """Parameters: one system's lab entries.
    What it does: counts entries by state and verdicts reused within 30 days of promotion.
    Output: the statistics the Systems area and list_verdicts show.
    """
    counts = {status: 0 for status in LAB_STATUSES}
    for entry in entries:
        counts[entry["status"]] += 1
    verdicts = counts["validated"] + counts["invalidated"]
    reuses = sum(1 for entry in entries if reused(entry))
    return {
        "entries": len(entries),
        "testing": counts["testing"],
        "works": counts["validated"],
        "does_not_work": counts["invalidated"],
        "retired": counts["retired"],
        "verdicts": verdicts,
        "reused_30d": reuses,
        "reuse_share": round(reuses / verdicts, 4) if verdicts else None,
    }


def overview(workspace, identity, space_id):
    """Parameters: Space.
    What it does: reads every active system and lab entry and adds each system's statistics
    and latest reading.
    Output: {systems, entries, domains}; entries newest first.
    """
    items = _items(workspace, identity, space_id)
    entries = [_entry(item) for item in items if item["kind"] == "lab_entry"]
    by_system = {}
    for entry in entries:
        by_system.setdefault(entry["system_id"], []).append(entry)
    latest = _latest(workspace, identity, space_id)
    today = datetime.now(UTC).date()
    systems = sorted(
        (
            system_defaults()
            | item
            | {"stats": stats(by_system.get(item["id"], []))}
            | freshness(latest.get(item["id"]), today)
            for item in items
            if item["kind"] == "system"
        ),
        key=lambda item: (item["name"].casefold(), item["id"]),
    )
    entries.sort(key=lambda entry: (entry["updated_at"], entry["id"]), reverse=True)
    return {"systems": systems, "entries": entries, "domains": list(DOMAINS)}


def live_verdicts(workspace, identity, space_id, system_id):
    """The system's lab entries that are verdicts, which keep it from being archived."""
    return [
        item
        for item in _items(workspace, identity, space_id, ("lab_entry",))
        if item["system_id"] == str(system_id) and item["status"] in VERDICT_LABELS
    ]


def _get(workspace, identity, space_id, item_id, kind, revision=None):
    from space.workspace import CommandError

    try:
        item = workspace_items.get(workspace, identity, space_id, item_id, revision)
    except CommandError as error:
        if error.status == 404:
            raise _error(f"{kind.replace('_', ' ').capitalize()} not found", 404) from None
        raise
    if item["kind"] != kind:
        raise _error(f"{kind.replace('_', ' ').capitalize()} not found", 404)
    return item


def _replayed(workspace, identity, space_id, request, entry_id, status=None):
    """Parameters: Space, a lab entry command, the entry it names and the status it sets.
    What it does: finds what this command ID already wrote. A retry of the same command
    returns that entry; the ID reused for another entry, revision or move is refused.
    Output: the committed entry, or None when the command is new.
    """
    with workspace.db.transaction(identity) as connection:
        workspace._scope(connection, identity, space_id)
        row = connection.execute(
            "SELECT item_id,revision FROM space.workspace_item_commands WHERE id=%s",
            (request.command_id,),
        ).fetchone()
    if row is None:
        return None
    saved = None
    if row["item_id"] == entry_id and row["revision"] == request.expected_revision + 1:
        saved = _get(workspace, identity, space_id, entry_id, "lab_entry", row["revision"])
    if saved is None or (status is not None and saved["status"] != status):
        raise _error("This command ID was already used for another operation")
    return _entry(saved)


def _current(workspace, identity, space_id, item_id, kind, expected_revision):
    """The item at the revision the caller read; a newer one is refused before any write."""
    item = _get(workspace, identity, space_id, item_id, kind)
    if item["revision"] != expected_revision:
        raise _error("This entry changed. Refresh before saving.")
    return item


def _active_system(workspace, identity, space_id, system_id):
    system = _get(workspace, identity, space_id, system_id, "system")
    if system["deleted"]:
        raise _error("Choose an active system")
    return system


def _commit(workspace, identity, space_id, kind, item_id, command_id, expected_revision, data):
    model = SystemData if kind == "system" else LabEntryData
    data = model.model_validate(data).model_dump(mode="json")
    workspace_items.check_item_size(data)
    workspace_items._commit(
        workspace,
        identity,
        space_id,
        kind=kind,
        item_id=item_id,
        expected_revision=expected_revision,
        command_id=command_id,
        fingerprint_input={
            kind: data,
            "client_id": identity.client_id,
            "agent_id": identity.agent_id,
        },
        data=data,
    )
    return _get(workspace, identity, space_id, item_id, kind)


def _stored(item, model):
    return {key: item[key] for key in model.model_fields if key in item}


def _base(workspace, identity, space_id, system_id, expected_revision):
    """Parameters: Space, system and the revision the caller read.
    What it does: reads that revision, so an update keeps what the caller saw for every
    field it leaves out, and its retry merges the same data again.
    Output: the stored fields of that revision.
    """
    current = _get(workspace, identity, space_id, system_id, "system")
    if expected_revision > current["revision"]:
        raise _error("This system changed. Refresh before saving.")
    read = (
        current
        if current["revision"] == expected_revision
        else _get(workspace, identity, space_id, system_id, "system", expected_revision)
    )
    return _stored(read, SystemData)


def save_system(workspace, identity, space_id, request: SaveSystem):
    """Parameters: Space and a system create or update.
    What it does: an update keeps the stored value of every field the request leaves out,
    and a null or empty value clears it. Checks a playbook or project the request names is
    a document or an active project in this Space, then writes one revision.
    Output: the system with its lab statistics.
    """
    # A retried create must land on the same item, so its ID comes from the command.
    system_id = request.system_id or uuid5(request.command_id, "system")
    sent = request.data.model_dump(mode="json", include=request.data.model_fields_set)
    playbook = request.data.playbook_id
    if playbook is not None:
        resource = workspace.get_resource(identity, space_id, playbook)
        if resource["kind"] != "document" or resource["recall"]["category"] != "source":
            raise _error("A playbook must be a document in this Space")
    if request.data.project_id is not None:
        project = _get(workspace, identity, space_id, request.data.project_id, "project")
        if project["deleted"]:
            raise _error("Choose an active project")
    data = sent
    if request.system_id is not None:
        data = _base(workspace, identity, space_id, system_id, request.expected_revision) | sent
    _commit(
        workspace,
        identity,
        space_id,
        "system",
        system_id,
        request.command_id,
        request.expected_revision,
        data,
    )
    return _system(workspace, identity, space_id, system_id)


def _system(workspace, identity, space_id, system_id):
    system = _get(workspace, identity, space_id, system_id, "system")
    entries = [
        _entry(item)
        for item in _items(workspace, identity, space_id, ("lab_entry",))
        if item["system_id"] == str(system_id)
    ]
    return system_defaults() | system | {"stats": stats(entries)}


def save_entry(workspace, identity, space_id, request: SaveLabEntry):
    """Parameters: Space and a lab entry create or update.
    What it does: writes the entry's text, system, domain and evidence; a new entry starts
    in testing. A live verdict is not edited: its memory would no longer match it.
    Output: the saved entry.
    """
    # A retry answers with what its first attempt wrote, before any check that the system is
    # still active: the write already happened, so the system may have been archived since.
    entry_id = request.entry_id or uuid5(request.command_id, "lab_entry")
    if replayed := _replayed(workspace, identity, space_id, request, entry_id):
        return replayed
    _active_system(workspace, identity, space_id, request.data.system_id)
    data = request.data.model_dump(mode="json")
    if request.entry_id:
        current = _current(
            workspace, identity, space_id, entry_id, "lab_entry", request.expected_revision
        )
        if current["status"] in VERDICT_LABELS:
            raise _error("This entry is a verdict. Move it back to Testing to change it")
        data |= {key: current[key] for key in ("status", "uses", "verdict", "retries")}
    return _entry(
        _commit(
            workspace,
            identity,
            space_id,
            "lab_entry",
            entry_id,
            request.command_id,
            request.expected_revision,
            data,
        )
    )


def _actor(workspace, identity):
    """The agent name a promotion or use is credited to, or None for the person."""
    if not identity.client_id:
        return None
    profile = agents.current_profile(workspace, identity)
    return profile["name"] if profile else "Agent"


def gate(entry, system):
    """Parameters: a lab entry and its system (None when missing or archived).
    What it does: lists what stops the entry from becoming a verdict.
    Output: the missing conditions as sentences; empty when promotion may proceed.
    """
    problems = []
    if entry["evidence"] not in VERDICT_EVIDENCE:
        problems.append("Set the evidence to Measured or Owner's call")
    if not entry["source"].strip():
        problems.append("Name the source of the evidence")
    if system is None or system["deleted"]:
        problems.append("Assign an active system")
    return problems


def _topic(name, fallback):
    """Parameters: a system name and the entry's domain.
    What it does: makes the name a topic a memory accepts. A topic forbids `|`, CR and LF,
    holds at most 100 characters and is never empty.
    Output: the cleaned name, or the domain when nothing of the name is left.
    """
    cleaned = " ".join(name.replace("|", " ").split())[:100].strip()
    return cleaned or fallback


def _verdict_text(entry, system, label):
    """The memory text of a verdict: what it says, where it holds and why. The source stays
    on the lab entry the verdict links to, so recall spends its budget on the lesson."""
    lines = [
        ("Works: " if label == "works" else "Does not work: ") + entry["name"],
        f"System: {system['name']}. Domain: {entry['domain']}. "
        f"Evidence: {EVIDENCE_LABELS[entry['evidence']]}.",
    ]
    for heading, key in (
        ("Why and evidence", "why"),
        ("How to apply", "apply"),
        ("Applies to", "applies_to"),
    ):
        if entry[key].strip():
            lines.append(f"{heading}: {entry[key].strip()}")
    return "\n".join(lines)


def _exclude(workspace, identity, space_id, memory_id, command_id):
    """Keep a verdict memory out of recall; an already excluded or deleted one is left."""
    from space.workspace import CommandError

    try:
        memory = workspace.get_resource(identity, space_id, memory_id)
    except CommandError as error:
        if error.status == 404:
            return
        raise
    if memory["recall"]["excluded"]:
        return
    workspace.set_memory_recall(
        identity,
        space_id,
        SetMemoryRecall(
            command_id=uuid5(command_id, f"exclude:{memory_id}:{memory['revision']}"),
            resource_id=memory_id,
            expected_revision=memory["revision"],
            excluded=True,
        ),
    )


def promote(workspace, identity, space_id, entry_id, request: Promote):
    """Parameters: Space, lab entry and the verdict (validated or invalidated).
    What it does: checks the gate, writes the verdict memory recall returns, then the entry.
    A verdict that changes label replaces its memory; a failed entry write takes the new
    memory back out of recall, so a refused promotion leaves nothing recallable.
    Output: the entry as a verdict.
    """
    if replayed := _replayed(workspace, identity, space_id, request, entry_id, request.verdict):
        return replayed
    from space.workspace import CommandError

    entry = _current(
        workspace, identity, space_id, entry_id, "lab_entry", request.expected_revision
    )
    if entry["deleted"]:
        raise _error("Restore this entry before promoting it")
    if entry["status"] == request.verdict:
        raise _error("This entry already has that verdict")
    if entry["status"] == "retired":
        raise _error("Move this entry back to Testing before promoting it")
    try:
        system = _get(workspace, identity, space_id, UUID(entry["system_id"]), "system")
    except CommandError:
        system = None
    if problems := gate(entry, system):
        raise _error("Not promoted: " + "; ".join(problems))
    label = VERDICT_LABELS[request.verdict]
    promoted_at = datetime.now(UTC)
    memory_id = uuid5(request.command_id, "verdict")
    topics = [_topic(system["name"], entry["domain"])]
    if topics[0] != entry["domain"]:
        topics.append(entry["domain"])
    memory = workspace.remember(
        identity,
        space_id,
        RememberMemory(
            command_id=uuid5(request.command_id, "verdict-memory"),
            title=entry["name"],
            text=_verdict_text(entry, system, label),
            topics=topics,
            importance=2 if entry["evidence"] == "measured" else 1,
        ),
        verdict={
            "label": label,
            "status": request.verdict,
            "evidence": EVIDENCE_LABELS[entry["evidence"]],
            "system": {"id": system["id"], "name": system["name"]},
            "domain": entry["domain"],
            "entry_id": str(entry_id),
            "promoted_at": promoted_at.isoformat(),
        },
        resource_id=memory_id,
    )
    # A retried promotion returns the memory its first attempt wrote, which that attempt's
    # cleanup may have taken out of recall.
    memory_id = UUID(memory["id"])
    current = workspace.get_resource(identity, space_id, memory_id)
    if current["recall"]["excluded"]:
        workspace.set_memory_recall(
            identity,
            space_id,
            SetMemoryRecall(
                command_id=uuid5(request.command_id, f"include:{memory_id}:{current['revision']}"),
                resource_id=memory_id,
                expected_revision=current["revision"],
                excluded=False,
            ),
        )
    data = _stored(entry, LabEntryData) | {
        "status": request.verdict,
        "note": request.note,
        "verdict": {
            "memory_id": str(memory_id),
            "label": label,
            "promoted_at": memory.get("verdict", {}).get("promoted_at", promoted_at.isoformat()),
            "promoted_by": _actor(workspace, identity),
        },
    }
    try:
        saved = _commit(
            workspace,
            identity,
            space_id,
            "lab_entry",
            entry_id,
            request.command_id,
            request.expected_revision,
            data,
        )
    except Exception:
        _exclude(workspace, identity, space_id, memory_id, request.command_id)
        raise
    if entry.get("verdict") and entry["verdict"]["memory_id"] != str(memory_id):
        _exclude(
            workspace, identity, space_id, UUID(entry["verdict"]["memory_id"]), request.command_id
        )
    return _entry(saved)


def demote(workspace, identity, space_id, entry_id, request: Demote):
    """Parameters: Space, lab entry and its new state (testing to retry, or retired).
    What it does: takes a verdict's memory out of recall first, so a failure never leaves a
    demoted entry recallable, then writes the entry. Both histories stay.
    Output: the entry.
    """
    if replayed := _replayed(workspace, identity, space_id, request, entry_id, request.status):
        return replayed
    entry = _current(
        workspace, identity, space_id, entry_id, "lab_entry", request.expected_revision
    )
    if entry["status"] == request.status:
        raise _error(f"This entry is already {request.status}")
    if entry.get("verdict"):
        _exclude(
            workspace, identity, space_id, UUID(entry["verdict"]["memory_id"]), request.command_id
        )
    retry = request.status == "testing"
    data = _stored(entry, LabEntryData) | {
        "status": request.status,
        "note": request.note,
        "verdict": None,
        "retries": entry["retries"] + (1 if retry else 0),
    }
    return _entry(
        _commit(
            workspace,
            identity,
            space_id,
            "lab_entry",
            entry_id,
            request.command_id,
            request.expected_revision,
            data,
        )
    )


def record_use(workspace, identity, space_id, entry_id, request: RecordUse):
    """Parameters: Space, a live verdict and where it was used.
    What it does: appends the use with today's date and who recorded it.
    Output: the entry with its new last use.
    """
    if replayed := _replayed(workspace, identity, space_id, request, entry_id):
        return replayed
    entry = _current(
        workspace, identity, space_id, entry_id, "lab_entry", request.expected_revision
    )
    if entry["status"] not in VERDICT_LABELS:
        raise _error("Only a verdict's use is recorded")
    if len(entry["uses"]) >= MAX_USES:
        raise _error(f"An entry keeps at most {MAX_USES} uses")
    use = {
        "used_in": request.used_in.strip(),
        "at": datetime.now(UTC).date().isoformat(),
        "by": _actor(workspace, identity),
    }
    data = _stored(entry, LabEntryData) | {"uses": [*entry["uses"], use], "note": ""}
    return _entry(
        _commit(
            workspace,
            identity,
            space_id,
            "lab_entry",
            entry_id,
            request.command_id,
            request.expected_revision,
            data,
        )
    )


# The revisions an activity read unpacks: the latest changes, or a wider window to filter by
# system, since the system is inside the packed payload.
MAX_ACTIVITY = 30
ACTIVITY_WINDOW = 300
REVISIONS = workspace_items.ROW.replace(
    "SELECT ", "SELECT v.author_client_id,v.author_principal_id,", 1
)


def _author(row, scope):
    if row["author_client_id"]:
        return "agent"
    return "you" if row["author_principal_id"] == scope["principal_id"] else "Teammate"


def activity(workspace, identity, space_id, system_id=None):
    """Parameters: Space and an optional system.
    What it does: reads the latest lab entry revisions of active entries with the revision
    each one replaced, so the page can say what changed: added, promoted, used, retried.
    Output: {events} newest first, at most 30, each {at, author, author_agent, entry,
    previous}; previous is None when the revision added the entry.
    """
    with workspace.db.transaction(identity) as connection:
        scope = workspace._scope(connection, identity, space_id)
        rows = connection.execute(
            REVISIONS + "WHERE i.kind='lab_entry' AND NOT i.deleted "
            "ORDER BY v.created_at DESC,v.item_id,v.revision DESC LIMIT %s",
            (MAX_ACTIVITY if system_id is None else ACTIVITY_WINDOW,),
        ).fetchall()
        earlier = [row for row in rows if row["revision"] > 1]
        previous_rows = (
            connection.execute(
                REVISIONS  # noqa: S608 - static SQL; all values are parameters
                + "WHERE (v.item_id,v.revision) IN "
                "(SELECT * FROM unnest(%s::uuid[],%s::integer[]))",
                ([row["id"] for row in earlier], [row["revision"] - 1 for row in earlier]),
            ).fetchall()
            if earlier
            else []
        )
        key_rows = workspace_items.item_keys(workspace, connection, scope, rows + previous_rows)
    if not rows:
        return {"events": []}
    decoded = workspace_items.decode(
        workspace, identity, space_id, scope, rows + previous_rows, key_rows
    )
    after, before = decoded[: len(rows)], decoded[len(rows) :]
    previous = {(item["id"], item["revision"]): _entry(item) for item in before}
    events = []
    for item, row in zip(after, rows, strict=True):
        if system_id is not None and item["system_id"] != str(system_id):
            continue
        events.append(
            {
                "at": item["updated_at"],
                "author": _author(row, scope),
                "author_agent": item["author_agent"],
                "entry": _entry(item),
                "previous": previous.get((item["id"], item["revision"] - 1)),
            }
        )
    return {"events": events[:MAX_ACTIVITY]}


def history(workspace, identity, space_id, entry_id):
    """Parameters: Space and lab entry.
    What it does: reads and unpacks the entry's latest revisions in one transaction.
    Output: {revisions} oldest first, each the full entry as it was with who wrote it.
    """
    with workspace.db.transaction(identity) as connection:
        scope = workspace._scope(connection, identity, space_id)
        rows = connection.execute(
            REVISIONS + "WHERE i.id=%s AND i.kind='lab_entry' ORDER BY v.revision DESC LIMIT %s",
            (entry_id, MAX_HISTORY),
        ).fetchall()
        key_rows = workspace_items.item_keys(workspace, connection, scope, rows)
    if not rows:
        raise _error("Lab entry not found", 404)
    rows.reverse()
    items = workspace_items.decode(workspace, identity, space_id, scope, rows, key_rows)
    for item, row in zip(items, rows, strict=True):
        item["author"] = _author(row, scope)
    return {"revisions": [_entry(item) for item in items]}


def verdicts(workspace, identity, space_id, query: VerdictQuery):
    """Parameters: Space and an optional system or domain.
    What it does: selects the live verdicts and summarises every system's lab.
    Output: {systems, verdicts}; each verdict with its label, evidence, system and link.
    """
    area = overview(workspace, identity, space_id)
    names = {system["id"]: system["name"] for system in area["systems"]}
    selected = [
        entry
        | {
            "label": entry["verdict"]["label"],
            "evidence_label": EVIDENCE_LABELS[entry["evidence"]],
            "system_name": names.get(entry["system_id"]),
            "reused_30d": reused(entry),
            "url": workspace.entry_url(space_id, entry["system_id"], entry["id"]),
        }
        for entry in area["entries"]
        if entry["status"] in VERDICT_LABELS
        and (query.system_id is None or entry["system_id"] == str(query.system_id))
        and (query.domain is None or entry["domain"] == query.domain)
    ]
    systems = [
        {
            key: system[key]
            for key in (
                "id",
                "name",
                "status",
                "goal_metric",
                *SUMMARY_FIELDS,
                "stats",
                "latest_reading",
                "reading_age_days",
            )
        }
        for system in area["systems"]
        if query.system_id is None or system["id"] == str(query.system_id)
    ]
    return {"systems": systems, "verdicts": selected}


def _reading(row):
    """A stored row as the tools show it."""
    return {
        "system_id": str(row["system_id"]),
        "date": row["day"].isoformat(),
        "value": row["value"],
        "unit": row["unit"],
        "note": row["note"],
        "source": row["source"],
        "metric": row["metric"],
        "recorded_by": row["recorded_by"],
    }


def _latest(workspace, identity, space_id):
    """Parameters: Space.
    Output: each system ID's latest goal reading by date; other metrics are left out.
    """
    with workspace.db.transaction(identity) as connection:
        workspace._scope(connection, identity, space_id)
        rows = connection.execute(
            "SELECT DISTINCT ON (system_id) system_id,day,metric,value,unit,note,source,"
            "recorded_by FROM space.system_readings "
            "WHERE metric=%s ORDER BY system_id,day DESC",
            (GOAL_METRIC,),
        ).fetchall()
    return {str(row["system_id"]): _reading(row) for row in rows}


def freshness(reading, today):
    """Parameters: a system's latest goal reading or None, and today's date.
    What it does: shows how old the latest number is, so a sensor that stopped is visible.
    Output: {latest_reading: {date, value, unit, metric} or None, reading_age_days: int or
    None}.
    """
    if reading is None:
        return {"latest_reading": None, "reading_age_days": None}
    age = (today - date.fromisoformat(reading["date"])).days
    return {
        "latest_reading": {key: reading[key] for key in ("date", "value", "unit", "metric")},
        "reading_age_days": max(age, 0),
    }


def _readings_replayed(connection, system_id, command_id, wanted):
    """Parameters: a scoped connection, the system, the command ID and the readings the
    command carries by (date, metric).
    What it does: finds what this command ID already wrote. A retry answers with what it
    wrote while any of its rows still holds it; the ID reused for another system, date,
    metric, number or operation is refused.
    Output: the committed readings in request order, or None when the command is new.
    """
    if connection.execute(
        "SELECT 1 FROM space.workspace_item_commands WHERE id=%s", (command_id,)
    ).fetchone():
        raise _error("This command ID was already used for another operation")
    rows = connection.execute(
        "SELECT system_id,day,metric,value,unit,note,source,recorded_by "
        "FROM space.system_readings WHERE command_id=%s",
        (command_id,),
    ).fetchall()
    if not rows:
        return None
    stored = {(row["day"].isoformat(), row["metric"]): _reading(row) for row in rows}
    for key, reading in stored.items():
        mine = wanted.get(key)
        if (
            reading["system_id"] != str(system_id)
            or mine is None
            or any(reading[field] != value for field, value in mine.items())
        ):
            raise _error("This command ID was already used for another operation")
    recorded_by = next(iter(stored.values()))["recorded_by"]
    return [stored.get(key) or _saved(system_id, mine, recorded_by) for key, mine in wanted.items()]


def _saved(system_id, reading, recorded_by):
    return {"system_id": str(system_id), **reading, "recorded_by": recorded_by}


def lock_readings(connection, space_id):
    """Parameters: a scoped connection and its Space.
    What it does: admits one reading writer per Space until the transaction ends, so the
    reading count stays exact and a concurrent retry of a command sees what it wrote.
    Output: none.
    """
    connection.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
        ("space.system_readings:" + str(space_id),),
    )


UPSERT = (
    "INSERT INTO space.system_readings(organization_id,space_id,system_id,day,metric,value,"
    "unit,note,source,recorded_by,command_id) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
    "ON CONFLICT (organization_id,space_id,system_id,day,metric) DO UPDATE SET "
    "value=EXCLUDED.value,unit=EXCLUDED.unit,note=EXCLUDED.note,source=EXCLUDED.source,"
    "recorded_by=EXCLUDED.recorded_by,command_id=EXCLUDED.command_id,recorded_at=now() "
    "RETURNING (xmax=0) AS inserted"
)


def _record(workspace, identity, space_id, system_id, command_id, readings):
    """Parameters: Space, an active system, the command ID and its readings.
    What it does: upserts one row per date and metric with who recorded it, in one
    transaction; a reading already on that date for the same metric is replaced.
    Output: the stored readings in request order.
    """
    wanted = {
        (reading.date.isoformat(), reading.metric): reading.model_dump(
            mode="json", include=set(ReadingFields.model_fields)
        )
        for reading in readings
    }
    with workspace.db.transaction(identity) as connection:
        workspace._scope(connection, identity, space_id)
        if replayed := _readings_replayed(connection, system_id, command_id, wanted):
            return replayed
    _active_system(workspace, identity, space_id, system_id)
    if max(reading.date for reading in readings) > datetime.now(UTC).date() + timedelta(days=1):
        raise _error("A reading cannot be dated after tomorrow", 422)
    recorded_by = _actor(workspace, identity)
    with workspace.db.transaction(identity) as connection:
        scope = workspace._scope(connection, identity, space_id, write=True)
        lock_readings(connection, scope["space_id"])
        if replayed := _readings_replayed(connection, system_id, command_id, wanted):
            return replayed
        inserted = False
        for (day, metric), reading in wanted.items():
            row = connection.execute(
                UPSERT,
                (
                    scope["organization_id"],
                    scope["space_id"],
                    system_id,
                    day,
                    metric,
                    reading["value"],
                    reading["unit"],
                    reading["note"],
                    reading["source"],
                    recorded_by,
                    command_id,
                ),
            ).fetchone()
            inserted = inserted or row["inserted"]
        if inserted:
            held = connection.execute("SELECT count(*) AS n FROM space.system_readings").fetchone()[
                "n"
            ]
            if held > MAX_SPACE_READINGS:
                raise _error(f"A Space keeps at most {MAX_SPACE_READINGS:,} readings", 413)
    return [_saved(system_id, reading, recorded_by) for reading in wanted.values()]


def record_reading(workspace, identity, space_id, system_id, request: RecordReading):
    """Parameters: Space, an active system and one dated number with where it came from.
    What it does: stores the reading with who recorded it; a reading already on that date
    for the same metric is replaced. Readings of other metrics on that date stay.
    Output: {system_id, date, value, unit, note, source, metric, recorded_by}.
    """
    [saved] = _record(workspace, identity, space_id, system_id, request.command_id, [request])
    return saved


def record_readings(workspace, identity, space_id, system_id, request: RecordReadings):
    """Parameters: Space, an active system and up to 20 readings, one per date and metric.
    What it does: stores them all or none, like record_reading for each.
    Output: {system_id, readings} in request order.
    """
    saved = _record(workspace, identity, space_id, system_id, request.command_id, request.readings)
    return {"system_id": str(system_id), "readings": saved}


def readings(workspace, identity, space_id, system_id, query: ReadingQuery):
    """Parameters: Space, system (active or archived), an optional first date, an optional
    metric and a limit.
    What it does: reads the system's readings.
    Output: {system_id, readings, more}; readings newest first (metrics of one date in name
    order), more when the limit cut some.
    """
    _get(workspace, identity, space_id, system_id, "system")
    with workspace.db.transaction(identity) as connection:
        workspace._scope(connection, identity, space_id)
        rows = connection.execute(
            "SELECT system_id,day,metric,value,unit,note,source,recorded_by "
            "FROM space.system_readings WHERE system_id=%s "
            "AND (%s::date IS NULL OR day>=%s) AND (%s::text IS NULL OR metric=%s) "
            'ORDER BY day DESC,metric COLLATE "C" LIMIT %s',
            (system_id, query.since, query.since, query.metric, query.metric, query.limit + 1),
        ).fetchall()
    return {
        "system_id": str(system_id),
        "readings": [_reading(row) for row in rows[: query.limit]],
        "more": len(rows) > query.limit,
    }


def restore_readings(entries, decisions, restore_id):
    """Parameters: validated archive entries, the restore decisions and the restore ID.
    What it does: points each archived reading at its system's restore target. A kept
    system keeps the readings it has and gains the missing ones; a replaced, copied or new
    one takes the archived readings.
    Output: rows for restore_publish, as (replace, row parameters).
    """
    rows = []
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("entity") != "reading":
            continue
        reading = ArchivedReading.model_validate(entry["value"])
        decision = decisions["item:" + str(reading.system_id)]
        key = f"{reading.system_id}:{reading.date}:{reading.metric}"
        rows.append(
            (
                decision["action"] != "keep",
                (
                    UUID(decision["target_id"]),
                    reading.date,
                    reading.metric,
                    reading.value,
                    reading.unit,
                    reading.note,
                    reading.source,
                    reading.recorded_by,
                    uuid5(restore_id, "reading:" + key),
                    reading.recorded_at,
                ),
            )
        )
    return rows
