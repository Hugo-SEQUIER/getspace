
import { useEffect, useState } from "react";
import type { MemoryAssignment, MemoryVector, OrganizationItem } from "../../../lib/space/types";
import { message } from "../../../lib/client";
import { useFeedback } from "../notifications";
import { useSpace } from "./provider";
import { Modal } from "./ui";

export function OrganizationCatalog({ onClose }: { onClose: () => void }) {
  const data = useSpace();
  useEffect(() => data.watchItems("category", true), [data.watchItems]);
  const [editing, setEditing] = useState<OrganizationItem>();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [newId, setNewId] = useState(() => crypto.randomUUID());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useFeedback({ error: error || data.organizationError, loading: busy ? "Saving category…" : !data.organizationReady && !data.organizationError ? "Loading organization…" : null, clearError: () => setError("") });
  function clear() {
    setEditing(undefined); setName(""); setDescription(""); setNewId(crypto.randomUUID());
  }
  if (!data.organizationReady) return <Modal title="Organize memory" onClose={onClose}>
    <div className="sp-modal-content">
      {data.organizationError && <button onClick={data.retryOrganization}>Try again</button>}
    </div>
  </Modal>;
  return <Modal title="Organize memory" onClose={onClose}>
    <div className="sp-modal-content">
      <p><strong>Categories describe a topic</strong>, such as Engineering or Finance. A memory can have several categories.</p>
      <p>{data.provider?.classification_provider === "typesafe"
        ? "New and edited memories are classified automatically. Existing memories can be classified from their details."
        : "Manual organization is available. Automatic classification needs to be enabled by your Space operator."}</p>
      <ul className="sp-organization-list">{data.organizationItems.filter(i => i.kind === "category").map(item =>
        <li key={item.id}>
          <div><strong>{item.name}{item.deleted ? " (archived)" : ""}</strong><p>{item.description}</p></div>
          <div className="sp-actions">
            {!item.deleted && <button disabled={busy || !data.canWrite} onClick={() => {
              setEditing(item); setName(item.name); setDescription(item.description);
            }}>Edit</button>}
            <button disabled={busy || !data.canWrite} onClick={async () => {
              setBusy(true); setError("");
              try { await data.archiveOrganizationItem(item, !item.deleted); if (editing?.id === item.id) clear(); }
              catch (e) { setError(message(e)); } finally { setBusy(false); }
            }}>{item.deleted ? "Restore" : "Archive"}</button>
          </div>
        </li>)}</ul>
      {!data.organizationItems.some(i => i.kind === "category") && <p>No categories yet.</p>}
      <form onSubmit={async e => {
        e.preventDefault(); if (busy) return; setBusy(true); setError("");
        try { await data.saveOrganizationItem({ ...editing, id: editing?.id ?? newId, kind: "category", name: name.trim(), description }); clear(); }
        catch (e) { setError(message(e)); } finally { setBusy(false); }
      }}>
        <label className="sp-field">Name<input required maxLength={200} value={name} onChange={e => setName(e.target.value)} /></label>
        <label className="sp-field">Description<textarea maxLength={1000} value={description} onChange={e => setDescription(e.target.value)} placeholder="What belongs here? Include examples or exclusions." /></label>
        <div className="sp-modal-footer">
          {editing && <button type="button" disabled={busy} onClick={clear}>Cancel</button>}
          <button className="sp-primary" disabled={busy || !data.canWrite || !name.trim()}>{editing ? "Save changes" : "Create category"}</button>
        </div>
      </form>
    </div>
  </Modal>;
}

