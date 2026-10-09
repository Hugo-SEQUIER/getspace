
import Link from "@web/lib/navigation";
import { useEffect, useId, useMemo, useRef, useState, type PointerEvent } from "react";
import { watchApi, invalidateReads, message } from "../../../lib/client";
import {
  activityPath,
  activityForChart,
  duration,
  filterOverview,
  resultRate,
  type Overview,
  type OverviewSnapshot,
  type Period,
} from "../../../lib/space/overview";
import { capacityRows } from "../../../lib/space/usage";
import { useSpace } from "./provider";
import { useFeedback } from "../notifications";
import { Icon, type IconName } from "./ui-icon";
import { Avatar } from "./ui";
import styles from "./overview-view.module.css";

const number = (n: number) => n.toLocaleString();
const date = (value: string) =>
  new Date(value).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
const series = [
  { key: "retrievals", label: "Retrievals", color: "#8052be" },
  { key: "memories_created", label: "Memories added", color: "#258677" },
  { key: "document_writes", label: "Document saves", color: "#8093b1" },
] as const;

function Metric({
  label,
  value,
  detail,
  icon,
  href,
}: {
  label: string;
  value: string;
  detail: string;
  icon: IconName;
  href?: string;
}) {
  const content = (
    <>
      <div className={styles.metricLabel}>
        <span>{label}</span>
        <Icon name={icon} size={17} />
      </div>
      <strong>{value}</strong>
      <span className={styles.metricDetail}>{detail}</span>
    </>
  );
  return href ? (
    <Link href={href} className={styles.metric}>
      {content}
    </Link>
  ) : (
    <div className={styles.metric}>{content}</div>
  );
}

function Activity({ value }: { value: Overview }) {
  const activity = activityForChart(value.activity);
  if (!activity.length) {
    return (
      <section className={`${styles.panel} ${styles.activity}`} aria-label="Daily activity">
        <div className={styles.panelHead}><h2>Activity</h2></div>
        <div className={styles.empty}>
          <Icon name="chart" size={28} />
          <h3>No activity in this period yet</h3>
          <p>The chart appears after your first document save, memory added, or retrieval in this period.</p>
        </div>
      </section>
    );
  }
  return <ActivityChart key={`${value.days}:${activity[0].date}`} value={{ ...value, activity }} />;
}

