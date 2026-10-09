import Link from "@web/lib/navigation";
import { useRef, useState } from "react";
import { message } from "../../../lib/client";
import { createDatabaseSaveQueue } from "../../../lib/space/database-save-queue";
import type { WorkspaceDatabase } from "../../../lib/space/databases";
import { useFeedback } from "../notifications";
import { saveRecovery } from "./database-save-recovery";
import { useSpace } from "./provider";
import { Icon } from "./ui-icon";
import "./project-databases.css";

const byName = (a: WorkspaceDatabase, b: WorkspaceDatabase) => a.name.localeCompare(b.name, "en", { sensitivity: "base" });

/**
 * Parameters: the workspace route base and the open project's ID.
 * What it does: lists the databases linked to the project, and links or unlinks one by saving
 * its project placement, the same field agents set and the Space map reads.
 * Output: the project's Databases section.
 */
export function ProjectDatabases({ base, projectId }: { base: string; projectId: string }) {
  const data = useSpace();
  const queue = useRef(createDatabaseSaveQueue(saveRecovery));
  const [saving, setSaving] = useState("");
  const [error, setError] = useState("");
  useFeedback({ error, loading: saving ? "Saving database link…" : "", clearError: () => setError("") });
  const live = data.databases.filter((database) => !database.deleted);
  const linked = live.filter((database) => database.project_id === projectId).sort(byName);
  const others = live.filter((database) => database.project_id !== projectId).sort(byName);
  const projectName = (id?: string | null) => data.projects.find((project) => project.id === id)?.name;

  async function place(database: WorkspaceDatabase, project: string | null) {
    setError("");
    setSaving(database.id);
    try {
      await queue.current(database, (current) => ({ ...current, project_id: project }), data.saveDatabase, data.latestDatabase);
    } catch (e) {
      setError(`Could not ${project ? "link" : "unlink"} “${database.name}”: ${message(e)}`);
    } finally {
      setSaving("");
    }
  }

  return (
    <section className="sp-home-section sp-project-databases" aria-label="Project databases">
      <div className="sp-project-databases-heading">
        <h2>Databases</h2>
        {data.canWrite && others.length > 0 && (
          <select
            aria-label="Link a database"
            value=""
            disabled={!!saving}
            onChange={(e) => {
              const database = others.find((item) => item.id === e.target.value);
              if (database) void place(database, projectId);
            }}
          >
            <option value="">Link a database…</option>
            {others.map((database) => (
              <option key={database.id} value={database.id}>
                {database.name}
                {projectName(database.project_id) ? ` (moves from ${projectName(database.project_id)})` : ""}
              </option>
            ))}
          </select>
        )}
      </div>
      {linked.map((database) => (
        <div className="sp-project-resource sp-project-database" key={database.id} aria-busy={saving === database.id}>
          <Link href={`${base}/databases/${database.id}`}>
            <Icon name="database" size={18} />
            {database.name}
          </Link>
          {database.tasks && <span className="sp-tag">Task list</span>}
          <span className="sp-muted">
            {database.records.length} {database.records.length === 1 ? "record" : "records"}
          </span>
          {data.canWrite && (
            <button
              type="button"
              className="text-button"
              aria-label={`Unlink ${database.name}`}
              disabled={!!saving}
              onClick={() => void place(database, null)}
            >
              Unlink
            </button>
          )}
        </div>
      ))}
      {!linked.length && (
        <p className="sp-muted">
          {data.databaseReady ? "No database is linked to this project yet." : "Loading databases…"}
        </p>
      )}
    </section>
  );
}
