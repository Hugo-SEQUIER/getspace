
import { useEffect, useMemo, useRef, useState } from "react";
import { message } from "../../../lib/client";
import {
  databaseFromImport,
  inferColumn,
  type ImportTable,
} from "../../../lib/space/import-data";
import {
  databaseExtensions,
  documentExtensions,
  readDatabaseFile,
  readDocumentFile,
} from "../../../lib/space/import-files";
import {
  fieldTypes,
  type FieldType,
  type WorkspaceDatabase,
} from "../../../lib/space/databases";
import type { Resource } from "../../../lib/space/types";
import { useSpace } from "./provider";
import { Modal } from "./ui";
import { Icon } from "./ui-icon";
import { DocumentMarkdown } from "./document-markdown";
import { insertDocumentImage } from "../../../lib/space/document-content";
import { useFeedback } from "../notifications";

function FilePicker({
  extensions,
  busy,
  choose,
}: {
  extensions: string[];
  busy: boolean;
  choose: (file: File) => void;
}) {
  const [over, setOver] = useState(false);
  const [error, setError] = useState("");
  useFeedback({ error, clearError: () => setError("") });
  function pick(files: FileList | null) {
    if (busy || !files?.length) return;
    if (files.length !== 1) {
      setError("Choose one file at a time.");
      return;
    }
    setError("");
    choose(files[0]);
  }
  return (
    <>
      <label
        className={`sp-import-dropzone ${over ? "is-over" : ""} ${busy ? "is-busy" : ""}`}
        aria-busy={busy}
        onDragOver={(event) => {
          event.preventDefault();
          setOver(true);
        }}
        onDragLeave={(event) => {
          if (!event.currentTarget.contains(event.relatedTarget as Node))
            setOver(false);
        }}
        onDrop={(event) => {
          event.preventDefault();
          setOver(false);
          pick(event.dataTransfer.files);
        }}
      >
        <input
          type="file"
          aria-label="Choose a file to import"
          accept={extensions.map((extension) => `.${extension}`).join(",")}
          disabled={busy}
          onChange={(event) => {
            pick(event.target.files);
            event.target.value = "";
          }}
        />
        <span className="sp-import-file-icon">
          <Icon name="upload" size={23} />
        </span>
        <strong>
          Drop a file here, or browse
        </strong>
        <span>
          {extensions.map((extension) => extension.toUpperCase()).join(" · ")}
        </span>
        <small>Up to 10 MB · Parsed on your device</small>
      </label>
    </>
  );
}

