const backendStages = new Set([
  "api_total", "db_pool", "db_transaction", "db_read", "db_recheck", "space_key", "object_queue", "object_get", "decode",
  "recall_total", "recall_sources", "recall_vector", "recall_lexical", "recall_context", "recall_assessment", "recall_auth",
  "embedding", "jev", "chat",
]);

export function serverTiming(authMs: number, backendMs?: number, upstream = "") {
  const entries = [`auth;dur=${Math.max(0, authMs).toFixed(1)}`];
  if (backendMs !== undefined) entries.push(`backend;dur=${Math.max(0, backendMs).toFixed(1)}`);
  // Forward only known, numeric durations, never upstream descriptions or identifiers.
  const seen = new Set<string>();
  if (upstream.length <= 2048) for (const entry of upstream.split(",")) {
    const match = /^\s*([a-z_]+);dur=(\d+(?:\.\d+)?)\s*$/.exec(entry);
    if (!match || !backendStages.has(match[1]) || seen.has(match[1])) continue;
    const duration = Number(match[2]);
    if (!Number.isFinite(duration)) continue;
    seen.add(match[1]);
    entries.push(`${match[1]};dur=${duration.toFixed(1)}`);
  }
  return entries.join(", ");
}
