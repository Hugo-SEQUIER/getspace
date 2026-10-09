import assert from "node:assert/strict";
import test from "node:test";
import { createNotificationStore, parseNotification } from "../lib/notifications.ts";

function fakeClock() {
  let now = 0;
  let nextId = 0;
  const timers = new Map();
  return {
    setTimeout(callback, delay) {
      const id = ++nextId;
      timers.set(id, { callback, at: now + delay });
      return id;
    },
    clearTimeout(id) { timers.delete(id); },
    advance(milliseconds) {
      const target = now + milliseconds;
      while (true) {
        const due = [...timers].filter(([, timer]) => timer.at <= target).sort((a, b) => a[1].at - b[1].at)[0];
        if (!due) break;
        const [id, timer] = due;
        now = timer.at;
        timers.delete(id);
        timer.callback();
      }
      now = target;
    },
    get pending() { return timers.size; },
  };
}

test("notification events reject invalid states and normalize readable messages", () => {
  assert.deepEqual(parseNotification({ id: "save", kind: "loading", message: "  Saving document…  ", extra: true }), {
    id: "save", kind: "loading", message: "Saving document…",
  });
  for (const event of [
    null, {}, { id: "save", message: "Saving" },
    { id: "save", kind: "pending", message: "Saving" },
    { id: "save", kind: "error", message: "   " },
    { id: "save", kind: "success", message: 42 },
    { id: "", kind: "info", message: "Ready" },
    { id: "x".repeat(201), kind: "info", message: "Ready" },
    { id: "save", kind: "info", message: "x".repeat(8001) },
  ]) assert.equal(parseNotification(event), null);
});

test("separate notification stores isolate browser roots and server requests", () => {
  const first = createNotificationStore();
  const second = createNotificationStore();
  first.notify({ id: "account", kind: "error", message: "First account failed" });
  second.notify({ id: "account", kind: "loading", message: "Opening another account" });
  first.dispose();
  assert.deepEqual(first.getSnapshot(), []);
  assert.deepEqual(second.getSnapshot(), [{ id: "account", kind: "loading", message: "Opening another account" }]);
  second.dispose();
});

test("concurrent operations retain separate notifications and update the same operation in place", () => {
  const clock = fakeClock();
  const store = createNotificationStore({ clock });
  const initial = store.getSnapshot();
  assert.equal(store.getSnapshot(), initial, "React snapshots remain stable between changes");
  const updates = [];
  const unsubscribe = store.subscribe(() => updates.push(store.getSnapshot()));
  assert.equal(store.notify({ id: "save-document", kind: "loading", message: "Saving document…" }), "save-document");
  store.notify({ id: "load-memory", kind: "loading", message: "Loading memory…" });
  store.notify({ id: "save-document", kind: "success", message: "Document saved" });
  assert.equal(store.getSnapshot().length, 2);
  assert.deepEqual(store.getSnapshot().find((item) => item.id === "save-document"), { id: "save-document", kind: "success", message: "Document saved" });
  assert.equal(store.getSnapshot().find((item) => item.id === "load-memory").kind, "loading");
  assert.equal(updates.length, 3);
  const snapshot = store.getSnapshot();
  store.notify({ id: "save-document", kind: "success", message: "Document saved" });
  assert.equal(store.getSnapshot(), snapshot);
  assert.equal(updates.length, 3, "Repeated identical feedback does not rerender the root");
  assert.equal(store.notify({ id: "save-document", kind: "error", message: "" }), null);
  assert.equal(store.getSnapshot(), snapshot, "Invalid feedback cannot replace a valid notification");
  unsubscribe();
  store.remove("save-document");
  assert.equal(updates.length, 3);
  store.dispose();
});

test("success and info expire independently while errors and loading remain until addressed", () => {
  const clock = fakeClock();
  const store = createNotificationStore({ clock });
  let dismissed = 0;
  store.notify({ id: "saved", kind: "success", message: "Document saved" }, () => { dismissed++; });
  store.notify({ id: "failure", kind: "error", message: "Could not save your task" });
  store.notify({ id: "working", kind: "loading", message: "Exporting your Space…" });
  clock.advance(1000);
  store.notify({ id: "tip", kind: "info", message: "Your preferences are saved for this session" });
  clock.advance(5499);
  assert.equal(store.getSnapshot().length, 4);
  clock.advance(1);
  assert.equal(store.getSnapshot().some((item) => item.id === "saved"), false);
  assert.equal(store.getSnapshot().some((item) => item.id === "tip"), true);
  assert.equal(dismissed, 1);
  clock.advance(1000);
  assert.deepEqual(store.getSnapshot().map((item) => item.id), ["failure", "working"]);
  clock.advance(60_000);
  assert.equal(store.getSnapshot().length, 2);
  assert.equal(clock.pending, 0);
  store.dispose();
});

