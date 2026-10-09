import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  DEVICE_CODE_LENGTH,
  deviceCodeInput,
  formatDeviceCode,
} from "../../../lib/device-flow";
import { Icon } from "../space/ui-icon";
import { ConnectionMarks, ConnectionShell } from "./connection-shell";
import { ConsentPage } from "./consent-page";
import styles from "./connection.module.css";

// The person's side of a device-code connection. The agent showed six digits; typed
// here, they open the usual consent, and the agent collects the decision itself.
export function CodePage() {
  const [typed, setTyped] = useState("");
  const [code, setCode] = useState<string | null>(null);
  // The field accepts input once the page can react to it; before hydration a
  // typed code would be lost.
  const [ready, setReady] = useState(false);
  const field = useRef<HTMLInputElement>(null);
  useEffect(() => {
    // An agent may hand over a link that already carries the code.
    const given = deviceCodeInput(
      new URLSearchParams(window.location.search).get("code") ?? "",
    );
    if (given) {
      setTyped(formatDeviceCode(given));
      setCode(given);
    }
    setReady(true);
  }, []);
  useEffect(() => {
    if (ready && !code) field.current?.focus();
  }, [ready, code]);
  if (code) {
    return (
      <ConsentPage
        deviceCode={code}
        onReset={() => {
          setCode(null);
          setTyped("");
        }}
      />
    );
  }
  const complete = deviceCodeInput(typed);
  function submit(event: FormEvent) {
    event.preventDefault();
    if (complete) setCode(complete);
  }
  return (
    <ConnectionShell>
      <section className={styles.card} aria-labelledby="code-title">
        <ConnectionMarks agent="your agent" />
        <p className={styles.eyebrow}>MCP connection</p>
        <h1 id="code-title">Enter your agent’s code</h1>
        <p className={styles.intro}>
          Your agent shows a six-digit code. Type it here to review what it may
          access. Nothing redirects, so this works for cloud agents, sandboxes
          and remote editors.
        </p>
        <form onSubmit={submit}>
          <label className={styles.codeField}>
            Connection code
            <input
              ref={field}
              name="code"
              inputMode="numeric"
              autoComplete="one-time-code"
              disabled={!ready}
              placeholder="000 000"
              maxLength={DEVICE_CODE_LENGTH + 1}
              value={typed}
              onChange={(event) => setTyped(formatDeviceCode(event.target.value))}
            />
          </label>
          <button
            type="submit"
            className={`${styles.primary} ${styles.fullWidth}`}
            disabled={!complete}
          >
            Continue
            <Icon name="arrow" size={16} />
          </button>
        </form>
        <p className={styles.revoke}>
          Codes expire after ten minutes. Ask your agent for a new one if yours
          has lapsed.
        </p>
      </section>
    </ConnectionShell>
  );
}
