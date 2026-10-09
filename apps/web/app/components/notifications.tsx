import { createContext, useCallback, useContext, useEffect, useId, useRef, useState, useSyncExternalStore, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { createNotificationStore, type Notification, type NotificationInput, type NotificationStore } from "@web/lib/notifications";
import { Icon } from "./space/ui-icon";
import "./notifications.css";

const NotificationContext = createContext<NotificationStore | null>(null);
const emptySnapshot: readonly Notification[] = [];

export function NotificationsProvider({ children }: { children: ReactNode }) {
  const [store] = useState(createNotificationStore);
  useEffect(() => {
    // Keep public builds independent of the authenticated API client.
    const reset = () => store.clear();
    window.addEventListener("space-access-reset", reset);
    return () => {
      window.removeEventListener("space-access-reset", reset);
      store.dispose();
    };
  }, [store]);
  return <NotificationContext.Provider value={store}>{children}<NotificationsHost /></NotificationContext.Provider>;
}

function useNotificationStore() {
  const store = useContext(NotificationContext);
  if (!store) throw new Error("Notifications must be used within NotificationsProvider.");
  return store;
}

/** Emit feedback directly when an action closes its component immediately. */
export function useNotifications() {
  const store = useNotificationStore();
  const prefix = useId();
  const sequence = useRef(0);
  const notify = useCallback((input: NotificationInput, onDismiss?: () => void) => {
    return store.notify({ ...input, id: input.id ?? `${prefix}-event-${++sequence.current}` }, onDismiss);
  }, [prefix, store]);
  return { notify, dismiss: store.dismiss, remove: store.remove };
}

export type Feedback = {
  error?: string | null;
  notice?: string | null;
  loading?: string | null;
  clearError?: () => void;
  clearNotice?: () => void;
};

/** Bridge component state to the shared host without changing action semantics. */
export function useFeedback({ error, notice, loading, clearError, clearNotice }: Feedback) {
  const store = useNotificationStore();
  const prefix = useId();
  const callbacks = useRef({ clearError, clearNotice });
  const mounted = useRef(false);
  const pendingLoading = useRef<number | undefined>(undefined);
  useEffect(() => { callbacks.current = { clearError, clearNotice }; });
  useEffect(() => {
    mounted.current = true;
    const cancelLoading = () => {
      window.clearTimeout(pendingLoading.current);
      store.remove(`${prefix}-loading`);
    };
    window.addEventListener("space-access-reset", cancelLoading);
    return () => {
      mounted.current = false;
      window.removeEventListener("space-access-reset", cancelLoading);
      cancelLoading();
    };
  }, [prefix, store]);

  useEffect(() => {
    const id = `${prefix}-error`;
    if (!error) { store.remove(id); return; }
    store.notify({ id, kind: "error", message: error }, () => {
      if (mounted.current) callbacks.current.clearError?.();
    });
  }, [error, prefix, store]);

  useEffect(() => {
    const id = `${prefix}-notice`;
    if (!notice) { store.remove(id); return; }
    store.notify({ id, kind: "success", message: notice }, () => {
      if (mounted.current) callbacks.current.clearNotice?.();
    });
  }, [notice, prefix, store]);

  useEffect(() => {
    const id = `${prefix}-loading`;
    if (!loading) { store.remove(id); return; }
    if (store.getSnapshot().some((event) => event.id === id)) {
      store.notify({ id, kind: "loading", message: loading });
      return;
    }
    const timer = window.setTimeout(() => store.notify({ id, kind: "loading", message: loading }), 250);
    pendingLoading.current = timer;
    return () => window.clearTimeout(timer);
  }, [loading, prefix, store]);
}

function NotificationsHost() {
  const store = useNotificationStore();
  const events = useSyncExternalStore(store.subscribe, store.getSnapshot, () => emptySnapshot);
  const [target, setTarget] = useState<HTMLElement | null>(null);
  useEffect(() => {
    let openDialogs: HTMLDialogElement[] = [];
    const updateTarget = (records: MutationRecord[] = []) => {
      openDialogs = openDialogs.filter((dialog) => dialog.isConnected && dialog.open);
      // The last dialog opened is above earlier dialogs in the browser top layer.
      for (const record of records) {
        if (record.type === "attributes" && record.target instanceof HTMLDialogElement && record.target.open) {
          openDialogs = openDialogs.filter((dialog) => dialog !== record.target);
          openDialogs.push(record.target);
        }
      }
      for (const dialog of document.querySelectorAll<HTMLDialogElement>("dialog[open]")) {
        if (!openDialogs.includes(dialog)) openDialogs.push(dialog);
      }
      setTarget(openDialogs.at(-1) ?? document.body);
    };
    updateTarget();
    const observer = new MutationObserver(updateTarget);
    observer.observe(document.body, { subtree: true, childList: true, attributes: true, attributeFilter: ["open"] });
    return () => observer.disconnect();
  }, []);
  // Parent and child actions can report the same failure; announce it only once.
  const grouped = new Map<string, { event: Notification; ids: string[] }>();
  for (const event of events) {
    const key = `${event.kind}\u0000${event.message}`;
    const previous = grouped.get(key);
    if (previous) previous.ids.push(event.id);
    else grouped.set(key, { event, ids: [event.id] });
  }
  if (!target) return null;
  return createPortal(
    <div className="sp-notifications" aria-label="Notifications">
      {[...grouped].map(([key, { event, ids }]) => (
        <div key={key} className={`sp-toast sp-toast-${event.kind}`} role={event.kind === "error" ? "alert" : "status"} aria-atomic="true">
          {event.kind === "error" ? <span className="sp-toast-error-icon" aria-hidden="true">!</span>
            : event.kind === "loading" ? <span className="sp-toast-spinner" aria-hidden="true" />
              : <Icon name={event.kind === "success" ? "check" : "clock"} size={16} />}
          <span className="sp-toast-message">{event.message}</span>
          <button type="button" className="sp-icon-button" aria-label={event.kind === "error" ? "Dismiss error" : "Dismiss notification"} onClick={() => ids.forEach((id) => store.dismiss(id))}>
            <Icon name="close" size={15} />
          </button>
        </div>
      ))}
    </div>, target,
  );
}
