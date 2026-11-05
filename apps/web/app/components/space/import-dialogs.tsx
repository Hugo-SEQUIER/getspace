import "../../document-files.css";

import { useEffect, useMemo, useRef, useState } from "react";
import { message } from "../../../lib/client";
import {
  databaseFromImport,
  importLimits,
  inferColumn,
  type ImportTable,
} from "../../../lib/space/import-data";
import {
  databaseExtensions,
  readDatabaseFile,
  readDocumentFile,
  readOriginalFileText,
} from "../../../lib/space/import-files";
import { MAX_FILE_BYTES, fileExtension, fileLabel, fileSummary } from "../../../lib/space/document-files";
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
import { documentImageExtensions, insertDocumentImage } from "../../../lib/space/document-content";
import { useFeedback } from "../notifications";

function FilePicker({
  extensions,
  busy,
  choose,
  formats,
  limit = "Up to 10 MB · Parsed on your device",
}: {
  /** Accepted extensions; leave empty to accept any file. */
  extensions: string[];
  busy: boolean;
  choose: (file: File) => void;
  /** The formats line under the prompt, when it differs from the extension list. */
  formats?: string;
  limit?: string;
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
          accept={extensions.length ? extensions.map((extension) => `.${extension}`).join(",") : undefined}
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
          {formats ?? extensions.map((extension) => extension.toUpperCase()).join(" · ")}
        </span>
        <small>{limit}</small>
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

// Markdown and text become editable pages and pictures image pages, as before; every other
// file, and any of those over 10 MB, is kept as the original, with its readable text saved
// for search and agents.
const editableExtensions = ["md", "markdown", "txt", ...documentImageExtensions];
const convertibleExtensions = ["pdf", "docx"];

type Original = { id: string; file: File; bytes: Uint8Array; text: string; note: string };

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
  const [original, setOriginal] = useState<Original | null>(null);
  const [keep, setKeep] = useState<"original" | "convert">("original");
  const [reading, setReading] = useState(false),
    [saving, setSaving] = useState(false),
    [error, setError] = useState("");
  const [progress, setProgress] = useState(0);
  useFeedback({
    error,
    clearError: () => setError(""),
    loading: reading ? "Reading document file…" : saving ? (original && keep === "original" ? "Saving file…" : "Importing document…") : "",
  });
  const request = useRef(0),
    savingRef = useRef(false),
    upload = useRef<AbortController | null>(null);
  useEffect(
    () => () => {
      request.current++;
      upload.current?.abort();
    },
    [],
  );
  const keepOriginal = !!original && keep === "original";
  const extension = original ? fileExtension(original.file.name) : "";
  const convertible = !!original && convertibleExtensions.includes(extension) && original.file.size <= importLimits.bytes;
  function reset() {
    setBody("");
    setNote("");
    setImage("");
    setOriginal(null);
    setKeep("original");
  }
  async function choose(file: File) {
    const token = ++request.current;
    setReading(true);
    setError("");
    reset();
    try {
      const name = file.name.replace(/\.[^.]+$/, "").slice(0, 300);
      if (!file.size) throw new Error("This file is empty.");
      // Editable pages and image pages are built on this device up to 10 MB; anything
      // larger is kept as the original file instead.
      if (editableExtensions.includes(fileExtension(file.name)) && file.size <= importLimits.bytes) {
        const parsed = await readDocumentFile(file);
        if (token !== request.current) return;
        setBody(parsed.text);
        setNote(parsed.note);
        setImage(parsed.image ?? "");
        setImageDescription(name);
      } else {
        if (file.size > MAX_FILE_BYTES) throw new Error("Choose a file up to 250 MB.");
        const buffer = await file.arrayBuffer();
        const read = await readOriginalFileText(file, buffer);
        if (token !== request.current) return;
        setOriginal({ id: crypto.randomUUID(), file, bytes: new Uint8Array(buffer), ...read });
      }
      setTitle(name || file.name.slice(0, 300));
    } catch (error) {
      if (token === request.current) setError(message(error));
    } finally {
      if (token === request.current) setReading(false);
    }
  }
  async function convert() {
    if (!original) return;
    const token = ++request.current;
    setKeep("convert");
    setReading(true);
    setError("");
    try {
      const parsed = await readDocumentFile(original.file);
      if (token !== request.current) return;
      setBody(parsed.text);
      setNote(parsed.note);
    } catch (error) {
      if (token !== request.current) return;
      setError(message(error));
      setKeep("original");
    } finally {
      if (token === request.current) setReading(false);
    }
  }
  const ready = keepOriginal ? !!title.trim() : !!body.trim() && !!title.trim();
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
          if (savingRef.current || reading || !data.canWrite || !ready) return;
          savingRef.current = true;
          setSaving(true);
          setError("");
          setProgress(0);
          try {
            let document: Resource;
            if (keepOriginal && original) {
              const controller = new AbortController();
              upload.current = controller;
              document = await data.saveFile(
                { id: original.id, file: original.file, bytes: original.bytes, title: title.trim(), body: original.text, project: initialProject, folder: initialFolder },
                { signal: controller.signal, onProgress: setProgress },
              );
            } else {
              document = await data.saveResource(
                { kind: "document", title: title.trim(), body, status: "todo", project: initialProject, folder: initialFolder },
              );
            }
            created(document);
          } catch (error) {
            if ((error as Error)?.name !== "AbortError") setError(message(error));
          } finally {
            upload.current = null;
            savingRef.current = false;
            setSaving(false);
          }
        }}
      >
        <p className="sp-import-intro">
          Keep PDFs, Word documents, presentations, spreadsheets and other files in your
          Space with a preview, or turn Markdown and text into an editable page.
        </p>
        <FilePicker
          extensions={[]}
          formats="PDF · DOCX · PPTX · XLSX · images · any other file"
          limit="Up to 250 MB · Text is read on your device"
          busy={reading || saving}
          choose={(file) => void choose(file)}
        />
        {original && (
          <>
            <div className="sp-import-file-card">
              <div className="sp-file-card">
                <span className="sp-file-badge" aria-hidden="true">{fileLabel(original.file).slice(0, 4)}</span>
                <span className="sp-file-name">
                  <strong title={original.file.name}>{original.file.name}</strong>
                  <small>{fileSummary({ name: original.file.name, type: original.file.type, bytes: original.file.size })}</small>
                </span>
              </div>
            </div>
            {convertibleExtensions.includes(extension) && (
              <fieldset className="sp-import-modes" disabled={saving}>
                <legend>Save it as</legend>
                <label className="sp-import-mode">
                  <input type="radio" name="import-mode" checked={keep === "original"} onChange={() => { request.current++; setReading(false); setKeep("original"); }} />
                  <span>
                    <strong>Keep the original file</strong>
                    <small>See it as it looks, download it any time. Its text stays searchable.</small>
                  </span>
                </label>
                <label className="sp-import-mode">
                  <input type="radio" name="import-mode" checked={keep === "convert"} disabled={!convertible} onChange={() => void convert()} />
                  <span>
                    <strong>Convert to an editable page</strong>
                    <small>{convertible ? "Keeps the text only; images, layout and the file itself are not kept." : "Available for files up to 10 MB."}</small>
                  </span>
                </label>
              </fieldset>
            )}
          </>
        )}
        {(note || original) && (
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
            {keepOriginal && original ? (
              <p className="sp-import-note">{original.note}</p>
            ) : keep === "convert" && reading ? null : (
              <>
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
          </>
        )}
        {saving && keepOriginal && (
          <div className="sp-import-progress" role="status" aria-label="Upload progress">
            <progress max={1} value={progress} />
            <span>Uploading… {Math.round(progress * 100)}%</span>
          </div>
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
              disabled={reading || saving || !data.canWrite || !ready}
            >
              {keepOriginal ? "Save file" : "Import document"}
            </button>
          </div>
        </div>
      </form>
    </Modal>
  );
}
