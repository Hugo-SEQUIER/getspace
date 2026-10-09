const notificationKinds = ["error", "success", "info", "loading"] as const;

export type Notification = { id: string; kind: (typeof notificationKinds)[number]; message: string };

/** Validates an untrusted event, trims its message and drops unknown keys; null when invalid.
 * Hand-written because this module ships on every public page, where a schema library
 * would dominate the eager bundle. */
export function parseNotification(input: unknown): Notification | null {
  if (typeof input !== "object" || input === null) return null;
  const { id, kind, message } = input as Record<string, unknown>;
  if (typeof id !== "string" || id.length < 1 || id.length > 200) return null;
  if (!notificationKinds.includes(kind as Notification["kind"])) return null;
  if (typeof message !== "string") return null;
  const text = message.trim();
  if (text.length < 1 || text.length > 8000) return null;
  return { id, kind: kind as Notification["kind"], message: text };
}
export type NotificationInput = Omit<Notification, "id"> & { id?: string };
export const notificationDuration = 6500;

export type NotificationClock = {
  setTimeout: (callback: () => void, delayMs: number) => unknown;
  clearTimeout: (handle: unknown) => void;
};

const defaultClock: NotificationClock = {
  setTimeout: (callback, delayMs) => globalThis.setTimeout(callback, delayMs),
  clearTimeout: (handle) => globalThis.clearTimeout(handle as ReturnType<typeof setTimeout>),
};

/** Each provider owns a store, so server requests never share notification state. */
export function createNotificationStore({ clock = defaultClock, maxVisible = 6 }: { clock?: NotificationClock; maxVisible?: number } = {}) {
  let snapshot: readonly Notification[] = [];
  const listeners = new Set<() => void>();
  const timers = new Map<string, unknown>();
  const dismissCallbacks = new Map<string, () => void>();
  const capacity = Number.isFinite(maxVisible) ? Math.max(1, Math.floor(maxVisible)) : 6;

  function publish(next: readonly Notification[]) {
    snapshot = next;
    listeners.forEach((listener) => listener());
  }

  function clearTimer(id: string) {
    if (timers.has(id)) clock.clearTimeout(timers.get(id));
    timers.delete(id);
  }

  function remove(id: string) {
    clearTimer(id);
    dismissCallbacks.delete(id);
    if (snapshot.some((event) => event.id === id)) publish(snapshot.filter((event) => event.id !== id));
  }

  function dismiss(id: string) {
    const callback = dismissCallbacks.get(id);
    remove(id);
    callback?.();
  }

  function notify(input: unknown, onDismiss?: () => void): string | null {
    const event = parseNotification(input);
    if (!event) return null;
    const previous = snapshot.find((candidate) => candidate.id === event.id);
    if (onDismiss) dismissCallbacks.set(event.id, onDismiss);
    else dismissCallbacks.delete(event.id);
    if (previous?.kind === event.kind && previous.message === event.message) return event.id;

    clearTimer(event.id);
    const next = snapshot.filter((candidate) => candidate.id !== event.id);
    next.push(event);
    while (next.length > capacity) {
      const evicted = next.shift()!;
      clearTimer(evicted.id);
      dismissCallbacks.delete(evicted.id);
    }
    publish(next);
    if (event.kind === "success" || event.kind === "info") {
      timers.set(event.id, clock.setTimeout(() => dismiss(event.id), notificationDuration));
    }
    return event.id;
  }

  function clear() {
    timers.forEach((handle) => clock.clearTimeout(handle));
    timers.clear();
    dismissCallbacks.clear();
    publish([]);
  }

  return {
    getSnapshot: () => snapshot,
    subscribe(listener: () => void) {
      listeners.add(listener);
      return () => { listeners.delete(listener); };
    },
    notify,
    dismiss,
    remove,
    clear,
    dispose() {
      clear();
      listeners.clear();
    },
  };
}

export type NotificationStore = ReturnType<typeof createNotificationStore>;
