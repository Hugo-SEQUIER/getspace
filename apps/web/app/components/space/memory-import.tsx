
import { useEffect, useRef, useState } from "react";
import { useFeedback } from "@web/app/components/notifications";
import { api, message } from "../../../lib/client";
import { importDestinations, importFailure, prepareMemoraExport, type ImportPreview, type MemoryImportJob } from "../../../lib/space/memory-import";
import { useSpace } from "./provider";
import { Modal } from "./ui";
import { Icon } from "./ui-icon";
import styles from "./memory-import.module.css";

export function MemoryImport() {
  const data = useSpace();
  const spaceId = data.space?.id;
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [fileName, setFileName] = useState("");
  const [sourceId, setSourceId] = useState("memora");
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [checked, setChecked] = useState<{ previous: number } | null>(null);
  const [jobs, setJobs] = useState<MemoryImportJob[]>([]);
  const [submitted, setSubmitted] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [pollError, setPollError] = useState("");
  const [historyLoading, setHistoryLoading] = useState(false);
  const [pollVersion, setPollVersion] = useState(0);
  const ids = useRef(new Map<string, string>());
  const known = useRef(new Map<string, number>());
  const knownStates = useRef(new Map<string, string>());
  const refresh = useRef(data.loadMemories);
  refresh.current = data.loadMemories;
  const spaces = (data.workspaces.length ? data.workspaces : data.space ? [data.space] : []).filter(s => s.role !== "viewer");
  const pending = jobs.filter(job => job.status === "queued" || job.status === "processing");
  const path = (target: string) => `spaces/${target}/memory/imports`;
  useFeedback({
    error,
    notice,
    loading: busy || (open && historyLoading ? "Loading import history…" : open && pending.length && !pollError ? `Importing memories… ${pending.reduce((total, job) => total + job.completed, 0)} of ${pending.reduce((total, job) => total + job.total, 0)}` : undefined),
    clearError: () => setError(""),
    clearNotice: () => setNotice(""),
  });
  useFeedback({ error: open && pollError ? "Import progress is temporarily unavailable. Retrying…" : undefined });

  useEffect(() => {
    setText(""); setPreview(null); setChecked(null); setJobs([]); setSubmitted(false);
    setMapping({}); setError(""); setNotice(""); setPollError(""); ids.current.clear(); known.current.clear(); knownStates.current.clear();
  }, [spaceId]);

  useEffect(() => {
    if (!spaceId) return;
    setHistoryLoading(true);
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const value = await api<{ items: MemoryImportJob[] }>(path(spaceId!));
        if (!active) return;
        setJobs(value.items); setPollError("");
        let changed = false;
        let completed = 0;
        let failed = 0;
        for (const job of value.items) {
          if (known.current.has(job.id) && known.current.get(job.id) !== job.completed) changed = true;
          const previousState = knownStates.current.get(job.id);
          if (previousState && previousState !== job.status) {
            if (job.status === "ready") completed++;
            if (job.status === "failed") failed++;
          }
          known.current.set(job.id, job.completed);
          knownStates.current.set(job.id, job.status);
        }
        if (completed) setNotice(completed === 1 ? "Memory import complete." : `${completed} memory imports complete.`);
        if (failed) setError("Memory import stopped. Open recent imports to review the problem and resume.");
        if (changed) void refresh.current();
      } catch (e) {
        if (active) setPollError(message(e));
      } finally {
        if (active) {
          setHistoryLoading(false);
          timer = setTimeout(poll, open ? 4000 : 15000);
        }
      }
    }
    void poll();
    return () => { active = false; clearTimeout(timer); };
  }, [spaceId, open, pollVersion]);

  function invalidate() { setChecked(null); ids.current.clear(); setSubmitted(false); }

  async function choose(file: File) {
    if (!spaceId) return;
    setError(""); setNotice(""); setBusy("Reading your export…"); setPreview(null); setText(""); invalidate();
    try {
      if (file.size > 25 * 1024 * 1024) throw new Error("Choose a Memora export smaller than 25 MB.");
      const content = prepareMemoraExport(await file.text());
      setBusy("Checking memory records…");
      const result = await api<ImportPreview>(`${path(spaceId)}/preview`, "POST", { text: content, source_id: sourceId });
      setText(content); setFileName(file.name); setPreview(result);
      setMapping(Object.fromEntries(result.namespaces.map(ns => [ns.namespace, result.namespaces.length === 1 ? spaceId : ""])));
    } catch (e) { setError(message(e)); }
    finally { setBusy(""); }
  }

  async function checkDestinations() {
    if (!preview || !sourceId.trim()) return;
    setBusy("Checking destination Spaces…"); setError(""); setNotice("");
    try {
      let previous = 0;
      for (const [target, namespaces] of importDestinations(preview, mapping)) {
        const result = await api<ImportPreview>(`${path(target)}/preview`, "POST", { text, source_id: sourceId, namespaces });
        if (result.errors.length) throw new Error("Resolve the record errors before importing.");
        if (result.conflict_rows.length) throw new Error(`${result.conflict_rows.length} records conflict with an earlier import in ${spaces.find(s => s.id === target)?.name ?? "the destination Space"}. Use the original export or resolve those records first.`);
        previous += result.previously_imported;
      }
      setChecked({ previous });
    } catch (e) { setError(message(e)); }
    finally { setBusy(""); }
  }

  async function start() {
    if (!preview || !checked) return;
    setBusy("Starting your import…"); setError(""); setNotice("");
    try {
      for (const [target, namespaces] of importDestinations(preview, mapping)) {
        const commandId = ids.current.get(target) ?? crypto.randomUUID();
        ids.current.set(target, commandId);
        await api<MemoryImportJob>(path(target), "POST", { text, source_id: sourceId, namespaces, command_id: commandId });
      }
      setSubmitted(true); setPollVersion(v => v + 1);
      setNotice("Memory import started. Progress is saved in each destination Space.");
    } catch (e) { setError(`${message(e)} Any imports already accepted will continue. Retry with the same choices.`); }
    finally { setBusy(""); }
  }

  async function resume(job: MemoryImportJob) {
    if (!spaceId) return;
    setError(""); setNotice(""); setBusy("Resuming import…");
    const key = `retry:${job.id}`;
    const id = ids.current.get(key) ?? crypto.randomUUID(); ids.current.set(key, id);
    try {
      await api(`${path(spaceId)}/${job.id}/retry`, "POST", { command_id: id });
      setPollVersion(v => v + 1);
      setNotice("Memory import resumed.");
    } catch (e) { setError(message(e)); }
    finally { setBusy(""); }
  }

  return <>
    <button onClick={() => setOpen(true)} disabled={data.space?.role === "viewer"}>
      <Icon name="upload" size={14} /> Import memories {pending.length > 0 && <span className={styles.badge}>{pending.length}</span>}
    </button>
    {open && <Modal title="Import memories" wide className={styles.modal} onClose={() => { if (!busy) setOpen(false); }}>
      <div className={styles.body}>
        <div className={styles.intro}>
          <span className={styles.glyph}><Icon name={submitted ? "check" : "memory"} size={24} /></span>
          <div><h3>{submitted ? "Your memories are on their way" : "Bring your memory with you"}</h3><p>{submitted ? "The import continues in the background. You can close this window and check progress in each destination Space." : "Move a Memora export into Space, preserving the original wording and metadata with each memory."}</p></div>
        </div>
        {!submitted && <>
          <ol className={styles.steps} aria-label="Import steps">
            {["Upload", "Review", "Import"].map((label, index) => <li key={label} aria-current={(checked ? 2 : preview ? 1 : 0) === index ? "step" : undefined}><span>{index + 1}</span>{label}</li>)}
          </ol>
          {!preview ? <label className={styles.dropzone}>
            <Icon name="upload" size={25} /><strong>Choose a Memora export</strong><span>Full export or memories · JSON · up to 25 MB</span>
            <input data-autofocus type="file" accept=".json,application/json" aria-label="Choose a Memora export" disabled={!!busy} onChange={e => { const file = e.target.files?.[0]; e.target.value = ""; if (file) void choose(file); }} />
          </label> : <>
            <div className={styles.file}><Icon name="document" size={19} /><span><strong>{fileName}</strong><small>{preview.valid_records} valid memories · {preview.namespaces.length} namespaces</small></span><button disabled={!!busy} onClick={() => { setPreview(null); setText(""); setError(""); invalidate(); }}>Change file</button></div>
            <label className="sp-field">Source name<input value={sourceId} maxLength={200} disabled={!!busy} onChange={e => { setSourceId(e.target.value); invalidate(); }} /><small>Keep this name when importing from the same Memora store again.</small></label>
            {preview.errors.length > 0 && <div className={styles.problems} role="alert"><strong>{preview.errors.length} records need attention</strong><ul>{preview.errors.slice(0, 10).map(problem => <li key={problem.row}>Row {problem.row}: {problem.reason}</li>)}</ul><p>Correct the export and choose it again. Nothing has been imported.</p></div>}
            <div className={styles.mappingHeading}><h4>Where should these memories live?</h4><button disabled={!!busy} onClick={() => { setMapping(Object.fromEntries(preview.namespaces.map(ns => [ns.namespace, spaceId!]))); invalidate(); }}>Use current Space for all</button></div>
            <div className={styles.namespaces}>{preview.namespaces.map(ns => <div className={styles.namespace} key={ns.namespace}><span><strong>{ns.namespace}</strong><small>{ns.records} memories</small></span><Icon name="arrow" size={15} /><select aria-label={`Destination for ${ns.namespace}`} value={mapping[ns.namespace] ?? ""} disabled={!!busy} onChange={e => { setMapping(old => ({ ...old, [ns.namespace]: e.target.value })); invalidate(); }}><option value="">Choose a Space…</option>{spaces.map(space => <option key={space.id} value={space.id}>{space.name}</option>)}</select></div>)}</div>
            <p className={styles.hint}>Everyone with access to a destination Space can read its imported memories. Namespaces remain labels; choose separate Spaces to keep access separate.</p>
            {checked && <div className={styles.confirmation}><Icon name="check" size={18} /><div><strong>Ready to import {preview.valid_records} memories</strong><p>{checked.previous > 0 ? `${checked.previous} were imported before and will be reused if unchanged. ` : ""}Original IDs, dates and metadata stay with each memory. No documents are created. Space will generate new embeddings using its configured memory provider.</p></div></div>}
          </>}
        </>}
        <section className={styles.history}>
          <h4>Recent imports in this Space</h4>
          {!jobs.length && !pollError && <p className={styles.hint}>Your import progress will appear here and stay available after you close this window.</p>}
          {jobs.map(job => <div className={styles.job} key={job.id}>
            <div><strong>{job.status === "ready" ? "Import complete" : job.status === "failed" ? "Import stopped" : job.status === "queued" ? "Waiting to continue" : "Importing memories"}</strong><span>{job.completed} / {job.total}</span></div>
            <progress value={job.completed} max={job.total} aria-label={`Imported memories: ${job.completed} of ${job.total}`} />
            {job.status === "failed" ? <div><small>{importFailure(job.error_code)}</small><button disabled={!!busy} onClick={() => void resume(job)}>Resume</button></div> : <small>{job.status === "ready" ? "Available in Memory now." : "Saved progress continues in the background. Queued work may take a few minutes."}</small>}
          </div>)}
        </section>
      </div>
      <div className="sp-modal-footer">
        <button disabled={!!busy} onClick={() => setOpen(false)}>{submitted ? "Done" : "Close"}</button>
        {submitted && <button onClick={() => { setSubmitted(false); setPreview(null); setText(""); invalidate(); }}>Import another file</button>}
        {!submitted && preview && <button className="sp-primary" disabled={!!busy || !!preview.errors.length || !sourceId.trim() || preview.namespaces.some(ns => !mapping[ns.namespace])} onClick={() => void (checked ? start() : checkDestinations())}>{checked ? `Import ${preview.valid_records} memories` : "Review import"}</button>}
      </div>
    </Modal>}
  </>;
}
