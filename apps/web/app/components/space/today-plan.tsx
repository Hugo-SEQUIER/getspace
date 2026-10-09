import Link from "@web/lib/navigation";
import { useRef, useState } from "react";
import { message } from "../../../lib/client";
import { createDatabaseSaveQueue } from "../../../lib/space/database-save-queue";
import { changeTaskRow, plannableRows, type PlannedRow } from "../../../lib/space/today";
import { useFeedback } from "../notifications";
import { saveRecovery } from "./database-save-recovery";
import { useSpace } from "./provider";
import { Modal } from "./ui";
import { Icon } from "./ui-icon";
import "./today.css";

const PICKER_LIMIT = 50;

export const rowKey = (row: PlannedRow) => `${row.database.id}:${row.record.id}`;

function shortDay(day: string) {
  return new Date(`${day}T00:00:00`).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
}

/**
 * Parameters: none.
 * What it does: saves changes to task database rows through one queue per database, so the
 * board's moves and the picker's choices land on the latest revision in order.
 * Output: the keys of rows being saved and a change function that reports its own errors.
 */
export function useTaskRowChanges() {
  const data = useSpace();
  const queue = useRef(createDatabaseSaveQueue(saveRecovery));
  const [saving, setSaving] = useState<ReadonlySet<string>>(() => new Set());
  const [error, setError] = useState("");
  useFeedback({ error, loading: saving.size ? "Saving database rows…" : "" });
  async function change(row: PlannedRow, next: { planned?: string | null; status?: string }) {
    const key = rowKey(row);
    setError("");
    setSaving((old) => new Set(old).add(key));
    try {
      await queue.current(row.database, (current) => changeTaskRow(current, row.record.id, next), data.saveDatabase, data.latestDatabase);
    } catch (e) {
      setError(`Could not update “${row.record.name}”: ${message(e)}`);
    } finally {
      setSaving((old) => { const rest = new Set(old); rest.delete(key); return rest; });
    }
  }
  return { saving, change, fail: setError };
}

/**
 * Parameters: the workspace route base, the person's day, the row change function and close.
 * What it does: lists open rows of task databases not planned for today and plans the chosen
 * ones for today, which puts them on the Tasks board.
 * Output: the picker dialog.
 */
export function AddFromDatabases({
  base,
  today,
  saving,
  change,
  close,
}: {
  base: string;
  today: string;
  saving: ReadonlySet<string>;
  change: (row: PlannedRow, next: { planned: string }) => Promise<void>;
  close: () => void;
}) {
  const data = useSpace();
  const [query, setQuery] = useState("");
  const tracked = data.databases.filter((database) => database.tasks && !database.deleted);
  const needle = query.trim().toLowerCase();
  const candidates = plannableRows(data.databases, today).filter((row) =>
    !needle || row.record.name.toLowerCase().includes(needle) || row.database.name.toLowerCase().includes(needle));
  return (
    <Modal title="Add from databases" onClose={close} wide className="sp-today-picker-modal">
      <div className="sp-modal-content">
        {!data.databaseReady ? (
          <p className="sp-today-empty">{data.databaseError ? "Task databases could not be loaded." : "Loading task databases…"}</p>
        ) : !tracked.length ? (
          <div className="sp-today-empty">
            <p>
              No database is tracked as tasks yet. Create one from the Project tasks template, or
              turn on “Track rows as tasks” in a database’s settings.
            </p>
            <Link href={`${base}/databases?new=1`} className="sp-today-create">Create a task database</Link>
          </div>
        ) : (
          <>
            <p className="sp-modal-lead">Rows you add are planned for today and appear on the board until they are done.</p>
            <label className="sp-search">
              <Icon name="search" size={16} />
              <input
                aria-label="Search task databases"
                placeholder="Search open rows…"
                autoFocus
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </label>
            <div className="sp-today-picker" role="list" aria-label="Open rows">
              {candidates.slice(0, PICKER_LIMIT).map((row) => {
                const busy = saving.has(rowKey(row));
                return (
                  <div className="sp-task-row sp-today-row" role="listitem" key={rowKey(row)} aria-busy={busy}>
                    <span className="sp-task-name">{row.record.name}</span>
                    <span className="sp-task-project">
                      <Icon name="database" size={13} />
                      {row.database.name}
                    </span>
                    {row.status && <span className="sp-today-day">{row.status}</span>}
                    {row.planned && <span className="sp-today-day">{shortDay(row.planned)}</span>}
                    <button
                      type="button"
                      aria-label={`Add ${row.record.name} to today`}
                      disabled={!data.canWrite || busy}
                      onClick={() => void change(row, { planned: today })}
                    >
                      <Icon name="plus" size={14} />
                      Today
                    </button>
                  </div>
                );
              })}
              {candidates.length > PICKER_LIMIT && (
                <p className="sp-today-empty">Showing {PICKER_LIMIT} of {candidates.length}. Search to narrow the list.</p>
              )}
              {!candidates.length && (
                <p className="sp-today-empty">{needle ? "No open rows match this search." : "Every open row is already on the board."}</p>
              )}
            </div>
          </>
        )}
        <div className="sp-modal-footer">
          <span />
          <button type="button" className="sp-primary" onClick={close}>Done</button>
        </div>
      </div>
    </Modal>
  );
}
