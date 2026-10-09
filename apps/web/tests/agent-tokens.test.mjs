import assert from "node:assert/strict";
import test from "node:test";
import {
  agentTokenProxyAllowed,
  agentTokenRequest,
  claudeCodeCommand,
  codexConfig,
  connectionHeaders,
  mcpJsonConfig,
  tokenLifetime,
} from "../lib/space/agent-tokens.ts";
import { proxyAllowed } from "../lib/space/proxy-allowlist.ts";

// A synthetic token in the shape POST /v1/agent-tokens returns; it grants nothing.
const agentId = "88888888-8888-4888-8888-888888888888";
const token = {
  access_token: "spc_FixtureToken000000000000",
  token_type: "Bearer",
  expires_in: 7_776_000,
  agent_id: agentId,
  mcp_url: "https://space.example.invalid/mcp/",
  headers: { Authorization: "Bearer spc_FixtureToken000000000000", "X-Space-Agent-ID": agentId },
};

test("the request names the agent in 1 to 80 characters and keeps its choices", () => {
  assert.deepEqual(
    agentTokenRequest({ name: "  Claude Code   laptop ", harness: "claude-code", canWrite: true, agentId, spaceId: "space" }),
    { name: "Claude Code laptop", harness: "claude-code", can_write: true, agent_id: agentId, space_id: "space" },
  );
  assert.equal(agentTokenRequest({ name: "x", harness: "", canWrite: false, agentId: null, spaceId: null }).harness, "other");
  assert.throws(() => agentTokenRequest({ name: "   ", harness: "codex", canWrite: false, agentId: null, spaceId: null }), /Name the agent/);
  assert.throws(() => agentTokenRequest({ name: "x".repeat(81), harness: "codex", canWrite: false, agentId: null, spaceId: null }), /at most 80/);
});

test("only creating a token passes the website proxy", () => {
  assert.equal(agentTokenProxyAllowed("agent-tokens", "POST"), true);
  assert.equal(proxyAllowed("agent-tokens", "POST"), true);
  for (const [method, path] of [["GET", "agent-tokens"], ["DELETE", "agent-tokens"], ["POST", "agent-tokens/x"], ["POST", "agent-tokens/../agents"]])
    assert.equal(proxyAllowed(path, method), false, `${method} ${path}`);
});

test("Claude Code gets one command with every header the API returned", () => {
  assert.equal(
    claudeCodeCommand(token),
    `claude mcp add --transport http space https://space.example.invalid/mcp/ --header "Authorization: Bearer spc_FixtureToken000000000000" --header "X-Space-Agent-ID: ${agentId}"`,
  );
  // Without headers in the response, the bearer token alone is sent.
  assert.equal(
    claudeCodeCommand({ ...token, headers: {} }),
    "claude mcp add --transport http space https://space.example.invalid/mcp/ --header \"Authorization: Bearer spc_FixtureToken000000000000\"",
  );
  // Characters a shell would interpret stay literal.
  const tricky = claudeCodeCommand({ ...token, mcp_url: "https://space.example.invalid/mcp/?a=1&b=$HOME", headers: { Authorization: "Bearer a\"b`c$d" } });
  assert.equal(tricky, 'claude mcp add --transport http space "https://space.example.invalid/mcp/?a=1&b=\\$HOME" --header "Authorization: Bearer a\\"b\\`c\\$d"');
});

test("Codex and JSON clients get the same URL and headers", () => {
  assert.equal(codexConfig(token), [
    "[mcp_servers.space]",
    'url = "https://space.example.invalid/mcp/"',
    `http_headers = { "Authorization" = "Bearer spc_FixtureToken000000000000", "X-Space-Agent-ID" = "${agentId}" }`,
    "",
  ].join("\n"));
  assert.deepEqual(JSON.parse(mcpJsonConfig(token)), {
    mcpServers: { space: { url: token.mcp_url, headers: token.headers } },
  });
  assert.deepEqual(connectionHeaders({ ...token, headers: { Authorization: "", Other: 3 } }), [["Authorization", `Bearer ${token.access_token}`]]);
  // A response that names only the agent header still sends the token first.
  assert.deepEqual(connectionHeaders({ ...token, headers: { "X-Space-Agent-ID": agentId } }), [
    ["Authorization", `Bearer ${token.access_token}`],
    ["X-Space-Agent-ID", agentId],
  ]);
});

test("lifetimes read in days, hours or minutes", () => {
  assert.equal(tokenLifetime(7_776_000), "90 days");
  assert.equal(tokenLifetime(86_400), "24 hours");
  assert.equal(tokenLifetime(5_400), "2 hours");
  assert.equal(tokenLifetime(600), "10 minutes");
  assert.equal(tokenLifetime(0), null);
  assert.equal(tokenLifetime(Number.NaN), null);
});
