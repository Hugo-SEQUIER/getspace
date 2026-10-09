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

// Space is licensed under the AGPL-3.0, whose section 13 asks that people who use it over a
// network can get its source. Every screen a visitor can reach links here.
export const sourceCode = { href: repository, label: "Source code (AGPL-3.0)" } as const;
