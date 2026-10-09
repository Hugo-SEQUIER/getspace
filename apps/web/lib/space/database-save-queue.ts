import type { WorkspaceDatabase } from "./databases";

type Change = (current: WorkspaceDatabase) => WorkspaceDatabase;

export type DatabaseSaveRecovery = {
  /** True when the save never reached the Space or its outcome is unknown. */
  transient(error: unknown): boolean;
  /** True when the Space rejected the save because its revision is no longer current. */
  stale(error: unknown): boolean;
  retryDelayMs?: number;
};

/**
 * Parameters: optional recovery rules for interrupted and outdated saves.
 * What it does: serializes database changes and applies them to the latest saved revision.
 * An interrupted save is resent unchanged once, which its command ID makes idempotent;
 * an outdated save is reapplied once to the revision read by `latest` instead of failing every
 * later edit.
 * Output: an enqueue function whose promise reports each save independently.
 */
export function createDatabaseSaveQueue(recovery?: DatabaseSaveRecovery) {
  const tails = new Map<string, Promise<unknown>>();
  const saved = new Map<string, WorkspaceDatabase>();
  return function enqueue(
    base: WorkspaceDatabase,
    change: Change,
    persist: (database: WorkspaceDatabase) => Promise<WorkspaceDatabase>,
    latest?: (id: string) => Promise<WorkspaceDatabase>,
  ): Promise<WorkspaceDatabase> {
    async function save(current: WorkspaceDatabase) {
      const next = change(current);
      let failure: unknown;
      try { return await persist(next); } catch (error) { failure = error; }
      if (recovery?.transient(failure)) {
        await new Promise(resolve => setTimeout(resolve, recovery.retryDelayMs ?? 1000));
        try { return await persist(next); } catch (error) { failure = error; }
      }
      if (!latest || !recovery?.stale(failure)) throw failure;
      return await persist(change(await latest(base.id)));
    }
    const previous = tails.get(base.id) ?? Promise.resolve();
    const result = previous.catch(() => {}).then(async () => {
      const known = saved.get(base.id);
      const current = known && (known.revision ?? 0) >= (base.revision ?? 0)
        ? known
        : base;
      const next = await save(current);
      saved.set(base.id, next);
      return next;
    });
    tails.set(base.id, result);
    return result;
  };
}
