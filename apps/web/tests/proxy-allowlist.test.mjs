import assert from "node:assert/strict";
import { test } from "node:test";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { PROXY_LOCAL, proxyAllowed, proxyPath } from "../lib/space/proxy-allowlist.ts";

const root = new URL("..", import.meta.url).pathname;
const UUID = "11111111-1111-4111-8111-111111111111";

function* sources(directory) {
  for (const entry of readdirSync(directory)) {
    const path = join(directory, entry);
    if (statSync(path).isDirectory()) { if (entry !== "node_modules") yield* sources(path); }
    else if (/\.(ts|tsx)$/.test(entry) && !/\.(test|e2e)\./.test(entry)) yield path;
  }
}

// Parameters: a template literal or string literal taken from a client call.
// What it does: turns every interpolation into a UUID and drops the query string, which
// is how the proxy sees the request.
// Output: the path the proxy would match, or null when the literal cannot be resolved
// statically (it starts with an interpolated helper call).
function resolve(literal) {
  const body = literal.slice(1, -1);
  if (body.startsWith("${")) return null;
  const resolved = body.replace(/\$\{[^}]*\}/g, UUID);
  // A nested template (an optional query suffix) ends the static part of the path.
  const nested = resolved.indexOf("${");
  return proxyPath(nested >= 0 ? resolved.slice(0, nested) : resolved);
}

// Every literal backend path the browser passes to the API client, by file.
function clientPaths() {
  const found = new Map();
  for (const file of [...sources(join(root, "app")), ...sources(join(root, "lib")), ...sources(join(root, "src"))]) {
    const text = readFileSync(file, "utf8");
    for (const match of text.matchAll(/\b(?:api|cachedApi|watchApi|command)(?:<[^>()]*>)?\(\s*(`[^`]*`|"[^"]*")/g)) {
      const path = resolve(match[1]);
      if (path !== null) found.set(path, relative(root, file));
    }
  }
  return found;
}

test("every literal backend path the web client calls is forwarded by the website proxy", () => {
  const paths = clientPaths();
  assert.ok(paths.size >= 30, `expected to find the client's backend calls, found ${paths.size}`);
  assert.ok(paths.has(`spaces/${UUID}/views`), "the seen-mark route must be among the calls");
  const missing = [...paths].filter(([path]) =>
    !PROXY_LOCAL.test(path) && !["GET", "POST", "DELETE"].some(method => proxyAllowed(path, method)));
  assert.deepEqual(missing, [], "paths the browser calls but the proxy answers with 404");
});

test("the allowlist admits what the app needs and refuses what it does not", () => {
  const space = `spaces/${UUID}`;
  for (const [method, path] of [
    ["GET", "workspace"], ["GET", "workspaces"], ["GET", `${space}/read-version`],
    ["GET", `${space}/resources?kind=task`], ["POST", `${space}/resources`], ["DELETE", `${space}/resources/${UUID}`],
    ["GET", `${space}/views`], ["POST", `${space}/views`],
    ["POST", `${space}/tasks/${UUID}/status`], ["GET", `${space}/tasks/columns`],
    ["GET", `${space}/api-keys`], ["DELETE", `${space}/api-keys/${UUID}`],
    ["GET", `${space}/webhooks`], ["POST", `${space}/webhooks/${UUID}`],
    ["GET", `agents?workspace_id=${UUID}`], ["POST", `agents/${UUID}/identity`],
    ["GET", `oauth/details?authorization_id=${UUID}`], ["POST", "oauth/consent"],
  ]) assert.ok(proxyAllowed(proxyPath(path), method), `${method} ${path}`);
  for (const [method, path] of [
    ["POST", `${space}/read-version`], ["GET", `${space}/unknown`], ["GET", "admin"],
    ["GET", `${space}/api-keys/${UUID}`], ["GET", "openapi.json"],
    // An app's own OAuth endpoints live outside /v1 and never pass through the website.
    ["POST", "oauth/token"], ["POST", "oauth/register"], ["GET", "oauth/authorize"],
  ]) assert.ok(!proxyAllowed(proxyPath(path), method), `${method} ${path} must be refused`);
});
