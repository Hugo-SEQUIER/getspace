import test from "node:test";
import assert from "node:assert/strict";
import {
  cellLink,
  createDatabase,
  fieldValue,
  validateField,
  validateRecord,
  queryRecords,
  isWorkspaceDatabase,
} from "../lib/space/databases.ts";
import { makeDemoDatabases } from "./support/sample-workspace.ts";

test("blank and client databases have independent stable schemas and start empty", () => {
  const first = createDatabase("clients", "Acquisition", "Clients"),
    second = createDatabase("clients", "Partners", "Partners");
  assert.notEqual(first.id, second.id);
  assert.equal(first.records.length, 0);
  assert.equal(first.fields.length, 5);
  assert.ok(
    first.fields.every(
      (f) => !second.fields.some((other) => other.id === f.id),
    ),
  );
  assert.equal(createDatabase("blank", "Research", "").fields.length, 0);
  assert.ok(isWorkspaceDatabase(first));
  assert.ok(makeDemoDatabases().every(isWorkspaceDatabase));
});

test("typed values preserve zero and false and reject invalid dates, numbers, and URLs", () => {
  const field = (type) => ({ id: "property", name: "Property", type });
  assert.equal(fieldValue(field("number"), "0"), 0);
  assert.equal(fieldValue(field("number"), ""), null);
  assert.equal(fieldValue(field("checkbox"), false), false);
  assert.equal(fieldValue(field("date"), "2028-02-29"), "2028-02-29");
  assert.throws(() => fieldValue(field("number"), "Infinity"));
  assert.throws(() => fieldValue(field("date"), "2026-02-29"));
  assert.throws(() => fieldValue(field("email"), "not an email"));
  assert.throws(() => fieldValue(field("url"), "javascript:alert(1)"));
  assert.throws(() => fieldValue(field("checkbox"), "false"));
});

test("schema changes preserve field identities and refuse duplicate names or removal of used options", () => {
  const db = makeDemoDatabases()[0],
    stage = db.fields.find((f) => f.name === "Stage");
  assert.throws(() =>
    validateField(db, { id: "new", name: "stage", type: "text" }),
  );
  assert.throws(() =>
    validateField(db, { id: "new", name: "Name", type: "text" }),
  );
  const renamed = {
    ...db,
    fields: db.fields.map((f) =>
      f.id === stage.id ? { ...f, name: "Pipeline" } : f,
    ),
  };
  assert.ok(isWorkspaceDatabase(renamed));
  assert.equal(
    validateRecord(renamed, db.records[0]).values[stage.id],
    "Proposal",
  );
  const removed = {
    ...db,
    fields: db.fields.map((f) =>
      f.id === stage.id ? { ...f, options: ["New"] } : f,
    ),
  };
  assert.throws(() => validateRecord(removed, db.records[0]));
  assert.equal(isWorkspaceDatabase(removed), false);
});

test("table and board queries intersect search with select filters without modifying records", () => {
  const db = makeDemoDatabases()[0],
    before = JSON.stringify(db),
    stage = db.fields.find((f) => f.name === "Stage");
  const rows = queryRecords(db, "sunday", "name", stage.id, "Contacted");
  assert.equal(rows.length, 1);
  assert.equal(rows[0].name, "Sunday Supply");
  assert.equal(queryRecords(db, "sunday", "name", stage.id, "Won").length, 0);
  assert.equal(JSON.stringify(db), before);
  assert.equal(isWorkspaceDatabase({ ...db, groupBy: "missing-field" }), false);
});

test("database cell links allow only web URLs in text and URL properties", () => {
  const field = (type) => ({ id: "link", name: "Link", type });
  for (const type of ["url", "text"]) {
    assert.equal(cellLink(field(type), " https://example.com/path?q=1 "), "https://example.com/path?q=1");
    assert.equal(cellLink(field(type), "http://example.com"), "http://example.com/");
    for (const value of [null, "", false, 0, "hello", "javascript:alert(1)", "data:text/html,test", "file:///tmp/test", "/relative"])
      assert.equal(cellLink(field(type), value), null);
  }
  assert.equal(cellLink(field("select"), "https://example.com"), null);
});

for (const [type, values] of [
  ["number", [0, 2, 10]],
  ["checkbox", [false, true]],
  ["date", ["2026-01-01", "2026-09-01", "2027-01-01"]],
  ["text", ["alpha", "Beta", "Item 10"]],
  ["select", ["Contacted", "New", "Won"]],
  ["email", ["a@example.com", "z@example.com"]],
  ["url", ["https://a.example", "https://z.example"]],
  ["phone", ["100", "200"]],
]) {
  test(`sort ${type} properties in both directions, keeping empty values last`, () => {
    const db = createDatabase("blank", "Sorting", "");
    db.fields = [{ id: "field", name: "Property", type }];
    db.records = [...values].reverse().concat([null, "", undefined]).map((value, index) => ({
      id: String(index), name: `Record ${index}`, notes: "",
      updatedAt: "2026-01-01T00:00:00Z", values: { field: value },
    }));
    const before = JSON.stringify(db);
    for (const direction of ["asc", "desc"]) {
      const rows = queryRecords(db, "", "property:field", "", "", direction);
      assert.deepEqual(rows.map((row) => row.values.field), [
        ...(direction === "asc" ? values : [...values].reverse()), null, "", undefined,
      ]);
    }
    assert.equal(JSON.stringify(db), before);
    assert.deepEqual(
      queryRecords(db, "Record 0", "property:field", "", "").map((row) => row.id), ["0"],
    );
  });
}

test("property sorts preserve ties, combine filters, and fall back safely for removed fields", () => {
  const db = makeDemoDatabases()[0];
  const field = db.fields.find((field) => field.name === "Stage");
  const expected = db.records.filter((record) => record.values[field.id] === "Contacted");
  assert.deepEqual(queryRecords(db, "", `property:${field.id}`, field.id, "Contacted"), expected);
  assert.deepEqual(queryRecords(db, "", "property:removed", "", ""), queryRecords(db, "", "updated", "", ""));
  const ascending = queryRecords(db, "", "name", "", "");
  assert.deepEqual(queryRecords(db, "", "name", "", "", "desc"), ascending.reverse());
});

test("date added uses creation time, not edits, and keeps legacy unknown dates last", () => {
  const db = makeDemoDatabases()[0];
  db.records = db.records.slice(0, 3);
  db.records[0].createdAt = "2026-09-01T09:00:00Z";
  db.records[0].updatedAt = "2027-01-01T00:00:00Z";
  db.records[1].createdAt = "2026-09-02T09:00:00Z";
  delete db.records[2].createdAt;
  const ids = db.records.map((row) => row.id);
  assert.deepEqual(queryRecords(db, "", "created", "", "").map((row) => row.id), ids);
  assert.deepEqual(queryRecords(db, "", "created", "", "", "desc").map((row) => row.id), [ids[1], ids[0], ids[2]]);
  assert.ok(isWorkspaceDatabase(db));
  db.records[0].createdAt = "invalid";
  assert.equal(isWorkspaceDatabase(db), false);
});
