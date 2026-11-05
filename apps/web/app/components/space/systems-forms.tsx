import { useState, type FormEvent, type ReactNode } from "react";
import { api, message } from "../../../lib/client";
import {
  CADENCES, EVIDENCE, SYSTEM_STATUSES, entryInput, systemInput,
  type Cadence, type Evidence, type LabEntry, type LabEntryInput, type System, type SystemInput, type SystemStatus,
} from "../../../lib/space/systems";
import { useFeedback } from "../notifications";
import { useSpace } from "./provider";
import { Modal, useConfirm } from "./ui";
import styles from "./systems.module.css";

function Field({ label, hint, children, wide = false }: { label: string; hint?: string; children: ReactNode; wide?: boolean }) {
  return (
    <label className={wide ? styles.wideField : undefined}>
      <span>{label}{hint && <small> {hint}</small>}</span>
      {children}
    </label>
  );
}

const blankSystem: SystemInput = {
  name: "", description: "", status: "not_measured", goal_metric: "", goal_target: "", current_value: "",
  counter_metric: "", source_of_truth: "", review_cadence: null, last_review: null, next_decision: "", playbook_id: null,
};

/** Archives an item through the shared visibility route; the backend refuses a live verdict. */
async function archive<T extends System | LabEntry>(spaceId: string, item: T) {
  return api<T>(`spaces/${spaceId}/items/${item.id}/visibility`, "POST", {
    command_id: crypto.randomUUID(), expected_revision: item.revision, deleted: true,
  });
}

