import test from "node:test";
import assert from "node:assert/strict";
import * as Effect from "effect/Effect";
import * as Exit from "effect/Exit";
import { ApiError, BrowserHttp, BrowserRequestError, browserRequest, createBrowserHttp, sendBrowserRequest } from "../lib/browser-http.ts";

test("Effect transport preserves same-origin JSON request semantics without exporting credentials", async () => {
  const calls = [];
  const service = createBrowserHttp(async (url, init) => {
    calls.push({ url, init });
    return Response.json({ revision: 4 });
  });
  const result = await sendBrowserRequest({ path: "spaces/a/items", method: "POST", body: { command_id: "one", title: "Private title" } }, service);
  assert.deepEqual(result, { revision: 4 });
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "/api/spaces/a/items");
  assert.equal(calls[0].init.credentials, "same-origin");
  assert.equal(calls[0].init.cache, "no-store");
  assert.equal(calls[0].init.redirect, "error");
  assert.deepEqual(calls[0].init.headers, { "Content-Type": "application/json" });
  assert.deepEqual(JSON.parse(calls[0].init.body), { command_id: "one", title: "Private title" });
});

test("HTTP statuses and messages cross the Effect Promise boundary as ApiError instances", async () => {
  for (const status of [401, 403, 409, 429, 503, 504]) {
    let calls = 0;
    const service = createBrowserHttp(async () => { calls++; return Response.json({ error: "Request rejected" }, { status }); });
    await assert.rejects(sendBrowserRequest({ path: "spaces/a/items", method: "POST" }, service), error =>
      error instanceof ApiError && error.status === status && error.message === "Request rejected");
    assert.equal(calls, 1, "uncertain writes must never be retried automatically");
  }
});

test("non-JSON access failures retain status; invalid success and connection failures stay distinct", async () => {
  const request = { path: "workspace", method: "GET" };
  await assert.rejects(sendBrowserRequest(request, createBrowserHttp(async () => new Response("Denied", { status: 403 }))),
    error => error instanceof ApiError && error.status === 403);
  await assert.rejects(sendBrowserRequest(request, createBrowserHttp(async () => new Response("not JSON"))),
    error => error instanceof BrowserRequestError && error.kind === "invalid-response");
  let calls = 0;
  await assert.rejects(sendBrowserRequest({ ...request, method: "POST" }, createBrowserHttp(async () => {
    calls++; throw new TypeError("fetch failed");
  })), error => error instanceof BrowserRequestError && error.kind === "network");
  assert.equal(calls, 1);
});

test("cancellation interrupts actual fetch and preserves its DOMException reason", async () => {
  const controller = new AbortController();
  const started = Promise.withResolvers();
  let fetchSignal;
  const service = createBrowserHttp((_url, { signal }) => {
    fetchSignal = signal;
    started.resolve();
    return new Promise((_resolve, reject) => signal.addEventListener("abort", () => reject(signal.reason), { once: true }));
  });
  const result = sendBrowserRequest({ path: "spaces/a/resources", method: "GET", signal: controller.signal }, service);
  await started.promise;
  const reason = new DOMException("Account changed", "AbortError");
  controller.abort(reason);
  await assert.rejects(result, error => error === reason);
  assert.equal(fetchSignal.aborted, true);
});

test("deadline remains TimeoutError and an already-cancelled request never starts fetch", async () => {
  const controller = new AbortController();
  const reason = new DOMException("Read deadline exceeded", "TimeoutError");
  controller.abort(reason);
  let calls = 0;
  await assert.rejects(sendBrowserRequest({ path: "spaces/a/resources", method: "GET", signal: controller.signal },
    createBrowserHttp(async () => { calls++; return Response.json({}); })), error => error === reason);
  assert.equal(calls, 0);
});

test("the HTTP service can be supplied to a composed Effect without a hidden global runtime", async () => {
  const service = createBrowserHttp(async () => Response.json({ id: "one" }));
  const program = Effect.gen(function*() {
    const value = yield* browserRequest({ path: "spaces/a/resources/one", method: "GET" });
    return value.id;
  }).pipe(Effect.provideService(BrowserHttp, service));
  const result = await Effect.runPromiseExit(program);
  assert.ok(Exit.isSuccess(result));
  assert.equal(result.value, "one");
});
