// Parameters: the Systems area as GET spaces/{id}/systems returns it, and a lab entry's
// revisions from GET .../systems/entries/{entry}/history.
// What it does: names the area's fixed vocabulary and derives what the pages show from it:
// health and lab totals, the reuse share against its target, when a review is due, the
// promotion gate, lab filters and the timeline of an entry. The backend owns the rules
// (backend/src/space/systems.py); these helpers only read them back.
// Output: types, labels and pure functions for the Systems views.

export type SystemStatus = "healthy" | "needs_attention" | "broken" | "not_measured" | "paused";
export type Cadence = "weekly" | "per_cohort" | "monthly" | "quarterly";
export type LabStatus = "testing" | "validated" | "invalidated" | "retired";
export type Evidence = "untested" | "suggestive" | "measured" | "owner_call";
type VerdictLabel = "works" | "does_not_work";
type AgentRef = { id: string; revision: number; name: string; harness: string } | null;

export type SystemStats = {
  entries: number; testing: number; works: number; does_not_work: number; retired: number;
  verdicts: number; reused_30d: number; reuse_share: number | null;
};
export type SystemInput = {
  name: string; description: string; status: SystemStatus;
  goal_metric: string; goal_target: string; current_value: string; counter_metric: string;
  source_of_truth: string; review_cadence: Cadence | null; last_review: string | null;
  next_decision: string; playbook_id: string | null;
};
export type System = SystemInput & {
  id: string; kind: "system"; revision: number; deleted: boolean; updated_at: string;
  author_agent: AgentRef; stats: SystemStats;
};
export type Use = { used_in: string; at: string; by: string | null };
export type Verdict = { memory_id: string; label: VerdictLabel; promoted_at: string; promoted_by: string | null };
export type LabEntryInput = {
  name: string; system_id: string; domain: string; evidence: Evidence;
  why: string; apply: string; applies_to: string; source: string; captured: string | null; note: string;
};
export type LabEntry = LabEntryInput & {
  id: string; kind: "lab_entry"; revision: number; deleted: boolean; updated_at: string; author_agent: AgentRef;
  status: LabStatus; uses: Use[]; last_used: string | null; verdict: Verdict | null; retries: number;
};
export type LabRevision = LabEntry & { author: string };
export type SystemsArea = { systems: System[]; entries: LabEntry[]; domains: string[] };

export const SYSTEM_STATUSES: { value: SystemStatus; label: string }[] = [
  { value: "healthy", label: "Healthy" },
  { value: "needs_attention", label: "Needs attention" },
  { value: "broken", label: "Broken" },
  { value: "not_measured", label: "Not measured" },
  { value: "paused", label: "Paused" },
];
export const CADENCES: { value: Cadence; label: string }[] = [
  { value: "weekly", label: "Weekly" },
  { value: "per_cohort", label: "Per cohort" },
  { value: "monthly", label: "Monthly" },
  { value: "quarterly", label: "Quarterly" },
];
/** The lab's lanes, in the order an entry moves through them. */
export const LANES: { value: LabStatus; label: string }[] = [
  { value: "testing", label: "Testing" },
  { value: "validated", label: "Works" },
  { value: "invalidated", label: "Does not work" },
  { value: "retired", label: "Retired" },
];
/** An ascending scale: only the last two can become a verdict. */
export const EVIDENCE: { value: Evidence; label: string }[] = [
  { value: "untested", label: "Untested idea" },
  { value: "suggestive", label: "Suggestive" },
  { value: "measured", label: "Measured" },
  { value: "owner_call", label: "Owner's call" },
];
/** Share of verdicts reused within 30 days of their promotion that the area aims for. */
export const REUSE_TARGET = 0.3;

const label = <T extends string>(list: { value: T; label: string }[], value: T) =>
  list.find((item) => item.value === value)?.label ?? value;
export const statusLabel = (value: SystemStatus) => label(SYSTEM_STATUSES, value);
export const cadenceLabel = (value: Cadence) => label(CADENCES, value);
export const laneLabel = (value: LabStatus) => label(LANES, value);
export const evidenceLabel = (value: Evidence) => label(EVIDENCE, value);
/** 1 to 4 on the evidence scale. */
export const evidenceStep = (value: Evidence) => EVIDENCE.findIndex((item) => item.value === value) + 1;
export const isVerdict = (status: LabStatus) => status === "validated" || status === "invalidated";

/** "34%", or an en dash when there is no verdict to reuse yet. */
export function percent(share: number | null) {
  return share === null ? "–" : `${Math.round(share * 100)}%`;
}

