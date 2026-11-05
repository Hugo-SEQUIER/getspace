import { useEffect, useRef, useState, type FormEvent } from "react";
import { message, watchApi } from "../../../lib/client";
import {
  entryTimeline, isVerdict, laneLabel, promotionBlocker, promotionGate,
  type LabEntry, type LabRevision, type System,
} from "../../../lib/space/systems";
import { useFeedback } from "../notifications";
import { Modal, authorName } from "./ui";
import { useSpace } from "./provider";
import { Icon } from "./ui-icon";
import { EntryDialog } from "./systems-forms";
import { EvidenceMeter, LaneMark, dayLabel, dayPhrase } from "./systems-parts";
import styles from "./systems.module.css";

export type Action = "validated" | "invalidated" | "testing" | "retired" | "use";

const VERDICT_EDIT = "A verdict is recalled as written. Move it back to Testing to change it.";

// Parameters: the open lab entry, its system, the active systems and the area's domains, whether the person may
// change it, the Space, `keep` to show a saved entry at once, `onEdited` for a saved edit
// (which may move it to another system), `onMoved` for a saved move (the page plays it), and
// how to close the panel.
// What it does: shows every field of the entry, its promotion gate as a checklist, the moves
// it can make (promote, back to testing, retire, record a use, edit), each disabled with the
// reason when it cannot, and the timeline derived from its revisions.
// Output: the side panel.
export function LabEntryPanel({ entry, system, systems, domains, canWrite, readOnlyNote, spaceId, keep, onEdited, onMoved, onClose }: {
  entry: LabEntry; system: System; systems: System[]; domains: string[]; canWrite: boolean; readOnlyNote: string; spaceId: string | null;
  keep: (item: LabEntry) => void; onEdited: (item: LabEntry) => void; onMoved: (item: LabEntry, action: Action) => void; onClose: () => void;
}) {
  const [action, setAction] = useState<Action | null>(null);
  const [editing, setEditing] = useState(false);
  const gate = promotionGate(entry, system);
  const live = isVerdict(entry.status);
  const blockers = { validated: promotionBlocker(entry, "validated", system), invalidated: promotionBlocker(entry, "invalidated", system) };
  // Like the database record panel: focus moves to the entry's title on open and returns to
  // the card that opened it on close (the card itself after a lane move remounted it).
  const panel = useRef<HTMLElement>(null);
  const title = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    const opener = document.activeElement;
    const root = panel.current;
    title.current?.focus({ preventScroll: true });
    return () => {
      // Opening another card keeps the focus there.
      const active = document.activeElement;
      if (active && active !== document.body && !root?.contains(active)) return;
      const target = opener instanceof HTMLElement && opener.isConnected ? opener
        : document.querySelector<HTMLElement>(`[data-entry-id="${entry.id}"]`);
      target?.focus({ preventScroll: true });
    };
    // The panel is keyed by its entry, so this runs once per opened entry.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const disabledNote = readOnlyNote || (!spaceId ? "Open your Space to change entries." : "");
  // The next step for this entry is the panel's one primary action.
  const primary: Action | null = !canWrite ? null : entry.status === "testing" ? (!blockers.validated ? "validated" : null)
    : live ? "use" : "testing";
  const button = (kind: Action, label: string, reason = "") => (
    <button type="button" className={primary === kind ? "sp-primary" : undefined} disabled={!canWrite || !!reason}
      aria-describedby={reason ? `entry-${kind}-reason` : undefined} onClick={() => setAction(kind)}>
      {label}
    </button>
  );
  return (
    <aside ref={panel} className={styles.panel} aria-labelledby="lab-entry-title"
      onKeyDown={(event) => {
        // Escape inside a dialog closes the dialog only; a select keeps it to close its list.
        const target = event.target as HTMLElement;
        if (event.key !== "Escape" || target.closest("dialog") || target instanceof HTMLSelectElement) return;
        event.preventDefault();
        onClose();
      }}>
      <div className={styles.panelHead}>
        <span className={styles.panelCrumb}><LaneMark status={entry.status} />{system.name} · {laneLabel(entry.status)}</span>
        <button type="button" className="sp-icon-button" aria-label="Close entry" onClick={onClose}><Icon name="close" size={16} /></button>
      </div>
      <h2 id="lab-entry-title" ref={title} tabIndex={-1} className={styles.panelTitle}>{entry.name}</h2>
      <p className={styles.panelMeta}>
        Revision {entry.revision} · updated {dayPhrase(entry.updated_at)}{entry.author_agent ? ` by ${entry.author_agent.name}` : ""}
        {entry.verdict && <> · promoted {dayPhrase(entry.verdict.promoted_at)} by {entry.verdict.promoted_by ?? "you"}</>}
      </p>
      {live && (
        <p className={styles.recallNote} data-label={entry.verdict?.label}>
          <Icon name="memory" size={15} />
          Agents recall this as “{entry.status === "validated" ? "Works" : "Does not work"}”.
        </p>
      )}

      <section className={styles.panelSection} aria-label="Actions">
        <div className={styles.panelActions}>
          {entry.status === "testing" && <>
            {button("validated", "Promote: works", blockers.validated)}
            {button("invalidated", "Promote: does not work", blockers.invalidated)}
            {button("retired", "Retire")}
          </>}
          {live && <>
            {button("use", "Record use")}
            {entry.status === "validated" ? button("invalidated", "Change to does not work", blockers.invalidated) : button("validated", "Change to works", blockers.validated)}
            {button("testing", "Back to testing")}
            {button("retired", "Retire")}
          </>}
          {entry.status === "retired" && button("testing", "Back to testing")}
          <button type="button" disabled={!canWrite || live} aria-describedby={live ? "entry-edit-reason" : undefined} onClick={() => setEditing(true)}>
            <Icon name="edit" size={14} />Edit
          </button>
        </div>
        {disabledNote ? <p className={styles.reason}>{disabledNote}</p> : <>
          {entry.status === "testing" && blockers.validated && <p className={styles.reason} id="entry-validated-reason">Not ready to promote: {blockers.validated}</p>}
          {entry.status === "testing" && blockers.invalidated && <span hidden id="entry-invalidated-reason">{blockers.invalidated}</span>}
          {live && (entry.status === "validated" ? blockers.invalidated : blockers.validated) &&
            <span hidden id={`entry-${entry.status === "validated" ? "invalidated" : "validated"}-reason`}>{entry.status === "validated" ? blockers.invalidated : blockers.validated}</span>}
          {live && <p className={styles.reason} id="entry-edit-reason">{VERDICT_EDIT}</p>}
        </>}
      </section>

      {entry.status !== "retired" && (
        <section className={styles.panelSection} aria-labelledby="entry-gate-title">
          <h3 id="entry-gate-title">Promotion gate</h3>
          <ul className={styles.gate}>
            {gate.map((check) => (
              <li key={check.id} data-met={check.met || undefined}>
                <span className={styles.gateMark} aria-hidden="true">{check.met && <Icon name="check" size={11} />}</span>
                <span>{check.label}</span>
                <span className="sr-only">{check.met ? "Met" : "Missing"}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      <dl className={styles.panelFields}>
        <div><dt>Domain</dt><dd>{entry.domain}</dd></div>
        <div><dt>Evidence</dt><dd><EvidenceMeter evidence={entry.evidence} /></dd></div>
        <Detail label="Why" value={entry.why} />
        <Detail label="How to apply" value={entry.apply} />
        <Detail label="Applies to" value={entry.applies_to} />
        <Detail label="Source" value={entry.source} />
        <div><dt>Captured</dt><dd>{entry.captured ? dayLabel(entry.captured) : <span className={styles.muted}>Empty</span>}</dd></div>
        <div><dt>Retries</dt><dd>{entry.retries}</dd></div>
        <div>
          <dt>Used in</dt>
          <dd>
            {entry.uses.length ? (
              <ul className={styles.uses}>
                {[...entry.uses].reverse().map((use, index) => (
                  <li key={index}><strong>{use.used_in}</strong><small>{dayLabel(use.at)} · {use.by ?? "you"}</small></li>
                ))}
              </ul>
            ) : <span className={styles.muted}>{live ? "Not used yet" : "Only a verdict's uses are recorded"}</span>}
          </dd>
        </div>
      </dl>

      <Timeline entry={entry} spaceId={spaceId} />

      {action && spaceId && (
        <ActionDialog action={action} entry={entry} spaceId={spaceId} onClose={() => setAction(null)}
          onDone={(saved) => { onMoved(saved, action); setAction(null); }} />
      )}
      {editing && spaceId && (
        <EntryDialog spaceId={spaceId} entry={entry} systemId={entry.system_id} systems={systems} domains={domains} onClose={() => setEditing(false)}
          onSaved={(saved) => { setEditing(false); onEdited(saved); }}
          onArchived={(archived) => { keep(archived); setEditing(false); onClose(); }} />
      )}
    </aside>
  );
}

function Detail({ label, value }: { label: string; value: string }) {
  return <div><dt>{label}</dt><dd>{value.trim() ? value : <span className={styles.muted}>Empty</span>}</dd></div>;
}

const ACTIONS: Record<Action, { title: string; confirm: string; busy: string; body: string }> = {
  validated: { title: "Promote to “works”?", confirm: "Promote: works", busy: "Promoting…",
    body: "Agents will recall it as something that works, with its why, source and how to apply. You can move it back to Testing at any time." },
  invalidated: { title: "Promote to “does not work”?", confirm: "Promote: does not work", busy: "Promoting…",
    body: "Agents will recall it as something that does not work, so they stop suggesting it. You can move it back to Testing at any time." },
  testing: { title: "Back to testing?", confirm: "Move to Testing", busy: "Moving…",
    body: "It goes back under test and counts as a retry. A verdict leaves what agents recall; its history stays." },
  retired: { title: "Retire this entry?", confirm: "Retire entry", busy: "Retiring…",
    body: "It stops being tested and leaves what agents recall. Its history stays, and it can come back to Testing." },
  use: { title: "Record a use", confirm: "Record use", busy: "Recording…",
    body: "Say where this verdict was applied. A use within 30 days of the promotion counts toward the reuse share." },
};

// Parameters: the move to make, the entry, the Space, and what to do once it is saved.
// What it does: asks for the note of a promotion, retry or retirement, or where a verdict
// was used, then sends the one command for that move at the entry's current revision.
// Output: a dialog.
function ActionDialog({ action, entry, spaceId, onClose, onDone }: {
  action: Action; entry: LabEntry; spaceId: string; onClose: () => void; onDone: (entry: LabEntry) => void;
}) {
  const copy = ACTIONS[action];
  const data = useSpace();
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useFeedback({ error, loading: busy ? copy.busy : null, clearError: () => setError("") });
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (action === "use" && !text.trim()) { setError("Say where it was used."); return; }
    setBusy(true);
    const base = { expected_revision: entry.revision };
    try {
      onDone(action === "use"
        ? await data.command<LabEntry>(`spaces/${spaceId}/systems/entries/${entry.id}/uses`, { ...base, used_in: text.trim() })
        : action === "validated" || action === "invalidated"
          ? await data.command<LabEntry>(`spaces/${spaceId}/systems/entries/${entry.id}/promote`, { ...base, verdict: action, note: text.trim() })
          : await data.command<LabEntry>(`spaces/${spaceId}/systems/entries/${entry.id}/demote`, { ...base, status: action, note: text.trim() }));
    } catch (e) {
      setError(message(e));
      setBusy(false);
    }
  }
  return (
    <Modal title={copy.title} onClose={() => !busy && onClose()}>
      <form className={`sp-modal-content ${styles.form}`} onSubmit={submit} noValidate>
        <p className="sp-modal-subject">{entry.name}</p>
        <p className="sp-modal-lead">{copy.body}</p>
        <label>
          <span>{action === "use" ? "Used in" : "Note for this revision"}{action !== "use" && <small> optional</small>}</span>
          {action === "use"
            ? <input data-autofocus value={text} maxLength={500} onChange={(e) => setText(e.target.value)} placeholder="Launch thread, October newsletter…" aria-invalid={!!error && !text.trim()} />
            : <textarea data-autofocus rows={3} maxLength={2000} value={text} onChange={(e) => setText(e.target.value)}
                placeholder={action === "testing" ? "Replies dropped after cohort 6." : "Measured over three cohorts."} />}
        </label>
        {error && <p className={styles.formError} role="alert">{error}</p>}
        <div className="sp-modal-footer">
          <button type="button" onClick={onClose} disabled={busy}>Cancel</button>
          <button className="sp-primary" type="submit" disabled={busy}>{busy ? copy.busy : copy.confirm}</button>
        </div>
      </form>
    </Modal>
  );
}

// Parameters: the open entry and the Space's ID.
// What it does: reads the entry's revisions and shows what changed in each, newest first,
// with its note, author and time. It reads again whenever the entry gets a new revision.
// Output: a section.
function Timeline({ entry, spaceId }: { entry: LabEntry; spaceId: string | null }) {
  const [read, setRead] = useState<{ revisions: LabRevision[] | null; error: string }>({ revisions: null, error: "" });
  useEffect(() => {
    if (!spaceId) return;
    setRead({ revisions: null, error: "" });
    // Every write to the entry refreshes this read through the shared cache.
    return watchApi<{ revisions: LabRevision[] }>(`spaces/${spaceId}/systems/entries/${entry.id}/history`,
      (value) => setRead({ revisions: value.revisions, error: "" }), (e) => setRead((current) => ({ ...current, error: message(e) })));
  }, [spaceId, entry.id]);
  const revisions = read.revisions;
  const error = read.error;
  const steps = revisions ? entryTimeline(revisions) : [];
  return (
    <section className={styles.panelSection} aria-labelledby="entry-history-title">
      <h3 id="entry-history-title">History</h3>
      {revisions === null ? (
        <p className={styles.muted} role="status">{error ? "History is unavailable right now." : "Loading history…"}</p>
      ) : (
        <ol className={styles.timeline}>
          {steps.map((step) => (
            <li key={step.revision} data-kind={step.events[0]?.kind ?? "edited"}>
              <span className={styles.timelineDot} aria-hidden="true" />
              <div>
                {step.events.length ? step.events.map((event, index) => <p key={index}>{event.text}</p>) : <p>Saved</p>}
                {step.note && <blockquote>{step.note}</blockquote>}
                <small>
                  {authorName(step.author, step.agent)} · <time dateTime={step.at}>{dayLabel(step.at)}, {new Date(step.at).toLocaleTimeString("en", { hour: "2-digit", minute: "2-digit" })}</time> · revision {step.revision}
                </small>
              </div>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