test("repeated notifications do not postpone expiration and a changed operation resets its lifetime", () => {
  const clock = fakeClock();
  const store = createNotificationStore({ clock });
  const saved = { id: "save", kind: "success", message: "Document saved" };
  store.notify(saved);
  clock.advance(4000);
  store.notify(saved);
  clock.advance(2500);
  assert.deepEqual(store.getSnapshot(), []);

  store.notify({ id: "export", kind: "loading", message: "Preparing export…" });
  clock.advance(20_000);
  store.notify({ id: "export", kind: "success", message: "Export ready" });
  clock.advance(6499);
  assert.equal(store.getSnapshot()[0].message, "Export ready");
  clock.advance(1);
  assert.deepEqual(store.getSnapshot(), []);
  store.dispose();
});

test("an earlier success timer cannot remove a later error for the same operation", () => {
  const clock = fakeClock();
  const store = createNotificationStore({ clock });
  store.notify({ id: "sync", kind: "success", message: "Changes saved" });
  clock.advance(3000);
  store.notify({ id: "sync", kind: "error", message: "The next save failed" });
  assert.equal(clock.pending, 0);
  clock.advance(60_000);
  assert.deepEqual(store.getSnapshot(), [{ id: "sync", kind: "error", message: "The next save failed" }]);
  store.dispose();
});

test("dismissal invokes pending-operation cleanup once without affecting another operation", () => {
  const clock = fakeClock();
  const store = createNotificationStore({ clock });
  const first = new AbortController();
  const second = new AbortController();
  let cleanupCalls = 0;
  store.notify({ id: "first", kind: "loading", message: "Loading first document…" }, () => { cleanupCalls++; first.abort(); });
  store.notify({ id: "second", kind: "loading", message: "Loading second document…" }, () => second.abort());
  store.dismiss("first");
  store.dismiss("first");
  assert.equal(first.signal.aborted, true);
  assert.equal(second.signal.aborted, false);
  assert.equal(cleanupCalls, 1);
  assert.equal(store.getSnapshot()[0].id, "second");
  store.remove("second");
  assert.equal(second.signal.aborted, false, "Finishing an operation silently must not cancel it");
  assert.deepEqual(store.getSnapshot(), []);
  store.dispose();
});

test("removal and disposal cancel timers without triggering dismissal callbacks", () => {
  const clock = fakeClock();
  const store = createNotificationStore({ clock });
  let dismissed = 0;
  let changes = 0;
  store.subscribe(() => { changes++; });
  store.notify({ id: "one", kind: "success", message: "First saved" }, () => { dismissed++; });
  store.notify({ id: "two", kind: "info", message: "Second ready" }, () => { dismissed++; });
  store.remove("one");
  assert.equal(clock.pending, 1);
  store.dispose();
  const changesAtDisposal = changes;
  assert.equal(clock.pending, 0);
  assert.deepEqual(store.getSnapshot(), []);
  clock.advance(60_000);
  assert.equal(dismissed, 0);
  assert.equal(changes, changesAtDisposal);
});

test("clearing an account removes its feedback and timers while subscribers remain ready for the next account", () => {
  const clock = fakeClock();
  const store = createNotificationStore({ clock });
  const updates = [];
  let previousAccountCallbacks = 0;
  store.subscribe(() => updates.push(store.getSnapshot()));
  store.notify({ id: "saved", kind: "success", message: "Previous account saved" }, () => { previousAccountCallbacks++; });
  store.notify({ id: "export", kind: "loading", message: "Previous account exporting…" }, () => { previousAccountCallbacks++; });
  store.notify({ id: "error", kind: "error", message: "Previous account save failed" }, () => { previousAccountCallbacks++; });
  store.clear();
  assert.deepEqual(store.getSnapshot(), []);
  assert.deepEqual(updates.at(-1), [], "The mounted notification UI sees the account reset");
  assert.equal(clock.pending, 0);
  clock.advance(60_000);
  for (const id of ["saved", "export", "error"]) store.dismiss(id);
  assert.equal(previousAccountCallbacks, 0);

  const updatesAfterReset = updates.length;
  store.notify({ id: "saved", kind: "success", message: "New account saved" });
  assert.equal(updates.length, updatesAfterReset + 1, "Existing subscribers receive the next account's feedback");
  assert.deepEqual(updates.at(-1), [{ id: "saved", kind: "success", message: "New account saved" }]);
  clock.advance(6500);
  assert.deepEqual(updates.at(-1), []);
  assert.equal(previousAccountCallbacks, 0);
  store.dispose();
});

test("visible capacity evicts old feedback and its timer without dismissing the underlying operation", () => {
  const clock = fakeClock();
  const store = createNotificationStore({ clock, maxVisible: 2 });
  let dismissed = 0;
  store.notify({ id: "old", kind: "success", message: "Old operation saved" }, () => { dismissed++; });
  store.notify({ id: "pending", kind: "loading", message: "Still working…" });
  store.notify({ id: "latest", kind: "error", message: "Latest operation failed" });
  assert.deepEqual(store.getSnapshot().map((item) => item.id), ["pending", "latest"]);
  assert.equal(clock.pending, 0);
  clock.advance(60_000);
  assert.equal(dismissed, 0);
  store.dispose();
});
