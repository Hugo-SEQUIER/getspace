import { fieldValue, validateTaskTracking, type DatabaseRecord, type TaskTracking, type WorkspaceDatabase } from "./databases.ts";
import type { TaskColumn } from "./types.ts";

export type PlannedRow = {
  database: WorkspaceDatabase;
  record: DatabaseRecord;
  status: string | null;
  planned: string | null;
};

type TrackedDatabase = WorkspaceDatabase & { tasks: TaskTracking };

// Parameters: a date, by default now.
// What it does: names the person's calendar day, not the UTC one, so evening work stays on today.
// Output: the day as YYYY-MM-DD.
export function localDay(date = new Date()): string {
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

function tracked(databases: readonly WorkspaceDatabase[]): TrackedDatabase[] {
  return databases.filter((database): database is TrackedDatabase => {
    if (database.deleted || !database.tasks) return false;
    try { validateTaskTracking(database, database.tasks); return true; } catch { return false; }
  });
}

function openRows(databases: readonly WorkspaceDatabase[]): PlannedRow[] {
  return tracked(databases).flatMap((database) => database.records.flatMap((record) => {
    const status = record.values[database.tasks.status];
    const planned = record.values[database.tasks.planned];
    if (typeof status === "string" && database.tasks.done.includes(status)) return [];
    return [{
      database,
      record,
      status: typeof status === "string" && status ? status : null,
      planned: typeof planned === "string" && planned ? planned : null,
    }];
  }));
}

const byDayThenName = (a: PlannedRow, b: PlannedRow) =>
  (a.planned ?? "").localeCompare(b.planned ?? "") || a.record.name.localeCompare(b.record.name, "en", { sensitivity: "base" });

// Parameters: every loaded database and the person's current day.
// What it does: offers open rows that are not planned yet or are planned for a later day.
// Output: the candidates, grouped by their database in name order.
export function plannableRows(databases: readonly WorkspaceDatabase[], today: string): PlannedRow[] {
  return openRows(databases)
    .filter((row) => row.planned === null || row.planned > today)
    .sort((a, b) => a.database.name.localeCompare(b.database.name) || byDayThenName(a, b));
}

/**
 * Parameters: the latest database, a row ID and the change: a planned day (null clears it)
 * and optionally a new status.
 * What it does: changes only that row, so a concurrent edit to another row survives.
 * Output: the new database snapshot, or an error when the row or setting is gone.
 */
export function changeTaskRow(
  database: WorkspaceDatabase,
  recordId: string,
  change: { planned?: string | null; status?: string },
): WorkspaceDatabase {
  const tracking = database.tasks;
  if (!tracking) throw new Error(`${database.name} is no longer tracked as tasks.`);
  validateTaskTracking(database, tracking);
  const record = database.records.find((row) => row.id === recordId);
  if (!record) throw new Error("This row no longer exists. Reload the database.");
  const values = { ...record.values };
  for (const [key, fieldId] of [["planned", tracking.planned], ["status", tracking.status]] as const) {
    if (!Object.hasOwn(change, key)) continue;
    const field = database.fields.find((item) => item.id === fieldId)!;
    values[fieldId] = fieldValue(field, change[key] ?? null);
  }
  const updated = { ...record, values, updatedAt: new Date().toISOString() };
  return { ...database, records: database.records.map((row) => row.id === recordId ? updated : row) };
}

/**
 * Parameters: every loaded database and the person's current day.
 * What it does: picks the rows the Tasks board shows beside native tasks: every row planned
 * for today, closed ones included so the day's finished work stays visible, and open rows
 * left over from earlier days.
 * Output: the rows, sorted by day then name.
 */
export function boardRows(databases: readonly WorkspaceDatabase[], today: string): PlannedRow[] {
  return tracked(databases).flatMap((database) => database.records.flatMap((record) => {
    const status = record.values[database.tasks.status];
    const planned = record.values[database.tasks.planned];
    if (typeof planned !== "string" || !planned || planned > today) return [];
    const done = typeof status === "string" && database.tasks.done.includes(status);
    if (done && planned < today) return [];
    return [{ database, record, status: typeof status === "string" && status ? status : null, planned }];
  })).sort(byDayThenName);
}

const sameLabel = (a: string, b: string) => a.trim().toLowerCase() === b.trim().toLowerCase();

// Parameters: a board row and the board's columns.
// What it does: places a closed row under Done and any other row under the column named like
// its status; a status no column matches lands in the first column.
// Output: the column value.
export function rowColumn(row: PlannedRow, columns: readonly TaskColumn[]): string {
  const done = row.status !== null && row.database.tasks!.done.includes(row.status);
  const match = done
    ? columns.find((column) => column.value === "done")
    : columns.find((column) => column.value !== "done" && row.status !== null && sameLabel(column.label, row.status));
  return (match ?? columns[0])?.value ?? "todo";
}

/**
 * Parameters: a row's database and the board column it was dropped on.
 * What it does: finds the row status that column stands for: the first done status for Done,
 * otherwise the status option named like the column.
 * Output: the status option, or an error naming the missing status.
 */
export function statusForColumn(database: WorkspaceDatabase, column: TaskColumn): string {
  const tracking = database.tasks;
  if (!tracking) throw new Error(`${database.name} is no longer tracked as tasks.`);
  if (column.value === "done") return tracking.done[0];
  const options = database.fields.find((field) => field.id === tracking.status)?.options ?? [];
  const option = options.find((item) => sameLabel(item, column.label) && !tracking.done.includes(item));
  if (!option) throw new Error(`${database.name} has no “${column.label}” status. Add it to the database or change the row there.`);
  return option;
}
