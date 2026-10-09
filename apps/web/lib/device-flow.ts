// The person's side of a connection request. Two kinds reach the website:
// - A device code: an agent shows six digits, the owner types them at /connect, reviews the
//   request and decides. Nothing redirects; the agent collects the decision itself.
// - A browser sign-in (OAuth): an app such as claude.ai sends the owner to
//   /connect/authorize. After the decision the browser returns to the app's callback.

/** What POST /v1/device/claim and GET /v1/oauth/details return for a pending request. */
export type AuthorizationDetails = {
  authorization_id: string;
  client: { id: string; name: string; uri?: string | null };
  // Null for a device-code request, which never returns to a callback.
  redirect_uri: string | null;
  scope: string;
  harness?: string;
};

/** The decision shown on /connect/done after the owner approves or declines. */
export type ConsentResult = {
  action: "approve" | "deny";
  clientName: string;
  canWrite: boolean | null;
  agent?: { id: string; name: string; harness: string } | null;
  // Where the browser returns to an app that signed in; null for a device code.
  redirectUrl?: string | null;
};

export const DEVICE_CODE_LENGTH = 6;

// The six digits an agent shows, typed with or without spacing. Null until complete.
export function deviceCodeInput(value: string): string | null {
  const digits = value.replace(/\D/g, "");
  return digits.length === DEVICE_CODE_LENGTH ? digits : null;
}

// "483920" reads as "483 920" while typing; never more than six digits.
export function formatDeviceCode(value: string): string {
  const digits = value.replace(/\D/g, "").slice(0, DEVICE_CODE_LENGTH);
  return digits.length > 3 ? `${digits.slice(0, 3)} ${digits.slice(3)}` : digits;
}

/** The pending sign-in in /connect/authorize?authorization_id=…, or null without a valid one. */
export function authorizationId(search: string): string | null {
  const id = new URLSearchParams(search).get("authorization_id");
  return id && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id)
    ? id.toLowerCase()
    : null;
}

const LOOPBACK_HOSTS = ["127.0.0.1", "localhost", "[::1]"];
// Script-bearing and browser-internal schemes, refused as the backend refuses them when an
// app registers. Any other scheme opens an app on this computer, such as cursor://.
const UNSAFE_SCHEMES = new Set([
  "about:", "blob:", "data:", "file:", "filesystem:", "ftp:", "intent:", "javascript:",
  "mailto:", "sms:", "tel:", "vbscript:", "view-source:", "ws:", "wss:",
]);

// Parameters: a callback URL from the backend's consent answer.
// What it does: admits HTTPS, plain HTTP on a loopback address, or an app's own scheme,
// with no credentials in the URL. The code and state in the query are preserved.
// Output: the URL the browser may open; throws for anything else.
export function agentCallback(value: string): string {
  const target = new URL(value);
  const loopback = LOOPBACK_HOSTS.includes(target.hostname);
  const web = target.protocol === "https:" || target.protocol === "http:";
  const allowed = target.protocol === "https:"
    || (target.protocol === "http:" && loopback)
    || (!web && !UNSAFE_SCHEMES.has(target.protocol));
  if (target.username || target.password || !allowed)
    throw new Error("Unsupported callback address. Start the connection again from your app.");
  return target.href;
}

// A loopback callback reaches only the computer the app runs on. agentCallback admits plain
// HTTP for loopback hosts alone, so HTTP here means loopback.
export function loopbackCallback(value: string): boolean {
  try {
    return new URL(agentCallback(value)).protocol === "http:";
  } catch {
    return false;
  }
}

/** Where the browser returns, as the person reads it: a host, or an app's scheme. */
export function callbackHost(value: string): string {
  try {
    const target = new URL(agentCallback(value));
    return target.host || target.protocol.replace(/:$/, "");
  } catch {
    return "your app";
  }
}
