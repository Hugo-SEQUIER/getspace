import assert from "node:assert/strict";
import test from "node:test";
import { apiBaseUrl, apiKeyExpired, apiKeyPath, apiKeyProxyAllowed } from "../lib/space/api-keys.ts";

const space = "11111111-1111-4111-8111-111111111111";
const keyId = "22222222-2222-4222-8222-222222222222";
const path = apiKeyPath(space);

test("proxy allows listing and creating keys, and revoking one key by ID", () => {
  for (const [route, method] of [[path, "GET"], [path, "POST"], [`${path}/${keyId}`, "DELETE"]])
    assert.equal(apiKeyProxyAllowed(route, method), true, `${method} ${route}`);
  for (const [route, method] of [
    [path, "DELETE"], [`${path}/${keyId}`, "GET"], [`${path}/${keyId}`, "POST"],
    [path, "PATCH"], [path, "PUT"], [`${path}/`, "GET"], [`${path}/not-a-uuid`, "DELETE"],
    [`${path}/${keyId}/token`, "GET"], [`${path}/${keyId}/extra`, "DELETE"],
    ["api-keys", "GET"], [`spaces/not-a-uuid/api-keys`, "GET"],
    [`spaces/${space}/resources/${keyId}/api-keys`, "GET"],
  ]) assert.equal(apiKeyProxyAllowed(route, method), false, `${method} ${route}`);
});

test("the API base URL is the origin of the Space's MCP endpoint", () => {
  assert.equal(apiBaseUrl("https://api.example.invalid/mcp/"), "https://api.example.invalid");
  assert.equal(apiBaseUrl("http://127.0.0.1:8000/mcp/"), "http://127.0.0.1:8000");
  for (const value of ["", undefined, "not a URL"]) assert.equal(apiBaseUrl(value), null);
});

test("a key with a past expiry is expired; a key without one never expires", () => {
  const key = { id: keyId, name: "Dashboard", prefix: "spk_AbCd1234", can_write: false, created_at: "2026-10-01T09:00:00Z", last_used_at: null };
  const now = Date.parse("2026-10-08T09:00:00Z");
  assert.equal(apiKeyExpired({ ...key, expires_at: null }, now), false);
  assert.equal(apiKeyExpired({ ...key, expires_at: "2026-10-09T09:00:00Z" }, now), false);
  assert.equal(apiKeyExpired({ ...key, expires_at: "2026-10-08T08:59:59Z" }, now), true);
});
