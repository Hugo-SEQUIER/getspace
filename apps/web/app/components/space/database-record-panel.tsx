import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type KeyboardEvent, type Ref, type RefObject, type TextareaHTMLAttributes } from "react";
import { fieldTypes, optionColor, type DatabaseField, type DatabaseRecord, type FieldValue, type WorkspaceDatabase } from "../../../lib/space/databases";
import { message } from "../../../lib/client";
import { Icon } from "./ui-icon";
import { useConfirm } from "./ui";
import { useFeedback } from "../notifications";

export function FieldInput({
  field,
  value,
  onChange,
  label,
  id,
  autoFocus,
  onBlur,
  onKeyDown,
}: {
  field: DatabaseField;
  value: FieldValue;
  onChange: (value: FieldValue) => void;
  label: string;
  id?: string;
  autoFocus?: boolean;
  onBlur?: () => void;
  onKeyDown?: (
    event: KeyboardEvent<HTMLInputElement | HTMLSelectElement>,
  ) => void;
}) {
  const common = { "aria-label": label, id, autoFocus, onBlur, onKeyDown };
  if (field.type === "select")
    return (
      <select
        {...common}
        value={String(value ?? "")}
        onChange={(e) => onChange(e.target.value || null)}
      >
        <option value="">No {field.name.toLowerCase()}</option>
        {field.options?.map((option) => (
          <option key={option} value={option}>{option}</option>
        ))}
      </select>
    );
  if (field.type === "checkbox")
    return (
      <input
        {...common}
        type="checkbox"
        checked={value === true}
        onChange={(e) => onChange(e.target.checked)}
      />
    );
  return (
    <input
      {...common}
      type={field.type === "phone" ? "tel" : field.type}
      step={field.type === "number" ? "any" : undefined}
      maxLength={2000}
      value={value === null ? "" : String(value)}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}

// A textarea that grows with its text, so long names and values wrap instead of scrolling
// out of view. It re-measures when its width changes, such as when the panel expands.
function GrowingTextarea({ ref, value, ...props }: Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, "value"> & {
  ref?: Ref<HTMLTextAreaElement>;
  value: string;
}) {
  const own = useRef<HTMLTextAreaElement>(null);
  const fit = () => {
    const element = own.current;
    if (!element) return;
    element.style.height = "auto";
    element.style.height = `${element.scrollHeight}px`;
  };
  useLayoutEffect(fit, [value]);
  useEffect(() => {
    const element = own.current;
    if (!element || typeof ResizeObserver === "undefined") return;
    let width = element.clientWidth;
    const observer = new ResizeObserver(() => {
      if (element.clientWidth !== width) { width = element.clientWidth; fit(); }
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  return <textarea {...props} rows={1} value={value} ref={(element) => {
    own.current = element;
    if (typeof ref === "function") ref(element);
    else if (ref) ref.current = element;
  }} />;
}

// "Oct 4, 2026", or "" when the record has no usable timestamp.
const longDate = (value?: string | null) => {
  const date = new Date(value ?? "");
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleDateString("en", { month: "short", day: "numeric", year: "numeric" });
};

// Text properties wrap in the panel; every other type keeps the shared single-line control.
function PropertyInput({ field, value, onChange, id, onEnter }: {
  field: DatabaseField;
  value: FieldValue;
  onChange: (value: FieldValue) => void;
  id: string;
  onEnter: (event: KeyboardEvent<HTMLTextAreaElement>) => void;
}) {
  if (field.type === "text")
    return <GrowingTextarea aria-label={field.name} id={id} maxLength={2000} placeholder="Empty"
      value={value === null ? "" : String(value)} onChange={(event) => onChange(event.target.value)}
      onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) onEnter(event); }} />;
  return <FieldInput field={field} id={id} label={field.name} value={value} onChange={onChange} />;
}

export function RecordPanel({ database, record, initialValues, canWrite, expanded, setExpanded, beforeClose, save, remove, close }: {
  database: WorkspaceDatabase;
  record?: DatabaseRecord;
  initialValues: Record<string, FieldValue>;
  canWrite: boolean;
  expanded: boolean;
  setExpanded: (expanded: boolean) => void;
  beforeClose: RefObject<(() => Promise<boolean>) | null>;
  save: (record: DatabaseRecord, original?: DatabaseRecord) => Promise<DatabaseRecord>;
  remove: () => Promise<void>;
  close: () => void;
}) {
  const confirm = useConfirm();
  const itemId = useRef(record?.id ?? crypto.randomUUID());
  const [original, setOriginal] = useState(record);
  const [name, setName] = useState(record?.name ?? "");
  const [notes, setNotes] = useState(record?.notes ?? "");
  const [values, setValues] = useState<Record<string, FieldValue>>(record?.values ?? initialValues);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useFeedback({ error });
  const submitting = useRef(false);
  const nameInput = useRef<HTMLTextAreaElement>(null);
  const dirty = name !== (original?.name ?? "") || notes !== (original?.notes ?? "") ||
    JSON.stringify(values) !== JSON.stringify(original?.values ?? initialValues);
  useEffect(() => {
    const previousFocus = document.activeElement;
    nameInput.current?.focus();
    return () => {
      if (previousFocus instanceof HTMLElement && previousFocus.isConnected) previousFocus.focus({ preventScroll: true });
    };
  }, []);
  useEffect(() => {
    beforeClose.current = async () => !submitting.current && (!dirty || await confirm({ title: "Discard changes?", message: "Your unsaved record changes will be lost.", confirmLabel: "Discard changes" }));
    return () => { beforeClose.current = null; };
  }, [beforeClose, dirty, confirm]);
  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);
  // Enter saves from the name and text properties, as it did when they were single-line inputs.
  function saveOnEnter(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.nativeEvent.isComposing) return;
    event.preventDefault();
    if (name.trim() && (!original || dirty)) event.currentTarget.form?.requestSubmit();
  }
  async function dismiss() {
    if (await beforeClose.current?.() !== false) close();
  }
  return (
    <aside className={`sp-db-record-panel${expanded ? " is-expanded" : ""}`} aria-label={original ? "Record details" : "New record"} aria-busy={busy}
      onKeyDown={(event) => {
        if (event.key === "Escape" && !(event.target instanceof HTMLSelectElement)) {
          event.preventDefault(); dismiss();
        }
      }}>
      <div className="sp-db-panel-head">
        <span className="sp-db-panel-crumb" title={database.name}><Icon name="database" size={14} /><span>{database.name}</span></span>
        <h2 className="sr-only">{original ? "Record details" : "New record"}</h2>
        <span className="sp-db-panel-tools">
          <button type="button" className="sp-icon-button sp-db-panel-expand" aria-label="Expand record"
            aria-pressed={expanded} title={expanded ? "Show database" : "Expand"} onClick={() => setExpanded(!expanded)}><Icon name="fit" size={16} /></button>
          <button type="button" className="sp-icon-button" aria-label="Close record panel" disabled={busy} onClick={dismiss}><Icon name="close" size={16} /></button>
        </span>
      </div>
      <form onSubmit={async (event) => {
        event.preventDefault();
        if (submitting.current || !canWrite) return;
        submitting.current = true; setBusy(true); setError("");
        try {
          const saved = await save({
            id: itemId.current, name, notes, values,
            createdAt: original ? original.createdAt : new Date().toISOString(),
            updatedAt: new Date().toISOString(),
          }, original);
          setOriginal(saved); setName(saved.name); setNotes(saved.notes); setValues(saved.values);
        } catch (cause) { setError(message(cause)); }
        finally { submitting.current = false; setBusy(false); }
      }}>
        {!canWrite && <p className="sp-db-form-note">View-only access. You can browse records but cannot change them.</p>}
        <fieldset className="sp-db-panel-fields" disabled={busy || !canWrite}>
          {/* The name is one line of data that only wraps on screen: Enter saves, and pasted breaks become spaces. */}
          <GrowingTextarea ref={nameInput} aria-label="Record name" className="sp-db-record-title" required maxLength={200}
            placeholder="Untitled" value={name} onChange={(event) => setName(event.target.value.replace(/\s*[\r\n]+\s*/g, " "))}
            onKeyDown={(event) => { if (event.key === "Enter") saveOnEnter(event); }} />
          {original && <p className="sp-db-record-meta">
            {[["Created", original.createdAt], ["Edited", original.updatedAt]].filter(([, at]) => longDate(at)).map(([label, at], index) =>
              <span key={label}>{index > 0 && " · "}{label} <time dateTime={at ?? undefined}>{longDate(at)}</time></span>)}
          </p>}
          <div className="sp-db-record-properties">
            {database.fields.map((field) => {
              const inputId = `${itemId.current}-${field.id}`;
              const value = values[field.id] ?? null;
              const empty = value === null || value === "";
              const color = field.type === "select" && !empty ? optionColor(field, value) : undefined;
              return <div className="sp-db-prop-row" key={field.id}>
                <label className="sp-db-prop-label" htmlFor={inputId} title={field.name}>
                  <span className="sp-db-property-symbol" aria-hidden="true">{fieldTypes.find((item) => item.type === field.type)?.symbol}</span>
                  <span className="sp-db-prop-name">{field.name}</span>
                </label>
                <div className={`sp-db-prop-value is-${field.type}${color ? " has-option" : ""}${empty ? " is-empty" : ""}`}
                  title={field.type === "select" && !empty ? String(value) : undefined}
                  style={color ? { "--sp-option-color": color } as CSSProperties : undefined}>
                  <PropertyInput field={field} id={inputId} value={value} onEnter={saveOnEnter}
                    onChange={(next) => setValues((old) => ({ ...old, [field.id]: next }))} />
                </div>
              </div>;
            })}
          </div>
          <div className="sp-db-panel-notes">
            {/* Named apart from properties: databases often have their own Notes column. */}
            <label htmlFor={`${itemId.current}-notes`}>Record notes</label>
            <GrowingTextarea id={`${itemId.current}-notes`} aria-label="Record notes" value={notes} maxLength={20000}
              placeholder="Add context, links, or next steps…" onChange={(event) => setNotes(event.target.value)} />
          </div>
        </fieldset>
        <div className="sp-db-panel-footer">
          <span className="sp-db-form-note">{dirty ? "Unsaved changes" : original ? "Saved record" : "Create a record without leaving the database."}</span>
          <div className="sp-actions">
            {original && canWrite && <button type="button" className="text-button sp-danger-text" aria-label="Delete record" disabled={busy} onClick={async () => {
              if (submitting.current || !(await confirm({ title: "Delete record?", message: `Delete “${original.name}” from this database? This cannot be undone.`, confirmLabel: "Delete record" }))) return;
              submitting.current = true; setBusy(true); setError("");
              try { await remove(); } catch (cause) { setError(message(cause)); }
              finally { submitting.current = false; setBusy(false); }
            }}>Delete</button>}
            <button type="button" className="text-button" disabled={busy} onClick={dismiss}>{original ? "Close" : "Cancel"}</button>
            {canWrite && <button className="sp-primary" aria-label={original ? "Save record" : "Create record"} disabled={busy || !name.trim() || (!!original && !dirty)}>{original ? "Save" : "Create"}</button>}
          </div>
        </div>
      </form>
    </aside>
  );
}
