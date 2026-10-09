import { useEffect, useState } from "react";
import { api, message } from "../../../lib/client";

type Match = {
  id: string;
  kind: "document" | "task" | "project" | "database" | "memory" | "category" | "section";
  title: string;
  description: string;
  status?: "todo" | "in_progress" | "done";
  updated_at?: string;
};
type Page = {
  items: Match[];
  next_cursor: string | null;
  snapshot: number;
  scanned: number;
};
const labels = {
  document: "Documents",
  task: "Tasks",
  project: "Projects",
  database: "Databases",
  memory: "Memory",
  category: "Categories",
  section: "Sections",
};

export function useWorkspaceSearch(
  spaceId: string | undefined,
  base: string,
  query: string,
  enabled: boolean,
  project?: string,
) {
  const [state, setState] = useState({
    items: [] as Match[],
    loading: false,
    error: "",
    scanned: 0,
  });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!enabled || !spaceId) return;
    let active = true;
    setState({ items: [], loading: true, error: "", scanned: 0 });
    const timer = setTimeout(async () => {
      let after: string | null = null;
      let snapshot: number | undefined;
      const items: Match[] = [];
      let scanned = 0;
      try {
        do {
          const page: Page = await api(`spaces/${spaceId}/search`, "POST", {
            query,
            after,
            snapshot,
            project,
          });
          if (!active) return;
          items.push(...page.items.filter(item => item.kind !== "section"));
          scanned += page.scanned;
          after = page.next_cursor;
          snapshot = page.snapshot;
          setState({ items: [...items], loading: !!after, error: "", scanned });
        } while (after && active);
      } catch (e) {
        if (active)
          setState({ items: [], loading: false, error: message(e), scanned });
      }
    }, 300);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [spaceId, query, enabled, project, attempt]);
  return {
    ...state,
    items: state.items.map((item) => ({
      ...item,
      type: labels[item.kind],
      href: item.kind === "category" || item.kind === "section" ? `${base}/memory` : `${base}/${item.kind === "memory" ? "memory" : item.kind === "database" ? "databases" : item.kind === "project" ? "projects" : item.kind === "task" ? "tasks" : "documents"}/${encodeURIComponent(item.id)}`,
    })),
    retry: () => setAttempt((n) => n + 1),
  };
}