// Parameters: the active systems with their statistics.
// What it does: counts systems by health and adds up their labs; the reuse share is taken
// over every verdict in the area, not averaged per system.
// Output: { health, lab, reuse }.
export function areaSummary(systems: System[]) {
  const health = Object.fromEntries(SYSTEM_STATUSES.map(({ value }) => [value, 0])) as Record<SystemStatus, number>;
  const lab = { testing: 0, works: 0, does_not_work: 0, retired: 0 };
  let reused = 0, verdicts = 0;
  for (const system of systems) {
    health[system.status] += 1;
    lab.testing += system.stats.testing;
    lab.works += system.stats.works;
    lab.does_not_work += system.stats.does_not_work;
    lab.retired += system.stats.retired;
    reused += system.stats.reused_30d;
    verdicts += system.stats.verdicts;
  }
  return { health, lab, reuse: { reused, verdicts, share: verdicts ? reused / verdicts : null } };
}

function day(value: string) {
  return new Date(`${value}T00:00:00Z`);
}
function iso(date: Date) {
  return date.toISOString().slice(0, 10);
}
/** Today in the person's time zone, as the YYYY-MM-DD the API uses for days. */
export function localToday(now = new Date()) {
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
}

// Parameters: a system and today's date (YYYY-MM-DD).
// What it does: adds the review cadence to the last review: 7 days, one month or three
// months. A per-cohort review has no fixed date, so it is never reported as due; a system
// with a fixed cadence and no review yet is due at once.
// Output: { next: the next review date or null, due: whether it has passed }.
export function reviewState(system: Pick<System, "review_cadence" | "last_review">, today: string) {
  const cadence = system.review_cadence;
  if (!cadence || cadence === "per_cohort") return { next: null, due: false };
  if (!system.last_review) return { next: null, due: true };
  const next = day(system.last_review);
  if (cadence === "weekly") next.setUTCDate(next.getUTCDate() + 7);
  else {
    // Jan 31 plus a month is the last day of February, not early March.
    const date = next.getUTCDate();
    next.setUTCDate(1);
    next.setUTCMonth(next.getUTCMonth() + (cadence === "monthly" ? 1 : 3));
    next.setUTCDate(Math.min(date, new Date(Date.UTC(next.getUTCFullYear(), next.getUTCMonth() + 1, 0)).getUTCDate()));
  }
  return { next: iso(next), due: iso(next) < today };
}

type GateCheck = { id: "evidence" | "source" | "system"; label: string; problem: string; met: boolean };

// Parameters: a lab entry and its system (undefined when it is missing or archived).
// What it does: lists the promotion gate the backend applies, one check per condition.
// Output: the checks, each with its label, the sentence shown when it fails, and whether it holds.
export function promotionGate(entry: Pick<LabEntry, "evidence" | "source">, system: Pick<System, "deleted"> | undefined): GateCheck[] {
  return [
    { id: "evidence", label: "Evidence is Measured or Owner's call", problem: "Set the evidence to Measured or Owner's call.",
      met: entry.evidence === "measured" || entry.evidence === "owner_call" },
    { id: "source", label: "A source is named", problem: "Name the source of the evidence.", met: !!entry.source.trim() },
    { id: "system", label: "Its system is active", problem: "Assign an active system.", met: !!system && !system.deleted },
  ];
}

// Parameters: a lab entry, the verdict asked for, and the entry's system.
// What it does: decides whether Promote is offered: from Testing, or from the other
// verdict, once every gate check holds. A retired entry goes back to Testing first.
// Output: an empty string when promotion may proceed, otherwise the reason it may not.
export function promotionBlocker(entry: LabEntry, verdict: "validated" | "invalidated", system: System | undefined) {
  if (entry.status === verdict) return `It already ${verdict === "validated" ? "works" : "does not work"}.`;
  if (entry.status === "retired") return "Move it back to Testing first.";
  return promotionGate(entry, system).find((check) => !check.met)?.problem ?? "";
}

type LabFilters = { query?: string; domain?: string; status?: LabStatus | ""; evidence?: Evidence | ""; system?: string };

/** The entries matching every filter that is set; the query searches the sentence and its notes. */
export function filterEntries(entries: LabEntry[], { query = "", domain, status, evidence, system }: LabFilters) {
  const needle = query.trim().toLowerCase();
  return entries.filter((entry) =>
    (!domain || entry.domain === domain) && (!status || entry.status === status)
    && (!evidence || entry.evidence === evidence) && (!system || entry.system_id === system)
    && (!needle || [entry.name, entry.why, entry.apply, entry.applies_to, entry.source]
      .some((text) => text.toLowerCase().includes(needle))));
}

