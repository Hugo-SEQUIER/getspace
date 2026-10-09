
import "../../database-editors.css";
import "../../database-rows.css";

import { ListPageControls, useListPage } from "./list-page";

import Link from "@web/lib/navigation";
import dynamic from "@web/lib/lazy";
import { useRouter, useSearchParams } from "@web/lib/navigation";
import { useEffect, useId, useRef, useState } from "react";
import {
  createDatabase,
  validateTaskTracking,
  queryRecords,
  applyDatabaseField,
  applyRecordChanges,
  validateRecord,
  type DatabaseField,
  type DatabaseRecord,
  type DatabaseTemplate,
  type TaskTracking,
  type FieldValue,
  type WorkspaceDatabase,
} from "../../../lib/space/databases";
import { message } from "../../../lib/client";
import { createDatabaseSaveQueue } from "../../../lib/space/database-save-queue";
import { saveRecovery } from "./database-save-recovery";
import { useSpace } from "./provider";
import { ItemTrash } from "./item-trash";
import { Empty, Modal, useConfirm } from "./ui";
import { Icon } from "./ui-icon";
import { PropertyForm } from "./database-property-form";
import { RecordPanel } from "./database-record-panel";
import { DatabaseTable, SelectedRowActions } from "./database-table";
import { DatabaseBoard } from "./database-board";
import { DatabaseBoardProperties, useDatabaseBoardProperties } from "./database-board-properties";
import { databaseRecordsTSV, deleteDatabaseRecords, duplicateDatabaseRecords } from "../../../lib/space/database-record-actions";
import { useFeedback } from "../notifications";

const ImportDatabaseDialog = dynamic(() =>
  import("./import-dialogs").then((m) => m.ImportDatabaseDialog),
);


function CreateDatabase({
  importFile,
  close,
  created,
}: {
  importFile: () => void;
  close: () => void;
  created: (database: WorkspaceDatabase) => Promise<void>;
}) {
  const itemId = useRef(crypto.randomUUID());
  const [template, setTemplate] = useState<DatabaseTemplate>("blank"),
    [name, setName] = useState(""),
    [description, setDescription] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useFeedback({ error });
  return (
    <Modal
      title="New database"
      onClose={close}
      wide
      className="sp-db-create-modal"
    >
      <form
        className="sp-modal-content"
        aria-busy={busy}
        onSubmit={async (e) => {
          e.preventDefault();
          if (busy) return;
          setBusy(true);
          setError("");
          try {
            await created(
              createDatabase(template, name, description, itemId.current),
            );
          } catch (e) {
            setError(message(e));
          } finally {
            setBusy(false);
          }
        }}
      >
        <p className="sp-modal-lead">
          Organize clients, research, inventory, or anything you’re working on.
        </p>
        <div
          className="sp-db-template-grid"
          aria-label="Database starting point"
        >
          {(
            [
              [
                "blank",
                "Start from scratch",
                "A blank table, ready for your own properties.",
              ],
              [
                "clients",
                "Clients & acquisition",
                "Contacts, stages, value, and your next follow-up.",
              ],
              [
                "tasks",
                "Project tasks",
                "Status, planned day, and due date. Rows can be planned for today.",
              ],
            ] as const
          ).map(([value, title, text]) => (
            <button
              key={value}
              type="button"
              aria-pressed={template === value}
              className={`sp-db-template ${template === value ? "selected" : ""}`}
              onClick={() => {
                setTemplate(value);
                if (!name || name === "Clients" || name === "Tasks")
                  setName(value === "clients" ? "Clients" : value === "tasks" ? "Tasks" : "");
              }}
            >
              <span
                className={`sp-db-template-sketch ${value}`}
                aria-hidden="true"
              >
                <i />
                <i />
                <i />
              </span>
              <strong>
                {title}
                <span>
                  {template === value && <Icon name="check" size={13} />}
                </span>
              </strong>
              <small>{text}</small>
            </button>
          ))}
        </div>
        <button className="sp-import-start" type="button" onClick={importFile}>
          <Icon name="upload" size={20} />
          <span>
            <strong>Import a file</strong>
            <small>Bring a spreadsheet, CSV, or JSON into Space.</small>
          </span>
          <Icon name="arrow" size={16} />
        </button>
        <label className="sp-field">
          Database name
          <input
            aria-label="Database name"
            autoFocus
            required
            maxLength={200}
            placeholder={
              template === "clients"
                ? "Clients"
                : template === "tasks"
                ? "e.g. Launch tasks"
                : "e.g. Clients, Reading list, Content calendar"
            }
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </label>
        <label className="sp-field">
          Description <span className="sp-db-optional">Optional</span>
          <input
            aria-label="Database description"
            maxLength={500}
            placeholder="What will you keep here?"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </label>
        <div className="sp-modal-footer">
          <span className="sp-muted">
            {template === "clients"
              ? "Starts empty, with 5 ready-to-use properties"
              : template === "tasks"
              ? "Starts empty. Its rows appear in Tasks → Today"
              : "Add your own properties as you go"}
          </span>
          <div className="sp-actions">
            <button type="button" disabled={busy} onClick={close}>Cancel</button>
            <button className="sp-primary" disabled={busy || !name.trim()}>
              Create database
            </button>
          </div>
        </div>
      </form>
    </Modal>
  );
}

