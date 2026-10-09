import assert from "node:assert/strict";
import test from "node:test";
import { importDestinations, prepareMemoraExport } from "../lib/space/memory-import.ts";

test("Memora preparation removes vectors and preserves original metadata", () => {
  const original = { namespace: "private", id: "one", created_at: "2026-09-18", metadata: { index: "A", value: "Literal text", history: "[]" }, vector_f32_b64: "private-vector" };
  const prepared = JSON.parse(prepareMemoraExport(JSON.stringify({ count: 1, records: [original] })));
  const { vector_f32_b64, ...expected } = original;
  assert.deepEqual(prepared.records, [expected]);
  assert.equal(prepared.count, 1);
});

test("Malformed envelopes never proceed to an import", () => {
  for (const text of ["broken", '{"count":2,"records":[{}]}', '{"count":0,"records":[]}', '{"count":1,"records":[null]}', '{"vectors":{}}']) {
    assert.throws(() => prepareMemoraExport(text));
  }
});

test("Ambiguous or non-finite metadata cannot be silently rewritten", () => {
  assert.throws(() => prepareMemoraExport('{"count":1,"records":[{"id":"a","id":"b"}]}'), /duplicate/);
  assert.throws(() => prepareMemoraExport('{"count":1,"records":[{"id":"a","\\u0069d":"b"}]}'), /duplicate/);
  assert.throws(() => prepareMemoraExport('{"count":1,"records":[{"value":1e999}]}'));
  assert.throws(() => prepareMemoraExport('{"count":1,"records":[' + '['.repeat(35) + '0' + ']'.repeat(35) + ']}'), /nested/);
  const record = { value: 'Escaped "braces{}", commas, and slashes \\', nested: [{ id: 1 }, { id: 2 }] };
  assert.deepEqual(JSON.parse(prepareMemoraExport(JSON.stringify({ count: 1, records: [record] }))).records, [record]);
});

test("Destination mapping requires every namespace and groups explicit merges", () => {
  const preview = { namespaces: [{ namespace: "work" }, { namespace: "personal" }] };
  assert.throws(() => importDestinations(preview, { work: "one" }));
  assert.deepEqual([...importDestinations(preview, { work: "one", personal: "two" })], [["one", ["work"]], ["two", ["personal"]]]);
  assert.deepEqual([...importDestinations(preview, { work: "one", personal: "one" })], [["one", ["work", "personal"]]]);
});
