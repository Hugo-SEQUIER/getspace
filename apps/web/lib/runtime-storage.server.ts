import { AsyncLocalStorage } from "node:async_hooks";
import type { RequestRuntime } from "./request-context";

// Each request carries its own runtime (an injected environment, the peer address) to the
// route handlers. Never copy it into process.env, or retain it in a module-level client.
const runtimes = new AsyncLocalStorage<RequestRuntime>();

export function currentRequestRuntime(): RequestRuntime {
  return runtimes.getStore() ?? {};
}

export function withRequestRuntime<T>(runtime: RequestRuntime, callback: () => T): T {
  return runtimes.run(runtime, callback);
}
