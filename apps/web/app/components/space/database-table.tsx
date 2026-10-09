import { useEffect, useId, useRef, useState } from "react";
import { cellLink, fieldTypes, fieldValue, optionColor, type DatabaseField, type DatabaseRecord, type FieldValue, type WorkspaceDatabase } from "../../../lib/space/databases";
import { message } from "../../../lib/client";
import { Avatar } from "./ui";
import { Icon } from "./ui-icon";
import { FieldInput } from "./database-record-panel";
import { InlineRecordDraft } from "./database-inline-draft";
import { useFeedback } from "../notifications";

function displayValue(field: DatabaseField, value: FieldValue) {
  if (value === null || value === undefined || value === "") return "-";
  if (field.type === "checkbox") return value ? "Checked" : "Unchecked";
  if (field.type === "number")
    return Number(value).toLocaleString("en", { maximumFractionDigits: 6 });
  if (field.type === "date")
    return new Date(`${value}T00:00:00`).toLocaleDateString("en", {
      month: "short",
      day: "numeric",
      year: "numeric",
    });
  return String(value);
}

export function RecordValue({
  field,
  value,
}: {
  field: DatabaseField;
  value: FieldValue;
}) {
  if (field.type === "select" && value) return (
    <span className="sp-db-option" style={{ backgroundColor: `${optionColor(field, value)}16` }}>
      <i aria-hidden="true" style={{ backgroundColor: optionColor(field, value) }} /><span>{String(value)}</span>
    </span>
  );
  const isPerson =
    field.type === "text" &&
    /^(contact|owner|assignee|person)$/i.test(field.name.trim());
  return isPerson && typeof value === "string" && value.trim() ? (
    <span className="sp-db-person">
      <Avatar name={value} small />
      <span>{value}</span>
    </span>
  ) : (
    displayValue(field, value)
  );
}

function EditableCell({
  field,
  record,
  save,
  readOnly = false,
}: {
  field: DatabaseField;
  record: DatabaseRecord;
  save: (value: FieldValue) => Promise<void>;
  readOnly?: boolean;
}) {
  const [pending, setPending] = useState<{ value: FieldValue } | null>(null);
  const value = pending ? pending.value : (record.values[field.id] ?? null);
  const href = cellLink(field, value);
  const saveVersion = useRef(0);
  const [editing, setEditing] = useState(false),
    [draft, setDraft] = useState(value),
    [error, setError] = useState("");
  const [saveError, setSaveError] = useState("");
  useFeedback({ error: saveError });
  const finished = useRef(false);
  const trigger = useRef<HTMLButtonElement>(null);
  function start() {
    setDraft(value);
    setError("");
    finished.current = false;
    setEditing(true);
  }
  async function persist(next: FieldValue) {
    const version = ++saveVersion.current;
    setPending({ value: next });
    setError("");
    setSaveError("");
    try {
      await save(next);
    } catch (e) {
      if (saveVersion.current === version) setSaveError(message(e));
    } finally {
      if (saveVersion.current === version) setPending(null);
    }
  }
  function commit(next = draft, restoreFocus = false) {
    if (finished.current) return;
    try {
      const parsed = fieldValue(field, next);
      finished.current = true;
      setEditing(false);
      setError("");
      if (restoreFocus) requestAnimationFrame(() => trigger.current?.focus());
      if (parsed !== value) void persist(parsed);
    } catch (e) {
      finished.current = false;
      setError(message(e));
    }
  }
  if (readOnly) return <div className="sp-db-readonly-cell"><RecordValue field={field} value={value} /></div>;
  if (field.type === "checkbox")
    return (
      <div className="sp-db-editable-cell" aria-busy={!!pending}>
        <input
          type="checkbox"
          aria-label={`${field.name} for ${record.name}`}
          checked={value === true}
          onChange={(e) => void persist(e.target.checked)}
        />
      </div>
    );
  return (
    <div className="sp-db-editable-cell" aria-busy={!!pending}>
      {editing ? (
        <FieldInput
          field={field}
          value={draft}
          label={`${field.name} for ${record.name}`}
          autoFocus
          onChange={(next) => {
            setDraft(next);
            if (field.type === "select") commit(next, true);
          }}
          onBlur={() => commit()}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              commit(draft, true);
            }
            if (e.key === "Escape") {
              e.preventDefault();
              finished.current = true;
              setEditing(false);
              setError("");
              requestAnimationFrame(() => trigger.current?.focus());
            }
          }}
        />
      ) : (
        <button
          ref={trigger}
          type="button"
          aria-label={`Edit ${field.name} for ${record.name}`}
          title={href ? "Ctrl/Cmd-click to open link in a new tab; click to edit" : undefined}
          onContextMenu={(event) => {
            if (href && event.ctrlKey) {
              event.preventDefault();
              window.open(href, "_blank", "noopener,noreferrer");
            }
          }}
          onClick={(event) => {
            if (href && (event.ctrlKey || event.metaKey)) {
              event.preventDefault();
              window.open(href, "_blank", "noopener,noreferrer");
              return;
            }
            start();
          }}
          className={value === null ? "is-empty" : ""}
        >
          <RecordValue field={field} value={value} />
        </button>
      )}
      {error && (
        <span className="sp-db-cell-error" role="alert">
          {error}
        </span>
      )}
    </div>
  );
}

