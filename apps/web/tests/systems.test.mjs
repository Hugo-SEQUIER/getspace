import assert from "node:assert/strict";
import { test } from "node:test";
import {
  activityRows, areaSummary, entryTimeline, odometerDigits, filterEntries, percent, promotionBlocker, promotionGate, reviewState,
} from "../lib/space/systems.ts";

const stats = (over = {}) => ({ entries: 0, testing: 0, works: 0, does_not_work: 0, retired: 0, verdicts: 0, reused_30d: 0, reuse_share: null, ...over });
const system = (over = {}) => ({ id: "s1", kind: "system", revision: 1, deleted: false, status: "healthy", review_cadence: null, last_review: null, stats: stats(), ...over });
const entry = (over = {}) => ({
  id: "e1", kind: "lab_entry", revision: 1, deleted: false, updated_at: "2026-10-01T09:00:00Z", author_agent: null,
  name: "Hooks with a number get more replies", system_id: "s1", domain: "Copy & hooks", status: "testing", evidence: "untested",
  why: "", apply: "", applies_to: "", source: "", captured: null, note: "", uses: [], last_used: null, verdict: null, retries: 0, ...over,
});

test("the summary counts health and adds labs, with reuse over every verdict in the area", () => {
  const summary = areaSummary([
    system({ status: "healthy", stats: stats({ testing: 2, works: 3, does_not_work: 1, verdicts: 4, reused_30d: 1 }) }),
    system({ status: "broken", stats: stats({ retired: 1, works: 1, verdicts: 1, reused_30d: 1 }) }),
    system({ status: "paused" }),
  ]);
  assert.deepEqual(summary.health, { healthy: 1, needs_attention: 0, broken: 1, not_measured: 0, paused: 1 });
  assert.deepEqual(summary.lab, { testing: 2, works: 4, does_not_work: 1, retired: 1 });
  assert.deepEqual(summary.reuse, { reused: 2, verdicts: 5, share: 0.4 });
  assert.equal(areaSummary([]).reuse.share, null);
  assert.equal(percent(0.4), "40%");
  assert.equal(percent(null), "–");
});

test("a review is due once the cadence has passed since the last one", () => {
  assert.deepEqual(reviewState({ review_cadence: "weekly", last_review: "2026-10-01" }, "2026-10-08"), { next: "2026-10-08", due: false });
  assert.deepEqual(reviewState({ review_cadence: "weekly", last_review: "2026-10-01" }, "2026-10-09"), { next: "2026-10-08", due: true });
  assert.deepEqual(reviewState({ review_cadence: "monthly", last_review: "2026-01-31" }, "2026-03-01"), { next: "2026-02-28", due: true });
  assert.equal(reviewState({ review_cadence: "quarterly", last_review: "2026-07-15" }, "2026-10-16").next, "2026-10-15");
  assert.deepEqual(reviewState({ review_cadence: "monthly", last_review: null }, "2026-10-01"), { next: null, due: true });
  // A cohort has no fixed date, and a system without a cadence is never due.
  assert.deepEqual(reviewState({ review_cadence: "per_cohort", last_review: "2020-01-01" }, "2026-10-01"), { next: null, due: false });
  assert.deepEqual(reviewState({ review_cadence: null, last_review: null }, "2026-10-01"), { next: null, due: false });
});

test("the promotion gate needs measured evidence, a source and an active system", () => {
  const unmet = (e, s) => promotionGate(e, s).filter((check) => !check.met).map((check) => check.id);
  assert.deepEqual(unmet(entry(), system()), ["evidence", "source"]);
  assert.deepEqual(unmet(entry({ evidence: "owner_call", source: " " }), undefined), ["source", "system"]);
  assert.deepEqual(unmet(entry({ evidence: "measured", source: "Reply rate, Oct" }), system({ deleted: true })), ["system"]);
  const ready = entry({ evidence: "measured", source: "Reply rate, Oct" });
  assert.equal(promotionBlocker(ready, "validated", system()), "");
  assert.equal(promotionBlocker({ ...ready, status: "validated" }, "invalidated", system()), "", "a verdict can flip to the other one");
  assert.match(promotionBlocker({ ...ready, status: "validated" }, "validated", system()), /already works/);
  assert.match(promotionBlocker({ ...ready, status: "retired" }, "validated", system()), /back to Testing/);
  assert.equal(promotionBlocker(entry({ source: "x" }), "validated", system()), "Set the evidence to Measured or Owner's call.");
});

