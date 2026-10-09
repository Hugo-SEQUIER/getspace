// Usage of one Space, as GET /v1/spaces/{id}/usage reports it. A self-hosted
// installation sets no limits by default, and then reports each limit as null.
export type SpaceUsage = {
  retained_bytes: number;
  items: number;
  storage_limit_bytes: number | null;
  item_limit: number | null;
  model_requests: number;
  model_request_limit: number | null;
};

const MIB = 1048576;

export function formatBytes(bytes: number, fraction = 1) {
  return bytes >= 1024 * MIB
    ? `${(bytes / (1024 * MIB)).toFixed(fraction)} GiB`
    : `${(bytes / MIB).toFixed(fraction)} MiB`;
}

// Parameters: a used amount, its limit (null when the installation sets none) and a formatter.
// What it does: shows the amount against a limit, or the amount alone when nothing caps it.
// Output: display text, such as "3 / 10" or "3 · No limit".
function againstLimit(used: number, limit: number | null | undefined, format: (value: number) => string) {
  return typeof limit === "number" ? `${format(used)} / ${format(limit)}` : `${format(used)} · No limit`;
}

export type CapacityRow = { label: string; amount: number; limit: number | null; text: string };

// Parameters: the usage an overview reports.
// What it does: describes storage and model requests for the overview's capacity panel.
// Output: one row each; a row without a limit has no meter to fill, only its amount.
export function capacityRows(usage: Pick<SpaceUsage, "retained_bytes" | "storage_limit_bytes" | "model_requests" | "model_request_limit">): CapacityRow[] {
  const used = (usage.retained_bytes / MIB).toFixed(1);
  const storageLimit = typeof usage.storage_limit_bytes === "number" ? usage.storage_limit_bytes : null;
  const requestLimit = typeof usage.model_request_limit === "number" ? usage.model_request_limit : null;
  return [
    {
      label: "Storage",
      amount: usage.retained_bytes,
      limit: storageLimit,
      text: storageLimit === null
        ? `${used} MiB · No limit`
        : `${used} / ${Math.round(storageLimit / MIB).toLocaleString()} MiB`,
    },
    {
      label: "Model requests this month",
      amount: usage.model_requests,
      limit: requestLimit,
      text: againstLimit(usage.model_requests, requestLimit, value => value.toLocaleString()),
    },
  ];
}

export function usageRows(usage: SpaceUsage) {
  return [
    {
      label: "Retained content",
      value: typeof usage.storage_limit_bytes === "number"
        ? `${formatBytes(usage.retained_bytes)} / ${formatBytes(usage.storage_limit_bytes, 0)}`
        : `${formatBytes(usage.retained_bytes)} · No limit`,
    },
    { label: "Workspace items", value: againstLimit(usage.items, usage.item_limit, value => value.toLocaleString()) },
    {
      label: "Model requests this month",
      value: againstLimit(usage.model_requests, usage.model_request_limit, value => value.toLocaleString()),
    },
  ];
}
