import { useState, type FormEvent } from "react";
import { message } from "../../../lib/client";
import { docsLinks } from "../../../lib/docs-links";
import { harnessName } from "../../../lib/space/agents";
import {
  AGENT_TOKEN_NAME_LIMIT,
  claudeCodeCommand,
  codexConfig,
  mcpJsonConfig,
  tokenLifetime,
  type AgentToken,
} from "../../../lib/space/agent-tokens";
import { deviceConnectPrompt } from "../../../lib/space/device-connect";
import type { AgentIdentity } from "../../../lib/space/types";
import { useFeedback } from "../notifications";
import { HarnessSelect } from "./harness-select";
import { useSpace } from "./provider";
import { Modal } from "./ui";
import { Icon } from "./ui-icon";
import styles from "./agents.module.css";

// Parameters: the agent to preselect, if any, and how to close.
// What it does: in key mode, first shows the MCP address that apps signing in through the
// browser need. Then creates a connection token for an agent identity (creating the identity
// first when it is new), and shows the token and its client setup once. Closing forgets the
// token.
// Output: the "Connect an agent" dialog.
export function ConnectAgentModal({ initialAgentId, onClose }: { initialAgentId?: string; onClose: () => void }) {
  const data = useSpace();
  const [agentId, setAgentId] = useState(initialAgentId && data.agents.some(agent => agent.id === initialAgentId) ? initialAgentId : "new");
  const [name, setName] = useState("");
  const [harness, setHarness] = useState("claude-code");
  const [canWrite, setCanWrite] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [created, setCreated] = useState<{ token: AgentToken; agent: AgentIdentity } | null>(null);
  useFeedback({
    error,
    notice,
    loading: busy ? "Creating the connection…" : null,
    clearError: () => setError(""),
    clearNotice: () => setNotice(""),
  });

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      let agent = data.agents.find(item => item.id === agentId);
      if (!agent) {
        agent = await data.createAgentIdentity({ name, harness });
        // A retry after a failed token reuses this identity instead of creating another.
        setAgentId(agent.id);
      }
      const token = await data.createAgentToken(agent, canWrite);
      setCreated({ token, agent });
    } catch (failure) {
      setError(message(failure));
    } finally {
      setBusy(false);
    }
  }

  async function copy(value: string, done: string) {
    try {
      await navigator.clipboard.writeText(value);
      setNotice(done);
    } catch {
      setError("Could not copy. Select the text and copy it manually.");
    }
  }

  if (created) {
    const { token, agent } = created;
    const lifetime = tokenLifetime(token.expires_in);
    const setups = [
      { id: "claude-code", title: "Claude Code", hint: "Run this in a terminal.", text: claudeCodeCommand(token), copy: "Copy command" },
      { id: "codex", title: "Codex", hint: "Add this to ~/.codex/config.toml.", text: codexConfig(token), copy: "Copy setting" },
      { id: "json", title: "Other MCP clients", hint: "Add this server to the client's JSON configuration, such as Cursor's mcp.json.", text: mcpJsonConfig(token), copy: "Copy JSON" },
    ];
    return (
      <Modal title="Connect an agent" size="wide" onClose={onClose}>
        <section className={`sp-modal-content ${styles.tokenResult}`} aria-label="New connection">
          <h3>Copy the token now</h3>
          <p>
            {agent.name} · {harnessName(agent.harness)} can now reach this Space
            with {canWrite ? "read and write" : "read-only"} access.
            This is the only time the token is shown: if you lose it, revoke this connection
            and create another.{lifetime ? ` It expires in ${lifetime}.` : ""}
          </p>
          <label className="sp-field">
            Connection token
            <input
              className={styles.tokenField}
              readOnly
              value={token.access_token}
              autoComplete="off"
              spellCheck={false}
              onFocus={event => event.currentTarget.select()}
            />
          </label>
          <div className={styles.actions}>
            <button type="button" onClick={() => void copy(token.access_token, "Token copied")}>
              <Icon name="copy" size={14} />
              Copy token
            </button>
          </div>
          <h3>Set up your client</h3>
          {setups.map(setup => (
            <section key={setup.id} className={styles.setupSnippet} aria-label={`${setup.title} setup`}>
              <h4>{setup.title}</h4>
              <p>{setup.hint}</p>
              <pre data-testid={`${setup.id}-setup`}>{setup.text}</pre>
              <button type="button" onClick={() => void copy(setup.text, `${setup.title} setup copied`)}>
                <Icon name="copy" size={14} />
                {setup.copy}
              </button>
            </section>
          ))}
          <p className="sp-muted">
            The connection appears under {agent.name} in Agents, where you can
            revoke it at any time.
          </p>
          <div className={`sp-modal-footer ${styles.formFooter}`}>
            <a className="sp-help-link" href={docsLinks.connectingAgents} target="_blank" rel="noreferrer">
              Connection guide <Icon name="external" size={13} />
            </a>
            <button type="button" className="sp-primary" onClick={onClose}>
              I’ve saved it
            </button>
          </div>
        </section>
      </Modal>
    );
  }

  return (
    <Modal title="Connect an agent" onClose={() => { if (!busy) onClose(); }}>
      <form className={`sp-modal-content ${styles.agentForm}`} onSubmit={event => void submit(event)}>
        {data.accessMode === "key" && data.space?.mcp_url && (
          <section className={styles.signInOption} aria-label="Connect an app that signs in">
            <h3>Claude, ChatGPT and other apps that sign in</h3>
            <p>
              Add Space to the app as a custom connector with this address. The
              app opens this website, where you choose its agent and access. No
              token is needed.
            </p>
            <div className={styles.connectorAddress}>
              <code data-testid="connector-address">{data.space.mcp_url}</code>
              <button type="button" onClick={() => void copy(data.space!.mcp_url, "Connector address copied")}>
                <Icon name="copy" size={14} />
                Copy address
              </button>
            </div>
          </section>
        )}
        <p className={`sp-muted ${styles.formIntro}`}>
          Create a connection token so an MCP client, such as Claude Code or
          Codex, can use this Space directly. You will copy it once, with the
          setup for your client.
        </p>
        <label className="sp-field">
          Agent
          <select value={agentId} onChange={event => setAgentId(event.target.value)} disabled={busy}>
            <option value="new">Create a new agent</option>
            {data.agents.map(agent => (
              <option key={agent.id} value={agent.id}>
                {agent.name} · {harnessName(agent.harness)}
              </option>
            ))}
          </select>
        </label>
        {agentId === "new" && (
          <>
            <label className="sp-field">
              Name
              <input
                data-autofocus
                required
                maxLength={AGENT_TOKEN_NAME_LIMIT}
                value={name}
                placeholder="e.g. Claude Code on my laptop"
                onChange={event => setName(event.target.value)}
                disabled={busy}
              />
            </label>
            <HarnessSelect value={harness} onChange={setHarness} disabled={busy} />
          </>
        )}
        <label className={styles.writeOption}>
          <input
            type="checkbox"
            checked={canWrite}
            onChange={event => setCanWrite(event.target.checked)}
            disabled={busy || data.space?.role === "viewer"}
          />
          <span>
            <strong>Allow changes</strong>
            <small>Create and update documents and tasks. Save, edit and delete memory. Without it the agent can only read and search.</small>
          </span>
        </label>
        {data.space?.mcp_url && (
          <details className={styles.configuration}>
            <summary>Let the agent ask instead, with a six-digit code</summary>
            <p>
              For an agent that can call the API itself. Paste this prompt into
              it: it shows you a code to enter at{" "}
              <code>{window.location.origin}/connect</code>, and collects its
              connection once you approve.
            </p>
            <pre>{deviceConnectPrompt(data.space.mcp_url, window.location.origin)}</pre>
            <button
              type="button"
              onClick={() => void copy(deviceConnectPrompt(data.space!.mcp_url, window.location.origin), "Prompt copied")}
            >
              Copy prompt
            </button>
          </details>
        )}
        <div className={`sp-modal-footer ${styles.formFooter}`}>
          <button type="button" disabled={busy} onClick={onClose}>
            Cancel
          </button>
          <button className="sp-primary" disabled={busy || (agentId === "new" && !name.trim())}>
            {busy ? "Creating…" : "Create connection"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
