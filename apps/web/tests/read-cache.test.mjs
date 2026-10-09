import test from "node:test";
import assert from "node:assert/strict";
import { ReadCache, ReadScheduler } from "../lib/read-cache.ts";
import { QueryObserver } from "@tanstack/react-query";

const tick = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => Promise.withResolvers();

test("React Query observers and imperative reads share one cache and account purge", async t => {
  let calls = 0;
  const cache = cacheFor(t, async () => { calls++; return { revision: 1 }; });
  const path = "spaces/a/resources/one";
  await cache.read(path);
  const seen = [];
  const observer = new QueryObserver(cache.queryClient, {
    queryKey: ["owner-a", path, false],
    enabled: false,
  });
  const stop = observer.subscribe(value => seen.push(value.data));
  t.after(stop);
  assert.deepEqual(observer.getCurrentResult().data, { revision: 1 });
  cache.queryClient.setQueryData(["owner-a", path, false], { revision: 2 });
  assert.deepEqual(await cache.read(path), { revision: 2 });
  assert.equal(calls, 1);
  assert.deepEqual(seen.at(-1), { revision: 2 });
  cache.setAccount("owner-b");
  assert.equal(cache.queryClient.getQueryData(["owner-a", path, false]), undefined);
});
function cacheFor(t, fetch) {
  const cache = new ReadCache(fetch);
  cache.setAccount("owner-a");
  t.after(() => cache.dispose());
  return cache;
}

test("fresh reads and simultaneous consumers share one request, scoped by account and Space", async t => {
  let calls = 0;
  const pending = deferred();
  const cache = cacheFor(t, async path => { calls++; await pending.promise; return { path, calls }; });
  const first = cache.read("spaces/a/resources/one");
  const duplicate = cache.read("spaces/a/resources/one");
  assert.equal(calls, 1);
  pending.resolve();
  assert.deepEqual(await first, await duplicate);
  assert.deepEqual(await cache.read("spaces/a/resources/one"), await first);
  assert.equal(calls, 1);
  await cache.read("spaces/b/resources/one");
  assert.equal(calls, 2);
  cache.setAccount("owner-b");
  await cache.read("spaces/a/resources/one");
  assert.equal(calls, 3);
  cache.setAccount(null);
  await assert.rejects(cache.read("spaces/a/resources/one"), /Open a space/);
});

test("logout cancels in-flight reads and late results never populate another account's cache", async t => {
  const pending = deferred();
  let calls = 0;
  const cache = cacheFor(t, async () => ++calls === 1 ? pending.promise : { owner: "b" });
  const old = cache.read("spaces/a/resources/one");
  const rejected = assert.rejects(old);
  cache.setAccount(null);
  cache.setAccount("owner-b");
  assert.deepEqual(await cache.read("spaces/a/resources/one"), { owner: "b" });
  pending.resolve({ owner: "a" });
  await rejected;
  await tick();
  assert.deepEqual(await cache.read("spaces/a/resources/one"), { owner: "b" });
  assert.equal(calls, 2);
});

test("a write invalidates its scope and discards an older read without evicting another Space", async t => {
  const pending = deferred();
  let calls = 0;
  const cache = cacheFor(t, async path => {
    calls++;
    return path.includes("/a/") && calls === 2 ? pending.promise : { revision: calls };
  });
  await cache.read("spaces/b/resources/one");
  const old = cache.read("spaces/a/resources/one");
  const rejected = assert.rejects(old);
  cache.invalidate(path => path.startsWith("spaces/a/"));
  assert.deepEqual(await cache.read("spaces/a/resources/one"), { revision: 3 });
  pending.resolve({ revision: 1 });
  await rejected;
  assert.deepEqual(await cache.read("spaces/a/resources/one"), { revision: 3 });
  assert.deepEqual(await cache.read("spaces/b/resources/one"), { revision: 1 });
  assert.equal(calls, 3);
});

test("failures are retried only on a new request; explicit fresh reads bypass a warm cache", async t => {
  let calls = 0;
  const cache = cacheFor(t, async () => {
    if (++calls === 1) throw new Error("503");
    return { revision: calls };
  });
  await assert.rejects(cache.read("resource"), /503/);
  assert.equal(calls, 1);
  assert.deepEqual(await cache.read("resource"), { revision: 2 });
  assert.deepEqual(await cache.read("resource", { fresh: true }), { revision: 3 });
});

