import { statuses, type Resource, type Status, type TaskColumn } from "./types.ts";

// Parameters: saved columns and the tasks currently available in the workspace.
// What it does: keeps built-in and unknown task statuses visible without duplicates. A
// built-in status that is not a stored column, such as needs_you, appears before Done
// with its own label while a task carries it.
// Output: the ordered columns to display.
export function taskColumnsWithResources(columns: TaskColumn[], resources: Resource[]): TaskColumn[] {
  const result = [...columns];
  for (const task of resources) {
    if (task.kind !== "task" || result.some(item => item.value === task.status)) continue;
    const builtIn = statuses.find(status => status.value === task.status);
    const done = result.findIndex(item => item.value === "done");
    if (builtIn && done >= 0) result.splice(done, 0, builtIn);
    else result.push(builtIn ?? { value: task.status, label: task.status.replaceAll("_", " ") });
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
