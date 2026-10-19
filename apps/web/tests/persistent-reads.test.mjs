import "fake-indexeddb/auto";
import test from "node:test";
import assert from "node:assert/strict";
import { openDB } from "idb";
import { PersistentReads, erasePersistentReads, READ_CACHE_DB, READ_CACHE_MAX_AGE, persistableRead } from "../lib/persistent-reads.ts";
import { ReadCache } from "../lib/read-cache.ts";

const space = "11111111-1111-4111-8111-111111111111";
const other = "22222222-2222-4222-8222-222222222222";
const key = Buffer.alloc(32, 7).toString("base64");
const path = `spaces/${space}/memories?limit=50`;
const value = (changes = {}) => ({ path, allPages: false, data: { private: "MEMORY-CANARY-731" }, updatedAt: Date.now(), ...changes });
async function store(t, account = "owner", secret = key, spaces = [space]) {
  const result = await PersistentReads.open(account, secret, spaces, new AbortController().signal);
  t.after(() => result.close());
  return result;
}
test.beforeEach(async () => { await erasePersistentReads(); });

test("reload restores encrypted content without persisting keys, paths or readable content", async t => {
  const first = await store(t);
  const entry = value();
  await first.save(entry, await first.ticket());
  first.close();
  const db = await openDB(READ_CACHE_DB);
  const records = await db.getAll("reads");
  const bytes = Buffer.from(records[0].ciphertext).toString();
  assert.ok(!bytes.includes("MEMORY-CANARY"));
  assert.ok(!JSON.stringify(records).includes(path));
  assert.ok(!JSON.stringify(await db.getAll("meta")).includes(key));
  db.close();
  const second = await store(t);
  assert.deepEqual(await second.restore(), [entry]);
});

test("account or login-session switches clear old snapshots and fence their late writes", async t => {
  const old = await store(t);
  const ticket = await old.ticket();
  await old.save(value(), ticket);
  const switched = await store(t, "other");
  assert.deepEqual(await switched.restore(), []);
  await old.save(value(), ticket);
  assert.deepEqual(await switched.restore(), []);
  const nextSession = await store(t, "owner", Buffer.alloc(32, 9).toString("base64"));
  assert.deepEqual(await nextSession.restore(), []);
});

test("only currently accessible spaces restore; unknown endpoints and search never persist", async t => {
  const first = await store(t);
  await first.save(value(), await first.ticket());
  first.close();
  const next = await store(t, "owner", key, [other]);
  assert.deepEqual(await next.restore(), []);
  for (const endpoint of ["workspace", "auth/session", `spaces/${space}/memory/jobs`, `${path}&query=private`, `spaces/${other}/memories`])
    assert.equal(persistableRead(endpoint, new Set([space])), false);
  // The index is Space content, so a reload can search before every body is re-read.
  assert.equal(persistableRead(`spaces/${space}/search/index`, new Set([space])), true);
  assert.equal(persistableRead(`spaces/${space}/search`, new Set([space])), false);
});

test("mutations and logout fence already-started reads across tabs", async t => {
  const a = await store(t), b = await store(t);
  const old = await b.ticket();
  await b.save(value(), old);
  await a.invalidate();
  await b.save(value(), old);
  assert.deepEqual(await b.restore(), []);
  await b.save(value(), await b.ticket());
  assert.equal((await a.restore()).length, 1);
  const beforeLogout = await b.ticket();
  await erasePersistentReads();
  await b.save(value(), beforeLogout);
  assert.deepEqual(await b.restore(), []);
});

test("expired and corrupted ciphertext is a cache miss", async t => {
  const cache = await store(t);
  await cache.save(value({ updatedAt: Date.now() - READ_CACHE_MAX_AGE - 1 }), await cache.ticket());
  assert.deepEqual(await cache.restore(), []);
  await cache.save(value(), await cache.ticket());
  const db = await openDB(READ_CACHE_DB);
  const [row] = await db.getAll("reads");
  row.ciphertext = new ArrayBuffer(20);
  await db.put("reads", row);
  db.close();
  assert.deepEqual(await cache.restore(), []);
});

test("restored reads render before background refresh and fresh reads still wait", async t => {
  const disk = await store(t);
  const entry = value();
  await disk.save(entry, await disk.ticket());
  const pending = Promise.withResolvers();
  const cache = new ReadCache(() => pending.promise);
  t.after(() => cache.dispose());
  cache.setAccount("owner");
  await cache.restore(disk);
  assert.deepEqual(await cache.read(path), entry.data);
  const seen = [], errors = [];
  const stop = cache.watch(path, data => seen.push(data), error => errors.push(error));
  t.after(stop);
  assert.deepEqual(seen, [entry.data]);
  pending.resolve({ private: "New revision" });
  assert.deepEqual(await cache.read(path, { fresh: true }), { private: "New revision" });
});

test("unresponsive browser storage cannot prevent online reads", async t => {
  const disk = await store(t);
  const cache = new ReadCache(async () => ({ online: true }));
  t.after(() => cache.dispose());
  cache.setAccount("owner");
  await cache.restore(disk);
  disk.ticket = () => new Promise(() => {});
  assert.deepEqual(await cache.read(path), { online: true });
});

test("disk replicas remain bounded by entry count and reject oversized content", async t => {
  const disk = await store(t);
  const ticket = await disk.ticket();
  for (let i = 0; i < 105; i++) await disk.save(value({ path: `${path}&revision=${i}` }), ticket);
  assert.equal((await disk.restore()).length, 100);
  await disk.save(value({ data: "x".repeat(10 * 1024 * 1024) }), ticket);
  assert.ok((await disk.restore()).every(entry => entry.path !== path));
});


test("active folder catalogs persist and restore as complete workspace item lists", async t => {
  const first = await store(t);
  const folderPath = `spaces/${space}/items?kind=folder&deleted=false`;
  const entry = value({ path: folderPath, allPages: true, data: {
    items: [{ id: "folder-id", kind: "folder", name: "Reference notes", description: "", revision: 1 }],
    next_cursor: null,
  } });
  assert.equal(persistableRead(folderPath, new Set([space])), true);
  await first.save(entry, await first.ticket());
  first.close();
  const second = await store(t);
  assert.deepEqual(await second.restore(), [entry]);
});
