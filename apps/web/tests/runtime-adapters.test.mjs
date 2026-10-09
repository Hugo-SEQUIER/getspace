import assert from "node:assert/strict";
import { mkdtemp, mkdir, writeFile, symlink, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { setTimeout } from "node:timers/promises";
import test from "node:test";
import { createStaticHandler, loadLocalEnvironment, serverOptions, staticHeaders } from "../scripts/serve.mjs";
import { currentRequestRuntime, withRequestRuntime } from "../lib/runtime-storage.server.ts";

test("request environments and backend transports stay isolated across concurrent requests", async () => {
  const first = { env: { SPACE_ENVIRONMENT: "first" }, backendFetch: async () => new Response("first") };
  const second = { env: { SPACE_ENVIRONMENT: "second" }, backendFetch: async () => new Response("second") };
  await Promise.all([first, second].map((runtime, index) => withRequestRuntime(runtime, async () => {
    await setTimeout(index ? 1 : 8);
    assert.equal(currentRequestRuntime(), runtime);
    assert.equal(await (await currentRequestRuntime().backendFetch("https://fixture.invalid")).text(), runtime.env.SPACE_ENVIRONMENT);
  })));
  assert.deepEqual(currentRequestRuntime(), {});
  assert.throws(() => withRequestRuntime(first, () => { throw new Error("fixture"); }), /fixture/);
  assert.deepEqual(currentRequestRuntime(), {});
});

test("local environment loading preserves injected values, including empty values", async () => {
  const directory = await mkdtemp(join(tmpdir(), "space-env-fixture-"));
  try {
    await writeFile(join(directory, ".env.local"), 'SPACE_TEST="local value"\nSPACE_INJECTED=local\nSPACE_EMPTY=local\n');
    const env = { SPACE_INJECTED: "injected", SPACE_EMPTY: "" };
    await loadLocalEnvironment(directory, env);
    assert.deepEqual(env, { SPACE_TEST: "local value", SPACE_INJECTED: "injected", SPACE_EMPTY: "" });
    await loadLocalEnvironment(join(directory, "absent"), env);
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test("static serving provides MIME, HEAD, caching, and refuses traversal, symlinks, and private route collisions", async () => {
  const directory = await mkdtemp(join(tmpdir(), "space-assets-fixture-"));
  const root = join(directory, "client");
  try {
    await mkdir(join(root, "assets"), { recursive: true });
    await mkdir(join(root, "api"), { recursive: true });
    await writeFile(join(root, "assets", "chunk-HASH.js"), "export default 1;");
    await writeFile(join(root, "api", "session"), "must not serve");
    await writeFile(join(root, ".env.local"), "must not serve");
    await writeFile(join(directory, "outside.txt"), "must not serve");
    await symlink(join(directory, "outside.txt"), join(root, "linked.txt"));
    const handler = await createStaticHandler(root);
    const asset = await handler(new Request("http://localhost/assets/chunk-HASH.js"));
    assert.equal(asset.headers.get("Content-Type"), "text/javascript; charset=utf-8");
    assert.match(asset.headers.get("Cache-Control"), /immutable/);
    assert.equal(asset.headers.get("X-Content-Type-Options"), "nosniff");
    assert.equal(await asset.text(), "export default 1;");
    const head = await handler(new Request("http://localhost/assets/chunk-HASH.js", { method: "HEAD" }));
    assert.equal(await head.text(), "");
    assert.equal(head.headers.get("Content-Length"), "17");
    for (const pathname of ["/api/session", "/linked.txt", "/missing", "/assets/"])
      assert.equal(await handler(new Request("http://localhost" + pathname)), null);
    for (const pathname of ["/.env.local", "/%2e%2e%2foutside.txt", "/assets/%5coutside.txt"])
      assert.equal((await handler(new Request("http://localhost" + pathname))).status, 404);
    assert.equal((await handler(new Request("http://localhost/%zz"))).status, 400);
    assert.equal(await handler(new Request("http://localhost/assets/chunk-HASH.js", { method: "POST" })), null);
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test("the server listens on this machine unless HOST and PORT, or flags, say otherwise", () => {
  assert.deepEqual(serverOptions([], {}), { hostname: "127.0.0.1", port: 3000 });
  assert.deepEqual(serverOptions([], { HOST: "0.0.0.0", PORT: "8080" }), { hostname: "0.0.0.0", port: 8080 });
  assert.deepEqual(serverOptions(["--host", "::", "-p", "4000"], { HOST: "0.0.0.0", PORT: "8080" }), { hostname: "::", port: 4000 });
  for (const [args, env] of [[[], { PORT: "web" }], [[], { PORT: "70000" }], [["--port"], {}], [["--verbose"], {}]])
    assert.throws(() => serverOptions(args, env));
});

test("static files are private: never indexed, framed or sniffed", () => {
  const headers = staticHeaders("/integrations/codex.svg", "image/svg+xml");
  assert.equal(headers.get("X-Robots-Tag"), "noindex, nofollow");
  assert.equal(headers.get("X-Frame-Options"), "DENY");
  assert.equal(headers.get("X-Content-Type-Options"), "nosniff");
  assert.equal(headers.get("Cache-Control"), "public, max-age=3600");
  assert.match(staticHeaders("/assets/app-HASH.js", "text/javascript").get("Cache-Control"), /immutable/);
});
