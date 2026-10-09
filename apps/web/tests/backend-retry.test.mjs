import assert from "node:assert/strict";
import test from "node:test";
import { backendFailureCode, fetchBackend } from "../lib/backend-fetch.ts";

const url = new URL("https://api.example.invalid/v1/resources");

test("a transient GET connection failure retries once with the same authorization and deadline", async t => {
  const controller = new AbortController();
  const calls = [];
  t.mock.method(console, "warn", () => {});
  t.mock.method(globalThis, "fetch", async (target, init) => {
    calls.push({ target, init });
    if (calls.length === 1) throw new TypeError("private connection details", { cause: { code: "ECONNRESET" } });
    return Response.json({ ok: true });
  });
  const init = { method: "GET", signal: controller.signal, headers: { Authorization: "Bearer fixture" } };
  assert.deepEqual(await (await fetchBackend(url, init)).json(), { ok: true });
  assert.equal(calls.length, 2);
  for (const call of calls) {
    assert.equal(call.target, url);
    assert.equal(call.init.signal, controller.signal);
    assert.equal(call.init.headers.Authorization, "Bearer fixture");
    assert.equal(call.init.redirect, "manual");
  }
});

test("gateway retry cancels the failed body and preserves the successful response", async t => {
  let calls = 0, cancelled = false;
  t.mock.method(console, "warn", () => {});
  t.mock.method(globalThis, "fetch", async () => {
    if (++calls === 1) return new Response(new ReadableStream({ cancel() { cancelled = true; } }), {
      status: 503, headers: { "Retry-After": "0" },
    });
    assert.equal(cancelled, true);
    return Response.json({ items: [] }, { headers: { "Server-Timing": "api_total;dur=12" } });
  });
  const response = await fetchBackend(url, { method: "GET" });
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("server-timing"), "api_total;dur=12");
  assert.equal(calls, 2);
});

test("writes, non-retryable failures, redirects and long backoffs are not retried", async t => {
  const cases = [
    ["POST", 503, {}], ["DELETE", 503, {}],
    ["POST", 503, { "Server-Timing": "api_total;dur=8, db_pool;dur=8" }],
    ["GET", 401, {}], ["GET", 403, {}], ["GET", 404, {}], ["GET", 429, {}],
    ["GET", 500, {}], ["GET", 503, { "Retry-After": "60" }],
    ["GET", 503, { "Retry-After": "Wed, 23 Sep 2026 10:00:00 GMT" }],
  ];
  t.mock.method(console, "warn", () => {});
  for (const [method, status, headers] of cases) {
    let calls = 0;
    const mock = t.mock.method(globalThis, "fetch", async () => {
      calls++; return new Response("fixture", { status, headers });
    });
    assert.equal((await fetchBackend(url, { method })).status, status);
    assert.equal(calls, 1);
    mock.mock.restore();
  }
  for (const method of ["POST", "DELETE"]) {
    let calls = 0;
    const mock = t.mock.method(globalThis, "fetch", async () => { calls++; throw new TypeError("fetch failed"); });
    await assert.rejects(fetchBackend(url, { method }), TypeError);
    assert.equal(calls, 1); mock.mock.restore();
  }
  let redirects = 0;
  t.mock.method(globalThis, "fetch", async () => { redirects++; return new Response(null, {
    status: 302, headers: { Location: "https://untrusted.invalid" },
  }); });
  await assert.rejects(fetchBackend(url, { method: "GET" }), /redirects are not allowed/);
  assert.equal(redirects, 1);
});

test("persistent gateway errors stop after two attempts", async t => {
  let calls = 0;
  t.mock.method(console, "warn", () => {});
  t.mock.method(globalThis, "fetch", async () => { calls++; return new Response("offline", { status: 502 }); });
  const response = await fetchBackend(url, { method: "GET" });
  assert.equal(response.status, 502);
  assert.equal(await response.text(), "offline");
  assert.equal(calls, 2);
});

test("Python read failures recover once, including the reported project and document lists", async t => {
  t.mock.method(console, "warn", () => {});
  for (const path of ["items?kind=project&deleted=false", "resources?kind=document"]) {
    let calls = 0, cancelled = false;
    const target = new URL(`/v1/spaces/fixture/${path}`, url);
    const signal = AbortSignal.timeout(5000);
    const mock = t.mock.method(globalThis, "fetch", async (actual, init) => {
      assert.equal(actual, target);
      assert.equal(init.signal, signal);
      if (++calls === 1) return new Response(new ReadableStream({ cancel() { cancelled = true; } }), {
        status: 503, headers: { "Server-Timing": "api_total;dur=8, db_pool;dur=8" },
      });
      assert.equal(cancelled, true);
      return Response.json({ items: [] }, { headers: { "Server-Timing": "api_total;dur=10" } });
    });
    const result = await fetchBackend(target, { method: "GET", signal });
    assert.equal(result.status, 200);
    assert.deepEqual(await result.json(), { items: [] });
    assert.equal(calls, 2);
    mock.mock.restore();
  }
});

test("persistent Python failures retain the final error and timing after one retry", async t => {
  let calls = 0;
  t.mock.method(console, "warn", () => {});
  t.mock.method(globalThis, "fetch", async () => {
    calls++;
    return Response.json({ error: "Service unavailable" }, {
      status: 503, headers: { "Server-Timing": "api_total;dur=8", "Retry-After": "0" },
    });
  });
  const result = await fetchBackend(url, { method: "GET" });
  assert.equal(calls, 2);
  assert.equal(result.status, 503);
  assert.equal(result.headers.get("server-timing"), "api_total;dur=8");
  assert.deepEqual(await result.json(), { error: "Service unavailable" });
});

test("cancellation during backoff prevents another request", async t => {
  let calls = 0;
  const controller = new AbortController();
  t.mock.method(console, "warn", () => {});
  t.mock.method(globalThis, "fetch", async () => {
    calls++;
    setTimeout(() => controller.abort(new DOMException("deadline", "TimeoutError")), 10);
    return new Response(null, { status: 504 });
  });
  await assert.rejects(fetchBackend(url, { method: "GET", signal: controller.signal }), { name: "TimeoutError" });
  assert.equal(calls, 1);
  await assert.rejects(fetchBackend(url, { method: "GET", signal: controller.signal }), { name: "TimeoutError" });
  assert.equal(calls, 1);
});

test("failure diagnostics contain only fixed categories", () => {
  assert.equal(backendFailureCode(new TypeError("secret hostname", { cause: { code: "UND_ERR_SOCKET" } })), "UND_ERR_SOCKET");
  assert.equal(backendFailureCode(new Error("secret token", { cause: { code: "secret account" } })), "unknown");
  assert.equal(backendFailureCode(new SyntaxError("private response")), "invalid_json");
  assert.equal(backendFailureCode(new DOMException("deadline", "TimeoutError")), "timeout");
});
