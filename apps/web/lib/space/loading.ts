export type WorkspaceRead = "document" | "task" | "project" | "database" | "category" | "section" | "folder";

/** Only these lists determine whether a page can display its main content. */
export function workspaceReadPlan(pathname: string) {
  const [, , view = "home", id] = pathname.split("/");
  const required: WorkspaceRead[] = view === "home" ? ["document", "task"]
    : view === "documents" && !id ? ["document"]
    : view === "tasks" ? ["task"]
    : view === "projects" ? ["project"]
    : view === "databases" ? ["database"]
    : [];
  // Sidebar shortcuts and the attention badge still populate, but never gate a page or
  // its own detail read.
  const supporting: WorkspaceRead[] = ["document", "task", "project", "database", "folder"];
  if (view === "documents") supporting.push("section");
  if (view === "projects") supporting.push("section");
  if (view === "memory") supporting.push("category");
  return { required, supporting: supporting.filter(kind => !required.includes(kind)) };
}

export function workspaceReadPath(spaceId: string, kind: WorkspaceRead) {
  return kind === "document" || kind === "task"
    ? `spaces/${spaceId}/resources?kind=${kind}`
    : `spaces/${spaceId}/items?kind=${kind}&deleted=false`;
}
