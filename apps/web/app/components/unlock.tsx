import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { api, ApiError, message } from "../../lib/client";
import { docsLinks, sourceCode } from "../../lib/docs-links";
import "../auth-design.css";

// Parameters: what to do once this browser is unlocked.
// What it does: asks for the installation's access key, which the server checks and exchanges
// for a signed session cookie. The key itself is never stored in the browser.
// Output: the "Unlock this Space" card shown wherever a locked browser needs the workspace.
export function Unlock({ onUnlocked }: { onUnlocked: () => void }) {
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [unencrypted, setUnencrypted] = useState(false);
  const titleId = useId();
  const errorId = useId();
  const unlocked = useRef(onUnlocked);
  unlocked.current = onUnlocked;
  useEffect(() => {
    // Loopback addresses never leave the machine, so plain HTTP is only a risk elsewhere.
    setUnencrypted(window.location.protocol === "http:" && !["localhost", "127.0.0.1", "[::1]"].includes(window.location.hostname));
    // Another tab of this browser may unlock the Space meanwhile: coming back here notices it.
    let active = true;
    const check = () => {
      if (document.visibilityState !== "visible") return;
      void api<{ unlocked?: boolean }>("auth/session").then(
        state => { if (active && state.unlocked) unlocked.current(); },
        () => { /* The form stays; an unlock attempt reports any problem. */ },
      );
    };
    window.addEventListener("focus", check);
    document.addEventListener("visibilitychange", check);
    return () => {
      active = false;
      window.removeEventListener("focus", check);
      document.removeEventListener("visibilitychange", check);
    };
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    if (!key.trim()) {
      setError("Enter the access key.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await api("auth/unlock", "POST", { key });
      setKey("");
      onUnlocked();
    } catch (failure) {
      setError(failure instanceof ApiError && failure.status === 401 ? "That access key is not correct." : message(failure));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="auth-card sp-auth-card" aria-labelledby={titleId}>
      <div className="sp-auth-heading">
        <h1 id={titleId}>Unlock this Space</h1>
        <p className="muted">
          Enter the access key of this installation. The person who runs the server finds it as{" "}
          <code>SPACE_ACCESS_KEY</code> in its <code>.env</code> file.
        </p>
      </div>
      {unencrypted && (
        <p className="notice">
          This page is not served over HTTPS, so the key would cross the network unencrypted. Ask
          the operator to put this Space behind HTTPS.
        </p>
      )}
      <form onSubmit={submit} noValidate>
        <label>
          Access key
          <input
            name="access-key"
            type="password"
            autoComplete="current-password"
            autoCapitalize="none"
            spellCheck={false}
            maxLength={1024}
            value={key}
            onChange={event => setKey(event.target.value)}
            disabled={busy}
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? errorId : undefined}
          />
        </label>
        {error && <p className="error" id={errorId} role="alert">{error}</p>}
        <button className="primary" disabled={busy}>{busy ? "Unlocking…" : "Unlock"}</button>
      </form>
      <p className="fine-print">
        This browser stays unlocked for 30 days, or until you lock it.
        <br />
        <a href={docsLinks.selfHosting} target="_blank" rel="noreferrer">Self-hosting guide</a>
        {" · "}
        <a href={sourceCode.href} target="_blank" rel="noreferrer">{sourceCode.label}</a>
      </p>
    </section>
  );
}
