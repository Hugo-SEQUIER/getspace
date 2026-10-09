import test from "node:test";
import assert from "node:assert/strict";
import { applyDatabaseField, createDatabase, isWorkspaceDatabase } from "../lib/space/databases.ts";
import { boardRows, changeTaskRow, localDay, plannableRows, rowColumn, statusForColumn } from "../lib/space/today.ts";

function row(database, id, name, status, planned) {
  const values = { [database.tasks.status]: status, [database.tasks.planned]: planned };
  return { id, name, notes: "", values, updatedAt: "2026-10-01T09:00:00Z" };
}

function launch() {
  const database = createDatabase("tasks", "Launch", "", "launch");
  database.records = [
    row(database, "a", "Write copy", "To do", "2026-10-08"),
    row(database, "b", "Fix footer", "In progress", "2026-10-06"),
    row(database, "c", "Ship v1", "Done", "2026-10-08"),
    row(database, "d", "Pick domain", "To do", null),
    row(database, "e", "Record demo", "To do", "2026-10-12"),
  ];
  return database;
}

test("the tasks template is a valid task list grouped by status", () => {
  const database = createDatabase("tasks", "Launch", "", "x");
  assert.deepEqual(database.tasks, { status: "x-status", done: ["Done"], planned: "x-planned" });
  assert.equal(database.groupBy, "x-status");
  assert.ok(isWorkspaceDatabase(database));
  assert.equal(createDatabase("clients", "Clients", "").tasks, undefined);
});

test("a task setting that names a missing or mistyped field fails validation", () => {
  const database = launch();
  assert.equal(isWorkspaceDatabase({ ...database, tasks: { ...database.tasks, planned: "launch-status" } }), false);
  assert.equal(isWorkspaceDatabase({ ...database, tasks: { ...database.tasks, done: ["Shipped"] } }), false);
  assert.equal(isWorkspaceDatabase({ ...database, tasks: { ...database.tasks, done: [] } }), false);
});

const columns = [
  { value: "todo", label: "To do" },
  { value: "in_progress", label: "In progress" },
  { value: "needs_you", label: "Needs you" },
  { value: "done", label: "Done" },
];

test("the board shows today's rows, closed ones included, and open rows left from earlier days", () => {
  const clients = createDatabase("clients", "Clients", "");
  const rows = boardRows([launch(), clients], "2026-10-08");
  assert.deepEqual(rows.map((r) => r.record.name), ["Fix footer", "Ship v1", "Write copy"]);
  assert.deepEqual(rows.map((r) => rowColumn(r, columns)), ["in_progress", "done", "todo"]);
  assert.deepEqual(plannableRows([launch()], "2026-10-08").map((r) => r.record.name), ["Pick domain", "Record demo"]);
  assert.deepEqual(boardRows([{ ...launch(), deleted: true }], "2026-10-08"), []);
  // The next day, a row closed earlier leaves the board and an open one carries over.
  assert.deepEqual(boardRows([launch()], "2026-10-09").map((r) => r.record.name), ["Fix footer", "Write copy"]);
});

test("a row whose status no column names lands in the first column", () => {
  const database = launch();
  database.fields[0] = { ...database.fields[0], options: ["Backlog", "In progress", "Done"] };
  database.records[0].values["launch-status"] = "Backlog";
  const [row] = boardRows([database], "2026-10-08").filter((r) => r.record.id === "a");
  assert.equal(rowColumn(row, columns), "todo");
});

test("dropping a row on a column picks the status that column names", () => {
  const database = launch();
  assert.equal(statusForColumn(database, columns[0]), "To do");
  assert.equal(statusForColumn(database, columns[1]), "In progress");
  assert.equal(statusForColumn(database, columns[3]), "Done");
  assert.throws(() => statusForColumn(database, columns[2]), /has no “Needs you” status/);
});

test("planning and closing a row changes only that row", () => {
  const database = launch();
  const planned = changeTaskRow(database, "d", { planned: "2026-10-08" });
  assert.equal(planned.records.find((r) => r.id === "d").values["launch-planned"], "2026-10-08");
  assert.deepEqual(planned.records.filter((r) => r.id !== "d"), database.records.filter((r) => r.id !== "d"));
  const closed = changeTaskRow(planned, "a", { status: "Done" });
  assert.equal(rowColumn(boardRows([closed], "2026-10-08").find((r) => r.record.id === "a"), columns), "done");
  assert.equal(changeTaskRow(database, "a", { planned: null }).records[0].values["launch-planned"], null);
  assert.throws(() => changeTaskRow(database, "a", { status: "Shipped" }), /Choose an option/);
  assert.throws(() => changeTaskRow(database, "missing", { planned: null }), /no longer exists/);
  assert.throws(() => changeTaskRow({ ...database, tasks: null }, "a", { planned: null }), /no longer tracked/);
});

test("done statuses follow renamed options and a tracked field keeps its type", () => {
  const database = launch();
  const status = database.fields[0];
  const renamed = applyDatabaseField(database, { ...status, options: ["To do", "In progress", "Shipped"], optionColors: { "In progress": "#456a9c", Shipped: "#3d806b" } }, status, { Done: "Shipped" });
  assert.deepEqual(renamed.tasks.done, ["Shipped"]);
  assert.equal(renamed.records.find((r) => r.id === "c").values["launch-status"], "Shipped");
  assert.throws(() => applyDatabaseField(database, { ...status, type: "text", options: [], optionColors: {} }, status), /Turn off task tracking/);
  const planned = database.fields[1];
  assert.throws(() => applyDatabaseField(database, { ...planned, type: "text" }, planned), /Turn off task tracking/);
});

test("the local day follows the person's clock", () => {
  assert.equal(localDay(new Date(2026, 9, 8, 23, 30)), "2026-10-08");
});