const NEW_PLANNED = "new";

// Parameters: a database without a task setting.
// What it does: guesses the status field, its closing options and the planned-day field.
// Output: a starting setting; planned is NEW_PLANNED when no date field fits.
function suggestedTracking(database: WorkspaceDatabase): TaskTracking | null {
  const status = database.fields.find((field) => field.type === "select");
  if (!status?.options?.length) return null;
  const done = status.options.filter((option) => /\b(done|complete|completed|closed|shipped|finished|won|lost|cancell?ed)\b/i.test(option));
  const dates = database.fields.filter((field) => field.type === "date");
  const planned = dates.find((field) => /plan|today|day/i.test(field.name)) ?? dates[0];
  return { status: status.id, done: done.length ? done : [status.options.at(-1)!], planned: planned?.id ?? NEW_PLANNED };
}

/**
 * Parameters: the latest database and the setting chosen in settings.
 * What it does: adds a “Planned for” date property when asked, then checks the setting.
 * Output: the database with its new task setting, or an error to show.
 */
function withTracking(database: WorkspaceDatabase, tracking: TaskTracking | null, plannedId: string): WorkspaceDatabase {
  if (!tracking) return { ...database, tasks: null };
  let fields = database.fields;
  if (tracking.planned === NEW_PLANNED) {
    const taken = new Set(fields.map((field) => field.name.toLowerCase()));
    const name = ["Planned for", "Planned day", "Planned date"].find((label) => !taken.has(label.toLowerCase()));
    if (!name) throw new Error("Choose a date property for the planned day.");
    if (fields.length >= 30) throw new Error("This database has 30 properties. Choose an existing date property for the planned day.");
    if (!fields.some((field) => field.id === plannedId)) fields = [...fields, { id: plannedId, name, type: "date" }];
    tracking = { ...tracking, planned: plannedId };
  }
  const next = { ...database, fields, tasks: tracking };
  validateTaskTracking(next, tracking);
  return next;
}

