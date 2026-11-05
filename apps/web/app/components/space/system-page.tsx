import Link, { useRouter, useSearchParams } from "@web/lib/navigation";
import { useCallback, useRef, useState } from "react";
import { message } from "../../../lib/client";
import {
  LANES, cadenceLabel, localToday, percent, reviewState, systemInput,
  type LabEntry, type LabStatus, type System,
} from "../../../lib/space/systems";
import { useFeedback } from "../notifications";
import { useSpace } from "./provider";
import { Empty } from "./ui";
import { Icon } from "./ui-icon";
import { EntryDialog, SystemDialog } from "./systems-forms";
import { LabEntryPanel, type Action } from "./lab-entry-panel";
import { ActivityFeed } from "./systems-activity";
import { Odometer } from "./systems-band";
import { EvidenceMeter, LaneMark, StatusPill, dayLabel, dayPhrase, dayWord, useLaneMotion, useSystemsArea } from "./systems-parts";
import styles from "./systems.module.css";

/** The motion a saved move plays on its card: a verdict stamp, a retry spin or a retire fade. */
type Moment = { id: string; kind: "works" | "fails" | "retry" | "retire" };
const MOMENTS: Partial<Record<Action, Moment["kind"]>> = { validated: "works", invalidated: "fails", testing: "retry", retired: "retire" };
// The stamp lands first; the card then takes its lane move.
const STAMP_MS = 240;

const EMPTY_LANE: Record<LabStatus, string> = {
  testing: "Nothing under test. Add a hypothesis to try.",
  validated: "Promote an entry here once it is measured.",
  invalidated: "What was tried and did not work lands here.",
  retired: "Entries no longer tested rest here.",
};