test("a stale observer displays cached data immediately, then delivers the background update", async t => {
  t.mock.timers.enable({ apis: ["Date"], now: 100000 });
  const next = deferred();
  let calls = 0;
  const cache = cacheFor(t, async () => ++calls === 1 ? { revision: 1 } : next.promise);
  await cache.read("resource");
  t.mock.timers.tick(31000);
  const seen = [], errors = [];
  const stop = cache.watch("resource", value => seen.push(value), error => errors.push(error));
  t.after(stop);
  assert.deepEqual(seen, [{ revision: 1 }]);
  assert.equal(calls, 2);
  next.resolve({ revision: 2 });
  await tick();
  assert.deepEqual(seen, [{ revision: 1 }, { revision: 2 }]);
  assert.deepEqual(errors, []);
});

test("an observer cannot deliver updates after changing account", async t => {
  const pending = deferred();
  const cache = cacheFor(t, () => pending.promise);
  const seen = [];
  const stop = cache.watch("resource", value => seen.push(value), () => {});
  t.after(stop);
  cache.setAccount("owner-b");
  pending.resolve({ private: "owner-a" });
  await tick();
  assert.deepEqual(seen, []);
});

test("focus checks elapsed freshness and successful recovery clears a background error", async t => {
  t.mock.timers.enable({ apis: ["Date"], now: 100000 });
  let calls = 0;
  const cache = cacheFor(t, async () => {
    if (++calls === 2) throw new Error("temporarily unavailable");
    return { revision: 1 };
  });
  const seen = [], errors = [];
  const stop = cache.watch("resource", value => seen.push(value), error => errors.push(error));
  t.after(stop);
  await tick();
  t.mock.timers.tick(31000);
  cache.refetchStale();
  await tick();
  assert.equal(errors.length, 1);
  cache.refetchStale();
  await tick();
  assert.equal(calls, 3);
  assert.deepEqual(seen, [{ revision: 1 }, { revision: 1 }], "unchanged recovered data still clears UI errors");
});

test("full catalog pages are combined and cached independently from a single page", async t => {
  const paths = [];
  const cache = cacheFor(t, async path => {
    paths.push(path);
    return path.includes("after=") ? { items: [2], next_cursor: null } : { items: [1], next_cursor: "next" };
  });
  const path = "spaces/a/items?kind=category&deleted=true";
  assert.deepEqual(await cache.read(path, { allPages: true }), { items: [1, 2], next_cursor: null });
  await cache.read(path, { allPages: true });
  assert.equal(paths.length, 2);
  assert.ok(paths[1].includes("deleted=true&after=next"));
  assert.deepEqual(await cache.read(path), { items: [1], next_cursor: "next" });
});

test("failed or looping pagination cannot be cached as a complete catalog", async t => {
  const cache = cacheFor(t, async () => ({ items: [1], next_cursor: "same" }));
  await assert.rejects(cache.read("items", { allPages: true }), /Repeated page cursor/);
});

function versionedCache(t, fetch, readVersion) {
  const cache = new ReadCache(fetch, readVersion);
  cache.setAccount("owner-a");
  t.after(() => cache.dispose());
  return cache;
}

test("older backends retain full refreshes until version checks are explicitly enabled", async t => {
  t.mock.timers.enable({ apis: ["Date"], now: 100000 });
  let calls = 0, checks = 0;
  const cache = cacheFor(t, async () => ({ calls: ++calls }));
  const path = "spaces/a/resources/one";
  t.after(cache.watch(path, () => {}, assert.fail));
  await tick();
  t.mock.timers.tick(31000);
  cache.refetchStale();
  await tick();
  assert.equal(calls, 2, "missing bootstrap capability must use the original full read protocol");
  cache.setReadVersion(async () => { checks++; return "1"; });
  for (let check = 0; check < 2; check++) {
    t.mock.timers.tick(31000);
    cache.refetchStale();
    await tick();
  }
  assert.equal(checks, 2);
  assert.equal(calls, 3, "new capability must fully read once before trusting a version");
  cache.setReadVersion();
  t.mock.timers.tick(31000);
  cache.refetchStale();
  await tick();
  assert.equal(checks, 2, "disabling the protocol must not call a missing endpoint");
  assert.equal(calls, 4);
});