function DatabaseSettings({
  database,
  save,
  remove,
  close,
}: {
  database: WorkspaceDatabase;
  save: (change: { name: string; description: string; projectId: string | null; tracking: TaskTracking | null; plannedId: string }) => Promise<void>;
  remove: () => Promise<void>;
  close: () => void;
}) {
  const confirm = useConfirm();
  const data = useSpace();
  const [projectId, setProjectId] = useState(database.project_id ?? "");
  const [name, setName] = useState(database.name),
    [description, setDescription] = useState(database.description),
    [error, setError] = useState("");
  const [tracking, setTracking] = useState<TaskTracking | null>(database.tasks ?? null);
  const plannedId = useRef(crypto.randomUUID());
  const trackingHint = useId();
  const suggestion = suggestedTracking(database);
  const selects = database.fields.filter((field) => field.type === "select" && field.options?.length);
  const dates = database.fields.filter((field) => field.type === "date");
  const statusField = selects.find((field) => field.id === tracking?.status);
  const [busy, setBusy] = useState(false);
  const [removing, setRemoving] = useState(false);
  useFeedback({ error, loading: removing ? "Moving database to trash…" : "" });
  return (
    <Modal title="Database settings" onClose={close} className="sp-db-settings-modal">
      <form
        className="sp-modal-content"
        aria-busy={busy}
        onSubmit={async (e) => {
          e.preventDefault();
          if (busy) return;
          setBusy(true);
          setError("");
          try {
            withTracking(database, tracking, plannedId.current);
            await save({ name: name.trim(), description: description.trim(), projectId: projectId || null, tracking, plannedId: plannedId.current });
          } catch (e) {
            setError(message(e));
          } finally {
            setBusy(false);
          }
        }}
      >
        <label className="sp-field">
          Database name
          <input
            aria-label="Database name"
            autoFocus
            required
            maxLength={200}
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </label>
        <label className="sp-field">
          Description
          <textarea
            aria-label="Database description"
            maxLength={500}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </label>
        <label className="sp-field">
          Project
          <select aria-label="Project" value={projectId} onChange={(e) => setProjectId(e.target.value)}>
            <option value="">No project</option>
            {projectId && !data.projects.some((project) => project.id === projectId) && (
              <option value={projectId}>Unavailable project</option>
            )}
            {data.projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}
          </select>
        </label>
        <fieldset className="sp-db-task-tracking">
          <label className="sp-db-task-toggle">
            <input
              type="checkbox"
              checked={Boolean(tracking)}
              aria-label="Track rows as tasks"
              aria-describedby={trackingHint}
              disabled={!tracking && !suggestion}
              onChange={(e) => setTracking(e.target.checked ? database.tasks ?? suggestion : null)}
            />
            <span>
              <strong>Track rows as tasks</strong>
              <small id={trackingHint}>
                {!tracking && !suggestion
                  ? "Add a select property for status first."
                  : "Open rows can be planned for a day and appear in Tasks → Today."}
              </small>
            </span>
          </label>
          {tracking && (
            <div className="sp-db-task-fields">
              <label className="sp-field">
                Status property
                <select
                  aria-label="Status property"
                  value={tracking.status}
                  onChange={(e) => {
                    const field = selects.find((item) => item.id === e.target.value);
                    setTracking({ ...tracking, status: e.target.value, done: field?.options?.length ? [field.options.at(-1)!] : [] });
                  }}
                >
                  {selects.map((field) => <option key={field.id} value={field.id}>{field.name}</option>)}
                </select>
              </label>
              <div className="sp-field" role="group" aria-label="Statuses that mean done">
                Done when status is
                <div className="sp-db-task-done">
                  {statusField?.options?.map((option) => (
                    <label key={option}>
                      <input
                        type="checkbox"
                        checked={tracking.done.includes(option)}
                        onChange={(e) => setTracking({
                          ...tracking,
                          done: e.target.checked
                            ? statusField.options!.filter((item) => item === option || tracking.done.includes(item))
                            : tracking.done.filter((item) => item !== option),
                        })}
                      />
                      {option}
                    </label>
                  ))}
                </div>
              </div>
              <label className="sp-field">
                Planned day property
                <select
                  aria-label="Planned day property"
                  value={tracking.planned}
                  onChange={(e) => setTracking({ ...tracking, planned: e.target.value })}
                >
                  {dates.map((field) => <option key={field.id} value={field.id}>{field.name}</option>)}
                  <option value={NEW_PLANNED}>Add a “Planned for” date property</option>
                </select>
              </label>
            </div>
          )}
        </fieldset>
        <div className="sp-modal-footer">
          <button
            type="button"
            className="text-button sp-danger-text"
            disabled={busy}
            onClick={async () => {
              if (
                await confirm({
                  title: "Move database to trash?",
                  message: `Move “${database.name}” and its ${database.records.length} records to trash? You can restore them later.`,
                  confirmLabel: "Move to trash",
                })
              ) {
                setBusy(true);
                setRemoving(true);
                try {
                  await remove();
                } catch (e) {
                  setError(message(e));
                } finally {
                  setBusy(false);
                  setRemoving(false);
                }
              }
            }}
          >
            Move to trash
          </button>
          <button className="sp-primary" disabled={busy || !name.trim()}>
            Save changes
          </button>
        </div>
      </form>
    </Modal>
  );
}

