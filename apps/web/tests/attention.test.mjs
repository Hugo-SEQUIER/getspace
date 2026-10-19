import assert from "node:assert/strict";
import { test } from "node:test";
import { attentionItems, isUnseen, mergeSeen, seenRevisions, unseenByProject } from "../lib/space/attention.ts";

const item = (id, values = {}) => ({
  id, kind: "task", title: id, status: "todo", revision: 2, author: "agent", updated_at: `2026-10-0${id.length}T00:00:00Z`, ...values,
});

test("an item is unseen only when someone else wrote a revision the person has not opened", () => {
  assert.ok(isUnseen(item("a"), {}));
  assert.ok(isUnseen(item("a"), { a: 1 }));
  assert.ok(!isUnseen(item("a"), { a: 2 }), "opening the current revision clears it");
  assert.ok(!isUnseen(item("a"), { a: 5 }), "a mark never lags behind");
  assert.ok(!isUnseen(item("a", { author: "you" }), {}), "the person's own writes are never new to them");
  assert.ok(isUnseen(item("a", { author: "Teammate" }), {}));
});

test("needs-you tasks are listed once, memory never appears, and both lists are newest first", () => {
  const seen = { old: 2 };
  const { needsYou, unseen } = attentionItems([
    item("old"),
    item("fresh", { revision: 3 }),
    item("flagged", { status: "needs_you", author: "agent" }),
    item("mine-flagged", { status: "needs_you", author: "you", updated_at: "2026-10-09T00:00:00Z" }),
    item("memory", { kind: "document", recall: { category: "memory" } }),
    item("memo-flag", { kind: "document", status: "needs_you", recall: { category: "memory" } }),
    item("doc-flag", { kind: "document", status: "needs_you" }),
  ], seen);
  assert.deepEqual(needsYou.map(r => r.id), ["mine-flagged", "flagged"], "a flag counts whoever set it");
  assert.deepEqual(unseen.map(r => r.id), ["doc-flag", "fresh"], "only tasks wait on the person");
});

test("seen marks index by resource, keep the highest revision and merge upwards only", () => {
  assert.deepEqual(seenRevisions([
    { resource_id: "a", seen_revision: 1, seen_at: "" },
    { resource_id: "a", seen_revision: 3, seen_at: "" },
    { resource_id: "b", seen_revision: 2, seen_at: "" },
  ]), { a: 3, b: 2 });
  const seen = { a: 3 };
  assert.deepEqual(mergeSeen(seen, [{ id: "a", revision: 1 }, { id: "c", revision: 4 }]), { a: 3, c: 4 });
  assert.deepEqual(seen, { a: 3 }, "merging never mutates the current marks");
});

test("unseen counts group by project name with unassigned items last among equals", () => {
  const names = new Map([["p1", "Space"], ["p2", "Predella"]]);
  const counts = unseenByProject([
    item("a", { project: "p1" }), item("b", { project: "p1" }), item("c", { project: "p2" }), item("d"), item("e", { project: "gone" }),
  ], names);
  assert.deepEqual(counts, [{ label: "Space", count: 2 }, { label: "No project", count: 2 }, { label: "Predella", count: 1 }]);
});
