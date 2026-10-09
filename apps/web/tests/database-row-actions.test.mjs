import test from "node:test";
import assert from "node:assert/strict";
import {
  databaseRecordsTSV,
  deleteDatabaseRecords,
  duplicateDatabaseRecord,
  duplicateDatabaseRecords,
} from "../lib/space/database-record-actions.ts";

const now = "2026-09-24T12:00:00.000Z";

function database() {
  return {
    id: "journal", name: "Trading journal", description: "Keep trades", revision: 7,
    fields: [
      { id: "asset", name: "Asset", type: "text" },
      { id: "price", name: "Price", type: "number" },
      { id: "closed", name: "Closed", type: "checkbox" },
      { id: "empty", name: "Empty", type: "text" },
      { id: "missing", name: "Missing", type: "text" },
    ],
    records: [
      {
        id: "first", name: "First trade", notes: " Exact notes\nincluding whitespace ",
        values: { asset: "BTC", price: 0, closed: false, empty: "" },
        updatedAt: "2026-01-01T00:00:00Z",
      },
      {
        id: "second", name: "Second trade", notes: "Other notes",
        values: { asset: "ETH", price: -12.5, closed: true, empty: null },
        createdAt: "2026-01-02T00:00:00Z", updatedAt: "2026-01-03T00:00:00Z",
      },
      {
        id: "third", name: "Third trade", notes: "",
        values: {}, createdAt: null, updatedAt: "2026-01-04T00:00:00Z",
      },
    ],
    view: "table", groupBy: null,
  };
}

function freezeDatabase(db) {
  db.fields.forEach(Object.freeze);
  db.records.forEach((record) => {
    Object.freeze(record.values);
    Object.freeze(record);
  });
  Object.freeze(db.fields);
  Object.freeze(db.records);
  return Object.freeze(db);
}

test("duplicate copies the latest saved values and exact notes with independent values and fresh timestamps", () => {
  const db = database();
  db.records[0].values.asset = "Updated elsewhere";
  const before = structuredClone(db);
  freezeDatabase(db);
  const next = duplicateDatabaseRecord(db, "first", "copy", now);
  const copy = next.records.at(-1);
  assert.notEqual(next, db);
  assert.notEqual(next.records, db.records);
  assert.deepEqual(next.records.slice(0, -1), before.records);
  assert.equal(next.records[0], db.records[0]);
  assert.equal(next.fields, db.fields);
  assert.equal(next.revision, db.revision);
  assert.deepEqual(copy, {
    ...db.records[0], id: "copy", name: "First trade (copy)",
    createdAt: now, updatedAt: now,
  });
  assert.notEqual(copy.values, db.records[0].values);
  copy.values.asset = "Only the copy";
  assert.deepEqual(db, before);
});

test("duplicate preserves legacy empty, null, missing, zero and false values without normalization", () => {
  const db = freezeDatabase(database());
  for (const original of db.records) {
    const next = duplicateDatabaseRecord(db, original.id, `copy-${original.id}`, now);
    const copy = next.records.at(-1);
    assert.deepEqual(copy.values, original.values);
    assert.equal(copy.notes, original.notes);
    assert.equal(copy.createdAt, now);
    assert.equal(copy.updatedAt, now);
    assert.notEqual(copy.values, original.values);
    assert.equal(Object.hasOwn(copy.values, "missing"), false);
  }
});

test("duplicate keeps the copy suffix within the 200-character name limit and generates defaults", () => {
  const db = database();
  db.records[0].name = "x".repeat(200);
  const copy = duplicateDatabaseRecord(db, "first", "copy", now).records.at(-1);
  assert.equal(copy.name, "x".repeat(193) + " (copy)");
  const before = Date.now();
  const generated = duplicateDatabaseRecord(db, "first").records.at(-1);
  assert.match(generated.id, /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/);
  assert.equal(generated.createdAt, generated.updatedAt);
  assert.ok(Date.parse(generated.createdAt) >= before);
  assert.ok(Date.parse(generated.createdAt) <= Date.now());
});