export function DatabasesView({ base, id }: { base: string; id?: string }) {
  const data = useSpace(),
    router = useRouter(),
    params = useSearchParams();
  const [creating, setCreating] = useState(params.get("new") === "1");
  const [importing, setImporting] = useState(false);
  const [property, setProperty] = useState<
    DatabaseField | "new" | "group" | null
  >(null);
  const [record, setRecord] = useState<DatabaseRecord | "new" | null>(null);
  // Kept while the database is open, so the next record opens in the same layout.
  const [recordExpanded, setRecordExpanded] = useState(false);
  const beforeCloseRecord = useRef<(() => Promise<boolean>) | null>(null);
  const confirm = useConfirm();
  const canWrite = data.canWrite;
  const [initialValues, setInitialValues] = useState<
    Record<string, FieldValue>
  >({});
  const [settings, setSettings] = useState(false),
    [query, setQuery] = useState(""),
    [sort, setSort] = useState("updated"),
    [sortDirection, setSortDirection] = useState<"asc" | "desc">("asc"),
    [filterValue, setFilterValue] = useState("");
  const savedDatabase = data.databases.find((d) => d.id === id);
  const databaseKey = JSON.stringify([data.space?.id, id]);
  const [pendingChanges, setPendingChanges] = useState<{
    token: number;
    key: string;
    apply: (current: WorkspaceDatabase) => WorkspaceDatabase;
  }[]>([]);
  const changeToken = useRef(0);
  const database = savedDatabase && pendingChanges.reduce(
    (current, pending) => pending.key === databaseKey ? pending.apply(current) : current,
    savedDatabase,
  );
  const groupField = database?.fields.find(
    (f) => f.id === database.groupBy && f.type === "select",
  );
  const boardProperties = useDatabaseBoardProperties(database);
  const selects = database?.fields.filter((f) => f.type === "select") ?? [];
  const activeSort = sort === "name" || sort === "created" || database?.fields.some((field) => sort === `property:${field.id}`)
    ? sort : "updated";
  const records = database
    ? queryRecords(database, query, activeSort, groupField?.id ?? "", filterValue, sortDirection)
    : [];
  const recordPage = useListPage(
    records,
    JSON.stringify([id, query, activeSort, sortDirection, filterValue, database?.view]),
  );
  const selectionKey = JSON.stringify([data.space?.id, id, query, filterValue, database?.groupBy, database?.view]);
  const [selection, setSelection] = useState<{ key: string; ids: string[] }>({ key: "", ids: [] });
  const availableIds = new Set(records.map((row) => row.id));
  const selectedIds = new Set(selection.key === selectionKey ? selection.ids.filter((id) => availableIds.has(id)) : []);
  const selectedRows = records.filter((row) => selectedIds.has(row.id));
  const [rowFeedback, setRowFeedback] = useState("");
  useEffect(() => {
    setSelection((current) => current.key === selectionKey ? current : { key: selectionKey, ids: [] });
    setRowFeedback("");
  }, [selectionKey]);
  const deletingRows = useRef(false);
  const [saveError, setSaveError] = useState("");
  const [saving, setSaving] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [copyingRows, setCopyingRows] = useState(false);
  useFeedback({
    error: saveError,
    notice: rowFeedback,
    clearNotice: () => setRowFeedback(""),
    loading: saving ? creating ? "Creating database…" : "Saving database changes…"
      : refreshing ? "Loading latest database…" : copyingRows ? "Copying records…" : "",
  });
  const saveQueue = useRef(createDatabaseSaveQueue(saveRecovery));
  const pendingSaves = useRef(0);
  async function update(
    change: WorkspaceDatabase | ((current: WorkspaceDatabase) => WorkspaceDatabase),
    optimistic?: (current: WorkspaceDatabase) => WorkspaceDatabase,
  ) {
    if (!canWrite) throw new Error("You have view-only access to this Space.");
    const base = typeof change === "function" ? savedDatabase : change;
    if (!base) return;
    const token = ++changeToken.current;
    if (optimistic) setPendingChanges((pending) => [...pending, { token, key: databaseKey, apply: optimistic }]);
    if (!pendingSaves.current) setSaveError("");
    pendingSaves.current++;
    setSaving(true);
    try {
      return await saveQueue.current(
        base,
        typeof change === "function" ? change : () => change,
        data.saveDatabase,
        data.latestDatabase,
      );
    } catch (e) {
      setSaveError(message(e));
      throw e;
    } finally {
      if (optimistic) setPendingChanges((pending) => pending.filter((change) => change.token !== token));
      pendingSaves.current--;
      setSaving(pendingSaves.current > 0);
    }
  }
  function selectRows(ids: string[], checked: boolean) {
    setRowFeedback("");
    setSelection((current) => {
      const next = new Set(current.key === selectionKey ? current.ids.filter((id) => availableIds.has(id)) : []);
      for (const id of ids) checked ? next.add(id) : next.delete(id);
      return { key: selectionKey, ids: [...next] };
    });
  }
  async function copyRows(ids: string[]) {
    if (!database) return;
    setRowFeedback("");
    if (!navigator.clipboard?.writeText) throw new Error("Clipboard copy is unavailable in this browser.");
    const text = databaseRecordsTSV(database, ids);
    setCopyingRows(true);
    try { await navigator.clipboard.writeText(text); }
    catch { throw new Error("Could not copy rows. Allow clipboard access and try again."); }
    finally { setCopyingRows(false); }
    setRowFeedback(`Copied ${ids.length} ${ids.length === 1 ? "record" : "records"}.`);
  }
  async function duplicateRows(rows: readonly DatabaseRecord[]) {
    if (!rows.length) return;
    setRowFeedback("");
    const ids = [...new Set(rows.map((row) => row.id))];
    const duplicateIds = ids.map(() => crypto.randomUUID());
    await update((current) => duplicateDatabaseRecords(current, ids, duplicateIds));
    setRowFeedback(`Duplicated ${ids.length} ${ids.length === 1 ? "record" : "records"}.`);
  }
  async function deleteRows(rows: DatabaseRecord[]) {
    if (!canWrite || !rows.length || deletingRows.current) return;
    deletingRows.current = true;
    try {
      const closesRecord = record && record !== "new" && rows.some((row) => row.id === record.id);
      if (closesRecord && await beforeCloseRecord.current?.() === false) return;
      const label = rows.length === 1 ? `“${rows[0].name}”` : `${rows.length} selected records`;
      if (!(await confirm({ title: rows.length === 1 ? "Delete record?" : "Delete records?", message: `Delete ${label} from this database? This cannot be undone.`, confirmLabel: rows.length === 1 ? "Delete record" : `Delete ${rows.length} records` }))) return;
      await update((current) => deleteDatabaseRecords(current, rows));
      const removed = new Set(rows.map((row) => row.id));
      setSelection((current) => ({ ...current, ids: current.ids.filter((id) => !removed.has(id)) }));
      if (closesRecord) setRecord(null);
      setRowFeedback(`Deleted ${rows.length} ${rows.length === 1 ? "record" : "records"}.`);
    } finally { deletingRows.current = false; }
  }
  function updateView(change: Partial<Pick<WorkspaceDatabase, "view" | "groupBy">>) {
    const apply = (current: WorkspaceDatabase) => ({ ...current, ...change });
    void update(apply, apply).catch(() => {});
  }

  async function openRecord(next: DatabaseRecord | "new", group?: FieldValue) {
    if (next === "new" && (!canWrite || record === "new")) return;
    if (typeof record === "object" && record && typeof next === "object" && next.id === record.id) return;
    if (await beforeCloseRecord.current?.() === false) return;
    setInitialValues(groupField && group !== undefined ? { [groupField.id]: group } : {});
    setRecord(next);
  }
  // New records are typed inline: a draft row in the table, a draft card in a board column.
  const [tableDraft, setTableDraft] = useState(false);
  const [boardDraft, setBoardDraft] = useState<{ group: string | null } | null>(null);
  function addRecord() {
    if (!canWrite) return;
    if (database?.view === "board" && groupField) setBoardDraft({ group: filterValue || null });
    else setTableDraft(true);
  }
  async function createRecord(recordId: string, name: string, group?: string | null) {
    if (!database) return;
    const now = new Date().toISOString();
    // A record created under an active stage filter keeps that value, so it stays in view.
    const groupValue = group !== undefined ? group : filterValue || null;
    const values: Record<string, FieldValue> = groupField && groupValue ? { [groupField.id]: groupValue } : {};
    const draft: DatabaseRecord = { id: recordId, name, values, notes: "", createdAt: now, updatedAt: now };
    await update(
      (current) => applyRecordChanges(current, draft),
      (current) => current.records.some((row) => row.id === recordId) ? current : { ...current, records: [...current.records, draft] },
    );
    const hidden = query.trim() && !name.toLowerCase().includes(query.trim().toLowerCase());
    setRowFeedback(hidden ? `Added “${name}”. It is hidden by your search.` : `Added “${name}”.`);
  }
  async function saveRecord(next: DatabaseRecord, original?: DatabaseRecord): Promise<DatabaseRecord> {
    const saved = await update((current) => applyRecordChanges(current, next, original));
    const row = saved?.records.find((record) => record.id === next.id);
    if (!row) throw new Error("The record could not be saved. Reload the database.");
    setRecord(row);
    return row;
  }
  async function saveCell(
    row: DatabaseRecord,
    field: DatabaseField,
    value: FieldValue,
    optimistic = false,
  ) {
    if (!database) return;
    const updatedAt = new Date().toISOString();
    const preview = (current: WorkspaceDatabase) => ({
      ...current,
      records: current.records.map((record) => record.id === row.id ? {
        ...record, values: { ...record.values, [field.id]: value }, updatedAt,
      } : record),
    });
    await update((current) => {
      if (!current.fields.some((item) => item.id === field.id && item.type === field.type))
        throw new Error("This property changed. Reload the database before editing it.");
      const latest = current.records.find((r) => r.id === row.id);
      if (!latest) throw new Error("This record no longer exists. Reload the database.");
      const next = validateRecord(current, {
        ...latest,
        values: { ...latest.values, [field.id]: value },
        updatedAt,
      });
      return {
        ...current,
        records: current.records.map((r) => (r.id === row.id ? next : r)),
      };
    }, optimistic ? preview : undefined);
  }
  return (
    <>
      {id && <div className="sp-topbar sp-db-topbar">
        <div className="sp-breadcrumbs">
          <Link href={`${base}/databases`}>Databases</Link>
          {id && (
            <>
              <Icon name="chevron" size={13} />
              <span>{database?.name ?? "Database"}</span>
            </>
          )}
        </div>
        <div className="sp-actions">
          {database ? (
            <>
              <span className="sp-db-topbar-private">
                <Icon name="lock" size={11} />
                Personal database
              </span>
            </>
          ) : null}
        </div>
      </div>}
      {saveError && (
        <div className="sp-actions">
          <button
            disabled={saving || refreshing}
            onClick={() => {
              if (data.space) {
                setRefreshing(true);
                void data
                  .refreshItems(data.space.id)
                  .then(() => setSaveError(""))
                  .catch((e) => setSaveError(message(e)))
                  .finally(() => setRefreshing(false));
              }
            }}
          >
            Load latest saved version
          </button>
        </div>
      )}
      {!id ? (
        <div className="sp-db-library">
          <div className="sp-work-heading sp-db-library-heading">
            <div>
              <h1>Databases</h1>
              <p>
                Give your clients, ideas, and research a structure of their own.
              </p>
            </div>
            <button className="sp-primary" disabled={!canWrite} onClick={() => setCreating(true)}>
              <Icon name="plus" size={16} />
              New database
            </button>
          </div>
          <div className="sp-db-library-label">
            <h2>Your databases</h2>
            <span>
              {data.databases.length}{" "}
              {data.databases.length === 1 ? "database" : "databases"}
            </span>
          </div>
          <div className="sp-db-library-grid">
            {data.databases.map((db) => (
              <Link
                className="sp-db-library-card"
                key={db.id}
                href={`${base}/databases/${db.id}`}
              >
                <div className="sp-db-card-top">
                  <span className="sp-db-large-icon">
                    <Icon name="database" size={22} />
                  </span>
                  <Icon name="arrow" size={15} />
                </div>
                <h3>{db.name}</h3>
                <p>{db.description || "Your records, organized your way."}</p>
                <div className="sp-db-card-columns">
                  <span>Name</span>
                  {db.fields.slice(0, 3).map((field) => (
                    <span key={field.id}>{field.name}</span>
                  ))}
                </div>
                <div className="sp-db-card-footer">
                  <span>{db.records.length} records</span>
                  <span>{db.fields.length + 1} properties</span>
                </div>
              </Link>
            ))}
            <button
              className="sp-db-add-card"
              disabled={!canWrite}
              onClick={() => setCreating(true)}
            >
              <span>
                <Icon name="plus" size={22} />
              </span>
              <strong>Create a database</strong>
              <small>Start fresh or use a template.</small>
            </button>
          </div>
          <div className="sp-db-library-note">
            <Icon name="sparkle" size={18} />
            <div>
              <strong>Built around your work.</strong>
              <p>
                Start with a clients list. Add a stage, a follow-up date, or any
                other detail you need.
              </p>
            </div>
          </div>
        </div>
      ) : !database ? (
        <Empty icon="database" title="Database not found">
          <p>It may have been moved to trash.</p>
          <Link className="sp-button" href={`${base}/databases`}>
            Back to databases
          </Link>
        </Empty>
      ) : (
        <div className={`sp-db-context${record && recordExpanded ? " is-record-expanded" : ""}`}>
        <div className="sp-db-workspace" aria-busy={saving || refreshing}>
          <div className="sp-db-heading">
            <div className="sp-db-title-row">
              <span className="sp-db-large-icon">
                <Icon name="database" size={25} />
              </span>
              <h1>{database.name}</h1>
            </div>
            <p>
              {database.description ||
                "Every record has a place. Make this database your own."}
            </p>
          </div>
          <div className="sp-db-viewbar">
            <div className="sp-db-view-tabs" aria-label="Database view">
              <button
                aria-pressed={database.view === "table"}
                disabled={!canWrite}
                onClick={() => updateView({ view: "table" })}
              >
                <Icon name="database" size={15} />
                Table
              </button>
              <button
                aria-pressed={database.view === "board"}
                disabled={!canWrite}
                onClick={() => updateView({ view: "board" })}
              >
                <Icon name="board" size={15} />
                Board
              </button>
            </div>
            <div className="sp-db-view-actions">
              <button className="text-button" aria-label="Database settings" disabled={!canWrite} onClick={() => setSettings(true)}>
                <Icon name="settings" size={14} />
                Settings
              </button>
              <button className="text-button" disabled={!canWrite} onClick={() => setProperty("new")}>
                <Icon name="plus" size={13} />
                Property
              </button>
              <button className="sp-primary" disabled={!canWrite} onClick={() => addRecord()}>
                <Icon name="plus" size={14} />
                New record
              </button>
            </div>
          </div>
          <div className={`sp-db-tools${database.view === "board" ? " sp-db-board-tools" : ""}`}
            onFocusCapture={event => {
              if (database.view === "board" && !event.target.closest("dialog"))
                event.target.scrollIntoView({ block: "nearest", inline: "nearest" });
            }}>
            <label className="sp-search">
              <Icon name="search" size={15} />
              <input
                aria-label="Search records"
                placeholder="Find a record…"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </label>
            <div className="sp-db-filters">
              {selects.length > 0 && (
                <label>
                  <Icon name="board" size={13} />
                  <span className="sr-only">Group board by</span>
                  <select
                    aria-label="Group board by"
                    disabled={!canWrite}
                    value={database.groupBy ?? ""}
                    onChange={(e) => {
                      updateView({ groupBy: e.target.value });
                      setFilterValue("");
                    }}
                  >
                    {selects.map((field) => (
                      <option value={field.id} key={field.id}>
                        Group: {field.name}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              {groupField && (
                <select
                  aria-label={`Filter by ${groupField.name}`}
                  value={filterValue}
                  onChange={(e) => setFilterValue(e.target.value)}
                >
                  <option value="">
                    All {groupField.name.toLowerCase()} options
                  </option>
                  {groupField.options?.map((option) => (
                    <option key={option} value={option}>{option}</option>
                  ))}
                </select>
              )}
              <select
                aria-label="Sort records"
                value={activeSort}
                onChange={(e) => setSort(e.target.value)}
              >
                <option value="updated">Last edited</option>
                <option value="created">Date added</option>
                <option value="name">Name</option>
                {database.fields.map((field) => (
                  <option key={field.id} value={`property:${field.id}`}>
                    {field.name}
                  </option>
                ))}
              </select>
              {activeSort !== "updated" && (
                <select
                  aria-label="Sort direction"
                  value={sortDirection}
                  onChange={(e) => setSortDirection(e.target.value === "desc" ? "desc" : "asc")}
                >
                  <option value="asc">{activeSort === "created" ? "Oldest first" : "Ascending"}</option>
                  <option value="desc">{activeSort === "created" ? "Newest first" : "Descending"}</option>
                </select>
              )}
              {database.view === "board" && groupField && <DatabaseBoardProperties
                key={`${databaseKey}:${groupField.id}`}
                database={database} field={groupField} {...boardProperties}
              />}
            </div>
          </div>
          {database.view === "table" && selectedRows.length > 0 && <SelectedRowActions
            count={selectedRows.length} canWrite={canWrite} saving={saving}
            clear={() => { setSelection({ key: selectionKey, ids: [] }); setRowFeedback(""); }}
            duplicate={() => duplicateRows(selectedRows)}
            copy={() => copyRows(selectedRows.map((row) => row.id))} remove={() => deleteRows(selectedRows)}
          />}
          {database.view === "table" ? (<>
            <DatabaseTable
              database={database} rows={recordPage.items} canWrite={canWrite} saving={saving}
              selected={selectedIds} select={selectRows} property={setProperty} open={openRecord}
              save={saveCell} drafting={tableDraft} setDrafting={setTableDraft} create={createRecord}
              duplicate={(row) => duplicateRows([row])} copy={copyRows} remove={deleteRows}
            />
            <ListPageControls {...recordPage} />
          </>) : !groupField ? (
            <Empty icon="board" title="Give your board a way to group records">
              <p>
                Add a select property such as Stage or Status. Its options
                become the columns of your board.
              </p>
              <button disabled={!canWrite} onClick={() => setProperty("group")}>
                Add a select property
              </button>
            </Empty>
          ) : (
            <DatabaseBoard
              key={`${database.id}:${groupField.id}`}
              database={database} field={groupField} rows={records}
              pageKey={JSON.stringify([query, activeSort, sortDirection, filterValue])}
              visibleFields={database.fields.filter(property => property.id !== groupField.id && boardProperties.selectedIds.includes(property.id))}
              canWrite={canWrite} open={openRecord} draft={boardDraft} setDraft={setBoardDraft}
              create={(recordId, name, group) => createRecord(recordId, name, group)}
              move={(row, group) => saveCell(row, groupField, group, true)}
            />
          )}
          {(query || filterValue) && records.length === 0 && (
            <div className="sp-db-no-results">
              <p>No matching records.</p>
              <button
                onClick={() => {
                  setQuery("");
                  setFilterValue("");
                }}
              >
                Clear filters
              </button>
            </div>
          )}
          <div className="sp-db-status">
            <span>
              {records.length} of {database.records.length} records
              <span> · </span>
              {database.fields.length + 1} properties
            </span>
            <span>Private Space</span>
          </div>
        </div>
        {record && <RecordPanel
          key={record === "new" ? "new" : record.id}
          database={database}
          record={record === "new" ? undefined : record}
          initialValues={initialValues}
          canWrite={canWrite}
          expanded={recordExpanded}
          setExpanded={setRecordExpanded}
          beforeClose={beforeCloseRecord}
          save={saveRecord}
          close={() => setRecord(null)}
          remove={async () => {
            if (record !== "new") await update((current) => ({
              ...current, records: current.records.filter((row) => row.id !== record.id),
            }));
            setRecord(null);
          }}
        />}
        </div>
      )}
      {!id && <ItemTrash kind="database" />}
      {importing && canWrite && (
        <ImportDatabaseDialog
          close={() => setImporting(false)}
          created={(db) => {
            setImporting(false);
            router.push(`${base}/databases/${db.id}`);
          }}
        />
      )}
      {creating && canWrite && (
        <CreateDatabase
          importFile={() => {
            setCreating(false);
            setImporting(true);
          }}
          close={() => {
            setCreating(false);
            if (params.has("new")) router.replace(`${base}/databases`);
          }}
          created={async (db) => {
            await update(db);
            setCreating(false);
            router.push(`${base}/databases/${db.id}`);
          }}
        />
      )}
      {database && property && canWrite && (
        <PropertyForm
          database={database}
          field={typeof property === "object" ? property : undefined}
          defaultSelect={property === "group"}
          close={() => setProperty(null)}
          save={async (field, renames) => {
            await update((current) => applyDatabaseField(current, field, typeof property === "object" ? property : undefined, renames));
            setProperty(null);
          }}
        />
      )}
      {database && settings && canWrite && (
        <DatabaseSettings
          database={database}
          close={() => setSettings(false)}
          save={async ({ name, description, projectId, tracking, plannedId }) => {
            await update((current) => withTracking({ ...current, name, description, project_id: projectId }, tracking, plannedId));
            setSettings(false);
          }}
          remove={async () => {
            await data.deleteDatabase(database.id);
            setSettings(false);
            router.push(`${base}/databases`);
          }}
        />
      )}
    </>
  );
}
