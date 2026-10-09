import { useRef, useState } from "react";
import {
  applyDatabaseField, fieldTypes, optionColor, optionColors, optionColorNames,
  selectOptionsFromRecords, type DatabaseField, type FieldType, type WorkspaceDatabase,
} from "../../../lib/space/databases";
import { message } from "../../../lib/client";
import { Modal } from "./ui";
import { Icon } from "./ui-icon";
import { useFeedback } from "../notifications";

type OptionDraft = { id: string; label: string; color: string; original?: string };

export function PropertyForm({ database, field, defaultSelect, save, close }: {
  database: WorkspaceDatabase;
  field?: DatabaseField;
  defaultSelect?: boolean;
  save: (field: DatabaseField, renames: Record<string, string>) => Promise<void>;
  close: () => void;
}) {
  const [name, setName] = useState(field?.name ?? "");
  const [type, setType] = useState<FieldType>(field?.type ?? (defaultSelect ? "select" : "text"));
  const [options, setOptions] = useState<OptionDraft[]>(() =>
    (field?.options ?? ["New", "In progress", "Done"]).map((label, index) => ({
      id: crypto.randomUUID(), label, color: field ? optionColor(field, label) : optionColors[index % optionColors.length],
      ...(field?.type === "select" ? { original: label } : {}),
    })),
  );
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useFeedback({ error });
  const submitting = useRef(false);
  const fieldId = useRef(field?.id ?? crypto.randomUUID());
  const labels = options.map((option) => option.original === option.label ? option.label : option.label.trim());
  const next: DatabaseField = {
    id: fieldId.current, name: name.trim(), type,
    ...(type === "select" ? {
      options: labels,
      optionColors: Object.fromEntries(options.map((option, index) => [labels[index], option.color])),
    } : {}),
  };
  const renames = Object.fromEntries(options.flatMap((option, index) =>
    option.original !== undefined && option.original !== labels[index] ? [[option.original, labels[index]]] : [],
  ));
  const converting = !!field && type !== field.type;
  let validationError = "";
  try { applyDatabaseField(database, next, field, renames); }
  catch (cause) { validationError = message(cause); }
  function changeType(nextType: FieldType) {
    setType(nextType);
    setError("");
    if (nextType !== "select" || type === "select") return;
    try {
      const labels = field ? selectOptionsFromRecords(database, field.id) : ["New", "In progress", "Done"];
      setOptions(labels.map((label, index) => ({
        id: crypto.randomUUID(), label, color: optionColors[index % optionColors.length], original: label,
      })));
    } catch (cause) {
      setOptions([]);
      setError(message(cause));
    }
  }
  const dismiss = () => { if (!submitting.current) close(); };
  return (
    <Modal title={field ? "Edit property" : "Add property"} onClose={dismiss} className="sp-db-property-modal">
      <form className="sp-modal-content" aria-busy={busy} onSubmit={async (event) => {
        event.preventDefault();
        if (submitting.current) return;
        submitting.current = true;
        setBusy(true);
        setError("");
        try {
          applyDatabaseField(database, next, field, renames);
          await save(next, renames);
        } catch (cause) { setError(message(cause)); }
        finally { submitting.current = false; setBusy(false); }
      }}>
        <label className="sp-field">
          Property name
          <input aria-label="Property name" autoFocus required maxLength={80} disabled={busy}
            value={name} onChange={(event) => { setName(event.target.value); setError(""); }} />
        </label>
        <label className="sp-field">
          Type
          <select aria-label="Property type" value={type} disabled={busy}
            onChange={(event) => changeType(event.target.value as FieldType)}>
            {fieldTypes.map((item) => <option key={item.type} value={item.type}>{item.symbol} {item.label}</option>)}
          </select>
        </label>
        {converting && (
          <div className="sp-db-conversion-note" role="status">
            <strong>Conversion preview</strong>
            <p>{type === "select"
              ? "Existing non-empty values become options. Empty cells stay empty."
              : "Existing values must fit the new type. Incompatible conversions are refused, not cleared."}</p>
            {field.id === database.groupBy && type !== "select" && <p>This removes the board grouping. You can choose another select property.</p>}
            <p>{validationError || `${database.records.length} records checked. Values and record IDs are preserved. Save property to apply; Cancel changes nothing.`}</p>
          </div>
        )}
        {type === "select" && (
          <fieldset className="sp-db-options-editor" disabled={busy}>
            <legend>Select options</legend>
            <p className="sp-db-form-note">{converting && field.type !== "select"
              ? "Options come from existing values. Convert first before renaming labels. You can choose colors or add options now."
              : "Rename labels, choose colors, or add options. Existing values follow renamed labels. Remove an option only when it is unused."}</p>
            {options.map((option, index) => (
              <div className="sp-db-option-editor" key={option.id}>
                <i aria-hidden="true" style={{ backgroundColor: option.color }} />
                <input aria-label={`Option ${index + 1} label`} value={option.label} maxLength={80}
                  onChange={(event) => {
                    setOptions((old) => old.map((item) => item.id === option.id ? { ...item, label: event.target.value } : item));
                    setError("");
                  }} />
                <select aria-label={`Color for ${option.label || `option ${index + 1}`}`} value={option.color}
                  onChange={(event) => setOptions((old) => old.map((item) => item.id === option.id ? { ...item, color: event.target.value } : item))}>
                  {optionColors.map((color, index) => <option key={color} value={color}>{optionColorNames[index]}</option>)}
                </select>
                <button type="button" className="sp-icon-button" aria-label={`Remove option ${option.label || index + 1}`}
                  onClick={() => { setOptions((old) => old.filter((item) => item.id !== option.id)); setError(""); }}>
                  <Icon name="close" size={14} />
                </button>
              </div>
            ))}
            <button type="button" className="text-button" disabled={options.length >= 30} onClick={() => {
              setOptions((old) => [...old, { id: crypto.randomUUID(), label: "", color: optionColors[old.length % optionColors.length] }]);
              setError("");
            }}><Icon name="plus" size={13} />Add option</button>
            <span className="sp-db-form-note">{options.length}/30 options · Colors also appear in table cells and board headings.</span>
          </fieldset>
        )}
        {validationError && field && !converting && <p className="sp-alert" role="alert">{validationError}</p>}
        <div className="sp-modal-footer">
          <button type="button" disabled={busy} onClick={dismiss}>Cancel</button>
          <button className="sp-primary" disabled={busy || !!validationError || !name.trim()}>{field ? "Save property" : "Add property"}</button>
        </div>
      </form>
    </Modal>
  );
}
