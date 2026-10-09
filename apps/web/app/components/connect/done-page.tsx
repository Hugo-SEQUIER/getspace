import { useState } from "react";
import { message } from "../../../lib/client";
import { docsLinks } from "../../../lib/docs-links";
import {
  agentCallback,
  callbackHost,
  loopbackCallback,
} from "../../../lib/device-flow";
import { harnessName } from "../../../lib/space/agents";
import { useFeedback } from "../notifications";
import { Icon } from "../space/ui-icon";
import { ConnectionMarks, ConnectionShell } from "./connection-shell";
import { useConnectFlow } from "./flow-context";
import { AgentMemorySetup } from "../agent-memory-setup";
import styles from "./connection.module.css";

// Parameters: none; the decision comes from the consent step in this tab.
// What it does: confirms an approval or a refusal. A device-code agent collects the outcome
// itself; an app that signed in through the browser is returned to with its code, and a
// callback on another computer can be finished by hand.
// Output: the result page, or a pointer back to the agent when this tab has no decision.
export function DonePage() {
  const { result } = useConnectFlow();
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [returning, setReturning] = useState(false);
  const approved = result?.action === "approve";
  const callback = result?.redirectUrl ?? null;
  const agent = result?.clientName || (callback ? "your app" : "your agent");
  // Desktop and command-line apps listen on their own computer. A browser anywhere else
  // cannot deliver that callback, so the link is offered to finish by hand.
  const loopback = callback ? loopbackCallback(callback) : false;
  useFeedback({
    error,
    notice,
    clearError: () => setError(""),
    clearNotice: () => setNotice(""),
  });

  function returnToApp() {
    if (!callback) return;
    setError("");
    setReturning(true);
    try {
      window.location.assign(agentCallback(callback));
    } catch (failure) {
      setError(message(failure));
      setReturning(false);
    }
  }

  async function copyCallback() {
    if (!callback) return;
    setError("");
    try {
      await navigator.clipboard.writeText(callback);
      setNotice("Copied. Open it where your app runs.");
    } catch {
      setError("Could not copy. Select the link and copy it manually.");
    }
  }

  return (
    <ConnectionShell wide={approved}>
      <section
        className={approved ? styles.approvedPage : `${styles.card} ${styles.validation}`}
        aria-labelledby="done-title"
      >
        {result ? (
          <>
            {!approved && <ConnectionMarks agent={agent} />}
            <p className={approved ? styles.successLabel : styles.eyebrow}>
              <Icon name={approved ? "check" : "close"} size={15} />{" "}
              {approved ? "Access approved" : "Request declined"}
            </p>
            <h1 id="done-title">
              {approved ? "Agent setup" : "You’re in control."}
            </h1>
            <p className={styles.intro}>
              {callback
                ? approved
                  ? `Return to ${agent} to finish connecting your Space, then give your agent a memory habit.`
                  : `You declined this request. Return to ${agent} to close the connection flow.`
                : approved
                  ? `${agent} collects the connection on its own within a few seconds. There is nothing more to do here; give your agent a memory habit while it finishes.`
                  : `You declined this request. ${agent} is told the next time it checks.`}
            </p>
            {callback && (
              <>
                <button
                  className={`${styles.primary} ${approved ? styles.returnAction : styles.fullWidth}`}
                  onClick={returnToApp}
                >
                  {returning ? "Return again" : `Return to ${agent}`}
                  <Icon name="arrow" size={17} />
                </button>
                <p className={styles.revoke}>
                  {returning
                    ? `If ${agent} didn’t open, return again or start the connection again from ${agent}.`
                    : `Continue to ${callbackHost(callback)}`}
                </p>
              </>
            )}
            {approved && <AgentMemorySetup hideHeading />}
            {approved && (
              <>
                <div className={styles.accessSummary}>
                  <Icon name="lock" size={18} />
                  <span>
                    <strong>Private Space</strong>
                    <span>
                      {result.canWrite ? "Read and write access" : "Read-only access"}
                    </span>
                  </span>
                  <span className={styles.approvedBadge}>Approved</span>
                </div>
                {result.agent && (
                  <p className={styles.identityHint}>
                    <strong>{result.agent.name}</strong> · {harnessName(result.agent.harness)}.
                    {" "}Choose this identity when connecting the same agent again.
                  </p>
                )}
              </>
            )}
            {callback && approved && (
              <details className={styles.handoff} open={loopback}>
                <summary>
                  Finish by hand
                  <Icon name="chevron" size={14} />
                </summary>
                <p>
                  {loopback
                    ? `This address is on the computer where ${agent} runs. If ${agent} runs somewhere else, open the link there.`
                    : `If ${agent} did not open, copy this link and open it where the app runs.`}
                </p>
                <pre data-testid="callback-link">{callback}</pre>
                <button
                  type="button"
                  className={styles.secondary}
                  onClick={() => void copyCallback()}
                >
                  Copy callback link
                  <Icon name="copy" size={15} />
                </button>
                <p>
                  The link carries a one-time code for this connection. It works
                  once and expires after ten minutes.
                </p>
              </details>
            )}
            <a className={styles.manageLink} href="/app/agents">
              Manage agent access <Icon name="arrow" size={14} />
            </a>
          </>
        ) : (
          <>
            <span className={styles.emptyIcon}>
              <Icon name="link" size={26} />
            </span>
            <p className={styles.eyebrow}>MCP connection</p>
            <h1 id="done-title">Start from your agent.</h1>
            <p className={styles.intro}>
              There’s no connection to finish in this tab. If you refreshed this
              page, start the connection again from your agent or app.
            </p>
            <a
              href={docsLinks.connectingAgents}
              target="_blank"
              rel="noreferrer"
              className={`${styles.primary} ${styles.fullWidth}`}
            >
              Open connection guide <Icon name="external" size={17} />
            </a>
            <a href="/app/agents" className={styles.manageLink}>
              Manage agent access
            </a>
          </>
        )}
      </section>
    </ConnectionShell>
  );
}
