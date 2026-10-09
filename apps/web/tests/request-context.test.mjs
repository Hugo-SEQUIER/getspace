import assert from "node:assert/strict";
import test from "node:test";
import { parseCookies, RequestCookies, requestContext, serializeCookie } from "../lib/request-context.ts";
import { handleApi } from "../app/api/[...path]/route.ts";
import { apiOrigin, readBody, webOrigin } from "../lib/server.ts";

// Synthetic configuration only: the key below unlocks nothing outside these tests.
const origin = "https://space.example.invalid";
const key = "fixture-access-key-0123456789-abcdefghij";
const env = {
  SPACE_WEB_URL: origin,
  SPACE_API_URL: "http://api:8000",
  SPACE_ACCESS_KEY: key,
};
const space = "11111111-1111-4111-8111-111111111111";
let peer = 0;
// Each test unlocks from its own address, so failed attempts never leak between tests.
const runtime = (overrides = {}, backendFetch) => ({ env: { ...env, ...overrides }, backendFetch, clientAddress: `203.0.113.${++peer}` });
const cookiesFrom = response => response.headers.getSetCookie().map(value => value.split(";")[0]).join("; ");
const request = (path, method = "GET", body, headers = {}) => new Request(origin + "/api/" + path, {
  method,
  headers: { Origin: origin, "Content-Type": "application/json", ...headers },
  ...(body === undefined ? {} : { body: JSON.stringify(body) }),
});
const noBackend = async () => { throw new Error("No backend request is allowed"); };

async function unlock(context = runtime()) {
  const response = await handleApi(request("auth/unlock", "POST", { key }), "auth/unlock", context);
  assert.equal(response.status, 200);
  return cookiesFrom(response);
}

