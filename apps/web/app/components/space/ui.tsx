import { createContext, useCallback, useContext, useEffect, useId, useRef, useState, type ReactNode } from "react";
import { Icon, type IconName } from "./ui-icon";
import { useSpace } from "./provider";
import { DocumentMarkdown } from "./document-markdown";
import { avatarInitials, avatarTone } from "../../../lib/space/profile";
import type { Status } from "../../../lib/space/types";
import { harnesses, harnessLogo } from "../../../lib/space/agents";
import { HarnessLogo } from "./harness-logo";
import { useFeedback } from "../notifications";

export function Modal({
  title,
  children,
  onClose,
  wide = false,
  size,
  className = "",
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  wide?: boolean;
  // small: confirmations; default: forms; wide: editors, imports, and long content.
  size?: "small" | "default" | "wide";
  className?: string;
}) {
  const sizeClass = size === "small" ? "sp-modal-small" : size === "wide" || wide ? "sp-modal-wide" : "";
  const ref = useRef<HTMLDialogElement>(null);
  const backdropPress = useRef(false);
  const titleId = useId();
  useEffect(() => {
    const dialog = ref.current;
    const previousFocus = document.activeElement;
    if (dialog && !dialog.open) {
      dialog.showModal();
      const focusTarget = dialog.querySelector<HTMLElement>("[data-autofocus], [autofocus]")
        ?? dialog.querySelector<HTMLElement>(".sp-modal-head h2");
      focusTarget?.focus({ preventScroll: true });
    }
    return () => {
      dialog?.close();
      if (previousFocus instanceof HTMLElement && previousFocus.isConnected) {
        previousFocus.focus({ preventScroll: true });
      }
    };
  }, []);
  return (
    <dialog
      ref={ref}
      className={`sp-modal ${sizeClass} ${className}`}
      aria-labelledby={titleId}
      onKeyDown={(event) => {
        if (event.key !== "Tab" || (event.target as Element).closest("dialog") !== event.currentTarget) return;
        const targets = [...event.currentTarget.querySelectorAll<HTMLElement>(
          'a[href], button, input, select, textarea, [tabindex]',
        )].filter(element => element.tabIndex >= 0 && !element.matches(":disabled") &&
          element.getClientRects().length > 0 && element.closest("dialog") === event.currentTarget);
        const first = targets[0], last = targets[targets.length - 1];
        if (!first || !last) {
          event.preventDefault();
          return;
        }
        if (event.shiftKey && (document.activeElement === first || !targets.includes(document.activeElement as HTMLElement))) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }}
      onPointerDown={(event) => {
        const rect = event.currentTarget.getBoundingClientRect();
        backdropPress.current = event.target === event.currentTarget && (
          event.clientX < rect.left || event.clientX > rect.right ||
          event.clientY < rect.top || event.clientY > rect.bottom
        );
      }}
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
      onClick={(e) => {
        if (backdropPress.current && e.target === ref.current) {
          const r = ref.current.getBoundingClientRect();
          if (
            e.clientX < r.left ||
            e.clientX > r.right ||
            e.clientY < r.top ||
            e.clientY > r.bottom
          )
            onClose();
        }
      }}
    >
      <div className="sp-modal-head">
        <h2 id={titleId} tabIndex={-1}>{title}</h2>
        <button
          type="button"
          className="sp-icon-button"
          onClick={onClose}
          aria-label="Close dialog"
        >
          <Icon name="close" />
        </button>
      </div>
      {children}
    </dialog>
  );
}
// Parameters: dialog title, message, confirm label, and the async action to run.
// What it does: renders every confirmation the same way, with Cancel before the action
// and a red action for destructive changes; it cannot be dismissed while busy.
// Output: a small modal dialog.
export function ConfirmModal({
  title,
  children,
  confirmLabel,
  busyLabel,
  busy = false,
  error,
  destructive = true,
  disabled = false,
  onConfirm,
  onClose,
}: {
  title: string;
  children: ReactNode;
  confirmLabel: string;
  busyLabel?: string;
  busy?: boolean;
  error?: string;
  destructive?: boolean;
  disabled?: boolean;
  onConfirm: () => void;
  onClose: () => void;
}) {
  useFeedback({ error });
  return (
    <Modal title={title} size="small" className="sp-confirm-modal" onClose={() => { if (!busy) onClose(); }}>
      <div className="sp-modal-content">
        <div className="sp-confirm-message">{children}</div>
        <div className="sp-modal-footer">
          <button type="button" disabled={busy} onClick={onClose}>Cancel</button>
          <button type="button" className={destructive ? "sp-danger" : "sp-primary"} disabled={busy || disabled} onClick={onConfirm}>
            {busy ? busyLabel ?? confirmLabel : confirmLabel}
          </button>
        </div>
      </div>
    </Modal>
  );
}
type ConfirmOptions = { title: string; message: string; confirmLabel: string; destructive?: boolean };
const ConfirmContext = createContext<((options: ConfirmOptions) => Promise<boolean>) | null>(null);

