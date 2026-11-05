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
import { MAX_FILE_BYTES, fileDetails, fileExtension, fileKind, fileLabel, fileSummary } from "../../../lib/space/document-files";
import { describeFile, type FileDescription } from "../../../lib/space/file-thumbnails";
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
  multiple = false,
}: {
  /** Accepted extensions; leave empty to accept any file. */
  extensions: string[];
  busy: boolean;
  choose: (files: File[]) => void;
  /** The formats line under the prompt, when it differs from the extension list. */
  formats?: string;
  limit?: string;
  /** Whether several files may be chosen or dropped at once. */
  multiple?: boolean;
}) {
  const [over, setOver] = useState(false);
  const [error, setError] = useState("");
  useFeedback({ error, clearError: () => setError("") });
  function pick(files: FileList | null) {
    if (busy || !files?.length) return;
    if (!multiple && files.length !== 1) {
      setError("Choose one file at a time.");
      return;
    }
    setError("");
    choose([...files]);
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
          multiple={multiple}
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
          {multiple ? "Drop files here, or browse" : "Drop a file here, or browse"}
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
          choose={([file]) => void choose(file)}
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

// Markdown and text become editable pages, as before; every other file, pictures and videos
// included, is kept as the original with its readable text saved for search and agents.
// PDF, Word and pictures up to 10 MB may instead be converted into an editable page.
const editableExtensions = ["md", "markdown", "txt"];
const convertibleExtensions = ["pdf", "docx", ...documentImageExtensions];

type Original = { id: string; file: File; bytes: Uint8Array; text: string; note: string } & FileDescription;

type BatchItem = {
  /** Fixed for the item, so a retry reuses the parts already staged. */
  id: string;
  file: File;
  status: "waiting" | "reading" | "uploading" | "saved" | "failed";
  progress: number;
  error?: string;
  saved?: Resource;
};

const batchStatus: Record<BatchItem["status"], string> = {
  waiting: "Waiting",
  reading: "Reading…",
  uploading: "Uploading…",
  saved: "Saved",
  failed: "Failed",
};

// Parameters: a chosen file and its bytes.
// What it does: reads the text search and agents can use, then what the browser can tell
// about a picture or recording and the thumbnail it can make, all on this device.
// Output: the pieces of an Original beyond the file itself.
async function readOriginal(file: File, buffer: ArrayBuffer) {
  const read = await readOriginalFileText(file, buffer);
  const description = await describeFile(file, buffer, fileKind(file));
  return { bytes: new Uint8Array(buffer), ...read, ...description };
}

export function ImportDocumentDialog({
  close,
  created,
  initialProject = "",
  initialFolder = "",
  initialFiles,
}: {
  close: () => void;
  /** The documents saved: one from the review flow, or one per file of a batch. */
  created: (documents: Resource[], options?: { batch: boolean }) => void;
  initialProject?: string;
  initialFolder?: string;
  /** Files dropped on the library, chosen before the dialog opened. */
  initialFiles?: File[];
}) {
  const data = useSpace();
  const [title, setTitle] = useState(""),
    [body, setBody] = useState(""),
    [note, setNote] = useState("");
  const [image, setImage] = useState("");
  const [imageDescription, setImageDescription] = useState("");
  const [original, setOriginal] = useState<Original | null>(null);
  const [keep, setKeep] = useState<"original" | "convert">("original");
  const [batch, setBatch] = useState<BatchItem[] | null>(null);
  const [reading, setReading] = useState(false),
    [saving, setSaving] = useState(false),
    [error, setError] = useState("");
  const [progress, setProgress] = useState(0);
  const batchSaving = saving && !!batch;
  const batchDone = !!batch && batch.length > 0 && batch.every((item) => item.status === "saved" || item.status === "failed");
  useFeedback({
    error,
    clearError: () => setError(""),
    loading: reading ? "Reading file…" : saving ? (batch ? "Saving files…" : original && keep === "original" ? "Saving file…" : "Importing document…") : "",
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
  useEffect(() => {
    // Dropped files are chosen as the dialog opens. A read this effect started is dropped by
    // the unmount cleanup above, so a repeated mount simply reads them again.
    if (initialFiles?.length) void choose(initialFiles);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialFiles]);
  const [thumbnailUrl, setThumbnailUrl] = useState("");
  useEffect(() => {
    const thumbnail = original?.thumbnail;
    if (!thumbnail) { setThumbnailUrl(""); return; }
    const url = URL.createObjectURL(new Blob([thumbnail.data as BlobPart], { type: thumbnail.type }));
    setThumbnailUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [original?.thumbnail]);
  const keepOriginal = !!original && keep === "original";
  const extension = original ? fileExtension(original.file.name) : "";
  const convertible = !!original && convertibleExtensions.includes(extension) && original.file.size <= importLimits.bytes;
  const picture = documentImageExtensions.includes(extension);
  function reset() {
    setBody("");
    setNote("");
    setImage("");
    setOriginal(null);
    setKeep("original");
    setBatch(null);
  }
  async function choose(files: File[]) {
    if (!files.length) return;
    // Several files, or more files once a batch exists, are saved without a review step; a
    // reviewed file joins them.
    if (batch || files.length > 1) {
      const existing = batch ?? (original ? [{ id: original.id, file: original.file, status: "waiting" as const, progress: 0 }] : []);
      request.current++;
      setReading(false);
      setError("");
      setBody("");
      setNote("");
      setImage("");
      setOriginal(null);
      setKeep("original");
      setBatch([
        ...existing.filter((item) => item.status !== "saved"),
        ...files.map((file) => ({ id: crypto.randomUUID(), file, status: "waiting" as const, progress: 0 })),
      ]);
      return;
    }
    const [file] = files;
    const token = ++request.current;
    setReading(true);
    setError("");
    reset();
    try {
      const name = file.name.replace(/\.[^.]+$/, "").slice(0, 300);
      if (!file.size) throw new Error("This file is empty.");
      // Editable pages are built on this device up to 10 MB; anything else is kept as the
      // original file.
      if (editableExtensions.includes(fileExtension(file.name)) && file.size <= importLimits.bytes) {
        const parsed = await readDocumentFile(file);
        if (token !== request.current) return;
        setBody(parsed.text);
        setNote(parsed.note);
      } else {
        if (file.size > MAX_FILE_BYTES) throw new Error("Choose a file up to 250 MB.");
        const read = await readOriginal(file, await file.arrayBuffer());
        if (token !== request.current) return;
        setOriginal({ id: crypto.randomUUID(), file, ...read });
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
      setImage(parsed.image ?? "");
      if (parsed.image) setImageDescription(title);
    } catch (error) {
      if (token !== request.current) return;
      setError(message(error));
      setKeep("original");
    } finally {
      if (token === request.current) setReading(false);
    }
  }
  function keepFile() {
    request.current++;
    setReading(false);
    setKeep("original");
    setImage("");
  }
  function updateItem(id: string, change: Partial<BatchItem>) {
    setBatch((old) => old?.map((item) => (item.id === id ? { ...item, ...change } : item)) ?? old);
  }
  // Parameters: none; works through the batch's waiting and failed items.
  // What it does: reads and uploads one file at a time, so one file's bytes are in memory at
  // once, under its own name and with the text it holds; a failure marks its item and the
  // next file still saves.
  // Output: none; the dialog reports what happened and closes when every file saved.
  async function saveBatch() {
    if (!batch || savingRef.current || !data.canWrite) return;
    savingRef.current = true;
    setSaving(true);
    setError("");
    const controller = new AbortController();
    upload.current = controller;
    const results = new Map<string, Resource>();
    for (const item of batch) {
      if (item.status === "saved") { results.set(item.id, item.saved!); continue; }
      if (controller.signal.aborted) break;
      try {
        if (!item.file.size) throw new Error("This file is empty.");
        if (item.file.size > MAX_FILE_BYTES) throw new Error("Choose a file up to 250 MB.");
        updateItem(item.id, { status: "reading", progress: 0, error: undefined });
        const read = await readOriginal(item.file, await item.file.arrayBuffer());
        updateItem(item.id, { status: "uploading" });
        const saved = await data.saveFile(
          {
            id: item.id,
            file: item.file,
            bytes: read.bytes,
            title: item.file.name.replace(/\.[^.]+$/, "").slice(0, 300) || item.file.name.slice(0, 300),
            body: read.text,
            project: initialProject,
            folder: initialFolder,
            media: read.media,
            thumbnail: read.thumbnail,
          },
          { signal: controller.signal, onProgress: (value) => updateItem(item.id, { progress: value }) },
        );
        results.set(item.id, saved);
        updateItem(item.id, { status: "saved", progress: 1, saved });
      } catch (error) {
        if ((error as Error)?.name === "AbortError") break;
        updateItem(item.id, { status: "failed", error: message(error) });
      }
    }
    upload.current = null;
    savingRef.current = false;
    setSaving(false);
    if (results.size === batch.length) created(batch.map((item) => results.get(item.id)!), { batch: true });
  }
  const ready = batch ? batch.some((item) => item.status === "waiting" || item.status === "failed") : keepOriginal ? !!title.trim() : !!body.trim() && !!title.trim();
  const pendingCount = batch?.filter((item) => item.status !== "saved").length ?? 0;
  const savedCount = batch?.filter((item) => item.status === "saved").length ?? 0;
  return (
    <Modal
      title="Upload files"
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
          if (batch) { void saveBatch(); return; }
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
                {
                  id: original.id, file: original.file, bytes: original.bytes, title: title.trim(), body: original.text,
                  project: initialProject, folder: initialFolder, media: original.media, thumbnail: original.thumbnail,
                },
                { signal: controller.signal, onProgress: setProgress },
              );
            } else {
              document = await data.saveResource(
                { kind: "document", title: title.trim(), body, status: "todo", project: initialProject, folder: initialFolder },
              );
            }
            created([document]);
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
          Keep pictures, videos, PDFs, presentations, spreadsheets and other files in your
          Space with a preview, or turn Markdown and text into an editable page. Drop several
          files to save them all at once.
        </p>
        <FilePicker
          extensions={[]}
          formats="Pictures · Videos · PDF · DOCX · PPTX · XLSX · any other file"
          limit="Up to 250 MB each · Read on your device"
          multiple
          busy={reading || saving}
          choose={(files) => void choose(files)}
        />
        {batch && (
          <>
            <ul className="sp-import-batch" aria-label="Files to save">
              {batch.map((item) => (
                <li key={item.id} className={`sp-import-batch-item is-${item.status}`}>
                  <span className={`sp-file-badge is-${fileKind(item.file)}`} aria-hidden="true">{fileLabel(item.file).slice(0, 4)}</span>
                  <span className="sp-file-name">
                    <strong title={item.file.name}>{item.file.name}</strong>
                    <small>
                      {fileSummary({ name: item.file.name, type: item.file.type, bytes: item.file.size })}
                      {item.status === "failed" && item.error ? ` · ${item.error}` : ""}
                    </small>
                  </span>
                  <span className="sp-import-batch-state" role="status" aria-label={`${item.file.name}: ${batchStatus[item.status]}`}>
                    {item.status === "uploading" ? <progress max={1} value={item.progress} /> : null}
                    {item.status === "saved" ? <Icon name="check" size={14} /> : null}
                    <span>{batchStatus[item.status]}</span>
                  </span>
                  {!saving && item.status !== "saved" && (
                    <button
                      type="button"
                      className="sp-icon-button"
                      aria-label={`Remove ${item.file.name}`}
                      onClick={() => setBatch((old) => {
                        const rest = old?.filter((entry) => entry.id !== item.id) ?? [];
                        return rest.length ? rest : null;
                      })}
                    >
                      <Icon name="close" size={14} />
                    </button>
                  )}
                </li>
              ))}
            </ul>
            <p className="sp-import-note">
              Each file is saved under its own name, with the text it holds for search and agents
              {initialFolder ? ", in this folder" : ""}. Files up to 250 MB each.
            </p>
          </>
        )}
        {original && (
          <>
            <div className="sp-import-file-card">
              <div className="sp-file-card">
                {thumbnailUrl ? (
                  <img className="sp-file-thumb" src={thumbnailUrl} alt="" />
                ) : (
                  <span className={`sp-file-badge is-${fileKind(original.file)}`} aria-hidden="true">{fileLabel(original.file).slice(0, 4)}</span>
                )}
                <span className="sp-file-name">
                  <strong title={original.file.name}>{original.file.name}</strong>
                  <small>{fileDetails({ name: original.file.name, type: original.file.type, bytes: original.file.size, ...original.media })}</small>
                </span>
              </div>
            </div>
            {convertibleExtensions.includes(extension) && (
              <fieldset className="sp-import-modes" disabled={saving}>
                <legend>Save it as</legend>
                <label className="sp-import-mode">
                  <input type="radio" name="import-mode" checked={keep === "original"} onChange={keepFile} />
                  <span>
                    <strong>Keep the original file</strong>
                    <small>{picture ? "See it as it is, download it any time." : "See it as it looks, download it any time. Its text stays searchable."}</small>
                  </span>
                </label>
                <label className="sp-import-mode">
                  <input type="radio" name="import-mode" checked={keep === "convert"} disabled={!convertible} onChange={() => void convert()} />
                  <span>
                    <strong>Convert to an editable page</strong>
                    <small>
                      {!convertible ? "Available for files up to 10 MB."
                        : picture ? "Embeds a smaller copy of the picture in a page you can write on; the original file is not kept."
                          : "Keeps the text only; images, layout and the file itself are not kept."}
                    </small>
                  </span>
                </label>
              </fieldset>
            )}
          </>
        )}
        {(note || original) && !batch && (
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
        {saving && keepOriginal && !batch && (
          <div className="sp-import-progress" role="status" aria-label="Upload progress">
            <progress max={1} value={progress} />
            <span>Uploading… {Math.round(progress * 100)}%</span>
          </div>
        )}
        <div className="sp-modal-footer">
          <span className="sp-muted">
            {batchSaving
              ? `Saving ${Math.min(savedCount + 1, batch!.length)} of ${batch!.length}…`
              : "Saves to your private space"}
          </span>
          <div className="sp-actions">
            {batchDone && savedCount > 0 ? (
              <button type="button" disabled={saving} onClick={() => created(batch!.filter((item) => item.saved).map((item) => item.saved!), { batch: true })}>
                Done
              </button>
            ) : (
              <button type="button" disabled={saving} onClick={close}>
                Cancel
              </button>
            )}
            <button
              className="sp-primary"
              disabled={reading || saving || !data.canWrite || !ready}
            >
              {batch
                ? batchDone ? `Retry ${pendingCount === 1 ? "the failed file" : `${pendingCount} failed files`}`
                  : `Save ${pendingCount} ${pendingCount === 1 ? "file" : "files"}`
                : keepOriginal ? "Save file" : "Import document"}
            </button>
          </div>
        </div>
      </form>
    </Modal>
  );
}