function RowActions({ record, canWrite, saving, duplicate, copy, remove }: {
  record: DatabaseRecord;
  canWrite: boolean;
  saving: boolean;
  duplicate: () => Promise<void>;
  copy: () => Promise<void>;
  remove: () => Promise<void>;
}) {
  const id = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const working = useRef(false);
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState("");
  useFeedback({ error });
  function position() {
    if (!trigger.current || !menu.current) return;
    const bounds = trigger.current.getBoundingClientRect();
    const height = menu.current.offsetHeight || 120;
    menu.current.style.left = `${Math.max(12, Math.min(window.innerWidth - 204, bounds.right - 192))}px`;
    menu.current.style.top = `${bounds.bottom + height + 4 < window.innerHeight - 12 ? bounds.bottom + 4 : Math.max(12, bounds.top - height - 4)}px`;
  }
  async function run(action: () => Promise<void>) {
    if (working.current) return;
    working.current = true; setBusy(true); setError("");
    try {
      await action();
      menu.current?.hidePopover();
      trigger.current?.focus({ preventScroll: true });
    } catch (cause) { setError(message(cause)); requestAnimationFrame(position); }
    finally { working.current = false; setBusy(false); }
  }
  return <div className="sp-db-row-actions" aria-busy={busy}>
    <button ref={trigger} type="button" className="sp-icon-button" aria-label={`Actions for ${record.name}`}
      aria-haspopup="menu" aria-expanded={open} popoverTarget={id} onClick={() => { setError(""); position(); }}
      onKeyDown={(event) => {
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
          event.preventDefault(); position(); menu.current?.showPopover();
        }
      }}><Icon name="more" size={15} /></button>
    <div ref={menu} id={id} popover="auto" className="sp-db-row-menu" role="menu" aria-label={`Actions for ${record.name}`}
      onToggle={() => {
        const isOpen = menu.current?.matches(":popover-open") ?? false;
        setOpen(isOpen);
        if (isOpen) {
          position();
          requestAnimationFrame(() => menu.current?.querySelector<HTMLButtonElement>("button:not(:disabled)")?.focus());
        }
      }}
      onKeyDown={(event) => {
        if (event.key === "Tab") { menu.current?.hidePopover(); return; }
        const buttons = [...event.currentTarget.querySelectorAll<HTMLButtonElement>("button:not(:disabled)")];
        if (!buttons.length || !["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
        event.preventDefault();
        const current = buttons.indexOf(document.activeElement as HTMLButtonElement);
        const next = event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1
          : (current + (event.key === "ArrowDown" ? 1 : -1) + buttons.length) % buttons.length;
        buttons[next]?.focus();
      }}>
      <button type="button" role="menuitem" disabled={!canWrite || saving || busy} onClick={() => void run(duplicate)}><Icon name="copy" size={14} />Duplicate row</button>
      <button type="button" role="menuitem" disabled={busy} onClick={() => void run(copy)}><Icon name="copy" size={14} />Copy row</button>
      <button type="button" role="menuitem" className="sp-danger-text" disabled={!canWrite || saving || busy} onClick={() => void run(remove)}><Icon name="trash" size={14} />Delete row</button>
    </div>
  </div>;
}

export function SelectedRowActions({ count, canWrite, saving, clear, duplicate, copy, remove }: {
  count: number;
  canWrite: boolean;
  saving: boolean;
  clear: () => void;
  duplicate: () => Promise<void>;
  copy: () => Promise<void>;
  remove: () => Promise<void>;
}) {
  const working = useRef(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useFeedback({ error });
  async function run(action: () => Promise<void>) {
    if (working.current) return;
    working.current = true; setBusy(true); setError("");
    try { await action(); }
    catch (cause) { setError(message(cause)); }
    finally { working.current = false; setBusy(false); }
  }
  return <div className="sp-db-selection-bar" role="group" aria-label="Selected record actions" aria-busy={busy}>
    <span>{count} selected</span>
    <div className="sp-actions">
      <button type="button" className="text-button" aria-label="Duplicate selected" disabled={!canWrite || saving || busy} onClick={() => void run(duplicate)}><Icon name="copy" size={14} />Duplicate</button>
      <button type="button" className="text-button" aria-label="Copy selected" disabled={busy} onClick={() => void run(copy)}><Icon name="copy" size={14} />Copy</button>
      <button type="button" className="text-button sp-danger-text" aria-label="Delete selected" disabled={!canWrite || saving || busy} onClick={() => void run(remove)}><Icon name="trash" size={14} />Delete</button>
      <button type="button" className="text-button" aria-label="Clear selection" disabled={busy} onClick={clear}>Clear</button>
    </div>
  </div>;
}

export function DatabaseTable({ database, rows, canWrite, saving, selected, select, property, open, save, drafting, setDrafting, create, duplicate, copy, remove }: {
  database: WorkspaceDatabase;
  rows: readonly DatabaseRecord[];
  canWrite: boolean;
  saving: boolean;
  selected: ReadonlySet<string>;
  select: (ids: string[], checked: boolean) => void;
  property: (field: DatabaseField | "new") => void;
  open: (record: DatabaseRecord) => void;
  save: (record: DatabaseRecord, field: DatabaseField, value: FieldValue) => Promise<void>;
  drafting: boolean;
  setDrafting: (drafting: boolean) => void;
  create: (id: string, name: string) => Promise<void>;
  duplicate: (record: DatabaseRecord) => Promise<void>;
  copy: (ids: string[]) => Promise<void>;
  remove: (records: DatabaseRecord[]) => Promise<void>;
}) {
  const all = useRef<HTMLInputElement>(null);
  const wrap = useRef<HTMLDivElement>(null);
  // After Tab, continue in the new record's first property, as in a spreadsheet.
  function editFirstCell(id: string) {
    requestAnimationFrame(() => wrap.current
      ?.querySelector<HTMLButtonElement>(`tr[data-record-id="${CSS.escape(id)}"] .sp-db-editable-cell button`)
      ?.click());
  }
  const selectedOnPage = rows.filter((row) => selected.has(row.id)).length;
  useEffect(() => {
    if (all.current) all.current.indeterminate = selectedOnPage > 0 && selectedOnPage < rows.length;
  }, [selectedOnPage, rows.length]);
  return <div ref={wrap} className="sp-db-table-wrap" tabIndex={0} aria-label="Database table, scroll horizontally for more properties">
    <table className="sp-db-table">
      <thead><tr>
        <th className="sp-db-selection-column"><input ref={all} type="checkbox" aria-label="Select all records on this page"
          disabled={!rows.length} checked={rows.length > 0 && selectedOnPage === rows.length}
          onChange={(event) => select(rows.map((row) => row.id), event.target.checked)} /></th>
        <th className="sp-db-name-column"><span className="sp-db-property-symbol">Aa</span>Name</th>
        {database.fields.map((field) => <th key={field.id}>
          <button aria-label={`Edit property ${field.name}`} disabled={!canWrite} onClick={() => property(field)}>
            <span className="sp-db-property-symbol">{fieldTypes.find((item) => item.type === field.type)?.symbol}</span>
            {field.name}<Icon name="down" size={10} />
          </button>
        </th>)}
        <th><button aria-label="Add property" disabled={!canWrite} onClick={() => property("new")}><Icon name="plus" size={14} /><span>Add property</span></button></th>
      </tr></thead>
      <tbody>{rows.map((row) => <tr key={row.id} data-record-id={row.id} className={selected.has(row.id) ? "is-selected" : undefined}>
        <td className="sp-db-selection-column"><input type="checkbox" aria-label={`Select ${row.name}`} checked={selected.has(row.id)}
          onChange={(event) => select([row.id], event.target.checked)} /></td>
        <td className="sp-db-name-column"><div className="sp-db-name-cell">
          <button className="sp-db-record-name" onClick={() => open(row)}>
            <Avatar name={row.name} small kind="organization" /><span className="sp-db-record-label" title={row.name}>{row.name}</span><Icon name="external" size={12} />
          </button>
          <RowActions record={row} canWrite={canWrite} saving={saving} duplicate={() => duplicate(row)} copy={() => copy([row.id])} remove={() => remove([row])} />
        </div></td>
        {database.fields.map((field) => <td key={field.id}><EditableCell field={field} record={row} readOnly={!canWrite} save={(value) => save(row, field, value)} /></td>)}
        <td />
      </tr>)}</tbody>
      {drafting && canWrite && <tfoot><tr className="sp-db-draft-row">
        <td className="sp-db-selection-column" aria-hidden="true"><Icon name="plus" size={14} /></td>
        <td className="sp-db-name-column">
          <InlineRecordDraft create={create} close={() => setDrafting(false)} afterTab={editFirstCell} hint={false} />
        </td>
        <td className="sp-db-draft-hint-cell" colSpan={database.fields.length + 1}>
          <span aria-hidden="true">Enter to add another · Tab to fill in details · Esc to cancel</span>
        </td>
      </tr></tfoot>}
    </table>
    {!drafting && <button className="sp-db-add-row" disabled={!canWrite} onClick={() => setDrafting(true)}><Icon name="plus" size={14} />New record</button>}
  </div>;
}
