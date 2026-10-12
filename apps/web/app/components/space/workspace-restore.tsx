
import { useEffect, useRef, useState } from "react";
import { useFeedback } from "@web/app/components/notifications";
import { api, message } from "../../../lib/client";
import { readArchive } from "../../../lib/space/archive-zip";
import { useSpace } from "./provider";

type Choices = Record<string, "keep" | "copy" | "replace">;
type Review = {
  snapshot: number;
  issues: string[];
  items: {
    key: string;
    title: string;
    entity: string;
    action: string;
    conflict: boolean;
    incoming_revisions: number;
    existing_revision: number | null;
  }[];
};
type Receipt = {
  id: string;
  state: string;
  result: {
    resources: number;
    items: number;
    agents: number;
    revisions: number;
  } | null;
};
type Upload = {
  id: string;
  text: string;
  sha256: string;
  bytes: number;
  parts: string[];
};

export function WorkspaceRestore() {
  const data = useSpace();
  const path = data.space ? `spaces/${data.space.id}/restores` : "";
  const [id, setId] = useState("");
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [pendingLoading, setPendingLoading] = useState(false);
  const [review, setReview] = useState<Review | null>(null);
  const [choices, setChoices] = useState<Choices>({});
  const [receipt, setReceipt] = useState<Receipt | null>(null);
  const [pending, setPending] = useState<{ id: string; state: string }[]>([]);
  const upload = useRef<Upload | null>(null);
  useFeedback({
    error,
    notice,
    loading: busy ? progress || "Checking restore…" : receipt?.state === "publishing" ? "Publishing restored items…" : pendingLoading ? "Loading pending restores…" : undefined,
    clearError: () => setError(""),
    clearNotice: () => setNotice(""),
  });

  async function pendingRestores() {
    if (!path) return;
    setPendingLoading(true);
    try {
      setPending((await api<{ items: typeof pending }>(path)).items);
    } finally {
      setPendingLoading(false);
    }
  }
  useEffect(() => {
    void pendingRestores().catch((e) => setError(message(e)));
  }, [path]);

  async function loadReview(
    selectedId: string,
    nextChoices: Choices = choices,
  ) {
    const next = await api<Review>(`${path}/${selectedId}/review`, "POST", {
      choices: nextChoices,
    });
    setId(selectedId);
    setChoices(nextChoices);
    setReview(next);
    setReceipt(null);
  }
  async function sendUpload() {
    if (!upload.current) return;
    const current = upload.current;
    setBusy(true);
    setError("");
    setNotice("");
    setProgress("Preparing archive upload…");
    setReceipt(null);
    try {
      await api(path, "POST", {
        id: current.id,
        sha256: current.sha256,
        bytes: current.bytes,
        parts: current.parts.length,
      });
      setId(current.id);
      for (const [part, text] of current.parts.entries()) {
        setProgress(`Uploading ${part + 1} of ${current.parts.length} parts…`);
        await api(`${path}/${current.id}/parts/${part}`, "POST", { text });
      }
      setProgress("Validating the archive and checking conflicts…");
      await loadReview(current.id, {});
      upload.current = null;
      await pendingRestores();
      setNotice("Archive ready for review.");
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
      setProgress("");
    }
  }
  async function checkResult(selectedId = id) {
    const result = await api<Receipt>(`${path}/${selectedId}`);
    setId(selectedId);
    setReceipt(result);
    if (result.state === "committed") {
      // Reloading the Space below replaces this page and its state, so the app-wide
      // notice carries the confirmation.
      if (receipt?.id !== result.id || receipt.state !== "committed")
        data.setNotice(
          result.result
            ? `Archive restored: ${result.result.resources} documents or memories, ${result.result.items} projects, databases or other items, ${result.result.agents} agent identities and ${result.result.revisions} archived revisions.`
            : "Archive restored.",
        );
      setReview(null);
      setError("");
      upload.current = null;
      await data.initialize();
      await pendingRestores();
    }
    return result;
  }

  return (
    <section>
      <h2>Restore an archive</h2>
      <p className="sp-muted">
        Upload a complete Space export, then review conflicts. Keep existing
        items, restore separate copies, or append the archived versions and make
        the archive’s latest version current. Existing history is retained.
        Existing memory edits and forget choices are preserved.
      </p>
      <label className="sp-field">
        Space archive
        <input
          type="file"
          accept=".zip,.json,application/zip,application/json"
          disabled={busy}
          onChange={async (e) => {
            const file = e.target.files?.[0];
            e.target.value = "";
            if (!file) return;
            setBusy(true);
            setError("");
            setNotice("");
            setProgress("Reading your archive…");
            setReview(null);
            setReceipt(null);
            try {
              const text = await readArchive(file, 200 * 1024 * 1024);
              if (JSON.parse(text).format !== "space.archive.v1")
                throw new Error(
                  "Choose a complete Space archive. Sample exports and document imports use different formats.",
                );
              const encoded = new TextEncoder().encode(text);
              const digest = await crypto.subtle.digest("SHA-256", encoded);
              const sha256 = Array.from(new Uint8Array(digest), (byte) =>
                byte.toString(16).padStart(2, "0"),
              ).join("");
              const parts: string[] = [];
              for (let start = 0; start < text.length; ) {
                let end = Math.min(text.length, start + 180000);
                const last = text.charCodeAt(end - 1);
                if (end < text.length && last >= 0xd800 && last <= 0xdbff)
                  end--;
                parts.push(text.slice(start, end));
                start = end;
              }
              upload.current = {
                id: crypto.randomUUID(),
                text,
                sha256,
                bytes: encoded.length,
                parts,
              };
              await sendUpload();
            } catch (e) {
              setError(message(e));
            } finally {
              setBusy(false);
              setProgress("");
            }
          }}
        />
      </label>
      {upload.current && !busy && (
        <button onClick={() => void sendUpload()}>
          Retry upload and review
        </button>
      )}
      {review && (
        <>
          <h3>Review merge</h3>
          <p>
            {review.items.filter((item) => item.action !== "keep").length} items
            will be restored.{" "}
            {review.items.filter((item) => item.conflict).length} existing items
            need a choice.
          </p>
          <div className="sp-table-wrap">
            <table className="sp-table">
              <thead>
                <tr>
                  <th>Archived item</th>
                  <th>Versions</th>
                  <th>Merge choice</th>
                </tr>
              </thead>
              <tbody>
                {review.items.map((item) => (
                  <tr key={item.key}>
                    <td>
                      {item.title}
                      <small>{item.entity}</small>
                    </td>
                    <td>
                      {item.incoming_revisions}
                      {item.conflict && ` · current v${item.existing_revision}`}
                    </td>
                    <td>
                      {item.conflict ? (
                        <select
                          aria-label={`Merge ${item.title}`}
                          disabled={busy}
                          value={choices[item.key] ?? "keep"}
                          onChange={async (e) => {
                            const next = {
                              ...choices,
                              [item.key]: e.target.value as Choices[string],
                            };
                            setBusy(true);
                            setError("");
                            setNotice("");
                            setProgress("Updating restore review…");
                            try {
                              await loadReview(id, next);
                            } catch (e) {
                              setError(message(e));
                              setReview(null);
                            } finally {
                              setBusy(false);
                              setProgress("");
                            }
                          }}
                        >
                          <option value="keep">Keep existing</option>
                          <option value="copy">Restore separate copy</option>
                          <option value="replace">
                            Use archive as current
                          </option>
                        </select>
                      ) : item.action === "keep" ? (
                        "Reuse existing identity"
                      ) : (
                        "Restore new item"
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {review.issues.map((issue) => (
            <p className="sp-alert" role="alert" key={issue}>
              {issue}
            </p>
          ))}
          <div className="sp-actions">
            <button
              disabled={busy}
              onClick={async () => {
                setBusy(true);
                setError("");
                setNotice("");
                setProgress("Refreshing restore review…");
                try {
                  await loadReview(id);
                } catch (e) {
                  setError(message(e));
                } finally {
                  setBusy(false);
                  setProgress("");
                }
              }}
            >
              Refresh review
            </button>
            <button
              className="sp-primary"
              disabled={busy || !!review.issues.length}
              onClick={async () => {
                setBusy(true);
                setError("");
                setNotice("");
                setProgress("Restoring the reviewed items…");
                try {
                  const result = await api<Receipt>(
                    `${path}/${id}/commit`,
                    "POST",
                    { snapshot: review.snapshot, choices },
                  );
                  setReceipt(result);
                  await checkResult();
                } catch (e) {
                  setError(message(e));
                  try {
                    await checkResult();
                  } catch {
                    /* Keep the original failure and offer status recovery. */
                  }
                } finally {
                  setBusy(false);
                  setProgress("");
                }
              }}
            >
              Apply reviewed merge
            </button>
          </div>
        </>
      )}
      {receipt?.state === "committed" && receipt.result && (
        <p>
          Restored {receipt.result.resources} documents or memories,{" "}
          {receipt.result.items} projects, databases, categories or legacy organization items, and{" "}
          {receipt.result.agents} agent identities. {receipt.result.revisions}{" "}
          archived revisions were saved.
        </p>
      )}
      {receipt?.state === "publishing" && (
        <p className="sp-muted">
          Your merge is still publishing. Check its result before starting
          another restore.
        </p>
      )}
      {!!pending.length && (
        <details>
          <summary>Pending restores ({pending.length})</summary>
          {pending.map((item) => (
            <div className="sp-actions" key={item.id}>
              <span>
                {item.id.slice(0, 8)} · {item.state}
              </span>
              <button
                disabled={busy}
                onClick={async () => {
                  setBusy(true);
                  setError("");
                  setNotice("");
                  setProgress("Checking restore result…");
                  try {
                    const result = await checkResult(item.id);
                    if (result.state === "staged")
                      await loadReview(item.id, {});
                  } catch (e) {
                    setError(message(e));
                  } finally {
                    setBusy(false);
                    setProgress("");
                  }
                }}
              >
                Check / review
              </button>
              <button
                disabled={busy}
                onClick={async () => {
                  setBusy(true);
                  setError("");
                  setNotice("");
                  setProgress("Canceling restore…");
                  try {
                    await api(`${path}/${item.id}`, "DELETE");
                    if (item.id === id) {
                      setReview(null);
                      setReceipt(null);
                      upload.current = null;
                      setId("");
                    }
                    await pendingRestores();
                    setNotice("Restore canceled.");
                  } catch (e) {
                    setError(message(e));
                  } finally {
                    setBusy(false);
                    setProgress("");
                  }
                }}
              >
                Cancel restore
              </button>
            </div>
          ))}
        </details>
      )}
      <p className="sp-muted">
        Uploads are encrypted and expire after 24 hours. Up to 200 MiB and
        10,000 retained revisions per archive.
      </p>
    </section>
  );
}
