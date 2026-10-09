// The instructions an agent needs to connect with a six-digit code. The agent calls
// the API origin directly; the website only serves the person's side of the flow.
export function deviceConnectPrompt(
  mcpUrl: string,
  siteOrigin: string,
  // What is already known about the agent, so the prompt needs no editing.
  agent: { name?: string | null; harness?: string | null } = {},
) {
  const api = new URL(mcpUrl).origin;
  const name = agent.name?.trim() || "<your product name>";
  const harness =
    agent.harness && agent.harness !== "other"
      ? agent.harness
      : "<one of codex, claude-code, cursor, claude, chatgpt, gemini, hermes, opencode, cline, devin, other>";
  return [
    "Connect to my Space MCP server with a device code. Do not open a browser and do not wait for a redirect.",
    `1. POST ${api}/v1/device/code with the JSON body ${JSON.stringify({ client_name: name, harness })}. The response has user_code, device_code, verification_url, interval and expires_in.`,
    `2. Show me the user_code and ask me to enter it at ${siteOrigin}/connect. Never show or send the device_code anywhere.`,
    `3. Every interval seconds, POST ${api}/v1/device/token with the JSON body {"device_code":"<device_code>"}. HTTP 400 with error authorization_pending means keep waiting; slow_down means wait longer; access_denied or expired_token means stop and tell me.`,
    `4. On HTTP 200, add the MCP server at mcp_url (${mcpUrl}) with the returned headers: Authorization: Bearer <access_token> and X-Space-Agent-ID: <agent_id>. Store the token only in the MCP configuration. Then call the server_info and workspace_info tools to confirm the connection.`,
  ].join("\n");
}
