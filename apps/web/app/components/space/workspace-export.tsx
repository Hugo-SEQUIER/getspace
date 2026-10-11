
import { useState } from "react";
import { useFeedback } from "@web/app/components/notifications";
import { api, message } from "../../../lib/client";
import { useSpace } from "./provider";
import { Icon } from "./ui-icon";
import type { TaskColumn } from "../../../lib/space/types";

type Page = {
  format: string;
  source_space: string;
  snapshot: number;
  exported_at: string;
  entries: unknown[];
  task_columns?: TaskColumn[];
  next_cursor: string | null;
};

export function WorkspaceExport() {
  const data = useSpace();
  const [busy, setBusy] = useState(false);
  const [count, setCount] = useState(0);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  useFeedback({
    error,
    notice,
    loading: busy ? `Preparing export… ${count} entries` : undefined,
    clearError: () => setError(""),
    clearNotice: () => setNotice(""),
  });
  return (
    <section>
      <h2>Export your Space</h2>
      <p className="sp-muted">
        Download all documents, tasks, memory, projects, databases, categories and agent
        identities, including retained content revisions and trash. Connection
        credentials are excluded. The downloaded JSON contains your readable
        content.
      </p>
      <button
        disabled={busy || !data.space}
        onClick={async () => {
          if (!data.space) return;
          setBusy(true);
          setCount(0);
          setError("");
          setNotice("");
          try {
            let after: string | null = null;
            let snapshot: number | undefined;
            const entries: unknown[] = [];
            let bytes = 0;
            let first: Page | undefined;
            do {
              const page: Page = await api(
                `spaces/${data.space.id}/export`,
                "POST",
                { after, snapshot },
              );
              first ??= page;
              bytes += new TextEncoder().encode(
                JSON.stringify(page.entries),
              ).length;
              if (bytes > 200 * 1024 * 1024)
                throw new Error(
                  "This archive exceeds the browser's 200 MiB limit. Contact the installation operator for a full export.",
                );
              entries.push(...page.entries);
              setCount(entries.length);
              after = page.next_cursor;
              snapshot = page.snapshot;
            } while (after);
            const archive: Omit<Page, "next_cursor"> = {
              format: first.format,
              source_space: first.source_space,
              snapshot: first.snapshot,
              exported_at: first.exported_at,
              ...(first.task_columns ? { task_columns: first.task_columns } : {}),
              entries,
            };
            const url = URL.createObjectURL(
              new Blob([JSON.stringify(archive)], { type: "application/json" }),
            );
            const link = document.createElement("a");
            link.href = url;
            link.download = `space-${new Date().toISOString().slice(0, 10)}.json`;
            link.click();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
            setNotice(`Export ready. Download started with ${archive.entries.length} entries.`);
          } catch (e) {
            setError(message(e));
          } finally {
            setBusy(false);
          }
        }}
      >
        <Icon name="download" size={15} />
        {busy
          ? `Preparing export… ${count} entries`
          : "Export complete Space"}
      </button>
    </section>
  );
}