export function MemoryOrganization({ memory, onUpdated }: { memory: MemoryVector; onUpdated: () => void }) {
  const data = useSpace();
  useEffect(() => data.watchItems("category", true), [data.watchItems]);
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useFeedback({ error: error || data.organizationError, loading: busy ? "Updating memory organization…" : !data.organizationReady && !data.organizationError ? "Loading organization…" : null, clearError: () => setError("") });
  const current = memory.classification;
  const [draft, setDraft] = useState<MemoryAssignment>({ scope: "unassigned", category_ids: [], project_ids: [], section_ids: [] });
  const active = data.organizationItems.filter(i => !i.deleted);
  const projects = data.projects;
  const selected = (field: "category_ids" | "project_ids") => {
    const items = field === "project_ids" ? projects : data.organizationItems;
    return (current?.[field] ?? []).map(id => {
      const item = items.find(i => i.id === id);
      return item ? `${item.name}${item.deleted ? " (archived)" : ""}` : data.organizationReady ? "Unavailable item" : "Loading…";
    }).join(", ");
  };
  async function save(assignment?: MemoryAssignment) {
    setBusy(true); setError("");
    try { await data.classifyMemory(memory, assignment); setEditing(false); onUpdated(); }
    catch (e) { setError(message(e)); } finally { setBusy(false); }
  }
  return <section className="sp-memory-organization">
    <h3>Organization</h3>
    {data.organizationError && <button onClick={data.retryOrganization}>Retry organization</button>}
    <dl className="sp-properties">
      <div><dt>Scope</dt><dd>{current?.scope === "global" ? "Global · across projects" : current?.scope === "project" ? "Project" : "Unassigned"}</dd></div>
      <div><dt>Categories</dt><dd>{selected("category_ids") || "Uncategorized"}</dd></div>
      <div><dt>Projects</dt><dd>{selected("project_ids") || "Unassigned"}</dd></div>
    </dl>
    {current?.method === "manual" && <p>Manual assignment · kept during automatic updates.</p>}
    {current?.method === "typesafe" && <p>Automatically classified. You can correct any assignment.</p>}
    {current?.reason === "unavailable" && <p>Automatic classification was unavailable. Your memory is saved.</p>}
    {current?.reason === "catalog_limit" && <p>The catalog exceeds the automatic classification limit. Use a manual assignment.</p>}
    {current?.reason === "input_limit" && <p>The catalog descriptions and memory exceed the automatic classification size limit.</p>}
    <div className="sp-actions">
      <button disabled={busy || !data.canWrite || !data.organizationReady} onClick={() => {
        setDraft({ scope: current?.scope ?? "unassigned",
          category_ids: (current?.category_ids ?? []).filter(id => active.some(i => i.id === id)),
          project_ids: (current?.project_ids ?? []).filter(id => projects.some(p => p.id === id)),
          section_ids: current?.section_ids ?? [],
        }); setEditing(true);
      }}>Edit organization</button>
      {data.provider?.classification_provider === "typesafe" && current?.method !== "manual" &&
        <button disabled={busy || !data.canWrite} onClick={() => void save()}>Classify now</button>}
    </div>
    {editing && <Modal title="Memory organization" onClose={() => setEditing(false)}>
      <form className="sp-modal-content" onSubmit={e => { e.preventDefault(); if (!busy) void save(draft); }}>
        <p className="sp-modal-subject">{memory.title}</p>
        <label className="sp-field">Scope<select value={draft.scope} onChange={e => setDraft({ ...draft, scope: e.target.value as MemoryAssignment["scope"], project_ids: [] })}>
          <option value="unassigned">Unassigned</option><option value="global">Global · applies across projects</option><option value="project">Specific projects</option>
        </select></label>
        {(["category", "project"] as const).filter(kind => kind !== "project" || draft.scope === "project").map(kind => {
          const field = `${kind}_ids` as const;
          const items = kind === "project" ? projects : active.filter(i => i.kind === kind);
          return <fieldset key={kind}><legend>{kind === "category" ? "Categories" : "Projects"}</legend>
            {!items.length && <p>None created yet.</p>}
            {items.map(item => <label key={item.id} className="sp-checkbox-label">
              <input type="checkbox" checked={draft[field].includes(item.id)} onChange={e => setDraft({ ...draft,
                [field]: e.target.checked ? [...draft[field], item.id] : draft[field].filter(id => id !== item.id),
              })} />{item.name}</label>)}
          </fieldset>;
        })}
        <p className="sp-modal-hint">Saving pins these assignments during automatic updates.</p>
        <div className="sp-modal-footer">
          {current?.method === "manual" && data.provider?.classification_provider === "typesafe" &&
            <button type="button" className="sp-text-button" disabled={busy} onClick={() => void save()}>Use automatic classification</button>}
          <div className="sp-actions">
            <button type="button" disabled={busy} onClick={() => setEditing(false)}>Cancel</button>
            <button className="sp-primary" disabled={busy || (draft.scope === "project" && !draft.project_ids.length)}>Save assignment</button>
          </div>
        </div>
      </form>
    </Modal>}
  </section>;
}
