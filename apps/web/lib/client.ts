import { scopedAgentPath, selectedSpaceKey } from "./space/teams";
import { ReadCache, ReadScheduler, type ReadOptions, type SpaceVersions } from "./read-cache";
import { isTimeout, READ_TIMEOUT_MS, timeoutMessage } from "./request-timeouts";
import { PersistentReads, erasePersistentReads } from "./persistent-reads";
import { affectedReads } from "./read-invalidation";
import { ApiError, sendBrowserRequest } from "./browser-http";

export { ApiError } from "./browser-http";

// Six reads in flight: the workspace opens with about a dozen independent lists, and
// HTTP/2 multiplexes them on one connection. Selected documents still go first.
const reads = new ReadScheduler(6);
let readCache: ReadCache | undefined;
let cacheAccount: string | null = null;
let cacheEpoch = 0;
let channel: BroadcastChannel | undefined;
let cacheRestore: AbortController | undefined;
let diskCleanup: Promise<void> = Promise.resolve();
let readCacheEventsMounted = false;
export const ACCESS_RESET_EVENT = "space-access-reset";

function browserReadCache() {
  return readCache ??= new ReadCache((path, signal) => api(path, "GET", undefined, signal));
}

async function readVersion(spaceId: string, signal: AbortSignal): Promise<SpaceVersions> {
  const result = await api<SpaceVersions>(`spaces/${spaceId}/read-version`, "GET", undefined, signal);
  if (typeof result.version !== "string" || !result.version)
    throw new Error("Could not check for changes. Try again.");
  // Older backends send only the Space-wide version; every read then compares that.
  const versions = result.versions && typeof result.versions === "object" ? result.versions : undefined;
  return { version: result.version, versions };
}

export function getReadQueryClient() {
  // Never share authenticated query state between server render requests.
  if (typeof window === "undefined")
    return new ReadCache((path, signal) => api(path, "GET", undefined, signal)).queryClient;
  return browserReadCache().queryClient;
}

async function optionalStorage(work: Promise<unknown>) {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    await Promise.race([work, new Promise<void>(resolve => { timer = setTimeout(resolve, 1000); })]);
  } finally { clearTimeout(timer); }
}

function cacheChannel() {
  if (!channel && typeof window !== "undefined" && typeof BroadcastChannel !== "undefined") {
    channel = new BroadcastChannel("space-cache-events");
    channel.onmessage = ({ data }) => {
      if (data === "access-reset" || (data?.type === "account" && cacheAccount && data.id !== cacheAccount))
        resetReadCache(401);
      if (data === "content-changed") readCache?.invalidate(() => true);
    };
  }
  return channel;
}

function scopedPath(path: string) {
  if (typeof window !== "undefined" && window.location.pathname.startsWith("/app")) {
    try { return scopedAgentPath(path, sessionStorage.getItem(selectedSpaceKey)); } catch {}
  }
  return path;
}

export function resetReadCache(status = 0, error = "", broadcast = false, preserveDisk = false) {
  cacheEpoch++;
  cacheRestore?.abort();
  cacheAccount = null;
  readCache?.setAccount(null);
  if (!preserveDisk && typeof indexedDB !== "undefined")
    diskCleanup = diskCleanup.then(() => erasePersistentReads()).catch(() => {});
  if (typeof window !== "undefined")
    window.dispatchEvent(new CustomEvent(ACCESS_RESET_EVENT, { detail: { status, error } }));
  if (broadcast) cacheChannel()?.postMessage("access-reset");
}