export function ImportDatabaseDialog({
  close,
  created,
}: {
  close: () => void;
  created: (database: WorkspaceDatabase) => void;
}) {
  const data = useSpace();
  const [tables, setTables] = useState<ImportTable[]>([]),
    [sheet, setSheet] = useState(0);
  const [name, setName] = useState(""),
    [primary, setPrimary] = useState(0),
    [types, setTypes] = useState<FieldType[]>([]);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [operation, setOperation] = useState("");
  useFeedback({ error, loading: busy ? operation : "", clearError: () => setError("") });
  const request = useRef(0);
  useEffect(
    () => () => {
      request.current++;
    },
    [],
  );
  const table = tables[sheet];
  const review = useMemo(() => {
    if (!table) return { database: null, error: "" };
    try {
      return {
        database: databaseFromImport(
          table,
          name,
          primary,
          types,
          "import-review",
        ),
        error: "",
      };
    } catch (error) {
      return { database: null, error: message(error) };
    }
  }, [table, name, primary, types]);
  function selectTable(index: number, source = tables) {
    setSheet(index);
    const table = source[index];
    const named = table.headers.findIndex((header) =>
      /^(name|title|client|company)$/i.test(header),
    );
    setPrimary(named >= 0 ? named : 0);
    setTypes(table.headers.map((_, i) => inferColumn(table, i)));
    setError("");
  }
  async function choose(file: File) {
    const token = ++request.current;
    setOperation("Reading database file…");
    setBusy(true);
    setError("");
    setTables([]);
    try {
      const tables = await readDatabaseFile(file);
      if (token !== request.current) return;
      setTables(tables);
      selectTable(0, tables);
      setName(file.name.replace(/\.[^.]+$/, "").slice(0, 200));
    } catch (error) {
      if (token === request.current) setError(message(error));
    } finally {
      if (token === request.current) setBusy(false);
    }
  }
  return (
    <Modal
      title="Import a database"
      wide
      className="sp-import-modal"
      onClose={close}
    >
      <form
        className="sp-modal-content"
        aria-busy={busy}
        onSubmit={async (event) => {
          event.preventDefault();
          if (busy || !table || !review.database) return;
          setOperation("Importing database…");
          setBusy(true);
          setError("");
          try {
            const database = databaseFromImport(
              table,
              name,
              primary,
              types,
              crypto.randomUUID(),
            );
            await data.saveDatabase(database);
            created(database);
          } catch (error) {
            setError(message(error));
          } finally {
            setBusy(false);
          }
        }}
      >
        <p className="sp-import-intro">
          Bring an existing list into Space. Review the columns, then make it
          your own.
        </p>
        <FilePicker
          extensions={databaseExtensions}
          busy={busy}
          choose={(file) => void choose(file)}
        />
        {table && (
          <>
            <div className="sp-import-settings">
              <label className="sp-field">
                Database name
                <input
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  required
                  maxLength={200}
                />
              </label>
              {tables.length > 1 && (
                <label className="sp-field">
                  Worksheet
                  <select
                    value={sheet}
                    onChange={(e) => selectTable(Number(e.target.value))}
                  >
                    {tables.map((t, i) => (
                      <option key={i} value={i}>
                        {t.name.split(" · ").slice(1).join(" · ") || t.name}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              <label className="sp-field">
                Record names
                <select
                  value={primary}
                  onChange={(e) => setPrimary(Number(e.target.value))}
                >
                  <option value={-1}>Generate names (Record 1, 2…)</option>
                  {table.headers.map((header, i) => (
                    <option key={i} value={i}>
                      {header}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <div className="sp-import-preview-heading">
              <strong>Review your columns</strong>
              <span>
                {table.rows.length.toLocaleString()} records ·{" "}
                {table.headers.length} source columns
              </span>
            </div>
            <div
              className="sp-import-table-scroll"
              role="region"
              aria-label="Import preview, scroll horizontally to review all columns"
              tabIndex={0}
            >
              <table className="sp-import-table">
                <thead>
                  <tr>
                    {table.headers.map((header, i) => (
                      <th key={i}>
                        <span>{header}</span>
                        {primary === i ? (
                          <small>Name property</small>
                        ) : (
                          <select
                            aria-label={`Type for ${header}`}
                            value={types[i] ?? "text"}
                            onChange={(e) =>
                              setTypes((old) =>
                                old.map((type, index) =>
                                  index === i
                                    ? (e.target.value as FieldType)
                                    : type,
                                ),
                              )
                            }
                          >
                            {fieldTypes.map((field) => (
                              <option key={field.type} value={field.type}>
                                {field.label}
                              </option>
                            ))}
                          </select>
                        )}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {table.rows.slice(0, 5).map((row, index) => (
                    <tr key={index}>
                      {row.map((value, i) => (
                        <td key={i} title={String(value ?? "")}>
                          {value === null || value === "" ? "-" : String(value)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="sp-import-note">
              Showing the first {Math.min(5, table.rows.length)} records. Column
              types are suggestions you can change. Spreadsheet values are
              imported; formulas and styling are not. Nested JSON is kept as
              text.
            </p>
          </>
        )}
        {review.error && (
          <p className="sp-alert" role="alert">
            {review.error}
          </p>
        )}
        <div className="sp-modal-footer">
          <span className="sp-muted">Saves to your private space</span>
          <div className="sp-actions">
            <button type="button" onClick={close}>
              Cancel
            </button>
            <button className="sp-primary" disabled={busy || !review.database}>
              Create database{table ? ` · ${table.rows.length} ${table.rows.length === 1 ? "record" : "records"}` : ""}
            </button>
          </div>
        </div>
      </form>
    </Modal>
  );
}

export function ImportDocumentDialog({
  close,
  created,
  initialProject = "",
  initialFolder = "",
}: {
  close: () => void;
  created: (document: Resource) => void;
  initialProject?: string;
  initialFolder?: string;
}) {
  const data = useSpace();
  const [title, setTitle] = useState(""),
    [body, setBody] = useState(""),
    [note, setNote] = useState("");
  const [image, setImage] = useState("");
  const [imageDescription, setImageDescription] = useState("");
  const [reading, setReading] = useState(false),
    [saving, setSaving] = useState(false),
    [error, setError] = useState("");
  useFeedback({
    error,
    clearError: () => setError(""),
    loading: reading ? "Reading document file…" : saving ? "Importing document…" : "",
  });
  const request = useRef(0),
    savingRef = useRef(false);
  useEffect(
    () => () => {
      request.current++;
    },
    [],
  );
  async function choose(file: File) {
    const token = ++request.current;
    setReading(true);
    setError("");
    setBody("");
    setNote("");
    setImage("");
    try {
      const parsed = await readDocumentFile(file);
      if (token !== request.current) return;
      setBody(parsed.text);
      setNote(parsed.note);
      setImage(parsed.image ?? "");
      setImageDescription(file.name.replace(/\.[^.]+$/, "").slice(0, 300));
      setTitle(file.name.replace(/\.[^.]+$/, "").slice(0, 300));
    } catch (error) {
      if (token === request.current) setError(message(error));
    } finally {
      if (token === request.current) setReading(false);
    }
  }
  return (
    <Modal
      title="Import a document"
      wide
      className="sp-import-modal"
      onClose={() => {
        if (!savingRef.current) close();
      }}
    >
      <form
        className="sp-modal-content"
        aria-busy={reading || saving}
        onSubmit={async (event) => {
          event.preventDefault();
          if (savingRef.current || reading || !data.canWrite || !title.trim() || !body.trim())
            return;
          savingRef.current = true;
          setSaving(true);
          setError("");
          try {
            const document = await data.saveResource(
              { kind: "document", title: title.trim(), body, status: "todo", project: initialProject, folder: initialFolder },
            );
            created(document);
          } catch (error) {
            setError(message(error));
          } finally {
            savingRef.current = false;
            setSaving(false);
          }
        }}
      >
        <p className="sp-import-intro">
          Give your existing work a home. We’ll turn readable content into an
          editable Space document, or keep an image as its own document.
        </p>
        <FilePicker
          extensions={documentExtensions}
          busy={reading || saving}
          choose={(file) => void choose(file)}
        />
        {note && (
          <>
            <label className="sp-field">
              Document title
              <input
                required
                maxLength={300}
                value={title}
                disabled={saving}
                onChange={(e) => setTitle(e.target.value)}
              />
            </label>
            <div className="sp-import-preview-heading">
              <strong>{image ? "Review the image" : "Review the content"}</strong>
              {!image && <span>
                {body.trim()
                  ? body.trim().split(/\s+/).length.toLocaleString()
                  : 0}{" "}
                words
              </span>}
            </div>
            {image ? (
              <>
                <label className="sp-field">
                  Image description
                  <input
                    value={imageDescription}
                    disabled={saving}
                    maxLength={300}
                    onChange={event => {
                      setImageDescription(event.target.value);
                      setBody(insertDocumentImage("", 0, 0, image, event.target.value).text);
                    }}
                  />
                </label>
                <div className="sp-import-image-preview" aria-label="Imported image preview">
                  <DocumentMarkdown text={body} />
                </div>
              </>
            ) : (
              <>
                <label className="sr-only" htmlFor="import-document-content">
                  Imported content
                </label>
                <textarea
                  id="import-document-content"
                  className="sp-import-document-text"
                  value={body}
                  disabled={saving}
                  maxLength={500000}
                  onChange={(e) => setBody(e.target.value)}
                />
              </>
            )}
            <p className="sp-import-note">{note}</p>
          </>
        )}
        <div className="sp-modal-footer">
          <span className="sp-muted">
            Saves to your private space
          </span>
          <div className="sp-actions">
            <button type="button" disabled={saving} onClick={close}>
              Cancel
            </button>
            <button
              className="sp-primary"
              disabled={reading || saving || !data.canWrite || !body.trim() || !title.trim()}
            >
              Import document
            </button>
          </div>
        </div>
      </form>
    </Modal>
  );
}