function ActivityChart({ value }: { value: Overview }) {
  const [selected, setSelected] = useState(value.activity.length - 1);
  const instructionsId = useId();
  const day = value.activity[Math.min(selected, value.activity.length - 1)];
  const selectedDescription = `${date(day.date)}. ${
    day.available
      ? series
          .map((s) => `${number(day[s.key] ?? 0)} ${s.label.toLowerCase()}`)
          .join(", ")
      : "Before activity tracking began"
  }.`;
  const inspectDay = (event: PointerEvent<SVGSVGElement>) => {
    const transform = event.currentTarget.getScreenCTM();
    if (!transform) return;
    const point = new DOMPoint(event.clientX, event.clientY).matrixTransform(
      transform.inverse(),
    );
    if (point.y < 22 || point.y > 180) return;
    const fraction = Math.max(0, Math.min(1, (point.x - 40) / 680));
    setSelected(Math.round(fraction * (value.activity.length - 1)));
  };
  const maximum = Math.max(
    4,
    ...value.activity.flatMap((row) => series.map((s) => row[s.key] ?? 0)),
  );
  const x =
    40 +
    (Math.min(selected, value.activity.length - 1) /
      Math.max(1, value.activity.length - 1)) *
      680;
  return (
    <section
      className={`${styles.panel} ${styles.activity}`}
      aria-label="Daily activity"
    >
      <div className={styles.panelHead}>
        <div>
          <h2>Activity</h2>
          <p>Daily saves and retrievals · UTC</p>
        </div>
        <div className={styles.legend}>
          {series.map((s) => (
            <span key={s.key}>
              <i style={{ background: s.color }} />
              {s.label}
            </span>
          ))}
        </div>
      </div>
      <svg
        className={styles.chart}
        viewBox="0 0 744 218"
        role="slider"
        aria-roledescription="interactive chart"
        aria-label={`Daily activity over ${value.activity.length} days`}
        aria-describedby={instructionsId}
        aria-orientation="horizontal"
        aria-valuemin={0}
        aria-valuemax={value.activity.length - 1}
        aria-valuenow={selected}
        aria-valuetext={selectedDescription}
        tabIndex={0}
        onPointerMove={inspectDay}
        onPointerDown={(event) => {
          if (event.button !== 0) return;
          event.currentTarget.focus({ preventScroll: true });
          inspectDay(event);
        }}
        onKeyDown={(event) => {
          const last = value.activity.length - 1;
          const next =
            event.key === "Home"
              ? 0
              : event.key === "End"
                ? last
                : event.key === "ArrowRight" || event.key === "ArrowUp"
                  ? Math.min(last, selected + 1)
                  : event.key === "ArrowLeft" || event.key === "ArrowDown"
                    ? Math.max(0, selected - 1)
                    : null;
          if (next === null) return;
          event.preventDefault();
          setSelected(next);
        }}
      >
        <rect x="40" y="22" width="680" height="158" fill="transparent" />
        {[0, 0.5, 1].map((fraction) => (
          <g key={fraction}>
            <line
              x1="40"
              x2="720"
              y1={180 - fraction * 152}
              y2={180 - fraction * 152}
              stroke="var(--line)"
              strokeDasharray="3 5"
            />
            <text x="28" y={184 - fraction * 152} textAnchor="end">
              {Math.round(maximum * fraction)}
            </text>
          </g>
        ))}
        <line
          x1={x}
          x2={x}
          y1="22"
          y2="180"
          stroke="var(--muted)"
          opacity="0.35"
        />
        {series.map((s) => (
          <path
            key={s.key}
            d={activityPath(value.activity, s.key, maximum)}
            stroke={s.color}
            fill="none"
            strokeWidth="2.5"
            strokeLinejoin="round"
            strokeLinecap="round"
          />
        ))}
        {day.available &&
          series.map((s) => (
            <circle
              key={s.key}
              cx={x}
              cy={180 - ((day[s.key] ?? 0) / maximum) * 152}
              r="4"
              fill={s.color}
              stroke="var(--surface)"
              strokeWidth="2"
            />
          ))}
        <text x="40" y="208">
          {date(value.activity[0].date)}
        </text>
        <text x="380" y="208" textAnchor="middle">
          {date(value.activity[Math.floor(value.activity.length / 2)].date)}
        </text>
        <text x="720" y="208" textAnchor="end">
          {date(value.activity.at(-1)!.date)}
        </text>
      </svg>
      <p id={instructionsId} className={styles.srOnly}>
        Hover or tap the chart to inspect a day. Use the arrow keys to move
        between days, or Home and End to jump to the first and last day.
      </p>
      <div className={styles.dayDetails} aria-live="polite">
        <strong>{date(day.date)}</strong>
        {day.available ? (
          series.map((s) => (
            <span key={s.key}>
              {number(day[s.key] ?? 0)} {s.label.toLowerCase()}
            </span>
          ))
        ) : (
          <span>Before activity tracking began</span>
        )}
      </div>
      <details className={styles.dailyData}>
        <summary>View daily data</summary>
        <div className={styles.tableScroll}>
          <table>
            <caption className={styles.srOnly}>Daily activity in UTC</caption>
            <thead>
              <tr>
                <th>Date</th>
                {series.map((s) => (
                  <th key={s.key}>{s.label}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {value.activity.map((row) => (
                <tr key={row.date}>
                  <th scope="row">{date(row.date)}</th>
                  {series.map((s) => (
                    <td key={s.key}>
                      {row[s.key] === null
                        ? "Not tracked"
                        : number(row[s.key]!)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </section>
  );
}

function Retrieval({ value }: { value: Overview }) {
  const s = value.summary,
    rate = resultRate(s);
  return (
    <section className={styles.panel}>
      <div className={styles.panelHead}>
        <div>
          <h2>Retrieval health</h2>
          <p>Requests for context through Space</p>
        </div>
        <Icon name="search" size={18} />
      </div>
      <div className={styles.rate}>
        <strong>{rate === null ? "—" : `${Math.round(rate * 100)}%`}</strong>
        <span>of completed requests returned results</span>
      </div>
      <div className={styles.outcomes} aria-hidden="true">
        {s.retrievals > 0 && (
          <>
            <i
              style={{
                width: `${(s.with_results / s.retrievals) * 100}%`,
                background: "#258677",
              }}
            />
            <i
              style={{
                width: `${(s.empty / s.retrievals) * 100}%`,
                background: "#c8c3ce",
              }}
            />
            <i
              style={{
                width: `${(s.errors / s.retrievals) * 100}%`,
                background: "#cb795d",
              }}
            />
          </>
        )}
      </div>
      <dl className={styles.breakdown}>
        <div>
          <dt>With results</dt>
          <dd>{number(s.with_results)}</dd>
        </div>
        <div>
          <dt>No results</dt>
          <dd>{number(s.empty)}</dd>
        </div>
        <div>
          <dt>Failed</dt>
          <dd>{number(s.errors)}</dd>
        </div>
      </dl>
      <div className={styles.reuse}>
        <Icon name="memory" size={20} />
        <div>
          <strong>{number(s.reused_memories)} memories retrieved</strong>
          <p>
            Distinct memories returned as ranked results. This does not confirm
            use in an answer.
          </p>
        </div>
      </div>
      <details className={styles.timing}>
        <summary>Retrieval timing by strategy</summary>
        <p>
          Server execution time, including model calls. Excludes queueing,
          network time, and failed requests.
        </p>
        {value.strategies.length ? (
          <div className={styles.tableScroll}>
            <table>
              <thead>
                <tr>
                  <th>Strategy</th>
                  <th>Requests</th>
                  <th>Median</th>
                  <th>95th %ile</th>
                </tr>
              </thead>
              <tbody>
                {value.strategies.map((row) => (
                  <tr key={row.strategy}>
                    <th scope="row">{row.strategy}</th>
                    <td>{number(row.retrievals)}</td>
                    <td>{duration(row.p50_ms)}</td>
                    <td>{duration(row.p95_ms)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p>No retrievals recorded in this period.</p>
        )}
      </details>
    </section>
  );
}

function Capacity({ value, base }: { value: Overview; base: string }) {
  const usage = value.usage;
  return (
    <section className={styles.panel}>
      <div className={styles.panelHead}>
        <h2>Capacity</h2>
        <Link href={`${base}/settings/data`}>
          Manage <Icon name="arrow" size={14} />
        </Link>
      </div>
      {capacityRows(usage).map((item) => (
        <div className={styles.capacity} key={item.label}>
          <div>
            <span>{item.label}</span>
            <strong>{item.text}</strong>
          </div>
          {item.limit !== null && (
            <progress
              max={Math.max(1, item.limit)}
              value={item.amount}
              aria-label={item.label}
            />
          )}
        </div>
      ))}
      <p className={styles.note}>
        Storage includes revision history. One retrieval or extraction may make
        several model requests.
      </p>
      <div className={styles.lines}>
        <Icon name="document" size={16} />
        <span>
          <strong>
            {value.totals.documents > 0 && value.totals.measured_documents === 0
              ? "Document lines not measured yet"
              : `${number(value.totals.document_lines)} ${value.totals.measured_documents < value.totals.documents ? "measured " : ""}document lines`}
          </strong>
          <small>
            {value.totals.measured_documents === value.totals.documents
              ? "Across current document versions"
              : `Measured in ${number(value.totals.measured_documents)} of ${number(value.totals.documents)} documents; older documents are counted on their next save.`}
          </small>
        </span>
      </div>
    </section>
  );
}

export function OverviewView({ base }: { base: string }) {
  const data = useSpace();
  const [days, setDays] = useState<Period>(30);
  const feedRef = useRef<HTMLOListElement>(null);
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<{
    key: string;
    value?: OverviewSnapshot;
    error?: string;
    loading: boolean;
  }>({ key: "", loading: true });
  const key = `${data.space?.id}`;
  useEffect(() => {
    if (!data.space) return;
    const path = `spaces/${data.space.id}/overview?days=90&all_periods=true`;
    setState(previous => ({ key, value: previous.key === key ? previous.value : undefined, loading: true }));
    if (attempt) invalidateReads(path);
    return watchApi<OverviewSnapshot>(path,
      value => setState({ key, value, loading: false }),
      error => setState(previous => ({ ...previous, key, error: message(error), loading: false })),
    );
  }, [data.space?.id, attempt, key]);
  // The overview is the primary read; agent labels may arrive afterwards.
  useEffect(() => data.watchAgents(), [data.watchAgents]);
  const snapshot = state.key === key ? state.value : undefined;
  const value = useMemo(() => snapshot ? filterOverview(snapshot, days) : undefined, [snapshot, days]);
  useEffect(() => {
    const feed = feedRef.current;
    if (!feed) return;
    const rows = Array.from(feed.children).slice(0, 3);
    const resize = () => {
      feed.style.maxHeight = `${rows.reduce((height, row) => height + row.getBoundingClientRect().height, 0)}px`;
    };
    const observer = new ResizeObserver(resize);
    rows.forEach(row => observer.observe(row));
    resize();
    feed.scrollTop = 0;
    return () => observer.disconnect();
  }, [value?.recent]);
  const loading = state.key !== key || state.loading;
  const error = state.key === key ? state.error : undefined;
  useFeedback({ error, loading: loading ? "Loading your overview…" : null });
  const agentName = (id: string | null, actor: string) =>
    actor === "website"
      ? "Website"
      : (data.agents.find((a) => a.id === id)?.name ??
        (id ? "Agent" : "Unidentified agent"));
  const profiles = value
    ? [
        ...value.agents,
        ...data.agents
          .filter(
            (agent) => !value.agents.some((row) => row.agent_id === agent.id),
          )
          .map((agent) => ({
            agent_id: agent.id,
            actor_type: "agent" as const,
            document_writes: 0,
            memories_created: 0,
            retrievals: 0,
            last_active_at: null,
          })),
      ]
    : [];
  return (
    <>
      <div className={styles.page} aria-busy={loading}>
        <header className={`sp-work-heading ${styles.header}`}>
          <div>
            <h1>Overview</h1>
            <p>
              Activity in {data.space?.name ?? "your workspace"}
            </p>
          </div>
          <div className="sp-actions">
            <div
              className={styles.period}
              role="group"
              aria-label="Activity period"
            >
              {([7, 30, 90] as const).map((period) => (
                <button
                  key={period}
                  aria-pressed={days === period}
                  onClick={() => setDays(period)}
                >
                  {period} days
                </button>
              ))}
            </div>
            <button onClick={() => setAttempt((n) => n + 1)} disabled={loading}>
              <Icon name="refresh" size={16} />
              {loading ? "Refreshing…" : "Refresh"}
            </button>
          </div>
        </header>
        {error && (
          <div>
            <button onClick={() => setAttempt((n) => n + 1)}>Try again</button>
          </div>
        )}
        {!value ? (
          <div className={styles.empty} role="status">
            <Icon name="chart" size={28} />
            <h2>
              {loading ? "Loading your overview…" : "Overview is unavailable"}
            </h2>
            <p>
              {loading
                ? "Gathering your Space’s activity."
                : "Your documents and memories are still available from the sidebar."}
            </p>
          </div>
        ) : (
          <>
            <div className={styles.periodNote}>
              <span>
                Activity · {date(value.period_start)} – {date(value.as_of)} ·
                UTC
              </span>
              <span>
                Updated{" "}
                {new Date(value.as_of).toLocaleTimeString([], {
                  hour: "2-digit",
                  minute: "2-digit",
                })}
              </span>
            </div>
            <div className={styles.metrics}>
              <Metric
                label="Documents created"
                value={number(value.summary.documents_created)}
                detail={`In the last ${days} days · ${number(value.totals.documents)} current documents`}
                icon="document"
                href={`${base}/documents`}
              />
              <Metric
                label="Memories added"
                value={number(value.summary.memories_created)}
                detail={`Individual claims in the last ${days} days · ${number(value.totals.memories)} current claims`}
                icon="memory"
                href={`${base}/memory`}
              />
              <Metric
                label="Retrievals"
                value={number(value.summary.retrievals)}
                detail={`Context requests in the last ${days} days`}
                icon="search"
              />
              <Metric
                label="Typical retrieval"
                value={duration(value.summary.p50_ms)}
                detail={
                  value.summary.p95_ms === null
                    ? "Timing appears after the first completed retrieval"
                    : `95% completed within ${duration(value.summary.p95_ms)}`
                }
                icon="clock"
              />
            </div>
            {new Date(value.tracking_started_at) >
              new Date(value.period_start) && (
              <p className={styles.coverage}>
                <Icon name="clock" size={15} />
                Activity tracking began {date(
                  value.tracking_started_at,
                )} at{" "}
                {new Date(value.tracking_started_at).toLocaleTimeString([], {
                  hour: "2-digit",
                  minute: "2-digit",
                  timeZone: "UTC",
                })}{" "}
                UTC. Earlier history is unavailable. Period counts include only
                tracked activity; capacity reflects current usage.
              </p>
            )}
            <div className={styles.chartGrid}>
              <Activity key={days} value={value} />
              <Retrieval value={value} />
            </div>
            <section className={styles.panel}>
              <div className={styles.panelHead}>
                <div>
                  <h2>Agent activity</h2>
                  <p>
                    Document saves, new memories, and context requests through
                    Space in this period.
                  </p>
                </div>
                <Link href={`${base}/agents`}>
                  Manage agents <Icon name="arrow" size={14} />
                </Link>
              </div>
              {profiles.length ? (
                <div className={styles.tableScroll}>
                  <table className={styles.agentTable}>
                    <thead>
                      <tr>
                        <th>Agent</th>
                        <th>Document saves</th>
                        <th>Memories added</th>
                        <th>Retrievals</th>
                        <th>Last activity in period</th>
                      </tr>
                    </thead>
                    <tbody>
                      {profiles.map((row) => (
                        <tr key={row.agent_id ?? row.actor_type}>
                          <th scope="row">
                            <div className={styles.agent}>
                              <Avatar
                                name={agentName(row.agent_id, row.actor_type)}
                                kind={row.actor_type === "agent" ? "agent" : "person"}
                                harness={row.actor_type === "agent" ? data.agents.find((agent) => agent.id === row.agent_id)?.harness : undefined}
                              />
                              <span>
                                {agentName(row.agent_id, row.actor_type)}
                                <small>
                                  {row.actor_type === "website"
                                    ? "Browser activity"
                                    : (data.agents.find(
                                        (a) => a.id === row.agent_id,
                                      )?.harness ?? "Agent connection")}
                                </small>
                              </span>
                            </div>
                          </th>
                          <td>{number(row.document_writes)}</td>
                          <td>{number(row.memories_created)}</td>
                          <td>{number(row.retrievals)}</td>
                          <td>
                            {row.last_active_at ? (
                              <time
                                dateTime={row.last_active_at}
                                title={new Date(
                                  row.last_active_at,
                                ).toLocaleString()}
                              >
                                {date(row.last_active_at)}
                              </time>
                            ) : (
                              "No activity"
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div className={styles.empty}>
                  <Icon name="agents" size={26} />
                  <h3>No agent activity yet</h3>
                  <p>
                    Connect an agent to start saving and retrieving context.
                  </p>
                  <Link className="sp-button" href={`${base}/agents`}>
                    Connect an agent <Icon name="arrow" size={14} />
                  </Link>
                </div>
              )}
            </section>
            <div className={styles.bottomGrid}>
              <section className={styles.panel}>
                <div className={styles.panelHead}>
                  <h2>Recent activity</h2>
                  <span className={styles.note}>Last 12 events in period</span>
                </div>
                {value.recent.length ? (
                  <ol ref={feedRef} className={styles.feed} tabIndex={0} aria-label="Recent activity, scroll for more">
                    {value.recent.map((event) => (
                      <li key={event.id}>
                        <span className={styles.feedIcon}>
                          <Icon
                            name={
                              event.kind === "retrieval"
                                ? "search"
                                : event.kind === "memory"
                                  ? "memory"
                                  : event.kind === "task"
                                    ? "tasks"
                                    : "document"
                            }
                            size={16}
                          />
                        </span>
                        <div>
                          <strong>
                            {agentName(event.agent_id, event.actor_type)}
                          </strong>
                          <span>
                            {event.kind === "retrieval"
                              ? event.outcome === "error"
                                ? "Retrieval failed"
                                : event.outcome === "empty"
                                  ? "Retrieved no results"
                                  : `Retrieved ${number(event.item_count)} results`
                              : event.kind === "memory"
                                ? `Saved memory · ${number(event.new_items)} claims added`
                                : `${event.new_items ? "Created" : "Updated"} a ${event.kind}`}
                          </span>
                        </div>
                        {event.resource_exists && event.resource_id && (
                          <Link
                            aria-label={`Open ${event.kind}`}
                            href={
                              event.kind === "memory"
                                ? `${base}/memory`
                                : `${base}/${event.kind === "task" ? "tasks" : "documents"}/${event.resource_id}`
                            }
                          >
                            <Icon name="arrow" size={14} />
                          </Link>
                        )}
                        <time
                          dateTime={event.created_at}
                          title={new Date(event.created_at).toLocaleString()}
                        >
                          {date(event.created_at)}
                        </time>
                      </li>
                    ))}
                  </ol>
                ) : (
                  <div className={styles.empty}>
                    <Icon name="clock" size={24} />
                    <h3>Your activity will appear here</h3>
                    <p>
                      Save a document or retrieve context to start the history.
                    </p>
                  </div>
                )}
              </section>
              <Capacity value={value} base={base} />
            </div>
          </>
        )}
      </div>
    </>
  );
}
