import type { AgentIdentity, Grant } from "./types";

export const harnesses = [
  { id: "codex", name: "Codex", logo: "codex.svg" },
  { id: "claude-code", name: "Claude Code", logo: "claude-code.svg" },
  { id: "hermes", name: "Hermes", logo: "hermes-portrait.png" },
  { id: "claude", name: "Claude", logo: "claude.svg" },
  { id: "chatgpt", name: "ChatGPT", logo: "chatgpt.svg" },
  { id: "cursor", name: "Cursor", logo: "cursor.svg" },
  { id: "gemini", name: "Gemini", logo: "gemini.svg" },
  { id: "pi", name: "Pi", logo: "pi.svg" },
  { id: "openclaw", name: "OpenClaw", logo: "openclaw.svg" },
  { id: "prime-agent", name: "PrimeAgent", logo: "prime-agent.svg" },
  { id: "opencode", name: "OpenCode", logo: "opencode.svg" },
  { id: "cline", name: "Cline", logo: "cline.svg" },
  { id: "devin", name: "Devin", logo: "devin.svg" },
  { id: "factory", name: "Factory (Droid)", logo: "factory.svg" },
  { id: "grok", name: "Grok", logo: "grok.svg" },
  { id: "perplexity", name: "Perplexity", logo: "perplexity.svg" },
  { id: "deepseek", name: "DeepSeek", logo: "deepseek.svg" },
  { id: "zai", name: "Z.ai", logo: "zai.svg" },
  { id: "meta", name: "Meta", logo: "meta.svg" },
  { id: "minimax", name: "MiniMax", logo: "minimax.svg" },
  { id: "kimi", name: "Kimi", logo: "kimi.svg" },
  { id: "other", name: "Other", logo: null },
] as const;

export function harnessLogo(id: string) {
  const logo = harnesses.find((h) => h.id === id)?.logo;
  return logo ? `/integrations/${logo}` : null;
}

export function harnessName(id: string) {
  return harnesses.find((h) => h.id === id)?.name ?? "Other";
}

export function groupAgentConnections(
  agents: AgentIdentity[],
  grants: Grant[],
) {
  const known = new Set(agents.map((agent) => agent.id));
  return [
    ...agents.map((agent) => ({
      id: agent.id,
      agent,
      connections: grants.filter((g) => g.agent_id === agent.id),
    })),
    ...grants
      .filter((g) => !g.agent_id || !known.has(g.agent_id))
      .map((connection) => ({
        id: `connection:${connection.id}`,
        agent: null,
        connections: [connection],
      })),
  ];
}