test("duplicate truncates on code-point boundaries within the UTF-16 name budget", () => {
  for (const length of [191, 192]) {
    const db = database();
    db.records[0].name = "a".repeat(length) + "🚀" + "tail";
    const before = structuredClone(db);
    freezeDatabase(db);
    const copy = duplicateDatabaseRecord(db, "first", "copy", now).records.at(-1);
    assert.equal(copy.name, "a".repeat(length) + (length === 191 ? "🚀" : "") + " (copy)");
    assert.ok(copy.name.length <= 200);
    assert.ok(copy.name.isWellFormed());
    assert.deepEqual(db, before);
  }
});

test("duplicate refuses missing sources, empty or existing IDs and the 1,000-record cap without mutation", () => {
  const db = freezeDatabase(database());
  const before = structuredClone(db);
  assert.throws(() => duplicateDatabaseRecord(db, "missing", "copy", now), /no longer exists/);
  for (const id of ["", "   "])
    assert.throws(() => duplicateDatabaseRecord(db, "first", id, now), /new ID/);
  for (const id of ["first", "second"])
    assert.throws(() => duplicateDatabaseRecord(db, "first", id, now), /already exists/);
  assert.deepEqual(db, before);
  const full = {
    ...db,
    records: Array.from({ length: 999 }, (_, index) => ({ ...db.records[0], id: `row-${index}` })),
  };
  const atLimit = duplicateDatabaseRecord(full, "row-0", "copy", now);
  assert.equal(atLimit.records.length, 1000);
  const fullBefore = structuredClone(atLimit);
  freezeDatabase(atLimit);
  assert.throws(() => duplicateDatabaseRecord(atLimit, "row-0", "another-copy", now), /1,000/);
  assert.deepEqual(atLimit, fullBefore);
  assert.equal(full.records.length, 999);
});

test("bulk duplication copies every latest selected row in one independent, immutable batch", () => {
  const db = database();
  db.records[1].notes = "Latest saved notes";
  db.records[2].values.asset = "Unrelated saved change";
  const before = structuredClone(db);
  freezeDatabase(db);
  const next = duplicateDatabaseRecords(db, ["first", "second"], ["copy-first", "copy-second"], now);
  assert.equal(next.records.length, 5);
  assert.deepEqual(next.records.slice(0, 3), before.records);
  assert.equal(next.fields, db.fields);
  assert.equal(next.revision, db.revision);
  for (const [index, copy] of next.records.slice(3).entries()) {
    const source = db.records[index];
    assert.equal(copy.name, source.name + " (copy)");
    assert.deepEqual(copy.values, source.values);
    assert.equal(copy.notes, source.notes);
    assert.equal(copy.createdAt, now);
    assert.equal(copy.updatedAt, now);
    assert.notEqual(copy.values, source.values);
    copy.values.asset = "Only changed on the copy";
  }
  assert.deepEqual(db, before);
});

test("bulk duplication keeps unique source order and generates distinct IDs by default", () => {
  const db = freezeDatabase(database());
  const next = duplicateDatabaseRecords(db, ["second", "first", "second"], ["second-copy", "first-copy"], now);
  assert.deepEqual(next.records.slice(3).map((row) => row.id), ["second-copy", "first-copy"]);
  assert.deepEqual(next.records.slice(3).map((row) => row.name), ["Second trade (copy)", "First trade (copy)"]);
  const generated = duplicateDatabaseRecords(db, ["first", "second", "first"]);
  assert.equal(generated.records.length, 5);
  assert.equal(new Set(generated.records.map((row) => row.id)).size, 5);
  assert.equal(generated.records[3].createdAt, generated.records[4].createdAt);
  assert.equal(duplicateDatabaseRecords(db, []), db);
});

test("bulk duplication refuses every missing source before creating any copy", () => {
  const db = freezeDatabase(database());
  const before = structuredClone(db);
  for (const [ids, copies] of [
    [["first", "missing"], ["copy-first", "copy-missing"]],
    [["first", "copy-first"], ["copy-first", "copy-again"]],
  ]) assert.throws(() => duplicateDatabaseRecords(db, ids, copies, now), /no longer exists/);
  assert.deepEqual(db, before);
});

