// A self-hosted Space serves no documentation pages of its own: in-app help opens the
// guides kept with the source code, which match the version an operator installed.
const repository = "https://github.com/Hugo-SEQUIER/getspace";
const guide = (file: string) => `${repository}/blob/main/docs/${file}`;

export const docsLinks = {
  index: `${repository}/tree/main/docs`,
  selfHosting: guide("SELF_HOSTING.md"),
  connectingAgents: guide("CONNECTING_AGENTS.md"),
  memory: guide("MEMORY.md"),
} as const;

// Space is open source under the MIT License. Every screen a visitor can reach links here.
export const sourceCode = { href: repository, label: "Source code (MIT)" } as const;
