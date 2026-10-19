
import { useEffect, useState } from "react";
import { message } from "../../../lib/client";
import { useFeedback } from "../notifications";
import {
  groupAgentConnections,
  harnessName,
} from "../../../lib/space/agents";
import type { AgentIdentity, Grant } from "../../../lib/space/types";
import { useSpace } from "./provider";
import { Avatar, Empty, Modal, ConfirmModal } from "./ui";
import { Icon } from "./ui-icon";
import styles from "./agents.module.css";
import { HarnessSelect } from "./harness-select";
import { AgentMemorySetup } from "../agent-memory-setup";
import { ConnectAgentModal } from "./agent-connect";

export function AgentsView() {
  const data = useSpace();
  useEffect(() => data.watchAgents(), [data.watchAgents]);
  const groups = groupAgentConnections(data.agents, data.grants);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [editing, setEditing] = useState<AgentIdentity | "new" | null>(null);
  const [name, setName] = useState("");
  const [harness, setHarness] = useState("codex");
  // Open with the agent to preselect, if any.
  const [connectFor, setConnectFor] = useState<{ agentId?: string } | null>(null);
  const [deleting, setDeleting] = useState<AgentIdentity | null>(null);
  const [revoke, setRevoke] = useState<Grant | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [assignment, setAssignment] = useState("");
  const [setupOpen, setSetupOpen] = useState(false);
  const [dismissedSetup, setDismissedSetup] = useState<string[]>([]);
  const [setupWorkspace, setSetupWorkspace] = useState<string | null>(null);
  useFeedback({
    error,
    loading: busy ? "Updating agent…" : null,
    clearError: () => setError(""),
  });
  const workspaceId = data.space?.id;
  const activeGrants = data.grants.filter((grant) => !grant.revoked_at);
  useEffect(() => {
    setSetupOpen(false);
    let dismissed: string[] = [];
    try {
      const saved: unknown = JSON.parse(localStorage.getItem(`space-agent-setup:${workspaceId}`) ?? "[]");
      if (Array.isArray(saved)) dismissed = saved.filter((id): id is string => typeof id === "string");
    } catch {}
    setDismissedSetup(dismissed);
    setSetupWorkspace(workspaceId ?? null);
  }, [workspaceId]);
  useEffect(() => {
    const openLinkedSetup = () => {
      if (window.location.hash === "#memory-setup") setSetupOpen(true);
    };
    openLinkedSetup();
    window.addEventListener("hashchange", openLinkedSetup);
    return () => window.removeEventListener("hashchange", openLinkedSetup);
  }, [workspaceId]);
  const suggestSetup = !!workspaceId && setupWorkspace === workspaceId &&
    activeGrants.some((grant) => !dismissedSetup.includes(grant.id));
  const showSetup = setupOpen || suggestSetup;
  function dismissSetup() {
    const dismissed = [...new Set([...dismissedSetup, ...activeGrants.map((grant) => grant.id)])];
    setDismissedSetup(dismissed);
    setSetupOpen(false);
    try {
      localStorage.setItem(`space-agent-setup:${workspaceId}`, JSON.stringify(dismissed));
    } catch {}
  }
  const selected = groups.find((g) => g.id === selectedId);
  function edit(agent: AgentIdentity | "new") {
    setEditing(agent);
    setName(agent === "new" ? "" : agent.name);
    setHarness(agent === "new" ? "codex" : agent.harness);
    setError("");
  }
  async function copy(value: string) {
    try {
      await navigator.clipboard.writeText(value);
      data.setNotice("Copied");
    } catch {
      data.setError("Could not copy. Select the text and copy it manually.");
    }
  }
  return (
    <>
      <div className="sp-page">
        <div className="sp-work-heading">
          <div>
            <h1>Agents</h1>
            <p>
              Name your agents once. Reuse their identity wherever they connect.
            </p>
          </div>
          <div className={styles.actions}>
            <button onClick={() => setSetupOpen(true)}>Set up agent memory</button>
            <button onClick={() => edit("new")}>New agent</button>
            <button className="sp-primary" onClick={() => setConnectFor({})}>
              <Icon name="plus" size={16} />
              Connect an agent
            </button>
          </div>
        </div>
        {showSetup && (
          <section id="memory-setup" className={styles.memorySetup} aria-label="Agent memory setup">
            <div className={styles.setupHeader}>
              <p>
                {activeGrants.length
                  ? "Connected. Now help your agent remember."
                  : "Connect your agent first, then give it these memory instructions."}
              </p>
              <button onClick={dismissSetup} aria-label="Dismiss agent memory setup">Dismiss</button>
            </div>
            <AgentMemorySetup />
          </section>
        )}
        <div className="sp-agents-layout">
          <div>
            {groups.length ? (
              groups.map((group) => {
                const active = group.connections.filter(
                  (g) => !g.revoked_at,
                ).length;
                return (
                  <button
                    key={group.id}
                    className={`sp-agent-row ${selectedId === group.id ? "selected" : ""}`}
                    onClick={() => {
                      setSelectedId(group.id);
                      setAssignment("");
                      setError("");
                    }}
                  >
                    <Avatar
                      name={group.agent?.name ?? "Unidentified connection"}
                      harness={group.agent?.harness}
                      kind="agent"
                    />
                    <span>
                      <strong>
                        {group.agent?.name ?? "Unidentified connection"}
                      </strong>
                      <small>
                        {group.agent
                          ? `${harnessName(group.agent.harness)} · ${group.connections.length} connection${group.connections.length === 1 ? "" : "s"}`
                          : "Choose an existing agent or create one"}
                      </small>
                    </span>
                    <span
                      className={`sp-connection-state ${active ? "connected" : "revoked"}`}
                    >
                      <i />
                      {active
                        ? "Connected"
                        : group.connections.length
                          ? "Revoked"
                          : "Ready to connect"}
                    </span>
                    <Icon name="chevron" size={15} />
                  </button>
                );
              })
            ) : (
              <Empty icon="agents" title="Connect your first agent" valley>
                <p>Give Claude Code, Codex or another MCP client its own connection to this Space.</p>
                <button onClick={() => setConnectFor({})}>Connect an agent</button>
              </Empty>
            )}
            <div className="sp-agent-principle">
              <Icon name="memory" size={22} />
              <div>
                <h3>One agent can have several connections.</h3>
                <p>
                  Your agent keeps its identity and attribution. Each connection
                  has its own access and can be revoked separately.
                </p>
              </div>
            </div>
          </div>
          {selected && (
            <aside className="sp-agent-detail">
              <div className="sp-inspector-head">
                <h2>{selected.agent?.name ?? "Identify this connection"}</h2>
                <button
                  className="sp-icon-button"
                  aria-label="Close agent details"
                  onClick={() => setSelectedId(null)}
                >
                  <Icon name="close" size={16} />
                </button>
              </div>
              {selected.agent ? (
                <>
                  <div className={styles.profile}>
                    <Avatar
                      name={selected.agent.name}
                      harness={selected.agent.harness}
                      kind="agent"
                    />
                    <div>
                      <strong>{selected.agent.name}</strong>
                      <p>{harnessName(selected.agent.harness)}</p>
                    </div>
                    <button onClick={() => edit(selected.agent!)}>Edit</button>
                  </div>
                  <div className={styles.identity}>
                    <label>Agent ID</label>
                    <code>{selected.agent.id}</code>
                    <button onClick={() => void copy(selected.agent!.id)}>
                      <Icon name="copy" size={14} />
                      Copy ID
                    </button>
                    <p>Use this same ID when this agent connects again.</p>
                  </div>
                  <details className={styles.configuration}>
                    <summary>Send the ID with MCP requests</summary>
                    <p>
                      Set this header in your MCP client so it is attached
                      automatically:
                    </p>
                    <pre>X-Space-Agent-ID: {selected.agent.id}</pre>
                    <button
                      onClick={() =>
                        void copy(`X-Space-Agent-ID: ${selected.agent!.id}`)
                      }
                    >
                      Copy header
                    </button>
                    <p>
                      Clients without custom headers can send{" "}
                      <code>agent_id</code> with each tool call. Call{" "}
                      <code>identify_agent</code> to retrieve the identity
                      chosen during consent.
                    </p>
                    {selected.agent.harness === "codex" && (
                      <>
                        <p>
                          Add this under your Space MCP server in the Codex
                          configuration:
                        </p>
                        <pre>{`http_headers = { "X-Space-Agent-ID" = "${selected.agent.id}" }`}</pre>
                        <button
                          onClick={() =>
                            void copy(
                              `http_headers = { "X-Space-Agent-ID" = "${selected.agent!.id}" }`,
                            )
                          }
                        >
                          Copy Codex setting
                        </button>
                      </>
                    )}
                  </details>
                </>
              ) : (
                <div className={styles.assign}>
                  <p>
                    Link this connection to the agent it belongs to. Multiple
                    connections can use the same identity.
                  </p>
                  <label>
                    Agent
                    <select
                      value={assignment}
                      onChange={(event) => setAssignment(event.target.value)}
                      disabled={busy}
                    >
                      <option value="">Choose an agent…</option>
                      {data.agents.map((agent) => (
                        <option key={agent.id} value={agent.id}>
                          {agent.name} · {harnessName(agent.harness)}
                        </option>
                      ))}
                    </select>
                  </label>
                  <div className={styles.actions}>
                    <button onClick={() => edit("new")}>New agent</button>
                    <button
                      className="sp-primary"
                      disabled={
                        !assignment ||
                        busy ||
                        !!selected.connections[0].revoked_at
                      }
                      onClick={async () => {
                        setBusy(true);
                        setError("");
                        try {
                          await data.assignAgent(
                            selected.connections[0],
                            assignment,
                          );
                          setSelectedId(assignment);
                        } catch (e) {
                          setError(message(e));
                        } finally {
                          setBusy(false);
                        }
                      }}
                    >
                      Use this agent
                    </button>
                  </div>
                </div>
              )}
              <h3>Connections</h3>
              {selected.connections.length ? (
                selected.connections.map((connection, index) => (
                  <section key={connection.id} className={styles.connection}>
                    <div>
                      <strong>Connection {index + 1}</strong>
                      <span>
                        {connection.revoked_at ? "Revoked" : "Connected"}
                      </span>
                    </div>
                    <p>
                      {connection.can_write ? "Read and write" : "Read only"} ·{" "}
                      {data.space?.name}
                    </p>
                    <details>
                      <summary>Connection details</summary>
                      <p>Connection ID</p>
                      <code>{connection.id}</code>
                      <p>Client ID</p>
                      <code>{connection.client_id}</code>
                    </details>
                    {selected.agent && !connection.revoked_at && (
                      <label className={styles.connectionAgent}>
                        Agent identity
                        <select
                          aria-label={`Agent for connection ${index + 1}`}
                          value={connection.agent_id ?? ""}
                          disabled={busy}
                          onChange={async (event) => {
                            const id = event.target.value;
                            setBusy(true);
                            setError("");
                            try {
                              await data.assignAgent(connection, id);
                              setSelectedId(id);
                            } catch (e) {
                              setError(message(e));
                            } finally {
                              setBusy(false);
                            }
                          }}
                        >
                          {data.agents.map((agent) => (
                            <option key={agent.id} value={agent.id}>
                              {agent.name} · {harnessName(agent.harness)}
                            </option>
                          ))}
                        </select>
                      </label>
                    )}
                    {!connection.revoked_at && (
                      <button
                        className="sp-danger-text"
                        onClick={() => {
                          setRevoke(connection);
                          setError("");
                        }}
                      >
                        Revoke this connection
                      </button>
                    )}
                  </section>
                ))
              ) : (
                <p className="sp-muted">
                  No client uses this agent yet. Create a connection for it to
                  set one up.
                </p>
              )}
              <button onClick={() => setConnectFor({ agentId: selected.agent?.id })}>
                {selected.connections.length ? "Connect another client" : "Create a connection"}
              </button>
              {selected.agent && (
                <button
                  className="sp-danger-text"
                  disabled={busy}
                  onClick={() => {
                    setDeleting(selected.agent);
                    setError("");
                  }}
                >
                  Delete agent
                </button>
              )}
            </aside>
          )}
        </div>
      </div>
      {editing && (
        <Modal
          title={editing === "new" ? "Name your agent" : "Edit agent"}
          onClose={() => {
            if (!busy) setEditing(null);
          }}
        >
          <form
            className={`sp-modal-content ${styles.agentForm}`}
            onSubmit={async (event) => {
              event.preventDefault();
              setBusy(true);
              setError("");
              try {
                const saved = await data.saveAgent(
                  { name, harness },
                  editing === "new" ? undefined : editing,
                );
                setSelectedId(saved.id);
                setEditing(null);
              } catch (e) {
                setError(message(e));
              } finally {
                setBusy(false);
              }
            }}
          >
            <div className={styles.profile}>
              <Avatar name={name || "Agent"} harness={harness} kind="agent" />
              <p>A name you recognize, with the harness it runs in.</p>
            </div>
            <label className="sp-field">
              Name
              <input
                autoFocus
                required
                maxLength={80}
                value={name}
                placeholder="e.g. Research assistant"
                onChange={(event) => setName(event.target.value)}
                disabled={busy}
              />
            </label>
            <HarnessSelect value={harness} onChange={setHarness} disabled={busy} />
            <p className={`sp-muted ${styles.formHint}`}>
              An existing name and harness reuse the same identity. Use
              different names for distinct agents running in the same harness.
            </p>
            <div className={`sp-modal-footer ${styles.formFooter}`}>
              <button
                type="button"
                disabled={busy}
                onClick={() => setEditing(null)}
              >
                Cancel
              </button>
              <button className="sp-primary" disabled={busy || !name.trim()}>
                {busy ? "Saving…" : "Save agent"}
              </button>
            </div>
          </form>
        </Modal>
      )}
      {connectFor && (
        <ConnectAgentModal
          initialAgentId={connectFor.agentId}
          onClose={() => setConnectFor(null)}
        />
      )}
      {deleting && (
        <ConfirmModal
          title="Delete agent?"
          confirmLabel="Delete agent"
          busyLabel="Deleting…"
          busy={busy}
          error={error}
          onClose={() => setDeleting(null)}
          onConfirm={async () => {
            setBusy(true);
            setError("");
            try {
              await data.deleteAgent(deleting);
              setSelectedId(null);
              setDeleting(null);
            } catch (e) {
              setError(message(e));
            } finally {
              setBusy(false);
            }
          }}
        >
          <p>
            Delete {deleting.name} from your agents? All its connections will lose
            access to your Space. Saved work and its attribution will remain.
          </p>
        </ConfirmModal>
      )}
      {revoke && (
        <ConfirmModal
          title="Revoke this connection?"
          confirmLabel="Revoke connection"
          busyLabel="Revoking…"
          busy={busy}
          error={error}
          onClose={() => setRevoke(null)}
          onConfirm={async () => {
            setBusy(true);
            setError("");
            try {
              await data.revoke(revoke);
              setRevoke(null);
            } catch (e) {
              setError(message(e));
            } finally {
              setBusy(false);
            }
          }}
        >
          <p>
            This connection will lose access to your Space. The agent
            identity, other connections, and saved work remain.
          </p>
        </ConfirmModal>
      )}
    </>
  );
}
