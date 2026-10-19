import { QueryClient, QueryObserver, isCancelledError } from "@tanstack/query-core";
import type { PersistentReads } from "./persistent-reads";

export type ReadOptions = { fresh?: boolean; allPages?: boolean };
type Page = { items: unknown[]; next_cursor: string | null };
/** `version` changes with any content; each `versions` entry only with its kind. */
export type SpaceVersions = { version: string; versions?: Record<string, string> };
type ReadVersion = (spaceId: string, signal: AbortSignal) => Promise<SpaceVersions>;
// A backstop full read for anything a version could miss. Per-kind versions already catch
// every content write, so this can be rare.
const FULL_RECHECK_MS = 15 * 60_000;
const ITEM_KINDS = ["project", "folder", "database", "category", "section"];
const RESOURCE_KINDS = ["document", "task", "memory"];

/** Only content covered by the Space revision can skip an unchanged response. */
function versionedSpace(path: string) {
  const url = new URL(path, "https://cache.invalid/");
  const match = /^\/spaces\/([^/]+)\/(?:resources(?:\/[^/]+)?|items(?:\/[^/]+)?|memories(?:\/[^/]+)?|tasks\/columns|search\/index)$/.exec(url.pathname);
  if (!match || url.searchParams.has("query")) return undefined;
  return { spaceId: match[1], kinds: contentKinds(url) };
}

function searchIndex(path: string) {
  return /\/search\/index$/.test(new URL(path, "https://cache.invalid/").pathname);
}

/** The kinds of content a read shows; undefined means it depends on every kind. */
function contentKinds(url: URL): string[] | undefined {
  const [, , , collection, id] = url.pathname.split("/");
  const kind = url.searchParams.get("kind");
  if (collection === "resources")
    return !id && (kind === "document" || kind === "task") ? [kind] : RESOURCE_KINDS;
  if (collection === "items") return !id && kind && ITEM_KINDS.includes(kind) ? [kind] : ITEM_KINDS;
  // Memory pages name the categories, projects and sections that classify each claim.
  if (collection === "memories") return ["memory", "category", "project", "section"];
  if (collection === "tasks") return ["columns"];
  return undefined;
}

/** The part of the Space version this read depends on; the whole one when unknown. */
function readVersionFor(versions: SpaceVersions, kinds: string[] | undefined) {
  const parts = kinds?.map(kind => versions.versions?.[kind]);
  return parts?.every(part => typeof part === "string") ? parts.join("|") : versions.version;
}

async function storageTicket(store: PersistentReads, barrier: Promise<void>) {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      barrier.then(() => store.ticket()).catch(() => undefined),
      new Promise<undefined>(resolve => { timer = setTimeout(() => resolve(undefined), 250); }),
    ]);
  } finally { clearTimeout(timer); }
}

/** A bounded, account-scoped cache. Only explicitly selected reads enter it. */
export class ReadCache {
  private account: string | null = null;
  private epoch = 0;
  private persistence?: PersistentReads;
  private persistenceBarrier: Promise<void> = Promise.resolve();
  private subscriptions = new Set<() => void>();
  private versions = new Map<string, { version: string; fetchedAt: number }>();
  private client = new QueryClient({
    defaultOptions: { queries: {
      staleTime: 30_000,
      gcTime: typeof window === "undefined" ? Infinity : 5 * 60_000,
      retry: false,
      refetchOnWindowFocus: true,
      refetchOnReconnect: true,
      refetchInterval: 60_000,
      refetchIntervalInBackground: false,
    } },
  });

  private fetch: (path: string, signal: AbortSignal) => Promise<unknown>;
  private readVersion?: ReadVersion;
  constructor(fetch: (path: string, signal: AbortSignal) => Promise<unknown>, readVersion?: ReadVersion) {
    this.fetch = fetch;
    this.readVersion = readVersion;
    this.client.getQueryCache().subscribe(event => {
      if (event.type === "removed")
        this.versions.delete(JSON.stringify([event.query.queryKey[1], event.query.queryKey[2]]));
    });
  }

  /** Enable only after the backend advertises this read protocol when the workspace opens. */
  setReadVersion(readVersion?: ReadVersion) {
    this.readVersion = readVersion;
    this.versions.clear();
    this.client.removeQueries({ predicate: query => String(query.queryKey[1]).endsWith("/read-version") });
  }

  /** React Query and imperative readers must observe the same account-scoped cache. */
  get queryClient() { return this.client; }

  mount() { this.client.mount(); }
  dispose() { this.client.unmount(); this.setAccount(null); }

