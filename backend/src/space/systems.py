"""Parameters: scoped Systems commands from MCP and the website.
What it does: stores systems (recurring activities with a goal, a health status and a
playbook) and their lab entries (hypotheses and lessons under test) as packed workspace
items. A lab entry reaches recall only when someone promotes it to a verdict: promotion
writes a memory carrying the verdict, and demotion keeps that memory out of recall while
both histories stay.
Output: systems with their lab statistics, lab entries, their revision history and verdicts.
"""

from datetime import UTC, date, datetime, timedelta
from typing import Literal
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
# One read of the area unpacks every active system and lab entry.
MAX_ITEMS = 5000
MAX_HISTORY = 200
MAX_USES = 500
KINDS = ("system", "lab_entry")

Domain = Literal[DOMAINS]
Evidence = Literal[tuple(EVIDENCE_LABELS)]


def _text(maximum):
    return Field(default="", max_length=maximum)


class SystemData(Input):
    name: str = Field(min_length=1, max_length=200)
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

    @field_validator("name")
    @classmethod
    def visible_name(cls, value):
        if not value.strip():
            raise ValueError("A name is required")
        return value.strip()


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
    data: SystemData

    @model_validator(mode="after")
    def create_or_update(self):
        if (self.system_id is None) != (self.expected_revision == 0):
            raise ValueError("Create with revision 0; updates require the system ID")
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
    What it does: reads every active system and lab entry and adds each system's statistics.
    Output: {systems, entries, domains}; entries newest first.
    """
    items = _items(workspace, identity, space_id)
    entries = [_entry(item) for item in items if item["kind"] == "lab_entry"]
    by_system = {}
    for entry in entries:
        by_system.setdefault(entry["system_id"], []).append(entry)
    systems = sorted(
        (
            item | {"stats": stats(by_system.get(item["id"], []))}
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


def save_system(workspace, identity, space_id, request: SaveSystem):
    """Parameters: Space and a system create or update.
    What it does: checks the playbook is a document in this Space and writes one revision.
    Output: the system with its lab statistics.
    """
    # A retried create must land on the same item, so its ID comes from the command.
    system_id = request.system_id or uuid5(request.command_id, "system")
    playbook = request.data.playbook_id
    if playbook is not None:
        resource = workspace.get_resource(identity, space_id, playbook)
        if resource["kind"] != "document" or resource["recall"]["category"] != "source":
            raise _error("A playbook must be a document in this Space")
    _commit(
        workspace,
        identity,
        space_id,
        "system",
        system_id,
        request.command_id,
        request.expected_revision,
        request.data.model_dump(mode="json"),
    )
    return _system(workspace, identity, space_id, system_id)


def _system(workspace, identity, space_id, system_id):
    system = _get(workspace, identity, space_id, system_id, "system")
    entries = [
        _entry(item)
        for item in _items(workspace, identity, space_id, ("lab_entry",))
        if item["system_id"] == str(system_id)
    ]
    return system | {"stats": stats(entries)}


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
        {key: system[key] for key in ("id", "name", "status", "goal_metric", "stats")}
        for system in area["systems"]
        if query.system_id is None or system["id"] == str(query.system_id)
    ]
    return {"systems": systems, "verdicts": selected}
