import {
  accessKey,
  accessMode,
  browserCacheKey,
  clientAddress,
  issueSession,
  keyMatches,
  OWNER_ID,
  ownerName,
  SESSION_COOKIE,
  sessionCookieOptions,
  unlockLimiter,
  verifySession,
  type Session,
} from "../../../lib/access.ts";
import { backendFailureCode, backendResponseJson, fetchBackend } from "../../../lib/backend-fetch.ts";
import { serverTiming } from "../../../lib/server-timing.ts";
import { backendTimeoutMs, isTimeout, timeoutMessage } from "../../../lib/request-timeouts.ts";
import { proxyAllowed } from "../../../lib/space/proxy-allowlist.ts";
import { requestContext, type RequestContext, type RequestRuntime } from "../../../lib/request-context.ts";
import { apiOrigin, json, readBody, RequestError } from "../../../lib/server.ts";

export async function handleApi(request: Request, path: string, runtime?: RequestRuntime) {
  const context = requestContext(request, runtime);
  return context.cookies.apply(await handle(request, path, context));
}

// Parameters: the request context and the access key.
// What it does: reads this browser's signed session, and clears a cookie that no longer
// verifies (expired, or signed with a previous access key) so it stops being sent.
// Output: the session, or null when the browser is locked.
function currentSession(context: RequestContext, key: string) {
  const value = context.cookies.get(SESSION_COOKIE)?.value;
  const session = verifySession(value, key);
  if (value && !session) context.cookies.set(SESSION_COOKIE, "", sessionCookieOptions(context.env, 0));
  return session;
}

function minutes(seconds: number) {
  const count = Math.max(1, Math.ceil(seconds / 60));
  return `${count} minute${count === 1 ? "" : "s"}`;
}

// The proxy answers auth/* itself: the session state, unlocking with the access key, and locking.
function access(path: string, method: string, body: { key?: unknown } | undefined, context: RequestContext) {
  const mode = accessMode(context.env);
  if (path === "auth/session" && method === "GET")
    return json({ mode, unlocked: mode === "private" || !!currentSession(context, accessKey(context.env)) });
  if (path === "auth/unlock" && method === "POST") {
    if (mode === "private") return json({ mode, unlocked: true });
    const key = accessKey(context.env);
    const client = clientAddress(context.request, context.clientAddress);
    const wait = unlockLimiter.retryAfter(client);
    if (wait) {
      console.warn("space_unlock_limited", { client });
      const response = json({ error: `Too many incorrect keys. Try again in ${minutes(wait)}.` }, 429);
      response.headers.set("Retry-After", String(wait));
      return response;
    }
    const submitted = body?.key;
    if (typeof submitted !== "string" || !submitted.trim() || submitted.length > 1024)
      throw new RequestError("Enter the access key.", 400);
    if (!keyMatches(submitted, key)) {
      unlockLimiter.fail(client);
      console.warn("space_unlock_refused", { client });
      throw new RequestError("That access key is not correct.", 401);
    }
    unlockLimiter.clear(client);
    context.cookies.set(SESSION_COOKIE, issueSession(key).value, sessionCookieOptions(context.env));
    return json({ mode, unlocked: true });
  }
  if (path === "auth/lock" && method === "POST") {
    if (mode === "private") return json({ mode, unlocked: true });
    context.cookies.set(SESSION_COOKIE, "", sessionCookieOptions(context.env, 0));
    return json({ mode, unlocked: false });
  }
  throw new RequestError("Not found", 404);
}

