import test from "node:test";
import assert from "node:assert/strict";
import { createDatabaseSaveQueue } from "../lib/space/database-save-queue.ts";
import { makeDemoDatabases } from "./support/sample-workspace.ts";

const changeCell = (row, field, value) => current => ({
  ...current,
  records: current.records.map(record => record.id === row
    ? { ...record, values: { ...record.values, [field]: value } }
    : record),
});

test("rapid edits serialize, preserve other cells and use returned revisions", async () => {
  const enqueue = createDatabaseSaveQueue();
  const base = { ...makeDemoDatabases()[0], revision: 1 };
  const stage = base.fields.find(field => field.name === "Stage").id;
  const email = base.fields.find(field => field.name === "Email").id;
  const [first, second] = base.records;
  const gate = Promise.withResolvers();
  const writes = [];
  const persist = async next => {
    writes.push(next);
    if (writes.length === 1) await gate.promise;
    return { ...next, revision: next.revision + 1 };
  };
  const one = enqueue(base, changeCell(first.id, stage, "Won"), persist);
  const two = enqueue(base, changeCell(second.id, stage, "Lost"), persist);
  const three = enqueue(base, changeCell(first.id, email, "new@example.com"), persist);
  const four = enqueue(base, changeCell(first.id, stage, "Lost"), persist);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(writes.length, 1);
  gate.resolve();
  const results = await Promise.all([one, two, three, four]);
  assert.deepEqual(writes.map(db => db.revision), [1, 2, 3, 4]);
  const final = results.at(-1);
  assert.equal(final.records[0].values[stage], "Lost");
  assert.equal(final.records[0].values[email], "new@example.com");
  assert.equal(final.records[1].values[stage], "Lost");
});

test("a failed save is reported, does not poison later saves or persist failed values", async () => {
  const enqueue = createDatabaseSaveQueue();
  const base = { ...makeDemoDatabases()[0], revision: 4 };
  const first = enqueue(base, current => ({ ...current, name: "Failed" }), async () => {
    throw new Error("Offline");
  });
  const later = enqueue(base, current => ({ ...current, description: "Kept" }), async next => ({
    ...next, revision: next.revision + 1,
  }));
  await assert.rejects(first, /Offline/);
  const saved = await later;
  assert.equal(saved.name, base.name);
  assert.equal(saved.description, "Kept");
  assert.equal(saved.revision, 5);
  const refreshed = await enqueue({ ...saved, revision: 8, name: "External edit" },
    current => ({ ...current, description: "After refresh" }), async next => next);
  assert.equal(refreshed.revision, 8);
  assert.equal(refreshed.name, "External edit");
});

test("rapid view switches save the last choice without overwriting queued record changes", async () => {
  const enqueue = createDatabaseSaveQueue();
  const base = { ...makeDemoDatabases()[0], revision: 1 };
  const stage = base.fields.find(field => field.name === "Stage").id;
  const gate = Promise.withResolvers();
  const writes = [];
  const persist = async next => {
    writes.push(next);
    if (writes.length === 1) await gate.promise;
    return { ...next, revision: next.revision + 1 };
  };
  const board = enqueue(base, current => ({ ...current, view: "board" }), persist);
  const moved = enqueue(base, changeCell(base.records[0].id, stage, "Won"), persist);
  const table = enqueue(base, current => ({ ...current, view: "table" }), persist);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(writes.length, 1);
  gate.resolve();
  await Promise.all([board, moved]);
  const final = await table;
  assert.equal(final.view, "table");
  assert.equal(final.records[0].values[stage], "Won");
  assert.deepEqual(writes.map(db => db.revision), [1, 2, 3]);
  assert.deepEqual(final.fields, base.fields);
  assert.equal(final.records[0].notes, base.records[0].notes);
});

test("a failed board move does not leak into a later queued view save", async () => {
  const enqueue = createDatabaseSaveQueue();
  const base = { ...makeDemoDatabases()[0], revision: 7, view: "board" };
  const stage = base.fields.find(field => field.name === "Stage").id;
  const gate = Promise.withResolvers();
  const moved = enqueue(base, changeCell(base.records[0].id, stage, "Won"), async () => {
    await gate.promise;
    throw new Error("Move failed");
  });
  const table = enqueue(base, current => ({ ...current, view: "table" }), async next => ({
    ...next, revision: next.revision + 1,
  }));
  const rejected = assert.rejects(moved, /Move failed/);
  gate.resolve();
  await rejected;
  const final = await table;
  assert.equal(final.view, "table");
  assert.deepEqual(final.records, base.records);
  assert.equal(final.revision, 8);
});

const recovery = {
  transient: error => error.message === "Offline",
  stale: error => error.message === "Stale",
  retryDelayMs: 0,
};

test("an interrupted save is resent once with the identical payload", async () => {
  const enqueue = createDatabaseSaveQueue(recovery);
  const base = { ...makeDemoDatabases()[0], revision: 2 };
  const writes = [];
  const saved = await enqueue(base, current => ({ ...current, name: "Renamed" }), async next => {
    writes.push(next);
    if (writes.length === 1) throw new Error("Offline");
    return { ...next, revision: next.revision + 1 };
  });
  assert.equal(writes.length, 2);
  assert.equal(writes[0], writes[1]);
  assert.equal(saved.revision, 3);
});

test("an outdated save is reapplied to the latest revision and later edits follow it", async () => {
  const enqueue = createDatabaseSaveQueue(recovery);
  const base = { ...makeDemoDatabases()[0], revision: 2 };
  const external = { ...base, revision: 5, name: "Agent rename" };
  const server = { revision: 5 };
  const persist = async next => {
    if (next.revision !== server.revision) throw new Error("Stale");
    server.revision++;
    return { ...next, revision: server.revision };
  };
  const latest = async id => { assert.equal(id, base.id); return external; };
  const first = await enqueue(base, current => ({ ...current, description: "Mine" }), persist, latest);
  assert.equal(first.revision, 6);
  assert.equal(first.name, "Agent rename");
  assert.equal(first.description, "Mine");
  const second = await enqueue(base, current => ({ ...current, name: "Later" }), persist, latest);
  assert.equal(second.revision, 7);
  assert.equal(second.description, "Mine");
});

test("recovery is bounded and leaves other failures unchanged", async () => {
  const enqueue = createDatabaseSaveQueue(recovery);
  const base = { ...makeDemoDatabases()[0], revision: 2 };
  let writes = 0;
  await assert.rejects(enqueue(base, current => current, async () => { writes++; throw new Error("Offline"); }), /Offline/);
  assert.equal(writes, 2);
  writes = 0;
  await assert.rejects(enqueue(base, current => current, async () => { writes++; throw new Error("Stale"); },
    async () => base), /Stale/);
  assert.equal(writes, 2);
  writes = 0;
  await assert.rejects(enqueue(base, current => current, async () => { writes++; throw new Error("Stale"); }), /Stale/);
  assert.equal(writes, 1);
  writes = 0;
  await assert.rejects(enqueue(base, current => current, async () => { writes++; throw new Error("Invalid"); },
    async () => base), /Invalid/);
  assert.equal(writes, 1);
});
