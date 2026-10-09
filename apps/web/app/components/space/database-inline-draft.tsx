import { useRef, useState } from "react";
import { message } from "../../../lib/client";
import { useFeedback } from "../notifications";

// Parameters: create(id, name) saves one record; close ends the draft; afterTab focuses the new record.
// What it does: types a record name in place. Enter saves and keeps the draft open for the next one,
// Tab saves and moves into the record, Escape or leaving an empty draft cancels. A failed save keeps
// the text and retries with the same record ID, and repeated submits write once.
// Output: a single-line inline input with its status.
export function InlineRecordDraft({ create, close, afterTab, label = "New record name", hint = true }: {
  create: (id: string, name: string) => Promise<void>;
  close: () => void;
  afterTab?: (id: string) => void;
  label?: string;
  hint?: boolean;
}) {
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  // The database owns the shared saving indicator for all pending record changes.
  useFeedback({ error });
  const recordId = useRef(crypto.randomUUID());
  const saving = useRef(false);
  const input = useRef<HTMLInputElement>(null);

  async function submit(then: "next" | "close" | "tab") {
    const value = name.trim();
    if (saving.current) return;
    if (!value) {
      if (then !== "next") close();
      return;
    }
    saving.current = true;
    setBusy(true);
    setError("");
    const id = recordId.current;
    try {
      await create(id, value);
      recordId.current = crypto.randomUUID();
      setName("");
      if (then === "next") input.current?.focus();
      else {
        close();
        if (then === "tab") afterTab?.(id);
      }
    } catch (cause) {
      setError(message(cause));
      input.current?.focus();
    } finally {
      saving.current = false;
      setBusy(false);
    }
  }

  return (
    <div className={`sp-db-draft${error ? " has-error" : ""}`} aria-busy={busy}>
      <input
        ref={input}
        autoFocus
        aria-label={label}
        aria-invalid={!!error || undefined}
        placeholder="Type a name"
        maxLength={200}
        value={name}
        readOnly={busy}
        onChange={(event) => { setName(event.target.value); setError(""); }}
        onKeyDown={(event) => {
          if (event.key === "Enter") { event.preventDefault(); void submit("next"); }
          else if (event.key === "Tab" && !event.shiftKey && name.trim()) { event.preventDefault(); void submit("tab"); }
          else if (event.key === "Escape") { event.preventDefault(); if (!saving.current) close(); }
        }}
        onBlur={() => { if (!error && !saving.current) void submit("close"); }}
      />
      {hint && <span className="sp-db-draft-hint" aria-hidden="true">Enter to add · Esc to cancel</span>}
    </div>
  );
}
