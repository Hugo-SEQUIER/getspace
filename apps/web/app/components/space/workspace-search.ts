import { useEffect, useMemo, useState } from "react";
import { message, watchApi } from "../../../lib/client";

type Kind = "document" | "task" | "project" | "database" | "memory" | "category" | "section";
type Entry = {
  id: string;
  kind: Kind;
  title: string;
  text: string;
  status?: "todo" | "in_progress" | "done";
  updated_at?: string;
  project?: string | null;
  resource_id?: string;
};
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
 */
export function useWorkspaceSearch(
  spaceId: string | undefined,
  base: string,
  query: string,
  enabled: boolean,
  project?: string,
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
  const prepared = useMemo(() => prepare(state.index ?? []), [state.index]);
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