// Parameters: the workspace base path and the system's id; ?entry=<id> opens a lab entry.
// What it does: shows one system: its health, goal against target, counter-metric, source of
// truth, review rhythm, next decision and playbook, then its lab as four lanes. Opening an
// entry puts its panel beside the lanes.
// Output: the page.
export function SystemPage({ base, id }: { base: string; id: string }) {
  const { area, error, canWrite, keep, spaceId, readOnlyNote } = useSystemsArea();
  const data = useSpace();
  const router = useRouter();
  const params = useSearchParams();
  const [editing, setEditing] = useState(false);
  const [adding, setAdding] = useState(false);
  const [busy, setBusy] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [notice, setNotice] = useState("");
  const [moment, setMoment] = useState<Moment | null>(null);
  // Only a move the person just made plays, never a read, so nothing moves on page load.
  const moved = useCallback((saved: LabEntry, action: Action) => {
    const kind = MOMENTS[action];
    setMoment(kind ? { id: saved.id, kind } : null);
    const verdict = kind === "works" || kind === "fails";
    if (verdict && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) setTimeout(() => keep(saved), STAMP_MS);
    else keep(saved);
  }, [keep]);
  useFeedback({ error: saveError || error, notice, loading: busy ? "Saving system…" : null, clearError: () => setSaveError(""), clearNotice: () => setNotice("") });
  const system = area?.systems.find((item) => item.id === id);
  const entries = area?.entries.filter((entry) => entry.system_id === id) ?? [];
  const entryId = params.get("entry");
  const selected = entryId ? entries.find((entry) => entry.id === entryId) : undefined;
  const href = `${base}/systems/${id}`;
  const open = (entry: LabEntry | null) => router.replace(entry ? `${href}?entry=${entry.id}` : href, { scroll: false });

  if (!system) {
    return (
      <div className={styles.page}>
        {area ? (
          <Empty icon="system" title="System not found">
            <p>It may have been archived, or the link is incomplete.</p>
            <Link className="sp-button" href={`${base}/systems`}>Back to Systems</Link>
          </Empty>
        ) : (
          <p className={styles.loading} role="status">{error ? "This system is unavailable right now." : "Loading system…"}</p>
        )}
      </div>
    );
  }
  const review = reviewState(system, localToday());
  const playbook = system.playbook_id ? data.resources.find((item) => item.id === system.playbook_id) : undefined;
  async function markReviewed() {
    if (!system || !spaceId) return;
    setBusy(true);
    try {
      keep(await data.command<System>(`spaces/${spaceId}/systems`, {
        system_id: system.id, expected_revision: system.revision,
        data: { ...systemInput(system), last_review: localToday() },
      }));
      setNotice("Review recorded for today");
    } catch (e) {
      setSaveError(message(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <div className="sp-topbar sp-db-topbar">
        <div className="sp-breadcrumbs">
          <Link href={`${base}/systems`}>Systems</Link>
          <Icon name="chevron" size={13} />
          <span>{system.name}</span>
        </div>
        <div className="sp-actions">
          <span className="sp-db-topbar-private"><Icon name="clock" size={11} />Revision {system.revision} · saved {dayPhrase(system.updated_at)}</span>
        </div>
      </div>
      <div className={styles.detail} data-panel={selected ? "" : undefined}>
        <div className={styles.detailMain}>
          <div className={styles.systemHead}>
            <div>
              <StatusPill status={system.status} />
              <h1>{system.name}</h1>
              {system.description && <p>{system.description}</p>}
            </div>
            {canWrite && (
              <div className={styles.actions}>
                <button onClick={() => setEditing(true)}><Icon name="edit" size={16} />Edit system</button>
                <button className="sp-primary" onClick={() => setAdding(true)}><Icon name="plus" size={16} />New entry</button>
              </div>
            )}
          </div>
          {readOnlyNote && <p className={styles.note}>{readOnlyNote}</p>}
          <div className={styles.overview}>
            <section className={styles.readout} aria-label="Goal">
              <span className={styles.readoutLabel}>{system.goal_metric || "No goal metric yet"}</span>
              <p>
                <strong>{system.current_value || "–"}</strong>
                <span>target {system.goal_target || "not set"}</span>
              </p>
              <dl>
                <div><dt>Counter-metric</dt><dd>{system.counter_metric || "None set"}</dd></div>
                <div><dt>Lab</dt><dd>Testing {system.stats.testing} · Works {system.stats.works} · Does not work {system.stats.does_not_work}</dd></div>
                <div><dt>Reuse</dt><dd>{percent(system.stats.reuse_share)} of verdicts used within 30 days</dd></div>
              </dl>
            </section>
            <dl className={styles.facts}>
              <div>
                <dt>Source of truth</dt>
                <dd>{system.source_of_truth || <span className={styles.muted}>Not set</span>}</dd>
              </div>
              <div>
                <dt>Review</dt>
                <dd>
                  {system.review_cadence ? cadenceLabel(system.review_cadence) : "No set review"}
                  {system.last_review && <span className={styles.muted}> · last {dayLabel(system.last_review)}</span>}
                  {review.due && <span className={styles.due}><Icon name="clock" size={12} />Review due</span>}
                  {!review.due && review.next && <span className={styles.muted}> · next review {dayWord(review.next)}</span>}
                  {canWrite && system.review_cadence && (
                    <button className={`sp-text-button ${styles.inlineAction}`} disabled={busy} onClick={() => void markReviewed()}>Mark reviewed today</button>
                  )}
                </dd>
              </div>
              <div>
                <dt>Next decision</dt>
                <dd>{system.next_decision || <span className={styles.muted}>None recorded</span>}</dd>
              </div>
              <div>
                <dt>Playbook</dt>
                <dd>
                  {system.playbook_id ? (
                    <Link className={styles.playbook} href={`${base}/documents/${system.playbook_id}`}>
                      <Icon name="document" size={15} />{playbook?.title ?? "Open the playbook"}
                    </Link>
                  ) : <span className={styles.muted}>No playbook yet</span>}
                </dd>
              </div>
            </dl>
          </div>
          <Pipeline key={system.id} entries={entries} selected={selected?.id} moment={moment} onOpen={open} />
          <ActivityFeed base={base} spaceId={spaceId} systemId={system.id} systemNames={new Map([[system.id, system.name]])} />
        </div>
        {selected && (
          <LabEntryPanel
            key={selected.id}
            entry={selected}
            system={system}
            systems={area!.systems}
            domains={area!.domains}
            canWrite={canWrite}
            readOnlyNote={readOnlyNote}
            spaceId={spaceId}
            keep={keep}
            onEdited={(saved) => {
              keep(saved);
              // An edit can move the entry to another system: follow it there.
              if (saved.system_id !== system.id) router.replace(`${base}/systems/${saved.system_id}?entry=${saved.id}`, { scroll: false });
            }}
            onMoved={moved}
            onClose={() => open(null)}
          />
        )}
        {entryId && !selected && area && (
          <p className={styles.missingEntry} role="status">
            This entry is not in {system.name}'s lab any more. <button className="sp-text-button" onClick={() => open(null)}>Close</button>
          </p>
        )}
      </div>
      {editing && spaceId && (
        <SystemDialog spaceId={spaceId} system={system} onClose={() => setEditing(false)}
          onSaved={(saved) => { keep(saved); setEditing(false); setNotice("System saved"); }}
          onArchived={(archived) => { keep(archived); router.push(`${base}/systems`); }} />
      )}
      {adding && spaceId && (
        <EntryDialog spaceId={spaceId} systemId={system.id} systems={area!.systems} domains={area!.domains} onClose={() => setAdding(false)}
          onSaved={(saved) => { keep(saved); setAdding(false); router.replace(`${base}/systems/${saved.system_id}?entry=${saved.id}`, { scroll: false }); }} />
      )}
    </>
  );
}

// Parameters: one system's lab entries, the open entry and how to open one.
// What it does: lays the lab out as lanes in the order entries move through them, Testing →
// Works → Does not work → Retired; a card that changes lane slides to its new place, and the
// lane counts roll.
// Output: a section.
function Pipeline({ entries, selected, moment, onOpen }: {
  entries: LabEntry[]; selected?: string; moment: Moment | null; onOpen: (entry: LabEntry) => void;
}) {
  const root = useRef<HTMLElement>(null);
  useLaneMotion(root);
  return (
    <section className={styles.lab} aria-labelledby="system-lab-title" ref={root}>
      <div className={styles.labHead}>
        <h2 id="system-lab-title">Lab</h2>
        <p>Agents recall only verdicts: what works and what does not. Entries in Testing stay here.</p>
      </div>
      <div className={styles.lanes}>
        {LANES.map((lane) => {
          const cards = entries.filter((entry) => entry.status === lane.value);
          return (
            <section key={lane.value} className={styles.lane} data-lane={lane.value} aria-label={`${lane.label}, ${cards.length} ${cards.length === 1 ? "entry" : "entries"}`}>
              <h3><LaneMark status={lane.value} />{lane.label}<span><Odometer value={cards.length} /></span></h3>
              {cards.length ? (
                <ul>
                  {cards.map((entry) => (
                    <li key={entry.id}>
                      <EntryCard entry={entry} selected={entry.id === selected} moment={moment?.id === entry.id ? moment.kind : undefined} onOpen={() => onOpen(entry)} />
                    </li>
                  ))}
                </ul>
              ) : <p className={styles.laneEmpty}>{EMPTY_LANE[lane.value]}</p>}
            </section>
          );
        })}
      </div>
    </section>
  );
}

function EntryCard({ entry, selected, moment, onOpen }: { entry: LabEntry; selected: boolean; moment?: Moment["kind"]; onOpen: () => void }) {
  return (
    <button type="button" className={styles.card} data-entry-id={entry.id} data-lane={entry.status} data-moment={moment} aria-pressed={selected} onClick={onOpen}>
      <span className={styles.cardSentence}>{entry.name}</span>
      <span className={styles.cardMeta}>
        <span className={styles.domain}>{entry.domain}</span>
        <EvidenceMeter evidence={entry.evidence} />
      </span>
      <span className={styles.cardFoot}>
        {entry.retries > 0 && <span title="Times it went back to testing"><Icon name="refresh" size={12} className={styles.retryIcon} />{entry.retries === 1 ? "1 retry" : `${entry.retries} retries`}</span>}
        {entry.verdict || entry.uses.length
          ? <span>{entry.uses.length ? `Used ${entry.uses.length}× · last ${dayLabel(entry.last_used!)}` : "Not used yet"}</span>
          : null}
        <span className={styles.cardWhen} title="Last change">{entry.author_agent ? `${entry.author_agent.name}, ` : ""}{dayWord(entry.updated_at)}</span>
      </span>
      {/* Last, so the card is read by its sentence; the stamp is placed over the corner. */}
      {(moment === "works" || moment === "fails") && (
        <span className={styles.stamp} data-verdict={moment} role="status">{moment === "works" ? "Works" : "Does not work"}</span>
      )}
    </button>
  );
}