// Parameters: workspace content.
// What it does: hosts one in-app confirmation dialog so actions never fall back to the browser's native popup.
// Output: children with a `useConfirm` context.
export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [request, setRequest] = useState<(ConfirmOptions & { resolve: (value: boolean) => void }) | null>(null);
  const confirm = useCallback((options: ConfirmOptions) => new Promise<boolean>((resolve) => {
    setRequest((previous) => {
      previous?.resolve(false);
      return { ...options, resolve };
    });
  }), []);
  function settle(value: boolean) {
    request?.resolve(value);
    setRequest(null);
  }
  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      {request && (
        <ConfirmModal
          title={request.title}
          confirmLabel={request.confirmLabel}
          destructive={request.destructive ?? true}
          onConfirm={() => settle(true)}
          onClose={() => settle(false)}
        >
          <p>{request.message}</p>
        </ConfirmModal>
      )}
    </ConfirmContext.Provider>
  );
}

// Output: an async confirm; outside the workspace provider it uses the browser prompt.
export function useConfirm() {
  const confirm = useContext(ConfirmContext);
  return confirm ?? (async (options: ConfirmOptions) => window.confirm(options.message));
}
export function StatusDot({ status }: { status: Status }) {
  return (
    <span
      className={`sp-status-dot sp-status-${status}`}
      aria-label={status.replaceAll("_", " ")}
    >
      {status === "done" && <Icon name="check" size={11} />}
    </span>
  );
}
export function Empty({
  icon = "document",
  title,
  children,
  valley = false,
}: {
  icon?: IconName;
  title: string;
  children: ReactNode;
  /** A first-use state: ends on the public site's engraved valley. */
  valley?: boolean;
}) {
  return (
    <div className="sp-empty" data-valley={valley || undefined}>
      <span className="sp-empty-icon">
        <Icon name={icon} size={28} />
      </span>
      <h2>{title}</h2>
      <div>{children}</div>
    </div>
  );
}
export function Avatar({
  name,
  small = false,
  kind = "person",
  src,
  harness,
}: {
  name: string;
  small?: boolean;
  kind?: "person" | "agent" | "organization";
  src?: string | null;
  harness?: string;
}) {
  const data = useSpace();
  const [failedSource, setFailedSource] = useState<string | null>(null);
  const knownAgent =
    harness ??
    harnesses.find(h => h.id === name.toLowerCase().trim() || h.name.toLowerCase() === name.toLowerCase().trim())?.id;
  const hasHarnessLogo = !!knownAgent && !!harnessLogo(knownAgent);
  const isAgent = kind === "agent" || (kind === "person" && !!knownAgent);
  const isSelf =
    !isAgent &&
    kind === "person" &&
    (name === "You" || name === "you" || (!!data.profile.displayName && name === data.profile.displayName));
  const displayName = isSelf ? data.profile.displayName || name : name;
  const photo = src ?? (isSelf ? data.profile.avatarUrl : null);
  const showPhoto = photo && photo !== failedSource;
  const glyph: IconName =
    knownAgent === "codex" || knownAgent === "cursor"
      ? "code"
      : knownAgent === "claude" || knownAgent === "claude-code"
        ? "sun"
        : knownAgent === "hermes"
          ? "rocket"
          : knownAgent === "chatgpt"
            ? "chat"
            : "agents";
  return (
    <span
      className={`sp-avatar sp-avatar-${isAgent ? "agent" : kind} ${small ? "sp-avatar-small" : ""} ${showPhoto ? "sp-avatar-with-photo" : ""} ${isAgent && hasHarnessLogo ? "sp-avatar-with-logo" : ""}`}
      data-tone={avatarTone(displayName)}
      data-agent={isAgent ? (knownAgent ?? "agent") : undefined}
      aria-hidden="true"
    >
      {isAgent && hasHarnessLogo ? (
        <HarnessLogo harness={knownAgent!} className="sp-avatar-harness-logo" />
      ) : isAgent ? (
        <Icon name={glyph} size={small ? 13 : 17} />
      ) : (
        avatarInitials(displayName)
      )}
      {showPhoto && (
        <img
          src={photo}
          alt=""
          className="sp-avatar-photo"
          width={64}
          height={64}
          referrerPolicy="no-referrer"
          decoding="async"
          onError={() => setFailedSource(photo)}
        />
      )}
    </span>
  );
}
export function authorName(author: string, agent?: { name: string } | null) {
  if (author === "agent" && agent) return agent.name;
  return author === "agent" ? "Agent" : author === "you" ? "You" : author;
}
export function relativeDate(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "-";
  const days = Math.floor((Date.now() - date.getTime()) / 86400000);
  if (days <= 0) return "Today";
  if (days === 1) return "Yesterday";
  return date.toLocaleDateString("en", { month: "short", day: "numeric" });
}

// Render a bounded Markdown subset as React text nodes; raw HTML is never evaluated.
export function Markdown({ text }: { text: string }) {
  return <DocumentMarkdown text={text} />;
}
