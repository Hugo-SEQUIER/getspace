export type Period = 7 | 30 | 90;
export type Measures = {
  document_writes: number;
  documents_created: number;
  memories_created: number;
  memory_writes: number;
  retrievals: number;
  with_results: number;
  empty: number;
  errors: number;
  p50_ms: number | null;
  p95_ms: number | null;
};
export type ActivityDay = {
  date: string;
  available: boolean;
  document_writes: number | null;
  memories_created: number | null;
  retrievals: number | null;
};
export type Overview = {
  space_id: string;
  days: Period;
  as_of: string;
  period_start: string;
  tracking_started_at: string;
  totals: {
    documents: number;
    tasks: number;
    memories: number;
    document_lines: number;
    measured_documents: number;
  };
  summary: Measures & { reused_memories: number };
  activity: ActivityDay[];
  agents: (Measures & {
    agent_id: string | null;
    actor_type: "agent" | "website";
    last_active_at: string;
  })[];
  strategies: (Measures & { strategy: string })[];
  recent: {
    id: string;
    created_at: string;
    agent_id: string | null;
    actor_type: "agent" | "website";
    kind: "document" | "memory" | "retrieval" | "task" | "handoff";
    resource_id: string | null;
    resource_exists: boolean;
    new_items: number;
    item_count: number;
    outcome: "success" | "empty" | "error";
    duration_ms: number | null;
  }[];
  /** Limits are null when the installation sets none, which is the self-hosted default. */
  usage: {
    retained_bytes: number;
    storage_limit_bytes: number | null;
    items: number;
    item_limit: number | null;
    model_requests: number;
    model_request_limit: number | null;
  };
};

export type OverviewSnapshot = Overview & {
  periods: Record<Period, Pick<Overview, "period_start" | "summary" | "agents" | "strategies">>;
};

/** Select a window locally, retaining exact server-computed non-additive metrics. */
export function filterOverview(snapshot: OverviewSnapshot, days: Period): Overview {
  const period = snapshot.periods[days];
  const start = Date.parse(period.period_start);
  return {
    ...snapshot,
    period_start: period.period_start,
    summary: period.summary,
    agents: period.agents,
    strategies: period.strategies,
    days,
    activity: snapshot.activity.filter(row => Date.parse(row.date) >= start),
    recent: snapshot.recent.filter(row => Date.parse(row.created_at) >= start),
  };
}

export function duration(ms: number | null) {
  if (ms === null) return "—";
  return ms < 1000
    ? `${Math.round(ms).toLocaleString()} ms`
    : `${(ms / 1000).toFixed(2)} s`;
}

export function resultRate(summary: Measures) {
  const completed = summary.with_results + summary.empty;
  return completed ? summary.with_results / completed : null;
}

/**
 * Parameters: Daily activity in chronological order.
 * What it does: Starts the chart at the first recorded save, added claim, or retrieval.
 * Output: Activity from that day onward, or an empty array when no data exists.
 */
export function activityForChart(rows: ActivityDay[]): ActivityDay[] {
  const first = rows.findIndex(row => row.available && (
    (row.document_writes ?? 0) > 0 ||
    (row.memories_created ?? 0) > 0 ||
    (row.retrievals ?? 0) > 0
  ));
  return first < 0 ? [] : rows.slice(first);
}

export function activityPath(
  rows: ActivityDay[],
  key: "document_writes" | "memories_created" | "retrievals",
  maximum: number,
) {
  let started = false;
  return rows
    .map((row, index) => {
      const value = row[key];
      if (!row.available || value === null) {
        started = false;
        return "";
      }
      const point = `${40 + (index / Math.max(1, rows.length - 1)) * 680},${180 - (value / Math.max(1, maximum)) * 152}`;
      const command = started ? "L" : "M";
      started = true;
      return `${command}${point}`;
    })
    .join(" ");
}
