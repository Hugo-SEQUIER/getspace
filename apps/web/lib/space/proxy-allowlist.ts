import { agentTokenProxyAllowed } from "./agent-tokens.ts";
import { apiKeyProxyAllowed } from "./api-keys.ts";
import { webhookProxyAllowed } from "./webhooks.ts";

// Parameters: a backend path (without the /v1/ prefix) and the HTTP method.
// What it does: decides whether the website's /api proxy forwards the request to the
// backend. Every path the browser calls must be admitted here, or the website
// answers 404 while the backend would have answered; the unit test over the client
// source and the e2e fixtures both check this predicate.
// Output: true when the proxy forwards the path.
export const PROXY_ALLOWED =
  /^(workspace|workspaces|organizations(?:\/[a-f0-9-]{36}\/(?:spaces|members(?:\/[a-f0-9-]{36})?|invitations(?:\/[a-f0-9-]{36})?))?|invitations\/accept|agent-identities(?:\/[a-f0-9-]{36})?|agents(?:\/[a-f0-9-]{36}(?:\/identity)?)?|device\/(?:claim|consent)|oauth\/(?:details|consent)|spaces\/[a-f0-9-]{36}\/(?:access|usage|overview|restores(?:\/[a-f0-9-]{36}(?:\/(?:review|commit|parts\/\d+))?)?|export|search(?:\/index)?|items(?:\/[a-f0-9-]{36}(?:\/visibility)?)?|resources(?:\/[a-f0-9-]{36})?|tasks\/(?:columns|[a-f0-9-]{36}\/status)|handoffs|views|context|memories(?:\/[a-f0-9-]{36})?|memory\/(?:provider|build|remember|claims|change|imports(?:\/(?:preview|[a-f0-9-]{36}\/retry))?|jobs(?:\/[a-f0-9-]{36})?)))$/;

export const READ_VERSION = /^spaces\/[a-f0-9-]{36}\/read-version$/;

/** Paths the proxy answers itself, before the backend allowlist applies. */
export const PROXY_LOCAL = /^auth\//;

export function proxyAllowed(path: string, method: string) {
  return PROXY_ALLOWED.test(path)
    || (method === "GET" && READ_VERSION.test(path))
    || webhookProxyAllowed(path, method)
    || apiKeyProxyAllowed(path, method)
    || agentTokenProxyAllowed(path, method);
}

/** Strips the query string, which the proxy forwards untouched. */
export function proxyPath(pathWithQuery: string) {
  return pathWithQuery.split("?")[0];
}
