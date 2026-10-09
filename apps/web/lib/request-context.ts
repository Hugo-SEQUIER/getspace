export type ServerEnvironment = Record<string, string | undefined>;
export type CookieOptions = {
  path?: string;
  domain?: string;
  maxAge?: number;
  expires?: Date;
  httpOnly?: boolean;
  secure?: boolean;
  sameSite?: "lax" | "strict" | "none";
};
export type CookieUpdate = { name: string; value: string; options: CookieOptions };
export type RequestRuntime = {
  /** An injected environment is authoritative; never merge it with host secrets. */
  env?: ServerEnvironment;
  /** Stands in for fetch on backend calls, as tests do; the server never sets it. */
  backendFetch?: typeof fetch;
  /** The connecting peer's address, which the Node server supplies for unlock rate limits. */
  clientAddress?: string;
};

const COOKIE_NAME = /^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$/;

/** Parameters: a Cookie request header. Output: each cookie's decoded value, first one wins. */
export function parseCookies(header: string) {
  const values = new Map<string, string>();
  for (const part of header.split(";")) {
    const separator = part.indexOf("=");
    if (separator < 1) continue;
    const name = part.slice(0, separator).trim();
    if (!COOKIE_NAME.test(name) || values.has(name)) continue;
    let value = part.slice(separator + 1).trim();
    if (value.length >= 2 && value.startsWith('"') && value.endsWith('"')) value = value.slice(1, -1);
    try { value = decodeURIComponent(value); } catch { /* A malformed escape keeps its raw text. */ }
    values.set(name, value);
  }
  return values;
}

/** Parameters: a cookie and its attributes. Output: one Set-Cookie header value. */
export function serializeCookie(name: string, value: string, options: CookieOptions = {}) {
  if (!COOKIE_NAME.test(name)) throw new TypeError(`Invalid cookie name: ${name}`);
  const parts = [`${name}=${encodeURIComponent(value)}`];
  if (options.maxAge !== undefined) parts.push(`Max-Age=${Math.floor(options.maxAge)}`);
  if (options.expires) parts.push(`Expires=${options.expires.toUTCString()}`);
  if (options.domain) parts.push(`Domain=${options.domain}`);
  if (options.path) parts.push(`Path=${options.path}`);
  if (options.httpOnly) parts.push("HttpOnly");
  if (options.secure) parts.push("Secure");
  if (options.sameSite) parts.push(`SameSite=${options.sameSite[0].toUpperCase()}${options.sameSite.slice(1)}`);
  return parts.join("; ");
}

/** A cookie jar belongs to one request, including every cookie it sets. */
export class RequestCookies {
  private values: Map<string, string>;
  private updates = new Map<string, CookieUpdate>();

  constructor(header: string | null) {
    this.values = parseCookies(header ?? "");
  }

  get(name: string) {
    const value = this.values.get(name);
    return value === undefined ? undefined : { name, value };
  }

  getAll() {
    return [...this.values].map(([name, value]) => ({ name, value }));
  }

  set(name: string, value: string, options: CookieOptions = {}) {
    // Cookies default to the whole site, as the previous server cookie API did.
    const normalized = { path: "/", ...options };
    serializeCookie(name, value, normalized);
    this.values.set(name, value);
    this.updates.set(JSON.stringify([name, normalized.path, normalized.domain]), {
      name, value, options: normalized,
    });
  }

  apply(response: Response) {
    const headers = new Headers(response.headers);
    for (const { name, value, options } of this.updates.values())
      headers.append("Set-Cookie", serializeCookie(name, value, options));
    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers,
    });
  }
}

export type RequestContext = {
  request: Request;
  url: URL;
  cookies: RequestCookies;
  env: ServerEnvironment;
  backendFetch?: typeof fetch;
  clientAddress?: string;
};

export function requestContext(request: Request, runtime: RequestRuntime = {}): RequestContext {
  return {
    request,
    url: new URL(request.url),
    cookies: new RequestCookies(request.headers.get("Cookie")),
    env: runtime.env ?? process.env,
    backendFetch: runtime.backendFetch,
    clientAddress: runtime.clientAddress,
  };
}
