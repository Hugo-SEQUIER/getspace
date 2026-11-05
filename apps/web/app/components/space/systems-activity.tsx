import Link from "@web/lib/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { message, watchApi } from "../../../lib/client";
import { activityRows, type ActivityEvent, type ActivityRow } from "../../../lib/space/systems";
import { HarnessLogo } from "./harness-logo";
import { Avatar } from "./ui";
import { Icon } from "./ui-icon";
import { LaneMark, dayPhrase } from "./systems-parts";
import styles from "./systems-activity.module.css";

const SHOWN = 8;

function who(row: ActivityRow) {
  return row.author === "you" ? "You" : row.author === "agent" ? row.agent?.name ?? "An agent" : row.author;
}

/** "3 min ago", "2 h ago", then "yesterday" or "on Oct 4". */
function ago(at: string) {
  const minutes = Math.floor((Date.now() - Date.parse(at)) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  if (minutes < 24 * 60) return `${Math.floor(minutes / 60)} h ago`;
  return dayPhrase(at);
}

// Parameters: the workspace base, the Space (null in the sample workspace) and, on a
// system's page, that system's id and name.
// What it does: follows the lab's latest revisions and names what each changed, who made
// it and when, linking to the entry. Rows that arrive after the first read slide in from
// the top (only fade with reduced motion); the first 8 show, then up to the 30 returned.
// Output: a section.
export function ActivityFeed({ base, spaceId, systemId, systemNames }: {
  base: string; spaceId: string | null; systemId?: string; systemNames: Map<string, string>;
}) {
  const [read, setRead] = useState<{ events: ActivityEvent[] | null; error: string }>({ events: null, error: "" });
  const [expanded, setExpanded] = useState(false);
  useEffect(() => {
    if (!spaceId) return;
    setRead({ events: null, error: "" });
    return watchApi<{ events: ActivityEvent[] }>(`spaces/${spaceId}/systems/activity${systemId ? `?system_id=${systemId}` : ""}`,
      (value) => setRead({ events: value.events, error: "" }), (error) => setRead((current) => ({ ...current, error: message(error) })));
  }, [spaceId, systemId]);
  const events = read.events;
  const rows = useMemo(() => (events ? activityRows(events) : null), [events]);
  // Keys already shown: only rows that arrive later are new. The first read is never new.
  const seen = useRef<Set<string> | null>(null);
  const fresh = useMemo(() => new Set(rows && seen.current ? rows.map((row) => row.key).filter((key) => !seen.current!.has(key)) : []), [rows]);
  useEffect(() => {
    if (rows) seen.current = new Set([...(seen.current ?? []), ...rows.map((row) => row.key)]);
  }, [rows]);
  const shown = rows ? (expanded ? rows : rows.slice(0, SHOWN)) : [];
  return (
    <section className={styles.feed} aria-labelledby={`lab-activity-${systemId ?? "all"}`}>
      <h2 id={`lab-activity-${systemId ?? "all"}`}>Recent in the lab</h2>
      {rows === null ? (
        <p className={styles.quiet} role="status">{read.error ? "Recent activity is unavailable right now." : "Loading recent activity…"}</p>
      ) : !rows.length ? (
        <p className={styles.quiet}>Nothing has moved in the lab yet.</p>
      ) : (
        <>
          <ol className={styles.rows}>
            {shown.map((row) => (
              <li key={row.key} data-new={fresh.has(row.key) || undefined} data-kind={row.event.kind}>
                <span className={styles.actor}>
                  {row.author === "agent" && row.agent ? <HarnessLogo harness={row.agent.harness} /> : <Avatar name={who(row)} small kind={row.author === "agent" ? "agent" : "person"} />}
                </span>
                <span className={styles.body}>
                  <strong className={styles.change}>
                    {row.event.kind === "retried" ? <Icon name="refresh" size={13} /> : <LaneMark status={row.entry.status} />}
                    <span title={row.event.text}>{row.event.text}</span>
                  </strong>
                  <Link className={styles.sentence} href={`${base}/systems/${row.entry.system_id}?entry=${row.entry.id}`} replace={!!systemId}>
                    {row.entry.name}
                  </Link>
                  <small>
                    {who(row)}
                    {!systemId && systemNames.get(row.entry.system_id) && <> · {systemNames.get(row.entry.system_id)}</>}
                    {" · "}<time dateTime={row.at}>{ago(row.at)}</time>
                  </small>
                </span>
              </li>
            ))}
          </ol>
          {rows.length > SHOWN && (
            <button type="button" className={`sp-text-button ${styles.more}`} aria-expanded={expanded} onClick={() => setExpanded((open) => !open)}>
              {expanded ? "Show less" : `Show more (${rows.length - SHOWN})`}
            </button>
          )}
        </>
      )}
    </section>
  );
}