test("unchanged content checks share a small request and skip complete catalog/body downloads", async t => {
  t.mock.timers.enable({ apis: ["Date"], now: 100000 });
  const paths = [];
  let checks = 0, version = "1", body = "original";
  const cache = versionedCache(t, async path => {
    paths.push(path);
    return path.includes("resources/") ? { body }
      : { items: [path.includes("after=") ? 2 : 1], next_cursor: path.includes("after=") ? null : "next" };
  }, async () => { checks++; return version; });
  const seen = [];
  const catalog = "spaces/a/items?kind=project";
  const document = "spaces/a/resources/one";
  t.after(cache.watch(catalog, () => {}, assert.fail, true));
  t.after(cache.watch(document, value => seen.push(value), assert.fail));
  await tick();
  assert.equal(checks, 1);
  assert.equal(paths.length, 3);
  t.mock.timers.tick(61000);
  cache.refetchStale();
  await tick();
  assert.equal(checks, 2);
  assert.equal(paths.length, 3, "unchanged validation must not traverse catalog pages or fetch the body");
  version = "2"; body = "agent edit";
  t.mock.timers.tick(31000);
  cache.refetchStale();
  await tick();
  assert.equal(paths.length, 6);
  assert.deepEqual(seen.at(-1), { body: "agent edit" });
});

test("local invalidation and explicit refresh read content even when the version is unchanged", async t => {
  let calls = 0;
  const cache = versionedCache(t, async () => ({ calls: ++calls }), async () => "1");
  const path = "spaces/a/memories?limit=50";
  await cache.read(path);
  await cache.invalidate(candidate => candidate === path, false);
  assert.deepEqual(await cache.read(path), { calls: 2 });
  assert.deepEqual(await cache.read(path, { fresh: true }), { calls: 3 });
});

test("failed content refreshes retry at the same version and version failures report stale data", async t => {
  t.mock.timers.enable({ apis: ["Date"], now: 100000 });
  let version = "1", calls = 0, failVersion = false;
  const cache = versionedCache(t, async () => {
    if (++calls === 2) throw new Error("content unavailable");
    return { version };
  }, async () => {
    if (failVersion) throw new Error("access check unavailable");
    return version;
  });
  const seen = [], errors = [];
  t.after(cache.watch("spaces/a/memories", value => seen.push(value), error => errors.push(error.message)));
  await tick();
  version = "2";
  t.mock.timers.tick(31000);
  cache.refetchStale();
  await tick();
  assert.deepEqual(errors, ["content unavailable"]);
  cache.refetchStale();
  await tick();
  assert.equal(calls, 3);
  assert.deepEqual(seen.at(-1), { version: "2" });
  failVersion = true;
  t.mock.timers.tick(31000);
  cache.refetchStale();
  await tick();
  assert.equal(calls, 3, "failed access checks must not fetch or mark the cached body current");
  assert.equal(errors.at(-1), "access check unavailable");
  failVersion = false;
  cache.refetchStale();
  await tick();
  assert.equal(calls, 4, "recovery fully rechecks data invalidated by a failed refresh");
  assert.deepEqual(seen.at(-1), { version: "2" }, "recovery emits unchanged data to clear UI errors");
});

test("version captured before content cannot hide a concurrent external edit", async t => {
  t.mock.timers.enable({ apis: ["Date"], now: 100000 });
  let version = "1", calls = 0;
  const cache = versionedCache(t, async () => {
    calls++;
    const result = { version };
    version = "2";
    return result;
  }, async () => version);
  const seen = [];
  t.after(cache.watch("spaces/a/resources/one", value => seen.push(value), assert.fail));
  await tick();
  t.mock.timers.tick(31000);
  cache.refetchStale();
  await tick();
  assert.equal(calls, 2);
  assert.deepEqual(seen, [{ version: "1" }, { version: "2" }]);
});

