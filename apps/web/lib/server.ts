import type { ServerEnvironment } from "./request-context.ts";

export class RequestError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

// Parameters: an operator-supplied service URL and the variable it came from.
// What it does: accepts an http or https URL, so an internal address such as
// http://api:8000 works, but refuses credentials, queries and fragments.
// Output: the parsed URL; a misconfiguration fails closed with 503.
function serviceUrl(value: string, variable: string) {
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    throw new RequestError(`${variable} is not a valid URL.`, 503);
  }
  if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.search || url.hash)
    throw new RequestError(`${variable} must be an http or https URL without credentials, a query or a fragment.`, 503);
  return url;
}

// Compose passes every optional setting through, empty when .env leaves it blank, so a blank
// setting means unset, as it does for the API.
function setting(value: string | undefined) {
  return value?.trim() || undefined;
}

/** The exact public origin people open; it guards writes and decides the cookie Secure flag. */
export function webOrigin(env: ServerEnvironment = process.env) {
  return serviceUrl(setting(env.SPACE_WEB_URL) ?? "http://127.0.0.1:3000", "SPACE_WEB_URL").origin;
}

/** The API this server forwards /api calls to, which may be an internal plain-HTTP address. */
export function apiOrigin(env: ServerEnvironment = process.env) {
  return serviceUrl(
    setting(env.SPACE_API_URL) ?? `http://127.0.0.1:${setting(env.BACKEND_PORT) ?? "8000"}`,
    "SPACE_API_URL",
  ).origin;
}

export function json(value: unknown, status = 200) {
  return Response.json(value, {
    status,
    headers: { "Cache-Control": "no-store" },
  });
}

export async function readBody(request: Request, env: ServerEnvironment = process.env) {
  if (request.headers.get("origin") !== webOrigin(env)) {
    // Usually the page was opened at another address than the configured one, such as
    // localhost instead of 127.0.0.1, so the message names the setting to check.
    throw new RequestError(
      "This page's address does not match SPACE_WEB_URL. Open the Space at the address it is configured for, or set SPACE_WEB_URL to this one.",
      403,
    );
  }
  if (!request.headers.get("content-type")?.startsWith("application/json")) {
    throw new RequestError("JSON is required", 415);
  }
  const reader = request.body?.getReader();
  if (!reader) return {};
  let size = 0;
  const chunks: Uint8Array[] = [];
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > 1500000) {
      await reader.cancel();
      throw new RequestError("Request is too large", 413);
    }
    chunks.push(value);
  }
  try {
    return JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } catch {
    throw new RequestError("Invalid JSON", 400);
  }
}