type TimelineEvent = {
  kind: "created" | "evidence" | "promoted" | "retried" | "retired" | "used" | "edited";
  text: string;
};
type TimelineStep = { revision: number; at: string; author: string; agent: AgentRef; note: string; events: TimelineEvent[] };

const EDITED: [keyof LabEntryInput, string][] = [
  ["name", "the sentence"], ["domain", "the domain"], ["why", "why"], ["apply", "how to apply"],
  ["applies_to", "where it applies"], ["source", "the source"], ["captured", "the capture date"], ["system_id", "the system"],
];

function verdictText(status: LabStatus) {
  return status === "validated" ? "works" : "does not work";
}

// Parameters: an entry's revision and the one before it (null when the entry was just added).
// What it does: names what the revision changed: added, evidence raised or lowered, promoted
// to a verdict, a use recorded, back to Testing (a retry) or retired, and which text fields
// were edited.
// Output: the changes, the most telling first.
export function revisionEvents(previous: LabEntry | null, current: LabEntry): TimelineEvent[] {
  if (!previous) return [{ kind: "created", text: `Added as ${evidenceLabel(current.evidence).toLowerCase()}` }];
  const events: TimelineEvent[] = [];
  if (current.status !== previous.status) {
    if (isVerdict(current.status))
      events.push({ kind: "promoted", text: isVerdict(previous.status)
        ? `Verdict changed: ${verdictText(current.status)}` : `Promoted: ${verdictText(current.status)}` });
    else if (current.status === "testing")
      events.push({ kind: "retried", text: `Back to testing (retry ${current.retries})` });
    else events.push({ kind: "retired", text: "Retired" });
  }
  if (current.evidence !== previous.evidence)
    events.push({ kind: "evidence", text: `Evidence ${evidenceStep(current.evidence) > evidenceStep(previous.evidence) ? "raised" : "lowered"} to ${evidenceLabel(current.evidence)}` });
  for (const use of current.uses.slice(previous.uses.length))
    events.push({ kind: "used", text: `Used in ${use.used_in}` });
  const edited = EDITED.filter(([key]) => current[key] !== previous[key]).map(([, name]) => name);
  if (edited.length) events.push({ kind: "edited", text: `Edited ${edited.join(", ")}` });
  return events;
}

// Parameters: an entry's revisions, oldest first, each the whole entry as it was.
// What it does: names the changes of each revision against the one before it.
// Output: one step per revision, newest first, with its note, author and time.
export function entryTimeline(revisions: LabRevision[]): TimelineStep[] {
  return revisions.map((current, index) => ({
    revision: current.revision, at: current.updated_at, author: current.author, agent: current.author_agent,
    note: current.note, events: revisionEvents(revisions[index - 1] ?? null, current),
  })).reverse();
}

export type ActivityEvent = { at: string; author: string; author_agent: AgentRef; entry: LabEntry; previous: LabEntry | null };
export type ActivityRow = { key: string; at: string; author: string; agent: AgentRef; entry: LabEntry; event: TimelineEvent };

// Parameters: the lab's recent revisions, newest first, as GET .../systems/activity returns them.
// What it does: labels each revision by its most telling change, the same words as the
// entry's timeline; a revision that changed nothing it names reads "Saved".
// Output: one feed row per revision, keyed by entry and revision.
export function activityRows(events: ActivityEvent[]): ActivityRow[] {
  return events.map(({ at, author, author_agent, entry, previous }) => ({
    key: `${entry.id}:${entry.revision}`, at, author, agent: author_agent, entry,
    event: revisionEvents(previous, entry)[0] ?? { kind: "edited", text: "Saved" },
  }));
}

/** A count as the digits an odometer rolls, most significant first. */
export function odometerDigits(value: number) {
  return String(Math.max(0, Math.trunc(value))).split("").map(Number);
}

/** The fields a system save sends, taken from the system as read. */
export function systemInput(system: System): SystemInput {
  const { name, description, status, goal_metric, goal_target, current_value, counter_metric, source_of_truth,
    review_cadence, last_review, next_decision, playbook_id } = system;
  return { name, description, status, goal_metric, goal_target, current_value, counter_metric, source_of_truth,
    review_cadence, last_review, next_decision, playbook_id };
}

/** The fields a lab entry save sends; status, uses and the verdict change only through their own routes. */
export function entryInput(entry: LabEntry, note = ""): LabEntryInput {
  const { name, system_id, domain, evidence, why, apply, applies_to, source, captured } = entry;
  return { name, system_id, domain, evidence, why, apply, applies_to, source, captured, note };
}
