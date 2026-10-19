import assert from "node:assert/strict";
import { test } from "node:test";
import { mergeTaskSummary, optimisticTask, settleLimited, taskColumnLabel, taskColumnsWithResources } from "../lib/space/task-model.ts";
import { PROXY_ALLOWED } from "../lib/space/proxy-allowlist.ts";

const task = { id: "one", kind: "task", status: "todo", title: "A task", body: "Keep this body", project: "project", priority: "High", revision: 4 };

test("pending task status does not replace body, metadata, or revision and rolls back by removing its overlay", () => {
  assert.deepEqual(optimisticTask(task, { one: "backlog" }), { ...task, status: "backlog" });
  assert.equal(optimisticTask(task, {}), task);
  assert.equal(optimisticTask(task, { other: "done" }), task);
});

test("task refresh keeps the just-saved body and status instead of waiting for another detail read", () => {
  const { body, ...summary } = task;
  assert.deepEqual(mergeTaskSummary(summary, task), task);
  assert.equal(mergeTaskSummary({ ...summary, revision: 3, status: "done" }, task), task);
  assert.deepEqual(mergeTaskSummary({ ...summary, revision: 5, status: "done" }, task), {
    ...summary, revision: 5, status: "done",
  }, "newer summaries must not inherit the old revision's body");
  assert.equal(mergeTaskSummary(summary), summary);
  assert.equal(mergeTaskSummary(summary, { ...task, id: "other" }), summary);
});

test("Needs you is always a column before Done, with or without flagged tasks", () => {
  const columns = [{ value: "todo", label: "To do" }, { value: "in_progress", label: "In progress" }, { value: "done", label: "Done" }];
  const withNeedsYou = [columns[0], columns[1], { value: "needs_you", label: "Needs you" }, columns[2]];
  assert.deepEqual(taskColumnsWithResources(columns, [task]), withNeedsYou);
  assert.deepEqual(taskColumnsWithResources(columns, [{ ...task, status: "needs_you" }, { ...task, status: "needs_you" }]), withNeedsYou);
  assert.deepEqual(taskColumnsWithResources([columns[0]], []), [
    columns[0], { value: "in_progress", label: "In progress" }, { value: "needs_you", label: "Needs you" }, { value: "done", label: "Done" },
  ], "missing built-in columns are restored in their usual order");
});

test("a status not yet saved as a column shows under the label the API gives it", () => {
  const columns = [{ value: "todo", label: "To do" }, { value: "in_progress", label: "In progress" }, { value: "needs_you", label: "Needs you" }, { value: "done", label: "Done" }];
  assert.deepEqual(taskColumnsWithResources(columns, [task, { ...task, status: "review_queue" }, { ...task, status: "review_queue" }]), [
    ...columns.slice(0, 3), { value: "review_queue", label: "Review queue" }, columns[3],
  ]);
  assert.equal(columns.length, 4);
});

test("column names are trimmed and empty, long, or duplicate labels rejected", () => {
  const columns = [{ value: "todo", label: "To do" }];
  assert.equal(taskColumnLabel("  Backlog  ", columns), "Backlog");
  for (const value of ["", "   ", "a".repeat(81), " to DO "])
    assert.throws(() => taskColumnLabel(value, columns));
});

test("task API proxy permits only column and resource-status task paths", () => {
  const allowed = PROXY_ALLOWED;
  const space = "11111111-1111-4111-8111-111111111111";
  const id = "22222222-2222-4222-8222-222222222222";
  assert.equal(allowed.test(`spaces/${space}/tasks/columns`), true);
  assert.equal(allowed.test(`spaces/${space}/tasks/${id}/status`), true);
  assert.equal(allowed.test(`spaces/${space}/tasks/delete-all`), false);
  assert.equal(allowed.test(`spaces/${space}/tasks/${id}/status/extra`), false);
});

test("bulk task work keeps a bounded number in flight, continues past failures, and keeps input order", async () => {
  let active = 0, peak = 0;
  const results = await settleLimited([1, 2, 3, 4, 5, 6, 7], 3, async value => {
    active++; peak = Math.max(peak, active);
    await new Promise(resolve => setTimeout(resolve, value % 3));
    active--;
    if (value === 4) throw new Error("four failed");
    return value * 10;
  });
  assert.equal(peak, 3);
  assert.deepEqual(results.map(result => result.status === "fulfilled" ? result.value : result.reason.message),
    [10, 20, 30, "four failed", 50, 60, 70]);
  assert.deepEqual(await settleLimited([], 4, async () => 1), []);
});
