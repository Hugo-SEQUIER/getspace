import { useEffect, useMemo, useState } from "react";
import { message, watchApi } from "../../../lib/client";
import type { WorkspaceDatabase } from "../../../lib/space/databases";
import type { MemoryVector, Project, Resource, Status } from "../../../lib/space/types";

type Kind = "document" | "task" | "project" | "database" | "memory" | "category" | "section";
export type Entry = {
  id: string;
  kind: Kind;
  title: string;
  text: string;
  status?: Status;
  updated_at?: string;
  project?: string | null;
  resource_id?: string;
};

export type LocalSource = {
  resources: Resource[];
  projects?: Project[];
  databases?: WorkspaceDatabase[];
  memories?: MemoryVector[];
};

/**
 * What the workspace already holds in memory, with the same ids the index uses. Titles
 * match at once while the full-text index is still loading; bodies arrive with it.
 */
export function localEntries({ resources, projects = [], databases = [], memories = [] }: LocalSource): Entry[] {
  return [
    ...resources.filter((r) => r.recall?.category !== "memory").map((r) => ({
      id: r.id, kind: r.kind, title: r.title, text: "", status: r.status,
      updated_at: r.updated_at, project: r.project ?? null,
    })),
    ...projects.map((p) => ({ id: p.id, kind: "project" as const, title: p.name, text: p.description ?? "" })),
    ...databases.map((d) => ({ id: d.id, kind: "database" as const, title: d.name, text: d.description ?? "" })),
    ...memories.map((m) => ({
      id: m.id, kind: "memory" as const, title: m.value, text: m.sourceTitle ?? "", resource_id: m.memoryId,
    })),
  ];
}
type Match = Omit<Entry, "text"> & { description: string };
type Index = { items: Entry[] };
const labels = {
  document: "Documents",
  task: "Tasks",
  project: "Projects",
  database: "Databases",
  memory: "Memory",
  category: "Categories",
  section: "Sections",
};

/** Lowercased once per index load, so each keystroke only scans strings in memory. */
function prepare(items: Entry[]) {
  return items
    .filter((item) => item.kind !== "section")
    .map((item) => ({ item, haystack: `${item.title}\n${item.text}`.toLowerCase(), text: item.text.toLowerCase() }));
}

function match({ item, text }: ReturnType<typeof prepare>[number], needle: string): Match {
  const { text: body, ...rest } = item;
  const start = needle ? Math.max(0, text.indexOf(needle) - 50) : 0;
  return { ...rest, description: body.slice(start, start + 180) };
}

/**
 * The Space's search index loads once per Space version through the shared read cache,
 * which revalidates with the small read-version request. Typing never calls the backend.
 * Until the index arrives, `fallback` entries (see localEntries) answer by title, so a
 * first search is never blank while every body downloads.
 */
export function useWorkspaceSearch(
  spaceId: string | undefined,
  base: string,
  query: string,
  enabled: boolean,
  project?: string,
  fallback?: Entry[],
) {
  const [state, setState] = useState({ index: null as Entry[] | null, error: "" });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!enabled || !spaceId) return;
    // A cached index for this Space is emitted synchronously by the subscription below.
    setState({ index: null, error: "" });
    return watchApi<Index>(
      `spaces/${spaceId}/search/index`,
      (value) => setState({ index: value.items, error: "" }),
      (error) => setState((current) => ({ ...current, error: message(error) })),
      true,
    );
  }, [spaceId, enabled, attempt]);
  const prepared = useMemo(() => prepare(state.index ?? fallback ?? []), [state.index, fallback]);
  const items = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return prepared
      .filter((entry) => (!project || entry.item.project === project) && (!needle || entry.haystack.includes(needle)))
      .map((entry) => match(entry, needle))
      .map((item) => ({
        ...item,
        type: labels[item.kind],
        href: item.kind === "category" || item.kind === "section" ? `${base}/memory` : `${base}/${item.kind === "memory" ? "memory" : item.kind === "database" ? "databases" : item.kind === "project" ? "projects" : item.kind === "task" ? "tasks" : "documents"}/${encodeURIComponent(item.id)}`,
      }));
  }, [prepared, query, project, base]);
  return {
    items: enabled ? items : [],
    loading: enabled && !!spaceId && state.index === null && !state.error,
    error: enabled ? state.error : "",
    retry: () => setAttempt((n) => n + 1),
  };
}