export async function configureReadCache(userId: string, encryptionKey?: string, spaces: string[] = [], readVersionSupported = false) {
  if (typeof window === "undefined") return;
  const cache = browserReadCache();
  if (!readCacheEventsMounted) {
    readCacheEventsMounted = true;
    cache.mount();
    window.addEventListener("focus", () => {
      if (document.visibilityState === "visible") readCache?.refetchStale();
    });
  }
  cacheAccount = userId;
  cache.setAccount(userId);
  cache.setReadVersion(readVersionSupported ? readVersion : undefined);
  // Another tab may unlock or lock this browser; it learns which account is open here.
  cacheChannel()?.postMessage({ type: "account", id: userId });
  if (encryptionKey && typeof indexedDB !== "undefined" && crypto.subtle) {
    const epoch = cacheEpoch;
    const controller = new AbortController();
    cacheRestore = controller;
    // Storage unavailability must never strand an otherwise usable online session.
    const timer = setTimeout(() => controller.abort(), 1000);
    let store: PersistentReads | undefined;
    const restore = async () => {
      await diskCleanup;
      store = await PersistentReads.open(userId, encryptionKey, spaces, controller.signal);
      if (epoch !== cacheEpoch || controller.signal.aborted) { store.close(); return; }
      await readCache!.restore(store);
    };
    await Promise.race([
      restore().catch(() => { store?.close(); }),
      new Promise<void>(resolve => controller.signal.addEventListener("abort", () => {
        store?.close(); resolve();
      }, { once: true })),
    ]);
    clearTimeout(timer);
  }
}

export function cachedApi<T>(path: string, options?: ReadOptions): Promise<T> {
  if (!readCache) return api<T>(path);
  return readCache.read<T>(scopedPath(path), options);
}

export function watchApi<T>(path: string, onValue: (value: T) => void, onError: (error: unknown) => void, allPages = false) {
  return readCache?.watch(scopedPath(path), onValue, onError, allPages) ?? (() => {});
}

export function watchReadStatus(onStatus: (status: "idle" | "syncing" | "stale") => void) {
  return readCache?.watchStatus(onStatus) ?? (() => {});
}

export function invalidateReads(prefix: string) {
  readCache?.invalidate(path => path.startsWith(prefix));
}

export async function api<T>(
  path: string,
  method = "GET",
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  path = scopedPath(path);
  const mutation = method !== "GET" && !/\/(search|context|export|preview)$/.test(path);
  const auth = path.startsWith("auth/");
  const affected = affectedReads(path);
  if (mutation && auth) { resetReadCache(0, "", true); await optionalStorage(diskCleanup); }
  else if (mutation && path !== "workspace" && readCache)
    await optionalStorage(readCache.invalidate(affected, false));
  const epoch = cacheEpoch;
  // Keep the existing longer model/write timeout; bound queued and active reads.
  const requestSignal = method === "GET"
    ? AbortSignal.any([AbortSignal.timeout(READ_TIMEOUT_MS), ...(signal ? [signal] : [])])
    : signal;
  const send = async () => {
    if (!auth && epoch !== cacheEpoch) throw new DOMException("Account changed", "AbortError");
    try {
      const value = await sendBrowserRequest<T>({ path, method, body, signal: requestSignal });
      if (!auth && epoch !== cacheEpoch) throw new DOMException("Account changed", "AbortError");
      return value;
    } catch (error) {
      if (!auth && epoch !== cacheEpoch) throw new DOMException("Account changed", "AbortError");
      if (!auth && error instanceof ApiError && (error.status === 401 || error.status === 403 ||
        (error.status === 404 && /^spaces\/[^/]+\/read-version$/.test(path))))
        resetReadCache(error.status, error.message || "Access is no longer available.", true);
      throw error;
    }
  };
  try {
    const selectedResource = /^spaces\/[^/]+\/resources\/[^/?]+(?:\?|$)/.test(path);
    return requestSignal && method === "GET"
      ? await reads.run(send, requestSignal, selectedResource ? 1 : 0)
      : await send();
  } catch (error) {
    if (isTimeout(error)) throw new ApiError(timeoutMessage(method), 504);
    throw error;
  } finally {
    if (mutation && !auth && path !== "workspace" && epoch === cacheEpoch) {
      // Also refresh after uncertain failures and revision conflicts.
      readCache?.invalidate(affected);
      channel?.postMessage("content-changed");
    }
  }
}

export function message(error: unknown) {
  return error instanceof Error
    ? error.message
    : "Something went wrong. Try again.";
}