test("bulk duplication rejects incomplete, blank, existing or repeated new IDs atomically", () => {
  const db = freezeDatabase(database());
  const before = structuredClone(db);
  for (const ids of [["copy"], ["copy", "copy2", "extra"]])
    assert.throws(() => duplicateDatabaseRecords(db, ["first", "second"], ids, now), /new ID/);
  for (const ids of [["copy", ""], ["copy", "   "], ["copy", "second"], ["copy", "copy"]])
    assert.throws(() => duplicateDatabaseRecords(db, ["first", "second"], ids, now), /new ID|already exists/);
  assert.deepEqual(db, before);
});

test("bulk duplication reaches the row cap exactly but refuses an oversized batch without partial copies", () => {
  const db = database();
  db.records.push(...Array.from({ length: 995 }, (_, index) => ({ ...db.records[0], id: `filler-${index}` })));
  const before = structuredClone(db);
  freezeDatabase(db);
  const full = duplicateDatabaseRecords(db, ["first", "second"], ["copy-first", "copy-second"], now);
  assert.equal(full.records.length, 1000);
  assert.throws(() => duplicateDatabaseRecords(db, ["first", "second", "third"], ["a", "b", "c"], now), /1,000/);
  assert.throws(() => duplicateDatabaseRecords(full, ["first"], ["another"], now), /1,000/);
  assert.deepEqual(db, before);
});

test("delete removes only unique requested rows and keeps unrelated latest edits, order and metadata", () => {
  const db = database();
  const originals = structuredClone([db.records[0], db.records[2]]);
  db.records[1].notes = "Saved elsewhere";
  db.records[1].values.asset = "Latest asset";
  const before = structuredClone(db);
  freezeDatabase(db);
  const next = deleteDatabaseRecords(db, [originals[1], originals[0], originals[1]]);
  assert.notEqual(next, db);
  assert.notEqual(next.records, db.records);
  assert.deepEqual(next, { ...before, records: [before.records[1]] });
  assert.equal(next.records[0], db.records[1]);
  assert.equal(next.fields, db.fields);
  assert.deepEqual(db, before);
  const empty = deleteDatabaseRecords(db, []);
  assert.deepEqual(empty, db);
  assert.notEqual(empty, db);
  assert.notEqual(empty.records, db.records);
});

test("delete is atomic when any selected row is missing", () => {
  const db = database();
  const originals = structuredClone([db.records[0], db.records[1]]);
  db.records.splice(1, 1);
  const before = structuredClone(db);
  freezeDatabase(db);
  assert.throws(() => deleteDatabaseRecords(db, originals), /no longer exists/);
  assert.deepEqual(db, before);
});

test("delete refuses every saved row change even after an unchanged row was selected", () => {
  const changes = [
    (row) => { row.name = "Renamed elsewhere"; },
    (row) => { row.notes = "Changed notes"; },
    (row) => { row.values.price = 0; },
    (row) => { row.values.closed = false; },
    (row) => { row.values.added = "New value"; },
    (row) => { delete row.values.empty; },
    (row) => { row.createdAt = now; },
    (row) => { row.updatedAt = now; },
  ];
  for (const change of changes) {
    const db = database();
    const originals = structuredClone([db.records[0], db.records[1]]);
    change(db.records[1]);
    const before = structuredClone(db);
    freezeDatabase(db);
    assert.throws(() => deleteDatabaseRecords(db, originals), /changed/);
    assert.deepEqual(db, before);
  }
});

test("delete compares values by content, not property order, but catches null-versus-empty changes", () => {
  const db = database();
  const original = structuredClone(db.records[0]);
  db.records[0].values = { empty: "", closed: false, price: 0, asset: "BTC" };
  assert.deepEqual(deleteDatabaseRecords(db, [original]).records, db.records.slice(1));
  db.records[0].values.empty = null;
  assert.throws(() => deleteDatabaseRecords(db, [original]), /changed/);
  const inconsistent = structuredClone(db.records[1]);
  inconsistent.notes = "Stale duplicate selection";
  assert.throws(() => deleteDatabaseRecords(db, [db.records[1], inconsistent]), /changed/);
});

