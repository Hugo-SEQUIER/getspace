import test from "node:test";
import assert from "node:assert/strict";
import {
  parseTableText,
  tableFromRows,
  inferColumn,
  databaseFromImport,
  checkImportFile,
  cleanDocumentText,
  decodeImportText,
} from "../lib/space/import-data.ts";
import { isWorkspaceDatabase } from "../lib/space/databases.ts";

test("CSV/TSV preserve quoted delimiters, multiline cells, BOM, empty values and leading zeros", () => {
  const table = parseTableText(
    '\uFEFFName,Code,Notes,Value\r\n"Northstar, Inc",0012,"First line\nSecond line",0\r\nForma,0003,,25',
    "csv",
    "clients.csv",
  );
  assert.deepEqual(table.rows[0], [
    "Northstar, Inc",
    "0012",
    "First line\nSecond line",
    "0",
  ]);
  assert.equal(inferColumn(table, 1), "text");
  assert.equal(inferColumn(table, 3), "number");
  assert.deepEqual(parseTableText("Name\tValue\nOne\t0", "tsv", "file").rows, [
    ["One", "0"],
  ]);
  assert.throws(
    () => parseTableText('Name,Notes\nOne,"unterminated', "csv", "bad"),
    /Could not read/,
  );
  assert.throws(
    () => parseTableText("Name,Value\nOne,1,extra", "csv", "bad"),
    /different number/,
  );
});

test("JSON imports union fields, preserve false/zero/nesting, and treat special object keys as data", () => {
  const table = parseTableText(
    '{"records":[{"Name":"One","Active":false,"Value":0,"Details":{"nested":[1,2]},"__proto__":"literal"},{"Name":"Two","Email":"two@example.com"}]}',
    "json",
    "clients.json",
  );
  assert.equal(table.rows[0][1], false);
  assert.equal(table.rows[0][2], 0);
  assert.equal(table.rows[0][3], '{"nested":[1,2]}');
  const database = databaseFromImport(
    table,
    "Imported",
    0,
    table.headers.map((_, i) => inferColumn(table, i)),
    "test-import",
  );
  assert.ok(isWorkspaceDatabase(database));
  assert.ok(database.records.every((record) =>
    Number.isFinite(Date.parse(record.createdAt)) && record.createdAt === record.updatedAt));
  assert.equal(database.records[0].values["test-import-field-4"], "literal");
  assert.equal(database.records[0].values["test-import-field-2"], 0);
  assert.equal(database.records[0].values["test-import-field-1"], false);
  assert.throws(
    () => parseTableText('[{"id":9007199254740993}]', "json", "unsafe"),
    /supported range/,
  );
  assert.equal(
    parseTableText('{"Name":"A"}\n{"Name":"B"}', "jsonl", "lines").rows.length,
    2,
  );
  assert.throws(
    () => parseTableText("[1,2]", "json", "bad"),
    /must be an object/,
  );
});

test("mapping supports generated names, unique headers, precise errors and corrected field types", () => {
  const table = tableFromRows("sheet", [
    ["Name", "Name", "", "Stage"],
    ["One", "Other", "001", "New"],
    ["", "Name", "002", "Won"],
  ]);
  assert.deepEqual(table.headers, ["Name", "Name (2)", "Column 3", "Stage"]);
  const types = table.headers.map((_, i) => inferColumn(table, i));
  const database = databaseFromImport(table, "Sheet", -1, types, "test");
  assert.ok(isWorkspaceDatabase(database));
  assert.equal(database.fields[0].name, "Name (2)");
  assert.equal(database.records[0].name, "Record 1");
  assert.equal(database.records[0].values["test-field-0"], "One");
  assert.equal(database.groupBy, "test-field-3");
  assert.throws(
    () =>
      databaseFromImport(
        table,
        "Sheet",
        0,
        ["text", "number", "text", "select"],
        "id",
      ),
    /Row 1, Name \(2\): needs a number/,
  );
  const invalid = tableFromRows("date", [
    ["Name", "Date"],
    ["One", "2026-02-29"],
  ]);
  assert.equal(inferColumn(invalid, 1), "text");
  assert.throws(
    () => databaseFromImport(invalid, "Date", 0, ["text", "date"], "id"),
    /YYYY-MM-DD/,
  );
});

test("imports reject unsupported, empty, oversized, binary and excessive data without truncating", () => {
  assert.throws(
    () => checkImportFile({ name: "file.exe", size: 1 }, ["csv"]),
    /Choose/,
  );
  assert.throws(
    () => checkImportFile({ name: "file.csv", size: 0 }, ["csv"]),
    /empty/,
  );
  assert.throws(
    () => checkImportFile({ name: "file.csv", size: 11000000 }, ["csv"]),
    /10 MB/,
  );
  assert.throws(
    () =>
      tableFromRows("large", [
        ["Name"],
        ...Array.from({ length: 2001 }, (_, i) => [String(i)]),
      ]),
    /2,000/,
  );
  assert.throws(
    () =>
      tableFromRows("wide", [Array.from({ length: 51 }, (_, i) => String(i))]),
    /50 columns/,
  );
  assert.throws(() => cleanDocumentText("\0binary"), /readable text/);
  assert.throws(() => cleanDocumentText("   "), /OCR/);
  assert.equal(cleanDocumentText("\uFEFFHello\r\nworld"), "Hello\nworld");
  assert.throws(() => cleanDocumentText("a".repeat(500001)), /500,000/);
  assert.equal(
    decodeImportText(new Uint8Array([255, 254, 72, 0, 105, 0])),
    "Hi",
  );
  assert.throws(() => decodeImportText(new Uint8Array([0x80])), /UTF-8/);
});