async function handle(
  request: Request,
  path: string,
  context: RequestContext,
) {
  let authDuration = 0;
  let authStarted: number | undefined;
  let backendStarted: number | undefined;
  let backendTimings = "";
  try {
    if (!["GET", "POST", "DELETE"].includes(request.method)) {
      const response = json({ error: "Method not allowed" }, 405);
      response.headers.set("Allow", "GET, POST, DELETE");
      return response;
    }
    const body = request.method === "GET" ? undefined : await readBody(request, context.env);
    if (path.startsWith("auth/")) return access(path, request.method, body, context);
    // Every backend path the browser calls must be on this list; see lib/space/proxy-allowlist.ts.
    if (!proxyAllowed(path, request.method)) throw new RequestError("Not found", 404);
    authStarted = performance.now();
    const mode = accessMode(context.env);
    let key: string | undefined;
    let session: Session | null = null;
    if (mode === "key") {
      key = accessKey(context.env);
      session = currentSession(context, key);
      if (!session) throw new RequestError("This Space is locked. Enter the access key to continue.", 401);
    }
    authDuration = performance.now() - authStarted;
    backendStarted = performance.now();
    const response = await fetchBackend(
      new URL(`/v1/${path}${context.url.search}`, apiOrigin(context.env)),
      {
        method: request.method,
        // In private mode the API checks nothing, so no credential is sent.
        headers: {
          ...(key ? { Authorization: `Bearer ${key}` } : {}),
          "Content-Type": "application/json",
        },
        body: body === undefined ? undefined : JSON.stringify(body),
        cache: "no-store",
        signal: AbortSignal.any([request.signal, AbortSignal.timeout(backendTimeoutMs(path, request.method))]),
      },
      context.backendFetch,
    );
    backendTimings = response.headers.get("Server-Timing") ?? "";
    // This browser is already unlocked, so a refusal means the two services disagree.
    if (response.status === 401) {
      await response.body?.cancel();
      console.warn("space_backend_unauthorized", { mode });
      throw new RequestError(
        "The API refused this server's access. Set the same SPACE_ACCESS_MODE and SPACE_ACCESS_KEY for the web and API services.",
        502,
      );
    }
    const value = await backendResponseJson(response);
    const backendDuration = performance.now() - backendStarted;
    if (request.method === "GET" && authDuration + backendDuration >= 2000) {
      console.info("space_slow_read", {
        resource: /^spaces\/[^/]+\/resources\/[^/]+$/.test(path),
        status: response.status,
        timing: serverTiming(authDuration, backendDuration, backendTimings),
      });
    }
    const timedJson = (body: unknown, status = response.status) => {
      const result = json(body, status);
      const retryAfter = response.headers.get("Retry-After");
      if (retryAfter !== null) result.headers.set("Retry-After", retryAfter);
      result.headers.set(
        "Server-Timing",
        serverTiming(authDuration, backendDuration, backendTimings),
      );
      return result;
    };
    if (path === "workspace" && request.method === "POST" && response.ok) {
      // The owner has no account: the identity and display name come from this server's
      // configuration, never from the API response.
      return timedJson({
        ...value,
        user: { id: OWNER_ID, email: null },
        profile: { displayName: ownerName(context.env), avatarUrl: null },
        browser_cache_key: key && session ? browserCacheKey(key, session) : undefined,
        access_mode: mode,
      });
    }
    return timedJson(value);
  } catch (error) {
    if (!(error instanceof RequestError) || error.status >= 500) {
      console.warn("space_proxy_failure", {
        stage: backendStarted !== undefined ? "backend" : authStarted !== undefined ? "auth" : "request",
        code: backendFailureCode(error),
      });
    }
    const result = isTimeout(error)
      ? json({ error: timeoutMessage(request.method) }, 504)
      : error instanceof RequestError
      ? json({ error: error.message }, error.status)
      : json(
          {
            error:
              "Connection unavailable. Check that the backend is running and retry.",
          },
          503,
        );
    if (authStarted !== undefined) {
      result.headers.set(
        "Server-Timing",
        serverTiming(
          backendStarted === undefined ? performance.now() - authStarted : authDuration,
          backendStarted === undefined ? undefined : performance.now() - backendStarted,
          backendTimings,
        ),
      );
    }
    return result;
  }
}
