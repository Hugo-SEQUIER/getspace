import Link, { useRouter, useSearchParams } from "@web/lib/navigation";
import { useEffect, useRef, useState } from "react";
import { ApiError, message } from "../../../lib/client";
import { type Resource, type Status } from "../../../lib/space/types";
import { optimisticTask } from "../../../lib/space/task-model";
import "./tasks.css";
import "./work-views.css";
import { Icon } from "./ui-icon";
import { useSpace } from "./provider";
import { DocumentMarkdown } from "./document-markdown";
import { TaskBoard } from "./task-board";
import { AddFromDatabases, rowKey, useTaskRowChanges } from "./today-plan";
import { boardRows, localDay, rowColumn, statusForColumn } from "../../../lib/space/today";
import { PageIcon } from "./sidebar-preferences";
import {
  formatDocument,
  insertDocumentImage,
  readDocumentImage,
  documentImageExtensions,
  documentText,
  documentImageDraft,
  documentImageBody,
  MAX_DOCUMENT_BODY_LENGTH,
  type DocumentFormat,
} from "../../../lib/space/document-content";
import { Avatar, Empty, Markdown, Modal, StatusDot, authorName, relativeDate, ConfirmModal, useConfirm } from "./ui";
import { useFeedback } from "../notifications";

export function ResourceForm({
  kind,
  initialStatus = "todo",
  initialProject = "",
  initialFolder = "",
  resource,
  onClose,
  onSaved,
}: {
  kind: "document" | "task";
  initialStatus?: Status;
  initialProject?: string;
  initialFolder?: string;
  resource?: Resource;
  onClose: () => void;
  onSaved?: (resource: Resource) => void;
}) {
  const confirm = useConfirm();
  const data = useSpace();
  const [title, setTitle] = useState(resource?.title ?? "");
  const [initialDraft] = useState(() => kind === "document"
    ? documentImageDraft(resource?.body ?? "")
    : { text: resource?.body ?? "", images: {} });
  const [body, setBody] = useState(initialDraft.text);
  const [images, setImages] = useState<Record<string, string>>(initialDraft.images);
  const savedBody = kind === "document" ? documentImageBody(body, images) : body;
  const [status, setStatus] = useState<Status>(
    resource?.status ?? initialStatus,
  );
  const [project, setProject] = useState(resource?.project ?? initialProject);
  const [folder, setFolder] = useState(resource?.folder ?? initialFolder);
  const [folderChanged, setFolderChanged] = useState(false);
  const [priority, setPriority] = useState<Resource["priority"]>(
    resource?.priority ?? "Medium",
  );
  const [saving, setSaving] = useState(false),
    [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [addingImage, setAddingImage] = useState(false);
  const [loadingLatest, setLoadingLatest] = useState(false);
  const submitting = useRef(false);
  const busy = saving || addingImage || loadingLatest;
  useFeedback({
    error,
    notice,
    clearError: () => setError(""),
    clearNotice: () => setNotice(""),
    loading: saving ? kind === "task" && !resource ? "Creating task…" : "Saving changes…"
      : loadingLatest ? "Loading latest revision…" : addingImage ? "Preparing image…" : "",
  });
  useFeedback({
    error: kind === "document" ? data.folderError : "",
    loading: kind === "document" && !data.folderReady && !data.folderError ? "Loading folders…" : "",
  });
  const imageRequest = useRef(0);
  const imagePicker = useRef<HTMLInputElement>(null);
  const [base, setBase] = useState(resource);
  const [conflict, setConflict] = useState(false);
  const [mode, setMode] = useState<"write" | "preview">("write");
  const form = useRef<HTMLFormElement>(null);
  const textarea = useRef<HTMLTextAreaElement>(null);
  function format(kind: DocumentFormat) {
    const field = textarea.current;
    if (!field || busy) return;
    const result = formatDocument(
      body,
      field.selectionStart,
      field.selectionEnd,
      kind,
    );
    setBody(result.text);
    requestAnimationFrame(() => {
      field.focus();
      field.setSelectionRange(result.start, result.end);
    });
  }
  useEffect(() => () => { imageRequest.current++; }, []);
  async function addImage(file: File) {
    if (busy || kind !== "document" || !data.canWrite) return;
    const token = ++imageRequest.current;
    const start = textarea.current?.selectionStart ?? body.length;
    const end = textarea.current?.selectionEnd ?? start;
    setAddingImage(true);
    setError("");
    try {
      const src = await readDocumentImage(file);
      if (token !== imageRequest.current) return;
      const result = insertDocumentImage(body, start, end, src, file.name.replace(/\.[^.]+$/, ""));
      if (documentImageBody(result.text, images).length > MAX_DOCUMENT_BODY_LENGTH)
        throw new Error("This document has no room for another image. Use a smaller image or a separate document.");
      const reference = `space-image:${crypto.randomUUID()}`;
      setImages(previous => ({ ...previous, [reference]: src }));
      setBody(result.text.replace(src, reference));
      requestAnimationFrame(() => {
        textarea.current?.focus();
        textarea.current?.setSelectionRange(result.start, result.end);
      });
    } catch (error) {
      if (token === imageRequest.current) setError(message(error));
    } finally {
      if (token === imageRequest.current) setAddingImage(false);
    }
  }
  const dirty =
    title !== (resource?.title ?? "") ||
    savedBody !== (resource?.body ?? "") ||
    status !== (resource?.status ?? initialStatus) ||
    project !== (resource?.project ?? initialProject) ||
    (kind === "document" && folder !== (resource?.folder ?? initialFolder)) ||
    priority !== (resource?.priority ?? "Medium");
  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);
  async function close() {
    if (submitting.current || busy) return;
    if (!dirty || await confirm({ title: "Discard changes?", message: `Your unsaved ${kind} changes will be lost.`, confirmLabel: "Discard changes" }))
      onClose();
  }
  return (
    <Modal
      title={`${resource ? "Edit" : "New"} ${kind}`}
      onClose={close}
      wide
      className={kind === "document" ? "sp-doc-editor-dialog" : ""}
    >
      <form
        ref={form}
        className={`sp-resource-form ${kind === "document" ? "sp-doc-editor" : ""}`}
        aria-busy={busy}
        onKeyDown={(e) => {
          if (
            kind === "document" &&
            (e.metaKey || e.ctrlKey) &&
            e.key.toLowerCase() === "s"
          ) {
            e.preventDefault();
            if (!busy && !conflict && title.trim())
              form.current?.requestSubmit();
          }
        }}
        onSubmit={async (e) => {
          e.preventDefault();
          if (submitting.current || busy || conflict || !data.canWrite || !title.trim()) return;
          if (savedBody.length > MAX_DOCUMENT_BODY_LENGTH) {
            setError("This document is too large. Remove an image or shorten the content before saving.");
            return;
          }
          submitting.current = true;
          setSaving(true);
          setError("");
          try {
            const saved = await data.saveResource(
              {
                kind,
                title: title.trim(),
                body: savedBody,
                status,
                project,
                ...(kind === "document" && (!resource || folderChanged)
                  ? { folder } : {}),
                priority,
              },
              base,
            );
            onSaved?.(saved);
            onClose();
          } catch (e) {
            setError(message(e));
            setConflict(e instanceof ApiError && e.status === 409);
          } finally {
            submitting.current = false;
            setSaving(false);
          }
        }}
      >
        {kind === "document" && (
          <div className="sp-doc-editor-heading">
            <span className="sp-doc-reader-icon">
              <PageIcon
                pageKey={`document:${resource?.id ?? "new"}`}
                size={25}
              />
            </span>
            <span>
              {resource
                ? "Make room for the next version."
                : "A little space to think."}
            </span>
          </div>
        )}
        <label className="sr-only" htmlFor="resource-title">
          Title
        </label>
        <input
          id="resource-title"
          className="sp-editor-title"
          placeholder={`Untitled ${kind}`}
          data-autofocus
          required
          maxLength={300}
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          disabled={busy}
        />
        <div className="sp-form-properties">
          {kind === "task" && (
            <label>
              Status
              <select
                value={status}
                onChange={(e) => setStatus(e.target.value as Status)}
                disabled={busy}
              >
                {data.taskColumns.map((s) => (
                  <option value={s.value} key={s.value}>
                    {s.label}
                  </option>
                ))}
              </select>
            </label>
          )}
          {
            <>
              {kind === "document" && (
                <label>
                  Folder
                  <select
                    value={folder}
                    onChange={(e) => { setFolder(e.target.value); setFolderChanged(true); }}
                    disabled={busy || !data.folderReady}
                  >
                    <option value="">Unfiled</option>
                    {folder && !data.folders.some(item => item.id === folder) && (
                      <option value={folder} disabled>
                        {data.folderReady ? "Unfiled (folder unavailable)" : "Current folder"}
                      </option>
                    )}
                    {data.folders.map(item => (
                      <option value={item.id} key={item.id}>{item.name}</option>
                    ))}
                  </select>
                </label>
              )}
              <label>
                Project
                <select
                  value={project}
                  onChange={(e) => setProject(e.target.value)}
                  disabled={busy}
                >
                  <option value="">No project</option>
                  {data.projects.map((p) => (
                    <option value={p.id} key={p.id}>
                      {p.name}
                    </option>
                  ))}
                </select>
              </label>
              {kind === "task" && (
                <label>
                  Priority
                  <select
                    value={priority}
                    onChange={(e) =>
                      setPriority(e.target.value as Resource["priority"])
                    }
                  >
                    {["High", "Medium", "Low"].map((p) => (
                      <option key={p}>{p}</option>
                    ))}
                  </select>
                </label>
              )}
            </>
          }
        </div>
        {kind === "document" && data.folderError && (
          <div className="sp-actions">
            <button type="button" onClick={data.retryFolders} disabled={busy}>Retry folders</button>
          </div>
        )}
        {kind === "document" ? (
          <div className="sp-doc-editor-toolbar">
            <div className="sp-doc-editor-modes" aria-label="Editor mode">
              <button
                type="button"
                aria-pressed={mode === "write"}
                disabled={busy}
                onClick={() => setMode("write")}
              >
                <Icon name="edit" size={13} />
                Write
              </button>
              <button
                type="button"
                aria-pressed={mode === "preview"}
                disabled={busy}
                onClick={() => setMode("preview")}
              >
                <Icon name="document" size={13} />
                Preview
              </button>
            </div>
            <div
              className="sp-doc-format-tools"
              role="group"
              aria-label="Formatting"
            >
              {(
                [
                  ["bold", "Bold", "B"],
                  ["italic", "Italic", "I"],
                  ["heading", "Heading", "H"],
                  ["list", "Bulleted list", "≡"],
                  ["quote", "Quote", "❞"],
                  ["code", "Code block", "</>"],
                ] as const
              ).map(([value, label, glyph]) => (
                <button
                  type="button"
                  key={value}
                  aria-label={label}
                  title={label}
                  disabled={busy || mode !== "write"}
                  className={`sp-doc-format-${value}`}
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => format(value)}
                >
                  {glyph}
                </button>
              ))}
              <button
                type="button"
                aria-label="Add image"
                title="Add image"
                disabled={busy || mode !== "write" || !data.canWrite}
                onClick={() => imagePicker.current?.click()}
              >
                <Icon name="image" size={16} />
              </button>
              <input
                ref={imagePicker}
                type="file"
                hidden
                aria-label="Choose image"
                accept={documentImageExtensions.map(extension => `.${extension}`).join(",")}
                disabled={busy}
                onChange={event => {
                  const file = event.target.files?.[0];
                  event.target.value = "";
                  if (file) void addImage(file);
                }}
              />
            </div>
          </div>
        ) : (
          <div className="sp-editor-tools">
            <span>Markdown</span>
            <span className="sp-editor-tool-hint">
              # Heading &nbsp; • List &nbsp; ``` Code
            </span>
          </div>
        )}
        <label className="sr-only" htmlFor="resource-body">
          Content
        </label>
        <textarea
          ref={textarea}
          id="resource-body"
          hidden={kind === "document" && mode === "preview"}
          value={body}
          onChange={(e) => setBody(e.target.value)}
          placeholder="Start writing. Make room for your next idea…"
          maxLength={MAX_DOCUMENT_BODY_LENGTH}
          disabled={busy}
          onPaste={event => {
            if (kind !== "document" || !event.clipboardData.files.length) return;
            const images = Array.from(event.clipboardData.files).filter(file => file.type.startsWith("image/"));
            if (!images.length) return;
            event.preventDefault();
            if (images.length !== 1) setError("Add one image at a time.");
            else void addImage(images[0]);
          }}
        />
        {kind === "document" && mode === "preview" && (
          <div className="sp-doc-editor-preview" aria-label="Document preview">
            {body.trim() ? (
              <DocumentMarkdown text={savedBody} />
            ) : (
              <p>Your writing will appear here.</p>
            )}
          </div>
        )}
        {kind === "document" && (
          <div className="sp-doc-write-help">
            <span>
              {documentText(savedBody).trim()
                ? documentText(savedBody).trim().split(/\s+/).length.toLocaleString()
                : 0}{" "}
              words
            </span>
            <span>
              Markdown · Add or paste images<span> · </span>⌘ / Ctrl S to save
            </span>
          </div>
        )}
        {conflict && base && (
          <div className="sp-actions">
              <button
                type="button"
                disabled={busy}
                onClick={async () => {
                  if (submitting.current || busy) return;
                  submitting.current = true;
                  setLoadingLatest(true);
                  try {
                    const latest = await data.readResource(base.id, undefined, true);
                    setBase(latest);
                    if (!folderChanged) setFolder(latest.folder ?? "");
                    setConflict(false);
                    setError("");
                    setNotice(
                      "Latest version loaded below. Your draft is preserved; compare it before saving.",
                    );
                  } catch (e) {
                    setError(message(e));
                  } finally {
                    submitting.current = false;
                    setLoadingLatest(false);
                  }
                }}
              >
                Load latest & keep my draft
              </button>
          </div>
        )}
        {base && resource && base.revision !== resource.revision && (
          <details className="sp-revision-compare" open>
            <summary>Current saved version · v{base.revision}</summary>
            <h3>{base.title}</h3>
            <p>Status: {base.status.replaceAll("_", " ")}</p>
            {kind === "document" && <p>Folder: {data.folders.find(item => item.id === base.folder)?.name ?? "Unfiled"}</p>}
            <p>Project: {data.projects.find(item => item.id === base.project)?.name ?? "No project"}</p>
            <pre>{base.body}</pre>
          </details>
        )}
        <div className="sp-modal-footer">
          <span
            className={`sp-muted ${kind === "document" ? "sp-doc-save-state" : ""}`}
          >
            {kind === "document" && (
              <span className={dirty ? "is-unsaved" : ""} />
            )}
            {dirty ? "Unsaved changes" : base ? `Revision ${base.revision}` : "A new place to start"}
          </span>
          <div className="sp-actions">
            <button type="button" onClick={close} disabled={busy}>
              Cancel
            </button>
            <button
              className="sp-primary"
              disabled={busy || conflict || !data.canWrite || !title.trim()}
            >
              Save
            </button>
          </div>
        </div>
      </form>
    </Modal>
  );
}

