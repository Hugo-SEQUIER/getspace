import { createStartHandler, defaultStreamHandler } from "@tanstack/react-start/server";
import type { RequestRuntime } from "../lib/request-context";
import { withRequestRuntime } from "../lib/runtime-storage.server";

const handle = createStartHandler(defaultStreamHandler);
type RequestOptions = NonNullable<Parameters<typeof handle>[1]> & { runtime?: RequestRuntime };

export default {
  async fetch(request: Request, options: RequestOptions = {}): Promise<Response> {
    const { runtime = {}, ...startOptions } = options;
    const response = await withRequestRuntime(runtime, () => handle(request, startOptions));
    const headers = new Headers(response.headers);
    const pathname = new URL(request.url).pathname;
    headers.set("X-Content-Type-Options", "nosniff");
    // A connection code may sit in the /connect URL; it never leaves in a Referer header.
    headers.set("Referrer-Policy", /^\/connect(?:\/|$)/.test(pathname) ? "no-referrer" : "same-origin");
    headers.set("X-Frame-Options", "DENY");
    headers.set("Permissions-Policy", "camera=(), microphone=(), geolocation=()");
    // Every page is private: dynamic responses can contain session state, never enter
    // shared HTTP caches and are never indexed.
    headers.set("Cache-Control", "no-store");
    headers.set("X-Robots-Tag", "noindex, nofollow");
    return new Response(response.body, { status: response.status, statusText: response.statusText, headers });
  },
};
