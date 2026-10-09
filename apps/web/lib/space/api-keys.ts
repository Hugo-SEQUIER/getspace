export type ApiKey = {
  id: string;
  name: string;
  prefix: string;
  can_write: boolean;
  created_at: string;
  expires_at: string | null;
  last_used_at: string | null;
};
// The token is returned once, by the create call, and is never retrievable again.
export type CreatedApiKey = ApiKey & { token: string };

export const API_KEY_LIMIT = 25;
export const API_KEY_NAME_LIMIT = 80;
export const apiKeyExpiries = [
  { value: "30", label: "30 days", days: 30 },
  { value: "90", label: "90 days", days: 90 },
  { value: "365", label: "1 year", days: 365 },
  { value: "never", label: "No expiry", days: null },
] as const;

export function apiKeyPath(spaceId: string, keyId?: string) {
  return `spaces/${encodeURIComponent(spaceId)}/api-keys${keyId ? `/${encodeURIComponent(keyId)}` : ""}`;
}

export function apiKeyProxyAllowed(path: string, method: string) {
  const id = "[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}";
  const scope = `spaces/${id}/api-keys`;
  if (method === "GET" || method === "POST") return new RegExp(`^${scope}$`).test(path);
  if (method === "DELETE") return new RegExp(`^${scope}/${id}$`).test(path);
  return false;
}

/** The public REST API shares its origin with the Space's MCP endpoint. */
export function apiBaseUrl(mcpUrl: string | undefined) {
  try { return mcpUrl ? new URL(mcpUrl).origin : null; }
  catch { return null; }
}

export function apiKeyExpired(key: ApiKey, now = Date.now()) {
  return key.expires_at !== null && new Date(key.expires_at).getTime() <= now;
}
