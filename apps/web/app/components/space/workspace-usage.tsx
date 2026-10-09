
import { Fragment, useEffect, useState } from "react";
import { useFeedback } from "@web/app/components/notifications";
import { api, message } from "../../../lib/client";
import { usageRows, type SpaceUsage } from "../../../lib/space/usage";
import { useSpace } from "./provider";

export function WorkspaceUsage() {
  const data = useSpace();
  const [usage, setUsage] = useState<SpaceUsage | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useFeedback({
    error,
    loading: loading && data.space ? "Loading usage…" : undefined,
    clearError: () => setError(""),
  });
  useEffect(() => {
    if (!data.space) return;
    let active = true;
    setLoading(true);
    setError("");
    void api<SpaceUsage>(`spaces/${data.space.id}/usage`).then(
      (value) => {
        if (active) { setUsage(value); setLoading(false); }
      },
      (error) => {
        if (active) { setError(message(error)); setLoading(false); }
      },
    );
    return () => {
      active = false;
    };
  }, [data.space?.id, attempt]);
  return (
    <section>
      <h2>Usage</h2>
      {usage && (
        <dl>
          {usageRows(usage).map((row) => (
            <Fragment key={row.label}>
              <dt>{row.label}</dt>
              <dd>{row.value}</dd>
            </Fragment>
          ))}
        </dl>
      )}
      <p className="sp-muted">
        Storage includes revision history and pending uploads. Earlier files may
        use a conservative size estimate. One extraction or recall can make
        several model requests; failed provider requests also count. The monthly
        count restarts at the start of each month in UTC. Limits apply only when
        the operator of this installation sets them.
      </p>
      <button disabled={loading} onClick={() => setAttempt((n) => n + 1)}>Refresh usage</button>
    </section>
  );
}
