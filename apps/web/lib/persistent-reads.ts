import { openDB, type DBSchema, type IDBPDatabase } from "idb";

export const READ_CACHE_DB = "space-encrypted-reads-v1";
export const READ_CACHE_MAX_AGE = 24 * 60 * 60_000;
const MAX_BYTES = 10 * 1024 * 1024;
const MAX_ENTRIES = 100;
type Entry = { path: string; allPages: boolean; data: unknown; updatedAt: number };
type Fence = { namespace: string; epoch: string };
type Stored = { id: string; iv: Uint8Array<ArrayBuffer>; ciphertext: ArrayBuffer; updatedAt: number };
interface Schema extends DBSchema {
  meta: { key: string; value: Fence };
  reads: { key: string; value: Stored };
}

async function database() {
  const db = await openDB<Schema>(READ_CACHE_DB, 1, {
    upgrade(db) { db.createObjectStore("meta"); db.createObjectStore("reads", { keyPath: "id" }); },
    blocking() { db.close(); },
  });
  return db;
}

const digest = async (value: string) => Array.from(new Uint8Array(
  await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value)),
), byte => byte.toString(16).padStart(2, "0")).join("");

/**
 * Only successfully authorized content reads are eligible; credentials, jobs and search
 * queries are excluded. The search index is Space content like a document list, and
 * keeping it lets search answer right after a reload instead of re-reading every body.
 */
export function persistableRead(path: string, spaces: ReadonlySet<string>) {
  const match = /^spaces\/([a-f0-9-]{36})\/(resources(?:\/[a-f0-9-]{36})?|items|memories|search\/index)(?:\?|$)/i.exec(path);
  if (!match || !spaces.has(match[1])) return false;
  const url = new URL(path, "https://cache.invalid");
  return [...url.searchParams.keys()].every(key => ["kind", "deleted", "limit", "after", "revision"].includes(key));
}

/** Encrypted, disposable read replicas. Keys exist only in memory after online session verification. */
export class PersistentReads {
  private live = true;
  private db: IDBPDatabase<Schema>;
  private key: CryptoKey;
  private namespace: string;
  private spaces: ReadonlySet<string>;
  private constructor(
    db: IDBPDatabase<Schema>, key: CryptoKey, namespace: string, spaces: ReadonlySet<string>,
  ) { this.db = db; this.key = key; this.namespace = namespace; this.spaces = spaces; }

  static async open(account: string, encodedKey: string, spaces: string[], signal: AbortSignal) {
    const raw = Uint8Array.from(atob(encodedKey), c => c.charCodeAt(0));
    const key = await crypto.subtle.importKey("raw", raw, "AES-GCM", false, ["encrypt", "decrypt"]);
    const namespace = await digest(JSON.stringify([account, encodedKey]));
    signal.throwIfAborted();
    const db = await database();
    try {
      signal.throwIfAborted();
      const tx = db.transaction(["meta", "reads"], "readwrite");
      const previous = await tx.objectStore("meta").get("current");
      if (signal.aborted) { tx.abort(); await tx.done.catch(() => {}); signal.throwIfAborted(); }
      if (previous?.namespace !== namespace) {
        await tx.objectStore("reads").clear();
        await tx.objectStore("meta").put({ namespace, epoch: crypto.randomUUID() }, "current");
      }
      await tx.done;
      signal.throwIfAborted();
      return new PersistentReads(db, key, namespace, new Set(spaces));
    } catch (error) { db.close(); throw error; }
  }

  close() { this.live = false; this.db.close(); }

  async ticket(): Promise<Fence | undefined> {
    if (!this.live) return;
    const fence = await this.db.get("meta", "current");
    return fence?.namespace === this.namespace ? fence : undefined;
  }

  async restore(): Promise<Entry[]> {
    const fence = await this.ticket();
    if (!fence) return [];
    const rows = await this.db.getAll("reads");
    const entries: Entry[] = [];
    for (const row of rows) {
      if (row.updatedAt < Date.now() - READ_CACHE_MAX_AGE || row.updatedAt > Date.now()) continue;
      try {
        const data = await crypto.subtle.decrypt({ name: "AES-GCM", iv: row.iv,
          additionalData: new TextEncoder().encode(JSON.stringify([fence.namespace, fence.epoch, row.id])),
        }, this.key, row.ciphertext);
        const entry = JSON.parse(new TextDecoder().decode(data)) as Entry;
        if (entry.updatedAt === row.updatedAt && typeof entry.allPages === "boolean"
          && persistableRead(entry.path, this.spaces)) entries.push(entry);
      } catch { /* Corrupted, obsolete or wrong-session entries are cache misses. */ }
    }
    const current = await this.ticket();
    return this.live && current?.epoch === fence.epoch ? entries : [];
  }

  async save(entry: Entry, fence?: Fence) {
    if (!this.live || !fence || !persistableRead(entry.path, this.spaces)) return;
    const plaintext = new TextEncoder().encode(JSON.stringify(entry));
    if (plaintext.byteLength > MAX_BYTES) return;
    const id = await digest(JSON.stringify([entry.path, entry.allPages]));
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const ciphertext = await crypto.subtle.encrypt({ name: "AES-GCM", iv,
      additionalData: new TextEncoder().encode(JSON.stringify([fence.namespace, fence.epoch, id])),
    }, this.key, plaintext);
    if (!this.live) return;
    const tx = this.db.transaction(["meta", "reads"], "readwrite");
    const current = await tx.objectStore("meta").get("current");
    if (this.live && current?.namespace === fence.namespace && current.epoch === fence.epoch) {
      const store = tx.objectStore("reads");
      await store.put({ id, iv, ciphertext, updatedAt: entry.updatedAt });
      const rows = (await store.getAll()).sort((a, b) => b.updatedAt - a.updatedAt);
      let bytes = 0;
      for (const [index, row] of rows.entries()) {
        bytes += row.ciphertext.byteLength;
        if (index >= MAX_ENTRIES || bytes > MAX_BYTES || row.updatedAt < Date.now() - READ_CACHE_MAX_AGE)
          await store.delete(row.id);
      }
    }
    await tx.done;
  }

  /** Rotate the fence atomically so reads already in flight cannot resurrect discarded data. */
  async invalidate() {
    if (!this.live) return;
    const tx = this.db.transaction(["meta", "reads"], "readwrite");
    const current = await tx.objectStore("meta").get("current");
    if (current?.namespace === this.namespace) {
      await tx.objectStore("reads").clear();
      await tx.objectStore("meta").put({ namespace: this.namespace, epoch: crypto.randomUUID() }, "current");
    }
    await tx.done;
  }
}

/** Logout also fences writes from other tabs, even if their broadcast listener has not run yet. */
export async function erasePersistentReads() {
  const db = await database();
  try {
    const tx = db.transaction(["meta", "reads"], "readwrite");
    await tx.objectStore("reads").clear();
    await tx.objectStore("meta").put({ namespace: "", epoch: crypto.randomUUID() }, "current");
    await tx.done;
  } finally { db.close(); }
}
