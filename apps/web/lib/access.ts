import { createHash, createHmac, randomBytes, timingSafeEqual } from "node:crypto";
import { isIP } from "node:net";
import type { CookieOptions, ServerEnvironment } from "./request-context.ts";
import { RequestError, webOrigin } from "./server.ts";

// A self-hosted Space has one implicit owner and no accounts. In key mode the owner unlocks a
// browser with the installation's access key; in private mode nothing is checked, because the
// operator keeps the Space on localhost or a VPN. The key never leaves this server: the browser
// holds a signed session, and the API receives the key from this server alone.
export type AccessMode = "key" | "private";
export type Session = { id: string; expires: number };

export const SESSION_COOKIE = "space-session";
export const SESSION_SECONDS = 30 * 24 * 60 * 60;
export const MIN_ACCESS_KEY_LENGTH = 32;
/** The stable account identity of the owner, used to scope browser storage. */
export const OWNER_ID = "owner";

/** Parameters: the server environment. Output: the access mode; unset means key, anything unknown fails closed. */
export function accessMode(env: ServerEnvironment): AccessMode {
  const value = (env.SPACE_ACCESS_MODE ?? "").trim().toLowerCase();
  if (!value || value === "key") return "key";
  if (value === "private") return "private";
  throw new RequestError("This Space is not configured correctly: SPACE_ACCESS_MODE must be key or private.", 503);
}

/** Parameters: the server environment. Output: the access key that key mode requires. */
export function accessKey(env: ServerEnvironment): string {
  const key = env.SPACE_ACCESS_KEY ?? "";
  if (key.trim().length < MIN_ACCESS_KEY_LENGTH)
    throw new RequestError("This Space is not configured correctly: SPACE_ACCESS_KEY must be at least 32 characters.", 503);
  return key;
}

/** The owner's display name; it never grants anything. */
export function ownerName(env: ServerEnvironment) {
  return (env.SPACE_OWNER_NAME ?? "").trim().replace(/\s+/g, " ").slice(0, 80) || "Owner";
}

// Each use of the access key gets its own derived key, so a session signature or a cache key
// reveals nothing about another, and rotating the access key invalidates all of them.
const derived = (key: string, purpose: string) => createHmac("sha256", key).update(purpose).digest();

/** Compares a submitted key with the configured one in constant time, ignoring surrounding spaces. */
export function keyMatches(submitted: string, key: string) {
  const digest = (value: string) => createHash("sha256").update(value.trim()).digest();
  return timingSafeEqual(digest(submitted), digest(key));
}

function signature(key: string, id: string, expires: number) {
  return createHmac("sha256", derived(key, "space.web-session.v1")).update(`v1.${id}.${expires}`).digest("base64url");
}

/** Parameters: the access key. Output: a new session and its cookie value: a random ID, its expiry and their signature. */
export function issueSession(key: string, now = Date.now()) {
  const session: Session = { id: randomBytes(18).toString("base64url"), expires: Math.floor(now / 1000) + SESSION_SECONDS };
  return { session, value: `v1.${session.id}.${session.expires}.${signature(key, session.id, session.expires)}` };
}

/** Parameters: a cookie value and the access key. Output: its session, or null when unsigned, altered or expired. */
export function verifySession(value: string | undefined, key: string, now = Date.now()): Session | null {
  const match = value ? /^v1\.([A-Za-z0-9_-]{24})\.(\d{1,12})\.([A-Za-z0-9_-]{43})$/.exec(value) : null;
  if (!match) return null;
  const [, id, expiresText, mac] = match;
  const expires = Number(expiresText);
  if (expires <= Math.floor(now / 1000)) return null;
  const expected = Buffer.from(signature(key, id, expires));
  const actual = Buffer.from(mac);
  return expected.length === actual.length && timingSafeEqual(expected, actual) ? { id, expires } : null;
}

export function sessionCookieOptions(env: ServerEnvironment, maxAge = SESSION_SECONDS): CookieOptions {
  return { httpOnly: true, sameSite: "lax", secure: webOrigin(env).startsWith("https:"), path: "/", maxAge };
}

/** A per-session key for the encrypted browser read cache. A new session or a new access key changes it. */
export function browserCacheKey(key: string, session: Session) {
  return createHmac("sha256", derived(key, "space.browser-cache.v2")).update(session.id).digest("base64");
}

// Parameters: a peer address. Output: true for loopback, private and link-local addresses, where a
// reverse proxy on the same host or network forwards requests.
function nearbyHop(address: string) {
  if (isIP(address) === 4) {
    const [a, b] = address.split(".").map(Number);
    return a === 127 || a === 10 || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168) || (a === 169 && b === 254);
  }
  return address === "::1" || /^f[cd][0-9a-f]{2}:/.test(address) || /^fe[89ab][0-9a-f]:/.test(address);
}

function normalizedAddress(value: string | null | undefined) {
  let address = value?.trim().toLowerCase() ?? "";
  if (address.startsWith("::ffff:") && isIP(address.slice(7)) === 4) address = address.slice(7);
  return isIP(address) ? address : null;
}

// Parameters: the request and the TCP peer address.
// What it does: trusts the last X-Forwarded-For hop only when the peer is a nearby proxy, as a
// reverse proxy in front of this server is; a direct client cannot choose its address that way.
// Output: the address unlock attempts are counted against.
export function clientAddress(request: Request, peer: string | undefined) {
  const socket = normalizedAddress(peer);
  if (socket && nearbyHop(socket)) {
    const forwarded = request.headers.get("x-forwarded-for")?.split(",").map(part => part.trim()).filter(Boolean).at(-1);
    const hop = normalizedAddress(forwarded);
    if (hop) return hop;
  }
  return socket ?? "unknown";
}

export const UNLOCK_WINDOW_MS = 15 * 60_000;
export const UNLOCK_MAX_FAILURES = 10;

// Parameters: the window, the failures allowed in it, and how many addresses to remember.
// What it does: counts failed unlocks per address in memory; a restart forgets them.
// Output: retryAfter (seconds until the next attempt may be made, 0 when allowed), fail and clear.
export function createUnlockLimiter({ window = UNLOCK_WINDOW_MS, max = UNLOCK_MAX_FAILURES, capacity = 10_000 } = {}) {
  const failures = new Map<string, number[]>();
  function recent(client: string, now: number) {
    const times = (failures.get(client) ?? []).filter(time => now - time < window);
    if (times.length) failures.set(client, times);
    else failures.delete(client);
    return times;
  }
  return {
    retryAfter(client: string, now = Date.now()) {
      const times = recent(client, now);
      return times.length >= max ? Math.max(1, Math.ceil((times[0] + window - now) / 1000)) : 0;
    },
    fail(client: string, now = Date.now()) {
      const times = [...recent(client, now), now].slice(-max);
      failures.delete(client);
      failures.set(client, times);
      // The least recently failing addresses are forgotten first.
      while (failures.size > capacity) failures.delete(failures.keys().next().value!);
    },
    clear(client: string) {
      failures.delete(client);
    },
  };
}

export const unlockLimiter = createUnlockLimiter();