test("cookies parse, round-trip and keep one Set-Cookie line per name and path", () => {
  assert.deepEqual([...parseCookies('a=1; b="quoted%20value"; a=2; bad name=3; c=%zz')], [["a", "1"], ["b", "quoted value"], ["c", "%zz"]]);
  assert.equal(serializeCookie("space-session", "v1.a b", { httpOnly: true, secure: true, sameSite: "lax", path: "/", maxAge: 60 }),
    "space-session=v1.a%20b; Max-Age=60; Path=/; HttpOnly; Secure; SameSite=Lax");
  assert.throws(() => serializeCookie("bad name", "x"));
  for (const status of [200, 307, 503]) {
    const jar = new RequestCookies("session=first%2Bvalue; unrelated=value");
    assert.equal(jar.get("session").value, "first+value");
    jar.set("session", "new+value", { httpOnly: true, secure: true, sameSite: "lax" });
    jar.set("session", "newer+value", { httpOnly: true, secure: true, sameSite: "lax" });
    jar.set("other", "", { maxAge: 0, httpOnly: true, path: "/connect" });
    const response = jar.apply(new Response(null, {
      status, headers: { "Cache-Control": "no-store", ...(status === 307 ? { Location: origin + "/app" } : {}) },
    }));
    assert.equal(response.status, status);
    assert.equal(response.headers.get("cache-control"), "no-store");
    assert.equal(response.headers.getSetCookie().length, 2);
    const session = response.headers.getSetCookie().find(value => value.startsWith("session="));
    assert.match(session, /Path=\//);
    assert.match(session, /HttpOnly/);
    assert.match(session, /Secure/);
    assert.match(session, /SameSite=Lax/);
    assert.equal(new RequestCookies(cookiesFrom(response)).get("session").value, "newer+value");
    assert.match(response.headers.getSetCookie().find(value => value.startsWith("other=")), /Max-Age=0; Path=\/connect/);
  }
});

test("injected environments do not fall back to host configuration", () => {
  const context = requestContext(request("workspace"), { env: {} });
  assert.deepEqual(context.env, {});
  assert.equal(webOrigin(context.env), "http://127.0.0.1:3000");
  assert.equal(apiOrigin(context.env), "http://127.0.0.1:8000");
});

test("operators may point at internal HTTP services, never at URLs with credentials", () => {
  assert.equal(apiOrigin({ SPACE_API_URL: "http://api:8000" }), "http://api:8000");
  assert.equal(webOrigin({ SPACE_WEB_URL: "http://192.168.1.20:3000/" }), "http://192.168.1.20:3000");
  for (const value of ["https://user:secret@api.invalid", "http://api:8000/?debug=1", "ftp://api", "not a url"])
    assert.throws(() => apiOrigin({ SPACE_API_URL: value }), error => error.status === 503);
  // Compose passes a setting that .env leaves blank as an empty string: it means unset.
  assert.equal(webOrigin({ SPACE_WEB_URL: " " }), "http://127.0.0.1:3000");
  assert.equal(apiOrigin({ SPACE_API_URL: "", BACKEND_PORT: "" }), "http://127.0.0.1:8000");
  assert.equal(apiOrigin({ SPACE_API_URL: "", BACKEND_PORT: "8100" }), "http://127.0.0.1:8100");
});

test("a locked browser reaches nothing behind the proxy, and unlocks only with the access key", async () => {
  const context = runtime({}, noBackend);
  const locked = await handleApi(request(`spaces/${space}/resources?kind=task`), `spaces/${space}/resources`, context);
  assert.equal(locked.status, 401);
  assert.equal(locked.headers.get("cache-control"), "no-store");
  assert.deepEqual(await (await handleApi(request("auth/session"), "auth/session", context)).json(), { mode: "key", unlocked: false });

  const wrong = await handleApi(request("auth/unlock", "POST", { key: key.slice(1) }), "auth/unlock", context);
  assert.equal(wrong.status, 401);
  assert.deepEqual(await wrong.json(), { error: "That access key is not correct." });
  assert.deepEqual(wrong.headers.getSetCookie(), []);
  for (const body of [{}, { key: "" }, { key: 7 }, { key: "x".repeat(1025) }])
    assert.equal((await handleApi(request("auth/unlock", "POST", body), "auth/unlock", context)).status, 400);

  const unlocked = await handleApi(request("auth/unlock", "POST", { key: `  ${key} ` }), "auth/unlock", context);
  assert.equal(unlocked.status, 200);
  assert.deepEqual(await unlocked.json(), { mode: "key", unlocked: true });
  const [cookie] = unlocked.headers.getSetCookie();
  assert.match(cookie, /^space-session=v1\./);
  for (const attribute of [/HttpOnly/, /Secure/, /SameSite=Lax/, /Path=\//, /Max-Age=2592000/]) assert.match(cookie, attribute);
  assert.ok(!cookie.includes(key), "the cookie never carries the access key");
  const session = await handleApi(request("auth/session", "GET", undefined, { Cookie: cookiesFrom(unlocked) }), "auth/session", context);
  assert.deepEqual(await session.json(), { mode: "key", unlocked: true });
});

test("an unlocked browser reaches the API with the access key and gets the owner's identity", async () => {
  let calls = 0;
  const context = runtime({ SPACE_OWNER_NAME: "  Ada  Lovelace " }, async (input, init) => {
    calls++;
    assert.equal(String(input), "http://api:8000/v1/workspace?fixture=1");
    assert.equal(init.redirect, "manual");
    assert.equal(init.method, "POST");
    assert.equal(new Headers(init.headers).get("authorization"), `Bearer ${key}`);
    return Response.json({ id: space, name: "Private Space", mcp_url: "https://api.example.invalid/mcp/", user: { id: "untrusted" } });
  });
  const cookie = await unlock(context);
  const response = await handleApi(request("workspace?fixture=1", "POST", {}, { Cookie: cookie }), "workspace", context);
  assert.equal(response.status, 200);
  const value = await response.json();
  assert.deepEqual(value.user, { id: "owner", email: null });
  assert.deepEqual(value.profile, { displayName: "Ada Lovelace", avatarUrl: null });
  assert.equal(value.access_mode, "key");
  assert.equal(Buffer.from(value.browser_cache_key, "base64").length, 32);
  assert.equal(value.name, "Private Space");
  assert.equal(response.headers.get("cache-control"), "no-store");
  assert.equal(calls, 1);
});

test("rotating the access key locks every browser and clears its stale cookie", async () => {
  const cookie = await unlock(runtime());
  const response = await handleApi(request("workspaces", "GET", undefined, { Cookie: cookie }), "workspaces",
    runtime({ SPACE_ACCESS_KEY: "rotated-access-key-0123456789-abcdefghij" }, noBackend));
  assert.equal(response.status, 401);
  assert.match(response.headers.getSetCookie()[0], /^space-session=; Max-Age=0; Path=\//);
});

test("locking clears the session cookie and keeps the origin check", async () => {
  const context = runtime({}, noBackend);
  const cookie = await unlock(context);
  const foreign = await handleApi(request("auth/lock", "POST", {}, { Cookie: cookie, Origin: "https://evil.invalid" }), "auth/lock", context);
  assert.equal(foreign.status, 403);
  assert.match((await foreign.json()).error, /does not match SPACE_WEB_URL/);
  assert.deepEqual(foreign.headers.getSetCookie(), []);
  const locked = await handleApi(request("auth/lock", "POST", {}, { Cookie: cookie }), "auth/lock", context);
  assert.equal(locked.status, 200);
  assert.deepEqual(await locked.json(), { mode: "key", unlocked: false });
  assert.match(locked.headers.getSetCookie()[0], /^space-session=; Max-Age=0; Path=\/; HttpOnly; Secure; SameSite=Lax$/);
  const foreignUnlock = await handleApi(request("auth/unlock", "POST", { key }, { Origin: "https://evil.invalid" }), "auth/unlock", context);
  assert.equal(foreignUnlock.status, 403);
});

test("private mode checks nothing and sends the API no credential", async () => {
  let calls = 0;
  const context = runtime({ SPACE_ACCESS_MODE: "private", SPACE_ACCESS_KEY: undefined }, async (input, init) => {
    calls++;
    assert.equal(new Headers(init.headers).has("authorization"), false);
    return Response.json({ id: space, name: "Private Space", mcp_url: "" });
  });
  assert.deepEqual(await (await handleApi(request("auth/session"), "auth/session", context)).json(), { mode: "private", unlocked: true });
  const response = await handleApi(request("workspace", "POST", {}), "workspace", context);
  assert.equal(response.status, 200);
  const value = await response.json();
  assert.equal(value.access_mode, "private");
  assert.equal("browser_cache_key" in value, false);
  assert.deepEqual(value.profile, { displayName: "Owner", avatarUrl: null });
  const lock = await handleApi(request("auth/lock", "POST", {}), "auth/lock", context);
  assert.deepEqual(lock.headers.getSetCookie(), []);
  assert.equal(calls, 1);
});

test("a missing or short access key, or an unknown mode, fails closed before any backend call", async () => {
  for (const overrides of [{ SPACE_ACCESS_KEY: undefined }, { SPACE_ACCESS_KEY: "too-short" }, { SPACE_ACCESS_MODE: "open" }]) {
    for (const [path, method] of [["workspaces", "GET"], ["auth/session", "GET"], ["auth/unlock", "POST"]]) {
      const response = await handleApi(request(path, method, method === "POST" ? { key } : undefined), path, runtime(overrides, noBackend));
      assert.equal(response.status, 503, `${path} with ${JSON.stringify(overrides)}`);
      assert.deepEqual(response.headers.getSetCookie(), []);
    }
  }
});

test("an API that refuses this server's credentials is reported as a configuration problem", async t => {
  t.mock.method(console, "warn", () => {});
  const context = runtime({}, async () => Response.json({ error: "Authentication required" }, { status: 401 }));
  const cookie = await unlock(context);
  const response = await handleApi(request("workspaces", "GET", undefined, { Cookie: cookie }), "workspaces", context);
  assert.equal(response.status, 502);
  assert.match((await response.json()).error, /SPACE_ACCESS_MODE and SPACE_ACCESS_KEY/);
});

test("ten wrong keys from one address lock that address out for fifteen minutes", async t => {
  t.mock.method(console, "warn", () => {});
  const context = runtime({}, noBackend);
  for (let attempt = 0; attempt < 10; attempt++) {
    const response = await handleApi(request("auth/unlock", "POST", { key: `wrong-${attempt}` }), "auth/unlock", context);
    assert.equal(response.status, 401);
  }
  // While limited, even the correct key is refused, so guessing cannot continue.
  const limited = await handleApi(request("auth/unlock", "POST", { key }), "auth/unlock", context);
  assert.equal(limited.status, 429);
  assert.match(limited.headers.get("retry-after"), /^\d+$/);
  assert.ok(Number(limited.headers.get("retry-after")) > 800);
  assert.match((await limited.json()).error, /Try again in 15 minutes/);
  assert.deepEqual(limited.headers.getSetCookie(), []);
  // Another address is unaffected.
  await unlock(runtime());
});

test("read-version checks require an unlocked GET and keep private response headers", async () => {
  let backendCalls = 0;
  const path = `spaces/${space}/read-version`;
  const context = runtime({}, async (input, init) => {
    backendCalls++;
    assert.equal(String(input), "http://api:8000/v1/" + path);
    assert.equal(init.method, "GET");
    return Response.json({ version: "1:9007199254740993:owner:private" });
  });
  const cookie = await unlock(context);
  for (const method of ["POST", "DELETE"]) {
    const response = await handleApi(request(path, method, {}, { Cookie: cookie }), path, context);
    assert.equal(response.status, 404);
  }
  const anonymous = await handleApi(request(path), path, context);
  assert.equal(anonymous.status, 401);
  assert.equal(backendCalls, 0);
  const response = await handleApi(request(path, "GET", undefined, { Cookie: cookie }), path, context);
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { version: "1:9007199254740993:owner:private" });
  assert.equal(response.headers.get("cache-control"), "no-store");
  assert.equal(backendCalls, 1);
});

test("abandoning a browser read aborts its upstream request without replay", async t => {
  const controller = new AbortController();
  let backendCalls = 0;
  t.mock.method(console, "warn", () => {});
  const context = runtime({}, async (_input, init) => {
    backendCalls++;
    controller.abort(new DOMException("Browser navigation", "AbortError"));
    assert.equal(init.signal.aborted, true);
    init.signal.throwIfAborted();
  });
  const cookie = await unlock(context);
  const input = new Request(origin + "/api/workspaces", { signal: controller.signal, headers: { Cookie: cookie } });
  await handleApi(input, "workspaces", context);
  assert.equal(backendCalls, 1);
});

test("origin, JSON, method and path checks reject requests before any backend call", async () => {
  const context = runtime({}, noBackend);
  const cookie = await unlock(context);
  const cases = [
    [request("workspace", "POST", {}, { Origin: "https://evil.invalid", Cookie: cookie }), "workspace", 403],
    [request("workspace", "POST", {}, { "Content-Type": "text/plain", Cookie: cookie }), "workspace", 415],
    [request("workspace", "PATCH", {}, { Cookie: cookie }), "workspace", 405],
    [request("auth%2Fsession"), "auth%2Fsession", 404],
    [request("auth/signin", "POST", {}), "auth/signin", 404],
    // An app's OAuth endpoints are the backend's own, outside /v1; the proxy never forwards them.
    [request("oauth/token", "POST", {}, { Cookie: cookie }), "oauth/token", 404],
    [request("workspace/../admin", "GET", undefined, { Cookie: cookie }), "workspace/../admin", 404],
    [request("workspace%3Fadmin=1", "GET", undefined, { Cookie: cookie }), "workspace%3Fadmin=1", 404],
    [request("unknown", "GET", undefined, { Cookie: cookie }), "unknown", 404],
  ];
  for (const [input, path, status] of cases) {
    const response = await handleApi(input, path, context);
    assert.equal(response.status, status, path);
    assert.equal(response.headers.get("cache-control"), "no-store");
  }
});

test("streamed JSON enforces the byte limit without trusting Content-Length", async () => {
  let cancelled = false;
  const body = new ReadableStream({
    start(controller) {
      controller.enqueue(new Uint8Array(800_000));
      controller.enqueue(new Uint8Array(800_000));
    },
    cancel() { cancelled = true; },
  });
  const input = new Request(origin + "/api/workspace", {
    method: "POST", headers: { Origin: origin, "Content-Type": "application/json", "Content-Length": "1" },
    body, duplex: "half",
  });
  await assert.rejects(readBody(input, env), error => error.status === 413);
  assert.equal(cancelled, true);
});
