import type { WorkspaceDatabase } from "./databases";

export type WebhookTransition = { field_id: string; to: string };
/** endpoint: Space posts to a receiver you run. inbox: your agents pull over MCP. */
export type WebhookDeliveryKind = "endpoint" | "inbox";
export type WebhookSubscription = {
  id: string;
  space_id: string;
  database_id: string;
  delivery: WebhookDeliveryKind;
  endpoint_url: string | null;
  transition: WebhookTransition | null;
  paused: boolean;
  revision: number;
  created_at: string;
  updated_at: string;
  removed_at?: string | null;
};
export type WebhookReceipt = {
  cycle: number;
  attempt: number;
  started_at: string;
  finished_at: string | null;
  status_code: number | null;
  error_code: string | null;
};
export type WebhookDelivery = {
  id: string;
  event_id: string;
  record_id: string;
  event_type: string;
  revision: number;
  previous_revision: number | null;
  status: "queued" | "processing" | "delivered" | "failed";
  attempts: number;
  cycle: number;
  created_at: string;
  updated_at: string;
  available_at: string | null;
  error_code: string | null;
  status_code: number | null;
  receipts: WebhookReceipt[];
};
export type WebhookResult = {
  subscription: WebhookSubscription;
  signing_secret?: string;
};
export type WebhookAction = "pause" | "resume" | "remove" | "rotate";

export function webhookPath(spaceId: string, subscriptionId?: string) {
  return `spaces/${encodeURIComponent(spaceId)}/webhooks${subscriptionId ? `/${encodeURIComponent(subscriptionId)}` : ""}`;
}

export function webhookProxyAllowed(path: string, method: string) {
  const id = "[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}";
  const scope = `spaces/${id}/webhooks`;
  if (method === "GET")
    return new RegExp(`^${scope}(?:/${id}/deliveries)?$`).test(path);
  if (method === "POST")
    return new RegExp(`^${scope}(?:/${id}(?:/deliveries/${id}/redeliver)?)?$`).test(path);
  return false;
}

export function webhookEndpoint(value: string) {
  let endpoint: URL;
  try { endpoint = new URL(value.trim()); }
  catch { throw new Error("Enter a valid HTTPS endpoint URL."); }
  if (endpoint.protocol !== "https:")
    throw new Error("The endpoint must use HTTPS.");
  if (endpoint.username || endpoint.password || endpoint.hash || endpoint.search || endpoint.port)
    throw new Error("Use an endpoint on port 443 without credentials, query strings or a fragment.");
  return endpoint.href;
}

export function webhookTransition(database: WorkspaceDatabase, fieldId: string, to: string): WebhookTransition | null {
  if (!fieldId) return null;
  const field = database.fields.find(field => field.id === fieldId && field.type === "select");
  if (!field || !field.options?.includes(to))
    throw new Error("Choose a current select field and one of its options.");
  return { field_id: field.id, to };
}

export function canRedeliver(subscription: WebhookSubscription, delivery: WebhookDelivery) {
  return !subscription.paused && !subscription.removed_at &&
    (delivery.status === "delivered" || delivery.status === "failed");
}

/** Only an HTTPS receiver has a signing secret to rotate. */
export function canRotate(subscription: Pick<WebhookSubscription, "delivery">) {
  return subscription.delivery === "endpoint";
}

/** Where a subscription's events go, as shown in lists and dialogs. */
export function webhookDestination(subscription: Pick<WebhookSubscription, "delivery" | "endpoint_url">) {
  return subscription.delivery === "inbox" ? "Agent inbox" : subscription.endpoint_url ?? "";
}

/**
 * Parameters: the subscription a receipt belongs to and the receipt.
 * What it does: reads an attempt as a receiver's HTTP answer or as an agent's pull.
 * Output: the outcome text for the receipt line.
 */
export function receiptOutcome(
  subscription: Pick<WebhookSubscription, "delivery">,
  receipt: Pick<WebhookReceipt, "finished_at" | "status_code" | "error_code">,
) {
  if (subscription.delivery === "endpoint")
    return receipt.status_code === null ? "No response" : String(receipt.status_code);
  if (!receipt.finished_at) return "Leased to an agent";
  return receipt.error_code ? "Lease expired" : "Acknowledged";
}
