import assert from "node:assert/strict";
import test from "node:test";
import { checklistHiddenKey, createFirstPage, gettingStartedSteps, initialOnboarding, onboardingKey, parseOnboarding, readOnboarding, shouldOfferOnboarding, writeOnboarding } from "../lib/space/onboarding.ts";

test("onboarding progress is isolated by verified account and space", () => {
  const storage = new Map();
  const adapter = { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value) };
  const owner = onboardingKey("owner-1", "space-1");
  const progress = { ...initialOnboarding(), step: 2, focus: "research", status: "skipped", title: "PRIVATE TITLE", notes: "PRIVATE NOTES", token: "PRIVATE TOKEN" };
  assert.equal(writeOnboarding(owner, progress, adapter), true);
  assert.deepEqual(readOnboarding(owner, adapter), { version: 1, step: 2, focus: "research", status: "skipped" });
  assert.equal(readOnboarding(onboardingKey("owner-2", "space-1"), adapter), null);
  assert.equal(readOnboarding(onboardingKey("owner-1", "space-2"), adapter), null);
  assert.equal(onboardingKey(undefined, "space-1"), null);
  assert.equal(onboardingKey("owner-1", undefined), null);
  assert.ok(!storage.get(owner).includes("PRIVATE"));
});

test("invalid or unavailable browser storage cannot block onboarding; skipped progress resumes in this session", () => {
  for (const raw of ["broken", "null", '{"version":2}', JSON.stringify({ ...initialOnboarding(), step: 20 }), JSON.stringify({ ...initialOnboarding(), focus: "team" })])
    assert.equal(parseOnboarding(raw), null);
  const broken = { getItem() { throw new Error("disabled"); }, setItem() { throw new Error("quota"); } };
  const key = onboardingKey("session-fallback", "one");
  assert.equal(readOnboarding(key, broken), null);
  const saved = { ...initialOnboarding(), step: 1, focus: "projects", status: "skipped" };
  assert.equal(writeOnboarding(key, saved, broken), false);
  assert.deepEqual(readOnboarding(key, broken), saved);
});

test("automatic welcome requires a known identity and an empty writable personal space", () => {
  const eligible = { accountId: "verified", kind: "personal", canWrite: true, empty: true, ready: true };
  assert.equal(shouldOfferOnboarding(eligible), true);
  for (const change of [{ accountId: undefined }, { kind: "team" }, { kind: undefined }, { canWrite: false }, { empty: false }, { ready: false }])
    assert.equal(shouldOfferOnboarding({ ...eligible, ...change }), false);
});

test("failed first-page writes preserve progress and an explicit retry saves the original user content", async () => {
  const progress = { ...initialOnboarding(), step: 2, focus: "research" };
  const before = structuredClone(progress);
  const input = { title: "  My first page  ", notes: "User-written notes" };
  const calls = [];
  const save = async (...args) => {
    calls.push(args);
    if (calls.length === 1) throw new Error("Service unavailable");
    return { ...args[0], id: "saved-page", revision: 1, author: "You", updated_at: "2026-10-02T00:00:00Z" };
  };
  await assert.rejects(createFirstPage(save, input, progress), /Service unavailable/);
  assert.deepEqual(progress, before);
  const result = await createFirstPage(save, input, progress);
  assert.equal(result.progress.status, "completed");
  assert.equal(result.resource.id, "saved-page");
  assert.deepEqual(calls[0], calls[1]);
  assert.deepEqual(calls[1], [{ kind: "document", title: "My first page", body: "User-written notes", status: "todo" }]);
  await assert.rejects(createFirstPage(save, { ...input, title: " " }, progress), /Give your page a name/);
  assert.equal(calls.length, 2);
});

test("getting-started steps come from saved documents, tasks, and active agent connections", () => {
  assert.deepEqual(gettingStartedSteps([], 0).map(step => step.done), [false, false, false]);
  const memoryOnly = [{ kind: "document", recall: { category: "memory" } }];
  assert.equal(gettingStartedSteps(memoryOnly, 0)[0].done, false);
  const saved = [{ kind: "document" }, { kind: "task" }];
  assert.deepEqual(gettingStartedSteps(saved, 1).map(step => [step.id, step.done]), [["page", true], ["task", true], ["agent", true]]);
  assert.equal(checklistHiddenKey(onboardingKey("owner-1", "space-1")), "space-checklist-hidden-v1:account:owner-1:space-1");
});
