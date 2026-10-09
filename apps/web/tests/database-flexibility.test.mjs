import test from "node:test";
import assert from "node:assert/strict";
import {
  applyDatabaseField, applyRecordChanges, createDatabase, isWorkspaceDatabase,
  optionColor, optionColors, selectOptionsFromRecords, validateField,
} from "../lib/space/databases.ts";

function journal(values = ["Scalp", "Swing", "Scalp", "", null]) {
  const database = createDatabase("blank", "Disposable trading journal", "", "journal");
  database.fields = [
    { id: "entry", name: "Entry Type", type: "text" },
    { id: "asset", name: "Asset", type: "text" },
  ];
  database.records = values.map((value, index) => ({
    id: `trade-${index}`, name: `Trade ${index}`, notes: "Keep these notes",
    updatedAt: "2026-01-01T00:00:00Z", values: { entry: value, asset: "BTC" },
  }));
  return database;
}

test("text-to-select preserves exact values, empty cells, IDs and other properties without mutating the source", () => {
  const database = journal();
  const before = JSON.stringify(database);
  const original = database.fields[0];
  const options = selectOptionsFromRecords(database, original.id);
  assert.deepEqual(options, ["Scalp", "Swing"]);
  const next = applyDatabaseField(database, { ...original, type: "select", options, optionColors: { Scalp: optionColors[1] } }, original);
  assert.deepEqual(next.records, database.records);
  assert.equal(next.fields[0].id, original.id);
  assert.ok(isWorkspaceDatabase(next));
  assert.ok(isWorkspaceDatabase(JSON.parse(JSON.stringify(next))));
  assert.equal(optionColor(next.fields[0], "Scalp"), optionColors[1]);
  assert.equal(JSON.stringify(database), before);
});

test("imported select labels retain exact whitespace rather than rewriting compatible values", () => {
  const database = journal([" Scalp ", "Swing", "", null]);
  const original = database.fields[0];
  const options = selectOptionsFromRecords(database, "entry");
  assert.deepEqual(options, [" Scalp ", "Swing"]);
  const next = applyDatabaseField(database, { ...original, type: "select", options }, original);
  assert.deepEqual(next.records, database.records);
  assert.ok(isWorkspaceDatabase(next));
});

test("unsafe or unrepresentable conversions refuse without data loss", () => {
  for (const values of [["Scalp", "scalp"], [" "] , ["x".repeat(81)], Array.from({length: 31}, (_, i) => `Option ${i}`)]) {
    const database = journal(values), before = JSON.stringify(database);
    assert.throws(() => selectOptionsFromRecords(database, "entry"), /No values will be changed/);
    assert.equal(JSON.stringify(database), before);
  }
  for (const value of ["Scalp", "9007199254740993", "1.234567890123456789", " ", "01"]) {
    const database = journal([value]), original = database.fields[0], before = JSON.stringify(database);
    assert.throws(() => applyDatabaseField(database, { ...original, type: "number" }, original), /No values have been changed/);
    assert.equal(JSON.stringify(database), before);
  }
  const database = journal(["0", "", null]);
  const next = applyDatabaseField(database, { ...database.fields[0], type: "number" }, database.fields[0]);
  assert.deepEqual(next.records.map((row) => row.values.entry), [0, null, null]);
});

test("select option renames preserve values and colors; removing used options and duplicate labels are refused", () => {
  const database = journal();
  database.fields[0] = { ...database.fields[0], type: "select", options: ["Scalp", "Swing"] };
  const original = database.fields[0];
  const next = applyDatabaseField(database, { ...original, options: ["Intraday", "Swing"], optionColors: { Intraday: optionColors[4] } }, original, { Scalp: "Intraday" });
  assert.deepEqual(next.records.map((row) => row.values.entry), ["Intraday", "Swing", "Intraday", "", null]);
  assert.equal(optionColor(next.fields[0], "Intraday"), optionColors[4]);
  assert.throws(() => applyDatabaseField(database, { ...original, options: ["Swing"] }, original), /No values have been changed/);
  assert.throws(() => validateField(database, { ...original, options: ["Scalp", "scalp"] }), /distinct select options/);
  for (const optionColors of [{ Missing: "#456a9c" }, { Scalp: "red" }, { Scalp: "url(javascript:alert(1))" }])
    assert.throws(() => validateField(database, { ...original, optionColors }), /supported color/);
  assert.throws(() => validateField(database, { id: "new", name: "Other", type: "text", optionColors: { Scalp: "#456a9c" } }), /supported color/);
});

test("changing a board grouping to text clears only grouping and retains records", () => {
  const database = journal();
  database.fields[0] = { ...database.fields[0], type: "select", options: ["Scalp", "Swing"] };
  database.groupBy = "entry";
  database.view = "board";
  const next = applyDatabaseField(database, { id: "entry", name: "Entry Type", type: "text" }, database.fields[0]);
  assert.equal(next.groupBy, null);
  assert.deepEqual(next.records, database.records);
  assert.ok(isWorkspaceDatabase(next));
});

test("stale property editors cannot overwrite concurrent schema changes or miss new incompatible values", () => {
  const database = journal(), original = structuredClone(database.fields[0]);
  database.fields[0].name = "Renamed elsewhere";
  assert.throws(() => applyDatabaseField(database, { ...original, type: "select", options: ["Scalp", "Swing"] }, original), /changed while/);
  database.fields[0] = original;
  database.records[0].values.entry = "Position";
  assert.throws(() => applyDatabaseField(database, { ...original, type: "select", options: ["Scalp", "Swing"] }, original), /No values have been changed/);
});

test("record edits merge only changed values and refuse conflicts, deletions and duplicate creation", () => {
  const database = journal(), original = structuredClone(database.records[0]);
  database.fields.push({ id: "empty", name: "Empty", type: "text" });
  original.values.empty = "";
  database.records[0].values.empty = "";
  database.records[0].values.asset = "ETH";
  database.records[0].notes = "Changed elsewhere";
  const draft = { ...original, values: { ...original.values, entry: "Position" } };
  const next = applyRecordChanges(database, draft, original);
  assert.deepEqual(next.records[0].values, { entry: "Position", asset: "ETH", empty: "" });
  assert.equal(next.records[0].notes, "Changed elsewhere");
  assert.deepEqual(next.records.slice(1), database.records.slice(1));
  database.records[0].values.entry = "Concurrent change";
  assert.throws(() => applyRecordChanges(database, draft, original), /changed while/);
  assert.throws(() => applyRecordChanges(database, draft), /already exists/);
  database.records = [];
  assert.throws(() => applyRecordChanges(database, draft, original), /no longer exists/);
  assert.equal(applyRecordChanges(database, { ...draft, id: "new-trade" }).records.length, 1);
});