test("lab filters combine, and the query searches the sentence and its notes", () => {
  const entries = [
    entry({ id: "a", status: "validated", evidence: "measured", why: "Reply rate doubled" }),
    entry({ id: "b", system_id: "s2", domain: "SEO/GEO" }),
    entry({ id: "c", status: "retired" }),
  ];
  const ids = (filters) => filterEntries(entries, filters).map((item) => item.id);
  assert.deepEqual(ids({}), ["a", "b", "c"]);
  assert.deepEqual(ids({ status: "validated" }), ["a"]);
  assert.deepEqual(ids({ system: "s2" }), ["b"]);
  assert.deepEqual(ids({ domain: "Copy & hooks", status: "retired" }), ["c"]);
  assert.deepEqual(ids({ evidence: "measured" }), ["a"]);
  assert.deepEqual(ids({ query: "REPLY RATE" }), ["a"]);
});

test("the timeline names each change between revisions, newest first, with notes and authors", () => {
  const first = { ...entry(), author: "you" };
  const measured = { ...first, revision: 2, evidence: "measured", source: "Reply rate, Oct", note: "Two weeks of data", author: "agent", author_agent: { id: "x", revision: 1, name: "Codex", harness: "codex" } };
  const promoted = { ...measured, revision: 3, status: "validated", verdict: { memory_id: "m", label: "works", promoted_at: "2026-10-03T00:00:00Z", promoted_by: null }, note: "", author: "you", author_agent: null };
  const used = { ...promoted, revision: 4, uses: [{ used_in: "October newsletter", at: "2026-10-04", by: null }] };
  const retried = { ...used, revision: 5, status: "testing", verdict: null, retries: 1, note: "Replies dropped" };
  const flipped = { ...retried, revision: 6, status: "invalidated", name: "Hooks with a number get fewer replies" };
  const retired = { ...flipped, revision: 7, status: "retired" };
  const steps = entryTimeline([first, measured, promoted, used, retried, flipped, retired]);
  assert.deepEqual(steps.map((step) => step.revision), [7, 6, 5, 4, 3, 2, 1]);
  const texts = Object.fromEntries(steps.map((step) => [step.revision, step.events.map((event) => event.text)]));
  assert.deepEqual(texts[1], ["Added as untested idea"]);
  assert.deepEqual(texts[2], ["Evidence raised to Measured", "Edited the source"]);
  assert.deepEqual(texts[3], ["Promoted: works"]);
  assert.deepEqual(texts[4], ["Used in October newsletter"]);
  assert.deepEqual(texts[5], ["Back to testing (retry 1)"]);
  assert.deepEqual(texts[6], ["Promoted: does not work", "Edited the sentence"]);
  assert.deepEqual(texts[7], ["Retired"]);
  assert.equal(steps.find((step) => step.revision === 2).note, "Two weeks of data");
  assert.equal(steps.find((step) => step.revision === 2).agent.name, "Codex");
  assert.deepEqual(entryTimeline([first, { ...promoted, revision: 2 }, { ...promoted, revision: 3, status: "invalidated" }])[0].events.map((e) => e.text), ["Verdict changed: does not work"]);
});

test("activity rows take each revision's most telling change, keyed by entry and revision", () => {
  const added = entry();
  const promoted = { ...entry({ evidence: "measured", source: "x" }), revision: 3, status: "validated", note: "" };
  const retried = { ...promoted, revision: 4, status: "testing", retries: 2 };
  const saved = { ...retried, revision: 5 };
  const rows = activityRows([
    { at: "2026-10-05T00:00:00Z", author: "you", author_agent: null, entry: saved, previous: retried },
    { at: "2026-10-04T00:00:00Z", author: "agent", author_agent: { id: "a", revision: 1, name: "Codex", harness: "codex" }, entry: retried, previous: promoted },
    { at: "2026-10-03T00:00:00Z", author: "you", author_agent: null, entry: promoted, previous: { ...added, revision: 2 } },
    { at: "2026-10-01T00:00:00Z", author: "you", author_agent: null, entry: added, previous: null },
  ]);
  assert.deepEqual(rows.map((row) => row.event.text), ["Saved", "Back to testing (retry 2)", "Promoted: works", "Added as untested idea"]);
  assert.deepEqual(rows.map((row) => row.key), ["e1:5", "e1:4", "e1:3", "e1:1"]);
  assert.equal(rows[1].agent.name, "Codex");
});

test("odometer digits are the count's digits, most significant first", () => {
  assert.deepEqual(odometerDigits(0), [0]);
  assert.deepEqual(odometerDigits(107), [1, 0, 7]);
  assert.deepEqual(odometerDigits(-3), [0]);
});