test("version validation is scoped to account and Space and never gates jobs, usage or searches", async t => {
  const checks = [];
  let calls = 0;
  const cache = versionedCache(t, async () => ({ calls: ++calls }), async space => {
    checks.push(space); return "same";
  });
  await cache.read("spaces/a/resources/one");
  await cache.read("spaces/b/resources/one");
  for (const path of ["spaces/a/memory/jobs", "spaces/a/usage", "spaces/a/overview", "agents", "spaces/a/memories?query=hello"])
    await cache.read(path);
  assert.deepEqual(checks, ["a", "b"]);
  cache.setAccount("owner-b");
  assert.deepEqual(await cache.read("spaces/a/resources/one"), { calls: 8 });
  assert.deepEqual(checks, ["a", "b", "a"]);
});

test("unchanged content still gets a bounded full recheck for metadata outside the revision counter", async t => {
  t.mock.timers.enable({ apis: ["Date"], now: 100000 });
  let calls = 0;
  const cache = versionedCache(t, async () => ({ provider: ++calls }), async () => "1");
  t.after(cache.watch("spaces/a/memories", () => {}, assert.fail));
  await tick();
  for (let minute = 1; minute <= 5; minute++) {
    t.mock.timers.tick(60000);
    cache.refetchStale();
    await tick();
    assert.equal(calls, minute < 5 ? 1 : 2);
  }
});

test("cancelled content cannot seed a version or deliver data after an account switch", async t => {
  const pending = deferred();
  let checks = 0, calls = 0;
  const cache = versionedCache(t, async () => ({ calls: ++calls }), async () => ++checks === 1 ? pending.promise : "2");
  const old = assert.rejects(cache.read("spaces/a/resources/one"));
  cache.setAccount("owner-b");
  assert.deepEqual(await cache.read("spaces/a/resources/one"), { calls: 1 });
  pending.resolve("1");
  await old;
  await tick();
  assert.equal(calls, 1);
  assert.deepEqual(await cache.read("spaces/a/resources/one"), { calls: 1 });
});

test("the shared read queue admits at most two requests and cancels queued work", async () => {
  const scheduler = new ReadScheduler(2);
  const first = deferred(), second = deferred();
  let active = 0, peak = 0;
  const started = [];
  const run = (id, pending, signal = new AbortController().signal) => scheduler.run(async () => {
    started.push(id); active++; peak = Math.max(peak, active);
    try { return await pending; } finally { active--; }
  }, signal);
  const a = run("a", first.promise), b = run("b", second.promise);
  const controller = new AbortController();
  const canceled = run("c", Promise.resolve(), controller.signal);
  const rejected = assert.rejects(canceled);
  const d = run("d", Promise.resolve("done"));
  controller.abort();
  await tick();
  assert.deepEqual(started, ["a", "b"]);
  first.resolve();
  await a;
  assert.equal(await d, "done");
  second.resolve();
  await Promise.all([b, rejected]);
  assert.equal(peak, 2);
  assert.deepEqual(started, ["a", "b", "d"]);
});

test("a selected document goes ahead of queued refreshes without exceeding the concurrency limit", async () => {
  const scheduler = new ReadScheduler(2);
  const first = deferred(), second = deferred(), document = deferred();
  const started = [];
  const run = (name, pending, priority = 0, signal = new AbortController().signal) =>
    scheduler.run(async () => { started.push(name); return pending; }, signal, priority);
  const a = run("active-a", first.promise), b = run("active-b", second.promise);
  const refreshA = run("refresh-a", Promise.resolve());
  const refreshB = run("refresh-b", Promise.resolve());
  const cancelled = new AbortController();
  const canceledRead = assert.rejects(run("old-document", Promise.resolve(), 1, cancelled.signal));
  const selected = run("selected-document", document.promise, 1);
  cancelled.abort();
  await tick();
  assert.deepEqual(started, ["active-a", "active-b"]);
  first.resolve();
  await tick();
  assert.deepEqual(started, ["active-a", "active-b", "selected-document"]);
  document.resolve();
  await tick();
  assert.deepEqual(started, ["active-a", "active-b", "selected-document", "refresh-a", "refresh-b"]);
  second.resolve();
  await Promise.all([a, b, refreshA, refreshB, selected, canceledRead]);
});