  setAccount(account: string | null) {
    if (account === this.account) return;
    this.epoch++;
    this.persistence?.close();
    this.persistence = undefined;
    for (const stop of this.subscriptions) stop();
    this.client.clear();
    this.versions.clear();
    this.account = account;
  }

  async restore(store: PersistentReads) {
    const epoch = this.epoch;
    const entries = await store.restore();
    if (epoch !== this.epoch || !this.account) { store.close(); return; }
    this.persistence?.close();
    this.persistence = store;
    for (const entry of entries) this.client.setQueryData(
      [this.account, entry.path, entry.allPages], entry.data,
      // Restored content is immediately useful, but always revalidated after a reload.
      { updatedAt: Math.min(entry.updatedAt, Date.now() - 30_001) },
    );
  }

  private options<T>(path: string, allPages = false) {
    const account = this.account;
    const queryKey = [account, path, allPages] as const;
    const versionKey = JSON.stringify([path, allPages]);
    return {
      queryKey,
      // The search index is the whole Space's text and costs one full pass to rebuild,
      // so it outlives the default five minutes away from the search views.
      ...(searchIndex(path) ? { gcTime: 60 * 60_000 } : {}),
      queryFn: async ({ signal }: { signal: AbortSignal }): Promise<T> => {
        if (!account || account !== this.account) throw new Error("Account changed");
        const readVersion = this.readVersion;
        const cached = this.client.getQueryState<T>(queryKey);
        // A first read has nothing to compare with, so a version check would only add a
        // round trip before anything can show. The next background refresh reads once in
        // full and records the version; later refreshes can then skip unchanged content.
        const versioned = readVersion && cached?.data !== undefined && versionedSpace(path);
        // Capture the version BEFORE reading content. A concurrent edit will then
        // force another full read next time, even if this response spans the edit.
        const version = versioned ? readVersionFor(await this.client.fetchQuery({
          queryKey: [account, `spaces/${versioned.spaceId}/read-version`, false],
          queryFn: ({ signal }) => readVersion!(versioned.spaceId, signal),
          staleTime: 1000,
        }), versioned.kinds) : undefined;
        signal.throwIfAborted();
        if (account !== this.account) throw new Error("Account changed");
        const previous = this.versions.get(versionKey);
        if (version !== undefined && previous?.version === version &&
          Date.now() - previous.fetchedAt < FULL_RECHECK_MS &&
          cached?.data !== undefined && !cached.isInvalidated) return cached.data;
        const persistence = this.persistence;
        const fence = persistence
          ? await storageTicket(persistence, this.persistenceBarrier)
          : undefined;
        signal.throwIfAborted();
        const complete = (data: unknown) => {
          if (version !== undefined) this.versions.set(versionKey, { version, fetchedAt: Date.now() });
          if (persistence === this.persistence)
            void persistence?.save({ path, allPages, data, updatedAt: Date.now() }, fence).catch(() => {});
          return data as T;
        };
        const result = await this.fetch(path, signal);
        signal.throwIfAborted();
        if (account !== this.account) throw new Error("Account changed");
        if (!allPages) return complete(result);
        const first = result as Page;
        const items = [...first.items];
        let cursor = first.next_cursor;
        const seen = new Set<string>();
        while (cursor) {
          if (seen.has(cursor)) throw new Error("Repeated page cursor. Refresh and retry.");
          seen.add(cursor);
          const url = new URL(path, "https://cache.invalid/");
          url.searchParams.set("after", cursor);
          const page = await this.fetch(url.pathname.slice(1) + url.search, signal) as Page;
          signal.throwIfAborted();
          if (account !== this.account) throw new Error("Account changed");
          items.push(...page.items);
          cursor = page.next_cursor;
        }
        return complete({ ...first, items, next_cursor: null });
      },
    };
  }

  async read<T>(path: string, { fresh = false, allPages = false }: ReadOptions = {}): Promise<T> {
    if (!this.account) throw new Error("Open a space before reading cached data.");
    const options = this.options<T>(path, allPages);
    if (fresh) {
      this.versions.delete(JSON.stringify([path, allPages]));
      await this.client.cancelQueries({ queryKey: options.queryKey, exact: true });
      await this.client.invalidateQueries({ queryKey: options.queryKey, exact: true, refetchType: "none" });
    }
    this.trim();
    const cached = this.client.getQueryState<T>(options.queryKey);
    if (!fresh && cached?.data !== undefined && !cached.isInvalidated) {
      void this.client.fetchQuery(options).catch(() => {});
      return cached.data;
    }
    return this.client.fetchQuery(options);
  }

