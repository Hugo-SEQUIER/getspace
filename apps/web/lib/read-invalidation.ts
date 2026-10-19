export function affectedReads(path: string) {
  const match = /^spaces\/([^/]+)\/(.+)/.exec(path);
  if (match) {
    const [, space, command] = match;
    // Queuing work does not change content until the worker completes it.
    if (command === "memory/jobs" || command.startsWith("memory/imports")) return () => false;
    // API keys are credentials, not content; their list is read uncached.
    if (command.startsWith("api-keys")) return () => false;
    if (command === "tasks/columns")
      return (read: string) => read === `spaces/${space}/tasks/columns`;
    // Seen marks are personal state: they change nothing else, and nothing else changes them.
    if (command === "views")
      return (read: string) => read === `spaces/${space}/views`;
    // The search index holds every kind of content, so each content write refreshes it.
    const groups = /^tasks\/[^/]+\/status$/.test(command) ? ["resources", "memories", "overview", "search"]
      : command.startsWith("items") ? ["items", "memories", "overview", "search"]
      : /^(resources|handoffs|memory\/)/.test(command) ? ["resources", "memories", "search"] : null;
    return (read: string) => read.startsWith(`spaces/${space}/`)
      && (!groups || groups.includes(read.slice(`spaces/${space}/`.length).split(/[/?]/)[0]));
  }
  // A new connection token adds a connection, and may add the identity it belongs to.
  if (/^agent-tokens(\/|\?|$)/.test(path))
    return (read: string) => /^(agents|agent-identities)(\/|\?|$)/.test(read);
  if (/^(agents|agent-identities)(\/|\?|$)/.test(path)) {
    const workspace = new URL(path, "https://cache.invalid/").searchParams.get("workspace_id");
    return (read: string) => /^(agents|agent-identities)(\/|\?|$)/.test(read)
      && new URL(read, "https://cache.invalid/").searchParams.get("workspace_id") === workspace;
  }
  return () => true;
}