export function TasksView({ base, id }: { base: string; id?: string }) {
  const data = useSpace(),
    router = useRouter();
  const params = useSearchParams();
  const view = params.get("view") === "list" ? "list" : "board";
  const board = view === "board";
  function setView(next: "board" | "list") {
    router.replace(
      `${base}/tasks${id ? `/${id}` : ""}?view=${next}`,
      { scroll: false },
    );
  }
  const [query, setQuery] = useState(""),
    [filter, setFilter] = useState("");
  const [projectFilter, setProjectFilter] = useState("");
  const [form, setForm] = useState<Resource | "new" | null>(null),
    [selectedResource, setSelected] = useState<Resource | null>(null),
    [deleteOpen, setDeleteOpen] = useState(false),
    [deleting, setDeleting] = useState(false),
    [deleteError, setDeleteError] = useState("");
  const [columnOpen, setColumnOpen] = useState(false);
  const [columnName, setColumnName] = useState("");
  const [columnBusy, setColumnBusy] = useState(false);
  const [columnError, setColumnError] = useState("");
  const [selecting, setSelecting] = useState(false);
  const [checkedIds, setCheckedIds] = useState<ReadonlySet<string>>(() => new Set());
  const [bulkDeleteOpen, setBulkDeleteOpen] = useState(false);
  const [bulkDeleting, setBulkDeleting] = useState(false);
  const [bulkDeleteError, setBulkDeleteError] = useState("");
  const selected = selectedResource && optimisticTask(selectedResource, data.pendingTaskStatuses);
  const linkedDocuments = selected?.project
    ? data.resources.filter(resource => resource.kind === "document" && resource.project === selected.project).slice(0, 2)
    : [];
  const [readError, setReadError] = useState("");
  useFeedback({ error: readError });
  useFeedback({ error: columnError, loading: columnBusy ? "Adding task column…" : "" });
  useFeedback({
    error: data.taskColumnsError,
    loading: !data.taskColumnsReady && !data.taskColumnsError ? "Loading task columns…" : "",
  });
  useFeedback({ loading: Object.keys(data.pendingTaskStatuses).length ? "Saving task statuses…" : "" });
  useFeedback({ loading: id && !selected && !readError ? "Loading task…" : "" });
  const [newStatus, setNewStatus] = useState<Status>("todo");
  useEffect(() => data.watchTaskColumns(), [data.watchTaskColumns]);
  const tasks = data.resources.filter(
    (r) =>
      r.kind === "task" &&
      (!filter || r.status === filter) &&
      (!projectFilter || r.project === projectFilter) &&
      r.title.toLowerCase().includes(query.toLowerCase()),
  );
  // Task database rows planned for today join the board under the column their status names.
  const today = localDay();
  const rowChanges = useTaskRowChanges();
  const [picking, setPicking] = useState(false);
  const rows = board ? boardRows(data.databases, today)
    .map((row) => ({ key: rowKey(row), row, column: rowColumn(row, data.taskColumns) }))
    .filter(({ row, column }) =>
      (!filter || column === filter) &&
      (!projectFilter || row.database.project_id === projectFilter) &&
      row.record.name.toLowerCase().includes(query.toLowerCase())) : [];
  // Only tasks that are still listed under the current filters take part in bulk actions.
  const checkedTasks = tasks.filter(task => checkedIds.has(task.id));
  const allChecked = tasks.length > 0 && checkedTasks.length === tasks.length;
  const checkedSaving = checkedTasks.some(task => Object.hasOwn(data.pendingTaskStatuses, task.id));
  const selection = selecting ? {
    ids: checkedIds,
    toggle: (taskId: string) => setCheckedIds(old => {
      const next = new Set(old);
      if (!next.delete(taskId)) next.add(taskId);
      return next;
    }),
  } : undefined;
  function stopSelecting() {
    setSelecting(false);
    setCheckedIds(new Set());
  }
  async function moveChecked(status: Status) {
    const results = await data.changeTaskStatuses(checkedTasks, status);
    const failed = results.filter(({ result }) => result.status === "rejected");
    if (failed.length) {
      const reason = failed[0].result.status === "rejected" ? message(failed[0].result.reason) : "";
      data.setError(failed.length === 1
        ? `Could not move “${failed[0].task.title}”: ${reason}`
        : `Could not move ${failed.length} tasks: ${reason}`);
    }
  }
  useEffect(() => {
    const saved = data.resources.find(resource => resource.id === id && resource.kind === "task" && resource.body !== undefined);
    setSelected(saved ?? null);
    setReadError("");
    if (id) return data.watchResource(id, undefined, r => {
      if (r.kind !== "task") { setReadError("This link does not point to a task."); return; }
      setSelected(previous => previous?.id === r.id && previous.revision > r.revision ? previous : r);
      setReadError("");
    }, e => setReadError(message(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, data.space?.id]);
  async function changeStatus(task: Resource, status: Status) {
    if (task.status === status) return;
    const focusTarget = document.activeElement?.getAttribute("data-task-focus");
    try {
      const saved = await data.changeTaskStatus(task, status);
      if (saved) setSelected(previous => previous?.id === task.id ? saved : previous);
    } catch (e) {
      data.setError(message(e));
    } finally {
      if (focusTarget && document.activeElement === document.body)
        requestAnimationFrame(() => {
          if (document.activeElement !== document.body) return;
          document.querySelector<HTMLElement>(`[data-task-focus="${CSS.escape(focusTarget)}"]`)?.focus();
        });
    }
  }
  return (
    <>
      <div className="sp-task-layout sp-work-view sp-tasks-view">
        <div className="sp-task-main">
          <div className="sp-work-heading">
            <div>
              <h1>Tasks</h1>
              <p>Turn ideas into progress.</p>
            </div>
            <button
              className="sp-primary"
              disabled={!data.canWrite}
              onClick={() => {
                setNewStatus("todo");
                setForm("new");
              }}
            >
              <Icon name="plus" size={17} />
              New task
            </button>
          </div>
          <div className="sp-work-toolbar">
            <div className="sp-work-view-tabs" aria-label="Task view">
              <button
                aria-pressed={view === "board"}
                className={view === "board" ? "selected" : ""}
                onClick={() => setView("board")}
              >
                <Icon name="board" size={17} />
                Board
              </button>
            <button
              aria-pressed={view === "list"}
              className={view === "list" ? "selected" : ""}
              onClick={() => setView("list")}
            >
              <Icon name="list" size={17} />
              List
            </button>
          </div>
          <div className="sp-work-filters">
            <label className="sp-search">
              <Icon name="search" size={16} />
              <input
                aria-label="Search tasks"
                placeholder="Search tasks…"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </label>
            <select
              value={projectFilter}
              onChange={(event) => setProjectFilter(event.target.value)}
              aria-label="Filter tasks by project"
            >
              <option value="">All projects</option>
              {data.projects.map((project) => (
                <option value={project.id} key={project.id}>{project.name}</option>
              ))}
            </select>
          <select
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            aria-label="Filter tasks by status"
          >
            <option value="">All statuses</option>
            {data.taskColumns.map((s) => (
              <option value={s.value} key={s.value}>
                {s.label}
              </option>
            ))}
          </select>
            </div>
          </div>
          <div className="sp-task-collection-tools">
            {selecting ? (
              <div className="sp-task-bulk-bar" role="toolbar" aria-label="Selected tasks">
                <span className="sp-task-bulk-count" role="status">
                  {checkedTasks.length} selected
                </span>
                <button type="button" disabled={!tasks.length}
                  onClick={() => setCheckedIds(allChecked ? new Set() : new Set(tasks.map(task => task.id)))}>
                  {allChecked ? "Clear selection" : "Select all"}
                </button>
                <select
                  aria-label="Move selected tasks to"
                  value=""
                  disabled={!checkedTasks.length}
                  onChange={(event) => { if (event.target.value) void moveChecked(event.target.value as Status); }}
                >
                  <option value="">Move to…</option>
                  {data.taskColumns.map((column) => (
                    <option value={column.value} key={column.value}>{column.label}</option>
                  ))}
                </select>
                <button
                  type="button"
                  className="sp-task-bulk-delete"
                  disabled={!checkedTasks.length || checkedSaving}
                  title={checkedSaving ? "Wait for status changes to save." : undefined}
                  onClick={() => { setBulkDeleteError(""); setBulkDeleteOpen(true); }}
                >
                  <Icon name="trash" size={14} />
                  Delete
                </button>
                <button type="button" onClick={stopSelecting}>Done</button>
              </div>
            ) : (
              <span className="sp-muted">
                {tasks.length} {tasks.length === 1 ? "task" : "tasks"}
                {rows.length > 0 && ` · ${rows.length} from databases`}
              </span>
            )}
            <div className="sp-task-collection-actions">
              {!selecting && (
                <button
                  disabled={!data.canWrite || !tasks.length}
                  onClick={() => setSelecting(true)}
                >
                  <Icon name="check" size={15} />
                  Select
                </button>
              )}
              {board && !selecting && (
                <button disabled={!data.canWrite} onClick={() => setPicking(true)}>
                  <Icon name="database" size={15} />
                  Add from databases
                </button>
              )}
              <button
                disabled={!data.canWrite}
                onClick={() => { setColumnName(""); setColumnError(""); setColumnOpen(true); }}
              >
                <Icon name="plus" size={15} />
                Add column
              </button>
            </div>
          </div>
          {data.taskColumnsError && (
            <div className="sp-actions">
              <button onClick={data.retryTaskColumns}>Retry columns</button>
            </div>
          )}
          {board ? (
            <TaskBoard
              tasks={tasks}
              rows={rows}
              rowSaving={rowChanges.saving}
              today={today}
              moveRow={(row, column) => {
                try { void rowChanges.change(row, { status: statusForColumn(row.database, column) }); }
                catch (e) { rowChanges.fail(message(e)); }
              }}
              unplanRow={(row) => void rowChanges.change(row, { planned: null })}
              base={base}
              projects={data.projects}
              selectedId={selected?.id}
              disabled={!data.canWrite}
              columns={data.taskColumns}
              pending={data.pendingTaskStatuses}
              selection={selection}
              changeStatus={(task, status) => void changeStatus(task, status)}
              add={(status) => {
                setNewStatus(status);
                setForm("new");
              }}
            />
          ) : !tasks.length ? (
            <Empty icon="tasks" title="A little room to make progress">
              <p>
                {query || filter || projectFilter
                  ? "No tasks match these filters."
                  : "Turn your next step into a task."}
              </p>
              <button
                disabled={!data.canWrite}
                onClick={() => {
                  setNewStatus("todo");
                  setForm("new");
                }}
              >
                Create task
              </button>
            </Empty>
          ) : (
            <div className="sp-task-groups">
              {data.taskColumns.map((group) => {
                const items = tasks.filter((t) => t.status === group.value);
                if (filter && !items.length) return null;
                return (
                  <section className="sp-task-group" key={group.value}>
                    <h2>
                      <StatusDot status={group.value} />
                      {group.label}
                      <span className="sp-count">{items.length}</span>
                    </h2>
                    <div>
                      {items.map((task) => (
                        <div
                          className={`sp-task-row ${selected?.id === task.id ? "selected" : ""} ${checkedIds.has(task.id) && selecting ? "is-checked" : ""}`}
                          key={task.id}
                          aria-busy={Object.hasOwn(data.pendingTaskStatuses, task.id)}
                        >
                          {selection && (
                            <input
                              type="checkbox"
                              className="sp-task-checkbox"
                              checked={checkedIds.has(task.id)}
                              onChange={() => selection.toggle(task.id)}
                              aria-label={`Select ${task.title}`}
                            />
                          )}
                          <button
                            className="sp-status-button"
                            aria-label={`Change status of ${task.title}`}
                            title="Move to next status"
                            data-task-focus={`status-${task.id}`}
                            disabled={!data.canWrite}
                            onClick={() =>
                              void changeStatus(
                                task,
                                data.taskColumns[
                                  (data.taskColumns.findIndex(
                                    (s) => s.value === task.status,
                                  ) +
                                    1) %
                                    data.taskColumns.length
                                ].value,
                              )
                            }
                          >
                            <StatusDot status={task.status} />
                          </button>
                          <Link
                            href={`${base}/tasks/${task.id}?view=list`}
                            className="sp-task-name"
                          >
                            {task.title}
                          </Link>
                          <span className="sp-task-project">
                            <Icon name="folder" size={13} />
                            {data.projects.find((p) => p.id === task.project)
                              ?.name ?? "Personal space"}
                          </span>
                          <span className="sp-priority" data-priority={task.priority?.toLowerCase()}>
                            {task.priority && (
                              <>
                                <Icon name="flag" size={14} />
                                {task.priority}
                              </>
                            )}
                          </span>
                        </div>
                      ))}
                    </div>
                    <button
                      className="sp-add-task"
                      disabled={!data.canWrite}
                      onClick={() => {
                        setNewStatus(group.value);
                        setForm("new");
                      }}
                    >
                      <Icon name="plus" size={14} />
                      Add task
                    </button>
                  </section>
                );
              })}
            </div>
          )}
          {data.cursors.task && (
            <button
              className="sp-load-more"
              onClick={() => void data.loadMore("task")}
            >
              Load more tasks
            </button>
          )}
        </div>
        {picking && (
          <AddFromDatabases
            base={base}
            today={today}
            saving={rowChanges.saving}
            change={rowChanges.change}
            close={() => setPicking(false)}
          />
        )}
        {id && !form && !deleteOpen && (
          <Modal
            title="Task details"
            wide
            className="sp-task-dialog"
            onClose={() => router.replace(`${base}/tasks?view=${view}`, { scroll: false })}
          >
            {selected && (
              <div className="sp-task-dialog-toolbar">
                <span className="sp-with-status">
                  <StatusDot status={selected.status} />
                  {data.taskColumns.find((s) => s.value === selected.status)?.label}
                </span>
                {data.canWrite && (
                  <div className="sp-task-dialog-actions sp-actions">
                    <button disabled={Object.hasOwn(data.pendingTaskStatuses, selected.id)} onClick={() => setForm(selected)}>
                      <Icon name="edit" size={14} />
                      Edit
                    </button>
                    <button disabled={Object.hasOwn(data.pendingTaskStatuses, selected.id)} onClick={() => {
                      setDeleteError(""); setDeleteOpen(true);
                    }}>
                      <Icon name="trash" size={14} />
                      Delete
                    </button>
                  </div>
                )}
              </div>
            )}
            {selected ? (
              <div className="sp-inspector-body">
                <h1>{selected.title}</h1>
                <dl className="sp-properties">
                  <div>
                    <dt>Status</dt>
                    <dd>
                      <select
                        aria-label="Task status"
                        disabled={!data.canWrite}
                        value={selected.status}
                        onChange={(e) =>
                          void changeStatus(selected, e.target.value as Status)
                        }
                      >
                        {data.taskColumns.map((s) => (
                          <option value={s.value} key={s.value}>
                            {s.label}
                          </option>
                        ))}
                      </select>
                    </dd>
                  </div>
                  <div>
                    <dt>Priority</dt>
                    <dd>{selected.priority ?? "Not set"}</dd>
                  </div>
                  <div>
                    <dt>Project</dt>
                    <dd>
                      {data.projects.find((p) => p.id === selected.project)
                        ?.name ?? "-"}
                    </dd>
                  </div>
                </dl>
                <h3>Description</h3>
                {selected.body?.trim() ? <Markdown text={selected.body} /> : <p className="sp-muted">No description yet.</p>}
                {linkedDocuments.length > 0 && <div className="sp-inspector-section">
                  <h3>Linked documents</h3>
                  {linkedDocuments.map((r) => (
                      <Link
                        className="sp-document-link"
                        href={`${base}/documents/${r.id}`}
                        key={r.id}
                      >
                        <Icon name="document" size={16} />
                        {r.title}
                        <span className="sp-muted">v{r.revision}</span>
                      </Link>
                    ))}
                </div>}
                <small className="sp-muted">
                  Updated by{" "}
                  {authorName(selected.author, selected.author_agent)} ·{" "}
                  {relativeDate(selected.updated_at)}
                </small>
              </div>
            ) : readError ? (
              <Empty icon="tasks" title="This task is unavailable">
                <Link
                  className="sp-button"
                  href={`${base}/tasks?view=${view}`}
                >
                  Back to tasks
                </Link>
              </Empty>
            ) : (
              <div className="sp-skeleton" aria-busy="true" />
            )}
          </Modal>
        )}
      </div>
      {columnOpen && (
        <Modal title="New task column" onClose={() => { if (!columnBusy) setColumnOpen(false); }}>
          <form aria-busy={columnBusy} onSubmit={async event => {
            event.preventDefault();
            if (columnBusy || !columnName.trim()) return;
            setColumnBusy(true);
            setColumnError("");
            try {
              await data.addTaskColumn(columnName);
              setColumnOpen(false);
            } catch (error) { setColumnError(message(error)); }
            finally { setColumnBusy(false); }
          }}>
            <div className="sp-inspector-body sp-task-column-form">
              <label htmlFor="task-column-name">Column name</label>
              <input id="task-column-name" data-autofocus value={columnName} maxLength={80} required
                disabled={columnBusy} placeholder="For example, Backlog"
                onChange={event => setColumnName(event.target.value)} />
              <p className="sp-muted">Columns are shared by all tasks in this Space.</p>
              {data.taskColumnsError && <button type="button" onClick={data.retryTaskColumns}>Retry columns</button>}
            </div>
            <div className="sp-modal-footer">
              <button type="button" disabled={columnBusy} onClick={() => setColumnOpen(false)}>Cancel</button>
              <button className="sp-primary" disabled={columnBusy || !data.taskColumnsReady || !columnName.trim()}>
                {columnBusy ? "Adding…" : "Add column"}
              </button>
            </div>
          </form>
        </Modal>
      )}
      {bulkDeleteOpen && (
        <ConfirmModal
          title={checkedTasks.length === 1 ? "Delete 1 task?" : `Delete ${checkedTasks.length} tasks?`}
          confirmLabel={checkedTasks.length === 1 ? "Delete task" : `Delete ${checkedTasks.length} tasks`}
          busyLabel="Deleting…"
          busy={bulkDeleting}
          disabled={!checkedTasks.length}
          error={bulkDeleteError}
          onClose={() => setBulkDeleteOpen(false)}
          onConfirm={async () => {
            const targets = checkedTasks;
            setBulkDeleting(true);
            setBulkDeleteError("");
            try {
              const results = await data.deleteTasks(targets);
              const failed = targets.filter((_, index) => results[index].status === "rejected");
              setCheckedIds(new Set(failed.map(task => task.id)));
              if (selected && targets.some((task, index) => task.id === selected.id && results[index].status === "fulfilled"))
                router.replace(`${base}/tasks?view=${view}`);
              if (failed.length) {
                const first = results.find(result => result.status === "rejected");
                setBulkDeleteError(`Deleted ${targets.length - failed.length} of ${targets.length}. ${
                  first?.status === "rejected" ? message(first.reason) : ""}`);
              } else setBulkDeleteOpen(false);
            } finally {
              setBulkDeleting(false);
            }
          }}
        >
          <p>
            Delete these tasks and all their saved versions? This cannot be undone.
            Separately saved memories will remain.
          </p>
          <ul className="sp-task-bulk-list">
            {checkedTasks.slice(0, 5).map(task => <li key={task.id}>{task.title}</li>)}
            {checkedTasks.length > 5 && <li className="sp-muted">and {checkedTasks.length - 5} more</li>}
          </ul>
        </ConfirmModal>
      )}
      {deleteOpen && selected && (
        <ConfirmModal
          title="Delete task?"
          confirmLabel="Delete task"
          busyLabel="Deleting…"
          busy={deleting}
          error={deleteError}
          onClose={() => setDeleteOpen(false)}
          onConfirm={async () => {
            setDeleting(true);
            setDeleteError("");
            try {
              await data.deleteResource(selected);
              setDeleteOpen(false);
              setSelected(null);
              router.replace(`${base}/tasks?view=${view}`);
            } catch (e) {
              setDeleteError(message(e));
            } finally {
              setDeleting(false);
            }
          }}
        >
          <p>
            Delete “{selected.title}” and all its saved versions? This cannot be
            undone. Separately saved memories will remain.
          </p>
        </ConfirmModal>
      )}
      {form && (
        <ResourceForm
          kind="task"
          initialStatus={newStatus}
          initialProject={form === "new" ? projectFilter : ""}
          resource={form === "new" ? undefined : form}
          onClose={() => setForm(null)}
          onSaved={(r) => {
            setSelected(r);
            router.push(`${base}/tasks/${r.id}?view=${view}`);
          }}
        />
      )}
    </>
  );
}
