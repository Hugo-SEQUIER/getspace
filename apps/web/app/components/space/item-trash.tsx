
import { useEffect, useState } from "react";
import { message } from "../../../lib/client";
import { useFeedback } from "../notifications";
import { useSpace } from "./provider";

export function ItemTrash({ kind }: { kind: "project" | "database" }) {
  const data = useSpace();
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useFeedback({ error, loading: busy ? `Restoring ${kind}…` : loading ? "Loading trash…" : null, clearError: () => setError("") });
  useEffect(() => {
    if (!open) return;
    setLoading(true);
    setError("");
    return data.watchItems(kind, true, () => { setLoading(false); setError(""); }, e => {
      setLoading(false); setError(message(e));
    });
  }, [open, kind, data.watchItems, attempt]);
  const items = data.trashedItems.filter((item) => item.kind === kind);
  return (
    <details className="sp-page" aria-label={`${kind} trash`} onToggle={event => setOpen(event.currentTarget.open)}>
      <summary>Trash</summary>
      {open && <>
      <p className="sp-muted">Restore a {kind} with its saved contents.</p>
      {!loading && !error && !items.length && <p>Trash is empty.</p>}
      {error && (
        <p>
          <button onClick={() => setAttempt(value => value + 1)}>Retry</button>
        </p>
      )}
      {items.map((item) => (
        <div className="sp-project-resource" key={item.id}>
          <span>{item.name}</span>
          <button
            disabled={!!busy}
            onClick={async () => {
              setBusy(item.id);
              setError("");
              try {
                await data.setItemDeleted(item, false);
              } catch (e) {
                setError(message(e));
              } finally {
                setBusy("");
              }
            }}
          >
            {busy === item.id ? "Restoring…" : "Restore"}
          </button>
        </div>
      ))}
      </>}
    </details>
  );
}
