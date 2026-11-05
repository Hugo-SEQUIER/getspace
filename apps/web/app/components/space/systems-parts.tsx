import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type RefObject } from "react";
import { message, watchApi } from "../../../lib/client";
import {
  evidenceLabel, evidenceStep, laneLabel, statusLabel,
  type Evidence, type LabEntry, type LabStatus, type System, type SystemStats, type SystemStatus, type SystemsArea,
} from "../../../lib/space/systems";
import { useSpace } from "./provider";
import { Icon } from "./ui-icon";
import styles from "./systems.module.css";

// Parameters: the read area and the items this page saved since.
// What it does: shows a saved item at once, before the area's next read brings it back,
// and keeps whichever copy has the higher revision. A new create shows before the read has
// it; archived items leave the area.
// Output: the area as the page shows it.
function merged(area: SystemsArea | null, saved: Map<string, System | LabEntry>): SystemsArea | null {
  if (!area) return null;
  const pick = <T extends System | LabEntry>(items: T[], kind: T["kind"]) => {
    const known = new Set(items.map((item) => item.id));
    // Only a new create can be missing from a read that predates it; any other missing item
    // was archived elsewhere and stays gone.
    const added = [...saved.values()].filter((item): item is T => item.kind === kind && item.revision === 1 && !known.has(item.id));
    return [...added, ...items.map((item) => {
      const copy = saved.get(item.id) as T | undefined;
      return copy && copy.revision > item.revision ? copy : item;
    })].filter((item) => !item.deleted);
  };
  return { ...area, systems: pick(area.systems, "system"), entries: pick(area.entries, "lab_entry") };
}

// Parameters: none; reads the Space from the workspace provider.
// What it does: follows the whole Systems area through the shared read cache (one read,
// refreshed when a system or lab entry changes).
// Output: the area, its read error, who may change it, and `keep` to show a saved item now.
export function useSystemsArea() {
  const data = useSpace();
  const spaceId = data.space?.id ?? null;
  const [read, setRead] = useState<{ area: SystemsArea | null; error: string }>({ area: null, error: "" });
  const [saved, setSaved] = useState(() => new Map<string, System | LabEntry>());
  useEffect(() => {
    if (!spaceId) {
      setRead({ area: null, error: "" });
      return;
    }
    return watchApi<SystemsArea>(`spaces/${spaceId}/systems`, (area) => setRead({ area, error: "" }),
      (error) => setRead((current) => ({ ...current, error: message(error) })));
  }, [spaceId]);
  const keep = useCallback((item: System | LabEntry) => setSaved((current) => new Map(current).set(item.id, item)), []);
  const area = useMemo(() => merged(read.area, saved), [read.area, saved]);
  return {
    area, error: read.error, keep, spaceId,
    canWrite: data.canWrite,
    readOnlyNote: data.canWrite ? "" : "You can read this Space's systems but not change them.",
  };
}

/** A system's health as the public site's status pill: a mark and a word. */
export function StatusPill({ status }: { status: SystemStatus }) {
  return (
    <span className={styles.statusPill} data-status={status}>
      <StatusMark status={status} />
      {statusLabel(status)}
    </span>
  );
}

export function StatusMark({ status }: { status: SystemStatus }) {
  return status === "paused"
    ? <Icon name="pause" size={10} className={styles.statusMark} />
    : <i className={styles.statusMark} data-status={status} aria-hidden="true" />;
}

/** One shape per lane, so a lane never reads by its colour alone. */
export function LaneMark({ status }: { status: LabStatus }) {
  const icon = status === "testing" ? "flask" : status === "validated" ? "check" : status === "invalidated" ? "close" : "minus";
  return <span className={styles.laneMark} data-lane={status} aria-hidden="true"><Icon name={icon} size={11} /></span>;
}

/** The four-step evidence scale, filled up to the entry's step, with its name for everyone. */
export function EvidenceMeter({ evidence, showLabel = true }: { evidence: Evidence; showLabel?: boolean }) {
  const step = evidenceStep(evidence);
  return (
    <span className={styles.evidence} title={`Evidence: ${evidenceLabel(evidence)}`}>
      <span className={styles.evidenceSteps} role="img" aria-label={`Evidence: ${evidenceLabel(evidence)}, step ${step} of 4`}>
        {[1, 2, 3, 4].map((n) => <i key={n} data-on={n <= step || undefined} data-verdict={n >= 3 || undefined} />)}
      </span>
      {showLabel && <span aria-hidden="true">{evidenceLabel(evidence)}</span>}
    </span>
  );
}

