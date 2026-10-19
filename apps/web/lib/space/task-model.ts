import { statuses, type Resource, type Status, type TaskColumn } from "./types.ts";

// Parameters: a status value, such as "in_review".
// What it does: names it the way the API labels a status it saves as a new column.
// Output: a readable label, such as "In review".
export function statusLabel(value: string): string {
  const words = value.replaceAll("_", " ").replaceAll("-", " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

// Parameters: columns and a column to place before Done.
// What it does: inserts it before Done, or at the end when there is no Done column.
// Output: nothing; `columns` is changed in place.
export function insertBeforeDone(columns: TaskColumn[], column: TaskColumn) {
  const done = columns.findIndex(item => item.value === "done");
  if (done >= 0) columns.splice(done, 0, column);
  else columns.push(column);
}

// Parameters: saved columns and the tasks currently available in the workspace.
// What it does: always shows the built-in Needs you column before Done, even when empty, so
// the board keeps one shape. A status a task carries that is not yet a saved column (the
// API saves it as one) shows under the label the API gives it.
// Output: the ordered columns to display.
export function taskColumnsWithResources(columns: TaskColumn[], resources: Resource[]): TaskColumn[] {
  const result = [...columns];
  for (const builtIn of statuses)
    if (!result.some(item => item.value === builtIn.value)) insertBeforeDone(result, builtIn);
  for (const task of resources) {
    if (task.kind !== "task" || result.some(item => item.value === task.status)) continue;
    insertBeforeDone(result, { value: task.status, label: statusLabel(task.status) });
  }
  return result;
}

// Parameters: a task and the status changes that are still being saved.
// What it does: overlays a pending status without changing saved content or revision.
// Output: the visible task, or the original task if no change is pending.
export function optimisticTask(task: Resource, pending: Readonly<Record<string, Status>>): Resource {
  return Object.hasOwn(pending, task.id) ? { ...task, status: pending[task.id] } : task;
}

// Parameters: an incoming task summary and the locally known task, if any.
// What it does: retains saved content at the same revision and ignores older summaries.
// Output: the latest task without carrying a body into a newer revision.
export function mergeTaskSummary(summary: Resource, previous?: Resource): Resource {
  if (summary.kind !== "task" || previous?.kind !== "task" || previous.id !== summary.id || summary.revision > previous.revision)
    return summary;
  return summary.revision < previous.revision ? previous : { ...previous, ...summary };
}

// Parameters: a column label and the existing columns.
// What it does: rejects empty or duplicate labels before sending a column command.
// Output: the trimmed label.
export function taskColumnLabel(label: string, columns: TaskColumn[]): string {
  const value = label.trim();
  if (!value || value.length > 80) throw new Error("Use a column name between 1 and 80 characters.");
  if (columns.some(column => column.label.toLocaleLowerCase() === value.toLocaleLowerCase()))
    throw new Error("A column with this name already exists.");
  return value;
}

// Parameters: items, the most to run at once, and the work for one item.
// What it does: runs the work in order with at most `limit` items in flight and continues past failures.
// Output: one settled result per item, in input order.
export async function settleLimited<T, R>(
  items: readonly T[], limit: number, work: (item: T) => Promise<R>,
): Promise<PromiseSettledResult<R>[]> {
  const results: PromiseSettledResult<R>[] = new Array(items.length);
  let next = 0;
  async function worker() {
    while (next < items.length) {
      const index = next++;
      try { results[index] = { status: "fulfilled", value: await work(items[index]) }; }
      catch (reason) { results[index] = { status: "rejected", reason }; }
    }
  }
  await Promise.all(Array.from({ length: Math.min(Math.max(1, limit), items.length) }, worker));
  return results;
}
