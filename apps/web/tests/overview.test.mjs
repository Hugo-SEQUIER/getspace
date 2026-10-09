import test from "node:test";
import assert from "node:assert/strict";
import {
  activityPath,
  activityForChart,
  duration,
  filterOverview,
  resultRate,
} from "../lib/space/overview.ts";
import { sampleOverviewSnapshot } from "./support/sample-workspace.ts";

test("retrieval result rate excludes failures and distinguishes no completed requests", () => {
  assert.equal(resultRate({ with_results: 3, empty: 1, errors: 9 }), 0.75);
  assert.equal(resultRate({ with_results: 0, empty: 0, errors: 9 }), null);
  assert.equal(resultRate({ with_results: 0, empty: 4, errors: 0 }), 0);
  assert.equal(duration(null), "—");
  assert.equal(duration(0), "0 ms");
  assert.equal(duration(1250), "1.25 s");
});

test("local periods retain exact aggregates and filter UTC activity without mutating the snapshot", () => {
  const snapshot = sampleOverviewSnapshot([], [], [], new Date("2026-09-21T10:00:00Z"));
  snapshot.periods[7].summary = { ...snapshot.periods[7].summary, reused_memories: 2, p50_ms: 17, p95_ms: 951 };
  snapshot.periods[30].summary = { ...snapshot.periods[30].summary, reused_memories: 3, p50_ms: 29, p95_ms: 1290 };
  snapshot.recent = [
    { id: "before", created_at: "2026-09-14T23:59:59.999Z" },
    { id: "boundary", created_at: "2026-09-15T00:00:00Z" },
    { id: "offset", created_at: "2026-09-15T01:00:00+02:00" },
    { id: "today", created_at: "2026-09-21T09:00:00Z" },
  ];
  const original = structuredClone(snapshot);
  for (const days of [7, 90, 30, 7]) {
    const result = filterOverview(snapshot, days);
    assert.equal(result.days, days);
    assert.equal(result.activity.length, days);
    assert.equal(result.summary, snapshot.periods[days].summary);
    assert.equal(result.agents, snapshot.periods[days].agents);
    assert.equal(result.strategies, snapshot.periods[days].strategies);
    assert.equal(result.totals, snapshot.totals);
    assert.equal(result.usage, snapshot.usage);
    if (days === 7) assert.deepEqual(result.recent.map(row => row.id), ["boundary", "today"]);
  }
  assert.deepEqual(snapshot, original);
});

test("activity chart leaves gaps for untracked days instead of inventing zeroes", () => {
  const rows = [
    { available: false, retrievals: null },
    { available: true, retrievals: 2 },
    { available: false, retrievals: null },
    { available: true, retrievals: 0 },
  ];
  const path = activityPath(rows, "retrievals", 4);
  assert.equal((path.match(/M/g) ?? []).length, 2);
  assert.equal(path.includes("L"), false);
  assert.equal(path.includes("NaN"), false);
});

test("chart waits for real activity and starts at the first event, keeping later quiet days", () => {
  const quiet = { available: true, document_writes: 0, memories_created: 0, retrievals: 0 };
  assert.deepEqual(activityForChart([]), []);
  assert.deepEqual(activityForChart([quiet, quiet]), []);
  assert.deepEqual(activityForChart([{ ...quiet, available: false, retrievals: 5 }]), []);
  for (const key of ["document_writes", "memories_created", "retrievals"]) {
    const first = { ...quiet, [key]: 1 };
    const rows = [{ ...quiet, available: false }, quiet, first, quiet];
    assert.deepEqual(activityForChart(rows), [first, quiet]);
    assert.equal(rows.length, 4);
    assert.deepEqual(activityForChart([first]), [first]);
  }
});

test("document and memory period counts change independently of current totals", () => {
  const snapshot = sampleOverviewSnapshot([{ kind: "document", body: "Text" }], [{}, {}, {}], [], new Date("2026-09-21T10:00:00Z"));
  assert.equal(filterOverview(snapshot, 7).summary.documents_created, 0);
  assert.equal(filterOverview(snapshot, 30).summary.documents_created, 1);
  assert.equal(filterOverview(snapshot, 7).summary.memories_created, 2);
  assert.equal(filterOverview(snapshot, 30).summary.memories_created, 3);
});