const SEGMENTS = [["testing", "testing"], ["works", "validated"], ["does_not_work", "invalidated"], ["retired", "retired"]] as const;

/** A system's lab as one thin bar: testing, works, does not work and retired, in lane order. */
export function PipelineBar({ stats }: { stats: SystemStats }) {
  const label = SEGMENTS.map(([key, lane]) => `${stats[key]} ${laneLabel(lane).toLowerCase()}`).join(", ");
  return (
    <span className={styles.pipelineBar} role="img" aria-label={stats.entries ? `Lab: ${label}` : "Lab: no entries yet"}>
      {stats.entries
        ? SEGMENTS.map(([key, lane]) => stats[key] > 0 && <i key={key} data-lane={lane} style={{ flexGrow: stats[key] }} />)
        : <i data-lane="empty" />}
    </span>
  );
}

/** "Today", "Yesterday", "Tomorrow" or "Oct 4" for a YYYY-MM-DD day or an ISO time, in local time. */
export function dayLabel(value: string) {
  const date = new Date(value.length === 10 ? `${value}T12:00:00` : value);
  if (Number.isNaN(date.getTime())) return "–";
  const midnight = (moment: Date) => new Date(moment.getFullYear(), moment.getMonth(), moment.getDate()).getTime();
  const days = Math.round((midnight(date) - midnight(new Date())) / 86400000);
  if (days === 0) return "Today";
  if (days === -1) return "Yesterday";
  if (days === 1) return "Tomorrow";
  return date.toLocaleDateString("en", { month: "short", day: "numeric" });
}

const RELATIVE_DAYS = ["Today", "Yesterday", "Tomorrow"];

/** "today", "yesterday", "tomorrow" or "Oct 4", for the end of a line. */
export function dayWord(value: string) {
  const label = dayLabel(value);
  return RELATIVE_DAYS.includes(label) ? label.toLowerCase() : label;
}

/** "today", "yesterday", "tomorrow" or "on Oct 4", for the middle of a sentence. */
export function dayPhrase(value: string) {
  const label = dayLabel(value);
  return RELATIVE_DAYS.includes(label) ? label.toLowerCase() : `on ${label}`;
}

const MOVE_EASE = "cubic-bezier(0.77, 0, 0.175, 1)";

// Parameters: the element holding the lab's cards, each marked with data-entry-id and data-lane.
// What it does: after each render, slides a card that changed lane from where it was to where
// it is now (280ms, transform only), then rings it once so the eye finds it. With reduced
// motion the card does not travel; only the ring shows.
// Output: none; it animates the cards in place.
export function useLaneMotion(root: RefObject<HTMLElement | null>) {
  const last = useRef(new Map<string, { lane: string; rect: DOMRect }>());
  useLayoutEffect(() => {
    const container = root.current;
    if (!container) return;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const next = new Map<string, { lane: string; rect: DOMRect }>();
    for (const card of container.querySelectorAll<HTMLElement>("[data-entry-id]")) {
      const id = card.dataset.entryId!, lane = card.dataset.lane!, rect = card.getBoundingClientRect();
      next.set(id, { lane, rect });
      const before = last.current.get(id);
      if (!before || before.lane === lane || typeof card.animate !== "function") continue;
      if (!reduce)
        card.animate([
          { transform: `translate(${before.rect.left - rect.left}px, ${before.rect.top - rect.top}px)` },
          { transform: "translate(0, 0)" },
        ], { duration: 280, easing: MOVE_EASE });
      card.animate([
        { boxShadow: "0 0 0 2px rgb(66 100 76 / 0.5)" },
        { boxShadow: "0 0 0 2px rgb(66 100 76 / 0)" },
      ], { duration: 900, delay: reduce ? 0 : 280, easing: "ease-out" });
    }
    last.current = next;
  });
}
