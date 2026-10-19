
import { useState } from "react";
import { useFeedback } from "@web/app/components/notifications";
import { api, message } from "../../../lib/client";
import { archiveZip } from "../../../lib/space/archive-zip";
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
        credentials are excluded. The download is a ZIP holding one JSON file
        with your readable content.
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
            const name = `space-${new Date().toISOString().slice(0, 10)}`;
            const zip = await archiveZip(`${name}.json`);
            let total = 0;
            // Writes the same JSON as one archive object, an entry at a time.
            let after: string | null = null;
            let snapshot: number | undefined;
            do {
              const page: Page = await api(
                `spaces/${data.space.id}/export`,
                "POST",
                { after, snapshot },
              );
              if (snapshot === undefined) {
                const head = JSON.stringify({
                  format: page.format,
                  source_space: page.source_space,
                  snapshot: page.snapshot,
                  exported_at: page.exported_at,
                  ...(page.task_columns ? { task_columns: page.task_columns } : {}),
                });
                zip.write(`${head.slice(0, -1)},"entries":[`);
              }
              for (const entry of page.entries)
                zip.write((total++ ? "," : "") + JSON.stringify(entry));
              setCount(total);
              after = page.next_cursor;
              snapshot = page.snapshot;
            } while (after);
            zip.write("]}");
            const url = URL.createObjectURL(zip.finish());
            const link = document.createElement("a");
            link.href = url;
            link.download = `${name}.zip`;
            link.click();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
            setNotice(`Export ready. Download started with ${total} entries.`);
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
      <p className="sp-muted sp-export-hint">
        A large Space can take several minutes to export. Keep this page open
        until the download starts.
      </p>
    </section>
  );
}
