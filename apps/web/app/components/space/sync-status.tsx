
import { useEffect, useState } from "react";
import { watchReadStatus } from "../../../lib/client";
import styles from "./sync-status.module.css";

export function SyncStatus() {
  const [status, setStatus] = useState<"idle" | "syncing" | "stale">("idle");
  useEffect(() => watchReadStatus(setStatus), []);
  return <div className={styles.status} role="status" aria-live="polite">
    {status === "syncing" ? "Updating saved content…"
      : status === "stale" ? "Showing saved content · updates unavailable" : ""}
  </div>;
}
