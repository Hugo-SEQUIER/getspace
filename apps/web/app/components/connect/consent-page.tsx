
import { useRouter } from "@web/lib/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { useFeedback } from "@web/app/components/notifications";
import { api, ApiError, message } from "../../../lib/client";
import { docsLinks } from "../../../lib/docs-links";
import {
  agentCallback,
  authorizationId,
  callbackHost,
  loopbackCallback,
  type AuthorizationDetails,
} from "../../../lib/device-flow";
import { avatarInitials, type Profile } from "../../../lib/space/profile";
import { harnesses, harnessName } from "../../../lib/space/agents";
import { HarnessSelect } from "../space/harness-select";
import type { AgentIdentity, Space } from "../../../lib/space/types";
import { Unlock } from "../unlock";
import { Icon } from "../space/ui-icon";
import { ConnectionMarks, ConnectionShell } from "./connection-shell";
import { useConnectFlow } from "./flow-context";
import styles from "./connection.module.css";

type Account = Space & { profile?: Profile };

// Parameters: the six digits typed on /connect, and how to go back to typing another code.
// Without a code, the request is a browser sign-in named by ?authorization_id= on
// /connect/authorize.
// What it does: loads the request, then lets the owner choose the agent identity, the Space
// and write access before approving or declining. A device-code agent collects the decision
// itself; an app that signed in gets the browser back with its code on /connect/done.
// Output: the review card, or the unlock card while this browser is locked.
export function ConsentPage({
  deviceCode,
  onReset,
}: {
  deviceCode?: string;
  onReset?: () => void;
}) {
  const router = useRouter();
  const { complete } = useConnectFlow();
  const [locked, setLocked] = useState(false);
  const [details, setDetails] = useState<AuthorizationDetails | null>(null);
  const [account, setAccount] = useState<Account | null>(null);
  const [write, setWrite] = useState(false);
  const [spaces, setSpaces] = useState<Space[]>([]);
  const [selectedSpace, setSelectedSpace] = useState("");
  const activeSpace = spaces.find((space) => space.id === selectedSpace);
  const [profiles, setProfiles] = useState<AgentIdentity[]>([]);
  const [selectedAgent, setSelectedAgent] = useState("new");
  const [agentName, setAgentName] = useState("");
  const [harness, setHarness] = useState("other");
  const [loading, setLoading] = useState(true);
  const [pending, setPending] = useState<"approve" | "deny" | null>(null);
  const [error, setError] = useState("");
  const [missingRequest, setMissingRequest] = useState(false);
  const inFlight = useRef(false);
  const busy = loading || pending !== null;
  useFeedback({
    error,
    loading: loading ? "Checking connection…"
      : pending === "approve" ? "Connecting…"
        : pending === "deny" ? "Declining…" : null,
    clearError: () => setError(""),
  });

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    setMissingRequest(false);
    setDetails(null);
    setWrite(false);
    try {
      const requestId = deviceCode ? null : authorizationId(window.location.search);
      if (!deviceCode && !requestId) {
        setMissingRequest(true);
        return;
      }
      // Opening the workspace confirms this browser is unlocked and names the Space.
      const account = await api<Account>("workspace", "POST");
      setAccount(account);
      setLocked(false);
      const available = await api<{ items: Space[] }>("workspaces");
      setSpaces(available.items);
      setSelectedSpace(account.id ?? "");
      const value = deviceCode
        ? await api<AuthorizationDetails>("device/claim", "POST", {
            user_code: deviceCode,
          })
        : await api<AuthorizationDetails>(
            `oauth/details?authorization_id=${encodeURIComponent(requestId!)}`,
          );
      // Refuse a callback this browser must not open before any choice is made.
      if (!deviceCode) agentCallback(value.redirect_uri ?? "");
      const identities = await api<{ items: AgentIdentity[] }>("agent-identities");
      setProfiles(identities.items);
      setSelectedAgent("new");
      setAgentName(value.client.name || "");
      const clientName = (value.client.name || "").toLowerCase();
      setHarness(
        value.harness ||
          harnesses.find((item) => clientName.includes(item.id))?.id ||
          "other",
      );
      setDetails(value);
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) setLocked(true);
      else
        setError(
          error instanceof ApiError && error.status >= 500
            ? "We couldn’t load the connection. Please try again in a moment."
            : message(error),
        );
    } finally {
      setLoading(false);
    }
  }, [deviceCode]);

  useEffect(() => {
    void load();
  }, [load]);

  async function decide(action: "approve" | "deny") {
    if (!details || busy || inFlight.current) return;
    inFlight.current = true;
    setPending(action);
    setError("");
    try {
      const profile =
        action !== "approve"
          ? null
          : selectedAgent === "new"
            ? await api<AgentIdentity>(
                `agent-identities?workspace_id=${selectedSpace}`,
                "POST",
                {
                  name: agentName,
                  harness,
                },
              )
            : profiles.find((item) => item.id === selectedAgent);
      if (action === "approve" && !profile)
        throw new Error("Choose an agent identity.");
      const decision = {
        authorization_id: details.authorization_id,
        action,
        can_write: action === "approve" && write,
        agent_id: profile?.id,
        space_id: selectedSpace || undefined,
      };
      const result = deviceCode
        ? await api<{ status: string }>("device/consent", "POST", decision)
        : await api<{ redirect_url: string }>("oauth/consent", "POST", decision);
      complete({
        action,
        clientName: details.client.name || "your agent",
        canWrite: write,
        agent: profile,
        redirectUrl: "redirect_url" in result ? agentCallback(result.redirect_url) : null,
      });
      router.replace("/connect/done");
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        setDetails(null);
        setLocked(true);
      } else setError(message(error));
      inFlight.current = false;
      setPending(null);
    }
  }

  const agent = details?.client.name || (deviceCode ? "your agent" : "your app");
  const callback = details?.redirect_uri ?? null;
  const returnsTo = callback ? callbackHost(callback) : null;
  // A loopback callback reaches only the computer the app runs on; say so before approval.
  const loopback = callback ? loopbackCallback(callback) : false;
  const displayName = account?.profile?.displayName || "Owner";
  return (
    <ConnectionShell>
      {locked ? (
        <div className={styles.signIn}>
          <p className={styles.eyebrow}>One step before connecting</p>
          <p className={styles.signInIntro}>
            Unlock this Space to choose what your agent can access.
          </p>
          <Unlock onUnlocked={() => void load()} />
        </div>
      ) : (
        <section
          className={styles.card}
          aria-labelledby="consent-title"
          aria-busy={busy}
        >
          <ConnectionMarks agent={agent} />
          <p className={styles.eyebrow}>MCP connection</p>
          <h1 id="consent-title">
            {details ? (
              <>
                Connect {agent}
                <br />
                to Space
              </>
            ) : loading ? (
              "Opening your request…"
            ) : deviceCode ? (
              "That code didn’t work"
            ) : missingRequest ? (
              "Let’s start from your app"
            ) : (
              "We couldn’t open this request"
            )}
          </h1>
          <p className={styles.intro}>
            {details
              ? deviceCode
                ? "Your agent is waiting for your decision. Nothing opens or redirects after this step."
                : `After you decide, you return to ${returnsTo}.`
              : loading
                ? deviceCode
                  ? "Checking the code and your Space."
                  : "Checking your Space and the requested access."
                : deviceCode
                  ? "Codes expire after ten minutes and work for one person. Ask your agent for a new code, then try again."
                  : missingRequest
                    ? "Add Space in your app with its MCP address. The app sends you here to choose what it can access."
                    : "Requests expire after ten minutes. Start the connection again from your app."}
          </p>
          {details && (
            <>
              <div className={styles.account}>
                <span className={styles.avatar} aria-hidden="true">
                  {avatarInitials(displayName)}
                </span>
                <span className={styles.accountName}>
                  <strong>{displayName}</strong>
                  <span>{activeSpace?.name ?? account?.name}</span>
                </span>
                <span className={styles.privateLabel}>
                  <Icon
                    name={activeSpace?.kind === "team" ? "home" : "lock"}
                    size={12}
                  />{" "}
                  {activeSpace?.kind === "team" ? "Team" : "Personal"}
                </span>
              </div>
              <div className={styles.agentFields}>
                <h2>Which agent is connecting?</h2>
                <label>
                  Agent identity
                  <select
                    value={selectedAgent}
                    onChange={(event) => setSelectedAgent(event.target.value)}
                    disabled={busy}
                  >
                    <option
                      value="new"
                      disabled={activeSpace?.role === "viewer"}
                    >
                      Create an agent identity
                    </option>
                    {profiles.map((profile) => (
                      <option key={profile.id} value={profile.id}>
                        {profile.name} · {harnessName(profile.harness)}
                      </option>
                    ))}
                  </select>
                </label>
                {selectedAgent === "new" && activeSpace?.role !== "viewer" && (
                  <>
                    <label>
                      Name
                      <input
                        value={agentName}
                        onChange={(event) => setAgentName(event.target.value)}
                        maxLength={80}
                        placeholder="e.g. Coding partner"
                        disabled={busy}
                      />
                    </label>
                    <HarnessSelect value={harness} onChange={setHarness} disabled={busy} />
                  </>
                )}
                <p>
                  Connecting the same agent again? Choose its saved identity to
                  keep its activity together.
                </p>
              </div>
              {activeSpace?.role === "viewer" && profiles.length === 0 && (
                <p role="status">
                  Ask a Space editor to create an agent identity before
                  connecting.
                </p>
              )}
              <div className={styles.permissions}>
                <h2>
                  Access to {activeSpace?.name || account?.name || "your Space"}
                </h2>
                {spaces.length > 1 && (
                  <label className={styles.spaceField}>
                    Space
                    <select
                      value={selectedSpace}
                      disabled={busy}
                      onChange={(event) => {
                        const id = event.target.value;
                        setSelectedSpace(id);
                        setProfiles([]);
                        setSelectedAgent("new");
                        setWrite(false);
                        setLoading(true);
                        setError("");
                        void api<{ items: AgentIdentity[] }>(
                          `agent-identities?workspace_id=${id}`,
                        )
                          .then((value) => {
                            setProfiles(value.items);
                            if (
                              spaces.find((space) => space.id === id)?.role ===
                              "viewer"
                            )
                              setSelectedAgent(value.items[0]?.id ?? "new");
                          })
                          .catch((error) => setError(message(error)))
                          .finally(() => setLoading(false));
                      }}
                    >
                      {spaces.map((space) => (
                        <option key={space.id} value={space.id}>
                          {space.name} ·{" "}
                          {space.kind === "team" ? "Team" : "Personal"}
                          {space.role === "viewer" ? " (view only)" : ""}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
                <div className={styles.permission}>
                  <span className={styles.permissionIcon}>
                    <Icon name="book" size={20} />
                  </span>
                  <span>
                    <strong>Read your context</strong>
                    <span>Read and search documents, tasks, and memory.</span>
                  </span>
                  <Icon name="check" size={17} className={styles.included} />
                </div>
                <label className={styles.writeOption} data-selected={write}>
                  <input
                    type="checkbox"
                    checked={write}
                    onChange={(event) => setWrite(event.target.checked)}
                    disabled={busy || activeSpace?.role === "viewer"}
                    aria-describedby="write-description"
                  />
                  <span>
                    <strong>
                      Allow changes{" "}
                      <span className={styles.optional}>Optional</span>
                    </strong>
                    <span id="write-description">
                      Create and update documents and tasks. Save, edit, and
                      delete memory.
                    </span>
                  </span>
                </label>
              </div>
              <p className={styles.disclosure}>
                {agent} and its provider can see the content it reads. Access
                covers the selected Space. It cannot manage membership or share
                your Space.
                {!deviceCode &&
                  ` Space does not verify the name an app registers: allow access only if you started this connection in ${agent} and it returns to ${returnsTo}.`}
              </p>
              {loopback && (
                <p className={styles.disclosure}>
                  {agent} listens on the computer it runs on. If that is not this
                  computer, you can finish by hand after you decide.
                </p>
              )}
              <details className={styles.details}>
                <summary>
                  Connection details <Icon name="down" size={14} />
                </summary>
                <dl>
                  <dt>Connection method</dt>
                  <dd>
                    {deviceCode
                      ? "Six-digit code. Your agent collects the decision itself."
                      : "Browser sign-in (OAuth). The app receives a one-time code."}
                  </dd>
                  {callback && (
                    <>
                      <dt>Returns to</dt>
                      <dd>{callback.split("?")[0]}</dd>
                    </>
                  )}
                  <dt>Requested scopes</dt>
                  <dd>{details.scope || "None"}</dd>
                </dl>
              </details>
              <div className={styles.actions}>
                <button
                  className={styles.secondary}
                  disabled={busy}
                  onClick={() => void decide("deny")}
                >
                  {pending === "deny" ? "Declining…" : "Cancel"}
                </button>
                <button
                  className={styles.primary}
                  disabled={
                    busy || (selectedAgent === "new" && !agentName.trim())
                  }
                  onClick={() => void decide("approve")}
                >
                  {pending === "approve" ? "Connecting…" : "Allow access"}
                  <Icon name="arrow" size={16} />
                </button>
              </div>
              <p className={styles.revoke}>
                You can revoke access anytime in Agents.
              </p>
            </>
          )}
          {!details && !loading && (
            <div className={styles.emptyActions}>
              {onReset && (
                <button className={styles.secondary} onClick={onReset}>
                  <Icon name="refresh" size={16} /> Enter another code
                </button>
              )}
              <a href={docsLinks.connectingAgents} target="_blank" rel="noreferrer" className={styles.primary}>
                Connection guide <Icon name="external" size={16} />
              </a>
            </div>
          )}
        </section>
      )}
    </ConnectionShell>
  );
}