// Parameters: the Space, the system to edit (none to create), and what to do once saved or archived.
// What it does: edits every field of a system in one form and saves it as one revision; an
// existing system can also be archived from the footer's left.
// Output: a dialog.
export function SystemDialog({ spaceId, system, onClose, onSaved, onArchived }: {
  spaceId: string; system?: System; onClose: () => void; onSaved: (system: System) => void; onArchived?: (system: System) => void;
}) {
  const data = useSpace();
  const confirm = useConfirm();
  const [draft, setDraft] = useState<SystemInput>(system ? systemInput(system) : blankSystem);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useFeedback({ error, loading: busy ? "Saving system…" : null, clearError: () => setError("") });
  const set = <K extends keyof SystemInput>(key: K, value: SystemInput[K]) => setDraft((current) => ({ ...current, [key]: value }));
  const documents = data.resources.filter((item) => item.kind === "document" && item.recall?.category !== "memory" && item.recall?.category !== "handoff");
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!draft.name.trim()) { setError("Enter a name."); return; }
    setBusy(true);
    try {
      onSaved(await api<System>(`spaces/${spaceId}/systems`, "POST", {
        command_id: crypto.randomUUID(), ...(system ? { system_id: system.id } : {}),
        expected_revision: system?.revision ?? 0, data: { ...draft, name: draft.name.trim() },
      }));
    } catch (e) {
      setError(message(e));
      setBusy(false);
    }
  }
  async function remove() {
    if (!system || !await confirm({
      title: `Archive ${system.name}?`,
      message: "It leaves Systems with its lab, and agents no longer see it. Its entries keep their history.",
      confirmLabel: "Archive system",
    })) return;
    setBusy(true);
    try {
      onArchived?.(await archive(spaceId, system));
    } catch (e) {
      setError(message(e));
      setBusy(false);
    }
  }
  return (
    <Modal title={system ? "Edit system" : "New system"} size="wide" onClose={() => !busy && onClose()}>
      <form className={`sp-modal-content ${styles.form}`} onSubmit={submit} noValidate>
        <div className={styles.formGrid}>
          <Field label="Name" wide>
            <input data-autofocus value={draft.name} maxLength={200} placeholder="Cold email" onChange={(e) => set("name", e.target.value)} aria-invalid={!!error && !draft.name.trim()} />
          </Field>
          <Field label="What it is" hint="optional" wide>
            <textarea rows={2} value={draft.description} onChange={(e) => set("description", e.target.value)} placeholder="Outbound to product teams who shipped in the last month." />
          </Field>
          <Field label="Health">
            <select value={draft.status} onChange={(e) => set("status", e.target.value as SystemStatus)}>
              {SYSTEM_STATUSES.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
            </select>
          </Field>
          <Field label="Goal metric">
            <input value={draft.goal_metric} onChange={(e) => set("goal_metric", e.target.value)} placeholder="Reply rate" />
          </Field>
          <Field label="Current value">
            <input value={draft.current_value} onChange={(e) => set("current_value", e.target.value)} placeholder="5.1%" />
          </Field>
          <Field label="Target">
            <input value={draft.goal_target} onChange={(e) => set("goal_target", e.target.value)} placeholder="8%" />
          </Field>
          <Field label="Counter-metric" hint="what must not get worse">
            <input value={draft.counter_metric} onChange={(e) => set("counter_metric", e.target.value)} placeholder="Unsubscribe rate" />
          </Field>
          <Field label="Source of truth">
            <input value={draft.source_of_truth} onChange={(e) => set("source_of_truth", e.target.value)} placeholder="Campaign report per cohort" />
          </Field>
          <Field label="Review cadence">
            <select value={draft.review_cadence ?? ""} onChange={(e) => set("review_cadence", (e.target.value || null) as Cadence | null)}>
              <option value="">No set review</option>
              {CADENCES.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
            </select>
          </Field>
          <Field label="Last review">
            <input type="date" value={draft.last_review ?? ""} onChange={(e) => set("last_review", e.target.value || null)} />
          </Field>
          <Field label="Next decision" wide>
            <input value={draft.next_decision} onChange={(e) => set("next_decision", e.target.value)} placeholder="Decide whether cohort 6 drops the calendar ask." />
          </Field>
          <Field label="Playbook" hint="a document in this Space" wide>
            <select value={draft.playbook_id ?? ""} onChange={(e) => set("playbook_id", e.target.value || null)}>
              <option value="">No playbook</option>
              {documents.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}
            </select>
          </Field>
        </div>
        {error && <p className={styles.formError} role="alert">{error}</p>}
        <div className="sp-modal-footer">
          {system && <button type="button" className="text-button sp-danger-text" disabled={busy || system.stats.verdicts > 0}
            aria-describedby={system.stats.verdicts > 0 ? "system-archive-reason" : undefined} onClick={() => void remove()}>Archive system</button>}
          <span className={styles.footerSpacer}>
            {system && system.stats.verdicts > 0 && (
              <small id="system-archive-reason" className={styles.archiveReason}>Agents recall its verdicts. Move this system's verdicts back to Testing or retire them first.</small>
            )}
          </span>
          <button type="button" onClick={onClose} disabled={busy}>Cancel</button>
          <button className="sp-primary" type="submit" disabled={busy}>{busy ? "Saving…" : system ? "Save changes" : "Create system"}</button>
        </div>
      </form>
    </Modal>
  );
}

// Parameters: the Space, the entry to edit (none to add one), its system, the active
// systems and the area's domains, and what to do once saved or archived.
// What it does: writes a hypothesis or lesson with its domain, evidence, reasoning and
// source. A new entry starts in Testing; the note says what changed in this revision.
// Output: a dialog.
export function EntryDialog({ spaceId, entry, systemId, systems, domains, onClose, onSaved, onArchived }: {
  spaceId: string; entry?: LabEntry; systemId: string; systems: System[]; domains: string[];
  onClose: () => void; onSaved: (entry: LabEntry) => void; onArchived?: (entry: LabEntry) => void;
}) {
  const confirm = useConfirm();
  const [draft, setDraft] = useState<LabEntryInput>(entry ? entryInput(entry) : {
    name: "", system_id: systemId, domain: "", evidence: "untested", why: "", apply: "", applies_to: "", source: "", captured: null, note: "",
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useFeedback({ error, loading: busy ? "Saving entry…" : null, clearError: () => setError("") });
  const set = <K extends keyof LabEntryInput>(key: K, value: LabEntryInput[K]) => setDraft((current) => ({ ...current, [key]: value }));
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!draft.name.trim()) { setError("Write the hypothesis or lesson in one sentence."); return; }
    if (!draft.domain) { setError("Choose a domain."); return; }
    setBusy(true);
    try {
      onSaved(await api<LabEntry>(`spaces/${spaceId}/systems/entries`, "POST", {
        command_id: crypto.randomUUID(), ...(entry ? { entry_id: entry.id } : {}),
        expected_revision: entry?.revision ?? 0, data: { ...draft, name: draft.name.trim() },
      }));
    } catch (e) {
      setError(message(e));
      setBusy(false);
    }
  }
  async function remove() {
    if (!entry || !await confirm({
      title: "Archive this entry?",
      message: "It leaves the lab and its history stays. Agents never recalled it, since it is not a verdict.",
      confirmLabel: "Archive entry",
    })) return;
    setBusy(true);
    try {
      onArchived?.(await archive(spaceId, entry));
    } catch (e) {
      setError(message(e));
      setBusy(false);
    }
  }
  return (
    <Modal title={entry ? "Edit entry" : "New lab entry"} size="wide" onClose={() => !busy && onClose()}>
      <form className={`sp-modal-content ${styles.form}`} onSubmit={submit} noValidate>
        <div className={styles.formGrid}>
          <Field label="Hypothesis or lesson" hint="one sentence" wide>
            <textarea data-autofocus rows={2} maxLength={280} value={draft.name} onChange={(e) => set("name", e.target.value)}
              placeholder="A first line naming the prospect's last release gets more replies than a compliment." />
          </Field>
          <Field label="System">
            <select value={draft.system_id} onChange={(e) => set("system_id", e.target.value)}>
              {systems.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
            </select>
          </Field>
          <Field label="Domain">
            <select value={draft.domain} onChange={(e) => set("domain", e.target.value)} aria-invalid={!!error && !draft.domain}>
              <option value="">Choose a domain</option>
              {domains.map((item) => <option key={item}>{item}</option>)}
            </select>
          </Field>
          <fieldset className={`${styles.wideField} ${styles.evidenceChoice}`}>
            <legend>Evidence</legend>
            {EVIDENCE.map((item, index) => (
              <label key={item.value}>
                <input type="radio" name="evidence" value={item.value} checked={draft.evidence === item.value} onChange={() => set("evidence", item.value as Evidence)} />
                <span><small>{index + 1}</small>{item.label}</span>
              </label>
            ))}
          </fieldset>
          <Field label="Why, and the evidence" wide>
            <textarea rows={3} value={draft.why} onChange={(e) => set("why", e.target.value)} placeholder="Cohort 5 replied at 7.9% with the release line, 4.2% without." />
          </Field>
          <Field label="How to apply" wide>
            <textarea rows={2} value={draft.apply} onChange={(e) => set("apply", e.target.value)} placeholder="Quote the release name and one detail from it." />
          </Field>
          <Field label="Applies to">
            <input value={draft.applies_to} onChange={(e) => set("applies_to", e.target.value)} placeholder="First emails to product teams" />
          </Field>
          <Field label="Source" hint="needed to promote">
            <input value={draft.source} onChange={(e) => set("source", e.target.value)} placeholder="Campaign report, cohort 5" />
          </Field>
          <Field label="Captured">
            <input type="date" value={draft.captured ?? ""} onChange={(e) => set("captured", e.target.value || null)} />
          </Field>
          <Field label="Note for this revision" hint="optional">
            <input value={draft.note} maxLength={2000} onChange={(e) => set("note", e.target.value)} placeholder={entry ? "Raised to measured after cohort 5" : "Idea from the cohort 4 review"} />
          </Field>
        </div>
        {error && <p className={styles.formError} role="alert">{error}</p>}
        <div className="sp-modal-footer">
          {entry && <button type="button" className="text-button sp-danger-text" disabled={busy} onClick={() => void remove()}>Archive entry</button>}
          <span className={styles.footerSpacer} />
          <button type="button" onClick={onClose} disabled={busy}>Cancel</button>
          <button className="sp-primary" type="submit" disabled={busy}>{busy ? "Saving…" : entry ? "Save changes" : "Add to the lab"}</button>
        </div>
      </form>
    </Modal>
  );
}
