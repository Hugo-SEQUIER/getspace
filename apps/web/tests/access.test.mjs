import assert from "node:assert/strict";
import test from "node:test";
import {
  accessKey,
  accessMode,
  browserCacheKey,
  clientAddress,
  createUnlockLimiter,
  issueSession,
  keyMatches,
  ownerName,
  SESSION_SECONDS,
  verifySession,
} from "../lib/access.ts";

// Synthetic keys only; neither is a credential of any installation.
const key = "fixture-access-key-0123456789-abcdefghij";
const rotated = "rotated-access-key-0123456789-abcdefghij";
const now = Date.parse("2026-10-09T12:00:00Z");

test("the access mode defaults to key, accepts private, and fails closed on anything else", () => {
  assert.equal(accessMode({}), "key");
  assert.equal(accessMode({ SPACE_ACCESS_MODE: " Key " }), "key");
  assert.equal(accessMode({ SPACE_ACCESS_MODE: "private" }), "private");
  for (const value of ["public", "none", "off", "keys"])
    assert.throws(() => accessMode({ SPACE_ACCESS_MODE: value }), error => error.status === 503);
});

test("key mode needs an access key of at least 32 characters", () => {
  assert.equal(accessKey({ SPACE_ACCESS_KEY: key }), key);
  for (const value of [undefined, "", "short", " ".repeat(40), "x".repeat(31)])
    assert.throws(() => accessKey({ SPACE_ACCESS_KEY: value }), error => error.status === 503);
});

test("a submitted key must match exactly, apart from surrounding spaces", () => {
  assert.equal(keyMatches(key, key), true);
  assert.equal(keyMatches(`  ${key}\n`, key), true);
  for (const value of ["", key.slice(0, -1), `${key}x`, key.toUpperCase(), rotated])
    assert.equal(keyMatches(value, key), false);
});

test("sessions are signed with a key derived from the access key and expire after 30 days", () => {
  const { value, session } = issueSession(key, now);
  assert.equal(session.expires, now / 1000 + SESSION_SECONDS);
  assert.ok(!value.includes(key) && !value.includes(encodeURIComponent(key)));
  assert.deepEqual(verifySession(value, key, now), session);
  assert.deepEqual(verifySession(value, key, now + (SESSION_SECONDS - 1) * 1000), session);
  assert.equal(verifySession(value, key, now + SESSION_SECONDS * 1000), null);
  // Rotating the access key logs every browser out.
  assert.equal(verifySession(value, rotated, now), null);
  const [version, id, expires, mac] = value.split(".");
  const other = issueSession(key, now).value.split(".")[1];
  for (const altered of [
    [version, other, expires, mac], [version, id, String(Number(expires) + 86400), mac],
    [version, id, expires, mac.replace(/^./, c => c === "A" ? "B" : "A")], ["v2", id, expires, mac],
  ]) assert.equal(verifySession(altered.join("."), key, now), null);
  for (const malformed of [undefined, "", "v1", `${value}.extra`, key, "v1.x.1.y"])
    assert.equal(verifySession(malformed, key, now), null);
  assert.notEqual(issueSession(key, now).value, value);
});

test("the browser cache key is 256 bits, per session and per access key", () => {
  const first = issueSession(key, now).session, second = issueSession(key, now).session;
  const value = browserCacheKey(key, first);
  assert.equal(Buffer.from(value, "base64").length, 32);
  assert.equal(browserCacheKey(key, first), value);
  assert.notEqual(browserCacheKey(key, second), value);
  assert.notEqual(browserCacheKey(rotated, first), value);
});

test("forwarded addresses count only behind a nearby proxy", () => {
  const request = (forwarded) => new Request("http://space.test/api/auth/unlock", {
    headers: forwarded ? { "X-Forwarded-For": forwarded } : {},
  });
  // A client that connects directly cannot choose the address it is counted under.
  assert.equal(clientAddress(request("10.0.0.9"), "203.0.113.7"), "203.0.113.7");
  // A reverse proxy on the same host or network appends the client it saw last.
  assert.equal(clientAddress(request("198.51.100.1, 203.0.113.8"), "127.0.0.1"), "203.0.113.8");
  assert.equal(clientAddress(request("203.0.113.9"), "::ffff:172.18.0.2"), "203.0.113.9");
  assert.equal(clientAddress(request("2001:db8::1"), "fd00::2"), "2001:db8::1");
  assert.equal(clientAddress(request("not an address"), "192.168.1.4"), "192.168.1.4");
  assert.equal(clientAddress(request(), "::ffff:203.0.113.10"), "203.0.113.10");
  assert.equal(clientAddress(request("203.0.113.11"), undefined), "unknown");
});

test("failed unlocks are limited per address within a sliding window", () => {
  const limiter = createUnlockLimiter({ window: 60_000, max: 3, capacity: 2 });
  for (let attempt = 0; attempt < 3; attempt++) {
    assert.equal(limiter.retryAfter("a", now + attempt), 0);
    limiter.fail("a", now + attempt * 1000);
  }
  assert.equal(limiter.retryAfter("a", now + 2000), 58);
  assert.equal(limiter.retryAfter("b", now + 2000), 0);
  // The oldest failure leaves the window first.
  assert.equal(limiter.retryAfter("a", now + 60_000), 0);
  limiter.fail("a", now + 60_000);
  assert.ok(limiter.retryAfter("a", now + 60_001) > 0);
  limiter.clear("a");
  assert.equal(limiter.retryAfter("a", now + 60_001), 0);
  // Memory stays bounded: the least recently failing address is forgotten first.
  for (const client of ["x", "y", "z"]) for (let i = 0; i < 3; i++) limiter.fail(client, now + 70_000 + i);
  assert.equal(limiter.retryAfter("x", now + 70_010), 0);
  assert.ok(limiter.retryAfter("z", now + 70_010) > 0);
});

test("the owner's display name comes from SPACE_OWNER_NAME, with a neutral default", () => {
  assert.equal(ownerName({}), "Owner");
  assert.equal(ownerName({ SPACE_OWNER_NAME: "   " }), "Owner");
  assert.equal(ownerName({ SPACE_OWNER_NAME: "  Ada   Lovelace " }), "Ada Lovelace");
  assert.equal(ownerName({ SPACE_OWNER_NAME: "x".repeat(200) }).length, 80);
});