test("TSV includes all headers, keeps selected order and deduplicates IDs without normalizing empty or typed values", () => {
  const db = database();
  db.records[0].notes = "";
  const before = structuredClone(db);
  freezeDatabase(db);
  assert.equal(databaseRecordsTSV(db, ["second", "first", "second", "third"]), [
    "Name\tAsset\tPrice\tClosed\tEmpty\tMissing\tRecord notes",
    "Second trade\tETH\t-12.5\ttrue\t\t\tOther notes",
    "First trade\tBTC\t0\tfalse\t\t\t",
    "Third trade\t\t\t\t\t\t",
  ].join("\n"));
  assert.equal(databaseRecordsTSV(db, []), "Name\tAsset\tPrice\tClosed\tEmpty\tMissing\tRecord notes");
  assert.deepEqual(db, before);
});

test("TSV quotes tabs, multiline text and literal quotes in headers, names, properties and notes", () => {
  const db = database();
  db.fields = [
    { id: "text", name: 'A\t"property"', type: "text" },
    { id: "plain", name: "Plain", type: "text" },
  ];
  db.records[0].name = 'Trade\t"one"';
  db.records[0].values = { text: 'Line 1\r\n"Line 2"', plain: "Comma, stays plain" };
  db.records[0].notes = 'Notes\nnext "line"';
  assert.equal(databaseRecordsTSV(db, ["first"]), [
    'Name\t"A\t""property"""\tPlain\tRecord notes',
    '"Trade\t""one"""\t"Line 1\r\n""Line 2"""\tComma, stays plain\t"Notes\nnext ""line"""',
  ].join("\n"));
});

test("TSV protects formula-like textual headers and cells but does not escape negative numbers", () => {
  const db = database();
  db.fields = [
    { id: "equals", name: "=Header", type: "text" },
    { id: "plus", name: "+Header", type: "text" },
    { id: "minus", name: "-Header", type: "text" },
    { id: "at", name: "@Header", type: "text" },
    { id: "numeric", name: "Negative number", type: "number" },
    { id: "zero", name: "Zero", type: "number" },
  ];
  db.records[0] = {
    ...db.records[0], name: "=HYPERLINK()", notes: "@SUM(1)",
    values: { equals: "=1+1", plus: "+1+1", minus: "-12.5", at: "@SUM(1)", numeric: -12.5, zero: 0 },
  };
  const before = structuredClone(db);
  freezeDatabase(db);
  assert.equal(databaseRecordsTSV(db, ["first"]), [
    "Name\t'=Header\t'+Header\t'-Header\t'@Header\tNegative number\tZero\tRecord notes",
    "'=HYPERLINK()\t'=1+1\t'+1+1\t'-12.5\t'@SUM(1)\t-12.5\t0\t'@SUM(1)",
  ].join("\n"));
  assert.deepEqual(db, before);
});

test("TSV protects leading control characters and whitespace-prefixed formulas before quoting", () => {
  const db = database();
  db.fields = [{ id: "text", name: "\t=Header", type: "text" }];
  db.records[0].name = "\n=1+1";
  db.records[0].values = { text: "  =1+1" };
  db.records[0].notes = "\r@SUM(1)";
  assert.equal(databaseRecordsTSV(db, ["first"]), [
    "Name\t\"'\t=Header\"\tRecord notes",
    "\"'\n=1+1\"\t'  =1+1\t\"'\r@SUM(1)\"",
  ].join("\n"));
});

test("TSV refuses any missing selected ID instead of silently copying a partial selection", () => {
  const db = freezeDatabase(database());
  const before = structuredClone(db);
  for (const ids of [["missing"], ["first", "missing"], ["first", "first", "missing"], [""]])
    assert.throws(() => databaseRecordsTSV(db, ids), /no longer exists/);
  assert.deepEqual(db, before);
});
