// A connection token lets an MCP client reach the Space directly, without a device code or a
// browser: the owner creates it on the Agents page, sees it once, and pastes the setup into the
// client. It appears as a connection of its agent and is revoked like any other.

/** POST /v1/agent-tokens: the connection's name, client and access, for one agent identity. */
export type AgentTokenRequest = {
  name: string;
  harness: string;
  can_write: boolean;
  agent_id: string | null;
  space_id: string | null;
};

/** The response, shaped like a successful device-code poll. The token is never shown again. */
export type AgentToken = {
  access_token: string;
  token_type: string;
  expires_in: number;
  agent_id: string | null;
  mcp_url: string;
  headers: Record<string, string>;
};

export const AGENT_TOKEN_NAME_LIMIT = 80;

/** Parameters: what the form holds. Output: the request body, or an error that says what to fix. */
export function agentTokenRequest(input: {
  name: string; harness: string; canWrite: boolean; agentId: string | null; spaceId: string | null;
}): AgentTokenRequest {
  const name = input.name.normalize("NFKC").trim().replace(/\s+/g, " ");
  if (!name) throw new Error("Name the agent this connection is for.");
  if (Array.from(name).length > AGENT_TOKEN_NAME_LIMIT) throw new Error(`Use a name of at most ${AGENT_TOKEN_NAME_LIMIT} characters.`);
  return { name, harness: input.harness || "other", can_write: input.canWrite, agent_id: input.agentId, space_id: input.spaceId };
}

/** The website proxy forwards agent-tokens only to create one. */
export function agentTokenProxyAllowed(path: string, method: string) {
  return method === "POST" && path === "agent-tokens";
}

/** The headers a client sends: the response's own, always with the bearer token among them. */
export function connectionHeaders(token: AgentToken): [string, string][] {
  const headers = Object.entries(token.headers ?? {}).filter(
    (entry): entry is [string, string] => typeof entry[1] === "string" && entry[1].length > 0,
  );
  // The token is the credential, so the setup sends it even when the response names other headers only.
  if (!headers.some(([name]) => name.toLowerCase() === "authorization"))
    headers.unshift(["Authorization", `Bearer ${token.access_token}`]);
  return headers;
}

// Double quotes keep the header's space inside one shell argument; the characters a shell
// would still interpret inside them are escaped.
const doubleQuoted = (value: string) => `"${value.replace(/["\\$`]/g, character => `\\${character}`)}"`;
const shellWord = (value: string) => /^[A-Za-z0-9_@%+=:,./~-]+$/.test(value) ? value : doubleQuoted(value);

/** Claude Code: one command that registers the Space as an HTTP MCP server with its headers. */
export function claudeCodeCommand(token: AgentToken, server = "space") {
  return [
    `claude mcp add --transport http ${server} ${shellWord(token.mcp_url)}`,
    ...connectionHeaders(token).map(([name, value]) => `--header ${doubleQuoted(`${name}: ${value}`)}`),
  ].join(" ");
}

/** Codex: an entry for ~/.codex/config.toml. JSON string escapes are valid TOML basic strings. */
export function codexConfig(token: AgentToken, server = "space") {
  const headers = connectionHeaders(token).map(([name, value]) => `${JSON.stringify(name)} = ${JSON.stringify(value)}`).join(", ");
  return `[mcp_servers.${server}]\nurl = ${JSON.stringify(token.mcp_url)}\nhttp_headers = { ${headers} }\n`;
}

/** Most other MCP clients (Cursor, Windsurf and others): an mcpServers entry with headers. */
export function mcpJsonConfig(token: AgentToken, server = "space") {
  return JSON.stringify({ mcpServers: { [server]: { url: token.mcp_url, headers: Object.fromEntries(connectionHeaders(token)) } } }, null, 2);
}

/** Parameters: the token's lifetime in seconds. Output: a rounded duration, or null when it does not expire. */
export function tokenLifetime(seconds: number) {
  if (!Number.isFinite(seconds) || seconds <= 0) return null;
  const days = Math.round(seconds / 86400);
  if (days >= 2) return `${days} days`;
  const hours = Math.round(seconds / 3600);
  return hours >= 2 ? `${hours} hours` : `${Math.max(1, Math.round(seconds / 60))} minutes`;
}