  /** Observers render cached values immediately and refresh stale values in the background. */
  watch<T>(path: string, onValue: (value: T) => void, onError: (error: unknown) => void, allPages = false) {
    if (!this.account) return () => {};
    this.trim();
    const observer = new QueryObserver<T>(this.client, this.options<T>(path, allPages));
    const account = this.account;
    let previous: T | undefined;
    let previousError: unknown;
    const emit = (result: ReturnType<typeof observer.getCurrentResult>) => {
      if (account !== this.account) return;
      if (result.data !== undefined && (result.data !== previous || (result.isSuccess && previousError))) {
        previous = result.data;
        onValue(result.data);
      }
      if (result.isSuccess) previousError = undefined;
      if (result.error && result.error !== previousError && !isCancelledError(result.error)) {
        previousError = result.error;
        onError(result.error);
      }
    };
    const unsubscribe = observer.subscribe(emit);
    const stop = () => { unsubscribe(); this.subscriptions.delete(stop); };
    this.subscriptions.add(stop);
    emit(observer.getCurrentResult());
    return stop;
  }

  /** Cancel old reads before marking entries stale, so late results cannot undo a write. */
  invalidate(matches: (path: string) => boolean, refetch = true) {
    for (const key of this.versions.keys())
      if (matches((JSON.parse(key) as [string, boolean])[0])) this.versions.delete(key);
    const persistence = this.persistence;
    // Fence disk writes before starting replacement reads. Only subsequent successful
    // network results repopulate disk; stale in-memory entries are never reserialized.
    this.persistenceBarrier = this.persistenceBarrier.then(() => persistence?.invalidate()).catch(() => {});
    const predicate = (query: { queryKey: readonly unknown[] }) => matches(String(query.queryKey[1]));
    void this.client.cancelQueries({ predicate });
    void this.client.invalidateQueries({ predicate, refetchType: refetch ? "active" : "none" });
    return this.persistenceBarrier;
  }

  refetchStale() {
    void this.client.refetchQueries({ predicate: query => query.isActive() && query.isStaleByTime(30_000) });
  }

  watchStatus(onStatus: (status: "idle" | "syncing" | "stale") => void) {
    let previous = "";
    const emit = () => {
      const queries = this.client.getQueryCache().findAll({ type: "active" })
        .filter(query => query.state.data !== undefined);
      const status = queries.some(query => query.state.fetchStatus === "fetching") ? "syncing"
        : queries.some(query => query.state.status === "error" || query.state.fetchStatus === "paused") ? "stale" : "idle";
      if (status !== previous) { previous = status; onStatus(status); }
    };
    const stop = this.client.getQueryCache().subscribe(emit);
    emit();
    return stop;
  }

  private trim() {
    // Bound inactive document/search entries as well as their five-minute lifetime.
    const inactive = this.client.getQueryCache().findAll({ type: "inactive" })
      .sort((a, b) => a.state.dataUpdatedAt - b.state.dataUpdatedAt);
    for (const query of inactive.slice(0, Math.max(0, inactive.length - 99))) {
      this.client.removeQueries({ queryKey: query.queryKey, exact: true });
    }
  }
}

/** Queue reads across components, including paginated reads and background refreshes. */
export class ReadScheduler {
  private active = 0;
  private queue: { start: () => void; priority: number }[] = [];

  private limit: number;
  constructor(limit = 2) { this.limit = limit; }

  run<T>(operation: () => Promise<T>, signal: AbortSignal, priority = 0): Promise<T> {
    return new Promise<T>((resolve, reject) => {
      const cancel = () => {
        this.queue = this.queue.filter(entry => entry.start !== start);
        reject(signal.reason);
      };
      const start = () => {
        signal.removeEventListener("abort", cancel);
        if (signal.aborted) { reject(signal.reason); this.next(); return; }
        this.active++;
        void Promise.resolve().then(operation).then(resolve, reject).finally(() => {
          this.active--;
          this.next();
        });
      };
      if (signal.aborted) { reject(signal.reason); return; }
      signal.addEventListener("abort", cancel, { once: true });
      if (this.active < this.limit) start();
      else {
        this.queue.push({ start, priority });
        // A selected document must not wait behind queued catalog refreshes.
        // Stable sorting retains FIFO order among reads of the same priority.
        this.queue.sort((a, b) => b.priority - a.priority);
      }
    });
  }

  private next() {
    if (this.active < this.limit) this.queue.shift()?.start();
  }
}
