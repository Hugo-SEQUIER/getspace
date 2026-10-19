import { useEffect, useRef, useState } from "react";
import { useFeedback } from "@web/app/components/notifications";
import { ACCESS_RESET_EVENT, api, ApiError, message } from "../../../lib/client";
import {
  API_KEY_LIMIT, API_KEY_NAME_LIMIT, apiBaseUrl, apiKeyExpired, apiKeyExpiries, apiKeyPath,
  type ApiKey, type CreatedApiKey,
} from "../../../lib/space/api-keys";
import type { Space } from "../../../lib/space/types";
import { docsLinks } from "../../../lib/docs-links";
import { useSpace } from "./provider";
import { ConfirmModal, relativeDate } from "./ui";
import { Icon } from "./ui-icon";
import styles from "./api-key-settings.module.css";

const day = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric" });
const formatDay = (value: string) => day.format(new Date(value));
const lastUsed = (value: string) => {
  const label = relativeDate(value);
  return label === "Today" || label === "Yesterday" ? label.toLowerCase() : `on ${formatDay(value)}`;
};

export function ApiKeySettings() {
  const data = useSpace();
  const [accessEpoch, setAccessEpoch] = useState(0);
  useEffect(() => {
    const reset = () => setAccessEpoch(value => value + 1);
    window.addEventListener(ACCESS_RESET_EVENT, reset);
    return () => window.removeEventListener(ACCESS_RESET_EVENT, reset);
  }, []);
  return (
    <div className={styles.settings}>
      <h1>API keys</h1>
      <p className="sp-subtitle">Build your own apps and scripts on this Space’s documents, tasks and memory.</p>
      {data.space && (
        <SpaceApiKeys key={`${data.accountId}:${data.space.id}:${data.space.role}:${accessEpoch}`} space={data.space} />
      )}
      <section>
        <h2>Using the API</h2>
        <p className="sp-muted">Send a key as <code>Authorization: Bearer &lt;key&gt;</code>. The API describes every endpoint at <code>/openapi.json</code> on its URL.</p>
        <a className="sp-setting-link" href={docsLinks.index} target="_blank" rel="noreferrer">
          Read the documentation
          <Icon name="external" size={16} />
        </a>
      </section>
    </div>
  );
}

function SpaceApiKeys({ space }: { space: Space }) {
  const [keys, setKeys] = useState<ApiKey[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [name, setName] = useState("");
  const [canWrite, setCanWrite] = useState(false);
  const [expiry, setExpiry] = useState("90");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [created, setCreated] = useState<CreatedApiKey | null>(null);
  const [revoking, setRevoking] = useState<ApiKey | null>(null);
  const lifecycle = useRef<AbortController | null>(null);
  const running = useRef(false);
  // Viewers can only read this Space, so their keys can only read it too.
  const writable = space.role !== "viewer";
  const full = (keys?.length ?? 0) >= API_KEY_LIMIT;
  const disabled = !!busy || loading;
  const baseUrl = apiBaseUrl(space.mcp_url);
  useFeedback({
    error,
    notice,
    loading: busy || (loading ? "Loading API keys…" : undefined),
    clearError: () => setError(""),
    clearNotice: () => setNotice(""),
  });

  async function load(signal: AbortSignal) {
    setLoading(true);
    try {
      const response = await api<{ items: ApiKey[] }>(apiKeyPath(space.id), "GET", undefined, signal);
      signal.throwIfAborted();
      setKeys(response.items);
    } finally {
      if (!signal.aborted) setLoading(false);
    }
  }

  useEffect(() => {
    const controller = new AbortController();
    lifecycle.current = controller;
    void load(controller.signal).catch(error => {
      if (!controller.signal.aborted) setError(message(error));
    });
    // A new key's token must not outlive the page it was revealed on.
    const reset = () => {
      controller.abort();
      setCreated(null);
      setRevoking(null);
      setKeys(null);
    };
    window.addEventListener("pagehide", reset);
    return () => {
      controller.abort();
      window.removeEventListener("pagehide", reset);
    };
    // Parameters: the keyed Space scope. What it does: starts an uncached read and clears the token on exit. Output: no retained secrets.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [space.id]);

  async function run(label: string, action: (signal: AbortSignal) => Promise<void>) {
    if (running.current || !lifecycle.current || lifecycle.current.signal.aborted) return;
    const signal = lifecycle.current.signal;
    running.current = true;
    setBusy(label);
    setError("");
    setNotice("");
    try { await action(signal); }
    catch (error) { if (!signal.aborted) setError(message(error)); }
    finally {
      if (!signal.aborted) {
        running.current = false;
        setBusy("");
      }
    }
  }

  async function copy(value: string) {
    try {
      await navigator.clipboard.writeText(value);
      setNotice("Copied");
    } catch {
      setError("Could not copy. Select the key and copy it manually.");
    }
  }

  return (
    <>
      {created && (
        <section className={styles.secret} aria-label="New API key">
          <h2>Copy your new key</h2>
          <p>This is the only time the key for {created.name} is shown. Store it somewhere safe, such as a password manager or your server’s environment settings. You won’t see it again; if you lose it, revoke it and create another.</p>
          <label className="sp-field">API key
            <input readOnly value={created.token} autoComplete="off" spellCheck={false} onFocus={event => event.currentTarget.select()} />
          </label>
          <div className={styles.actions}>
            <button type="button" onClick={() => void copy(created.token)}>Copy key</button>
            <button type="button" onClick={() => setCreated(null)}>I’ve saved it</button>
          </div>
        </section>
      )}
      <section>
        <h2>Create a key</h2>
        <p className="sp-muted">A key acts as you, in this Space only. It can never manage agents, members or other keys.</p>
        <form onSubmit={event => {
          event.preventDefault();
          void run("Creating API key…", async signal => {
            const option = apiKeyExpiries.find(option => option.value === expiry) ?? apiKeyExpiries[1];
            const result = await api<CreatedApiKey>(apiKeyPath(space.id), "POST", {
              name: name.trim(),
              can_write: writable && canWrite,
              expires_in_days: option.days,
            }, signal);
            signal.throwIfAborted();
            const { token: _token, ...key } = result;
            setKeys(items => [key, ...(items ?? []).filter(item => item.id !== key.id)]);
            setCreated(result);
            setName("");
            setCanWrite(false);
            setExpiry("90");
            setNotice("API key created. Copy it now.");
          });
        }}>
          <fieldset className={styles.form} disabled={disabled || keys === null || full}>
            <label className="sp-field">Name
              <input required maxLength={API_KEY_NAME_LIMIT} value={name} onChange={event => setName(event.target.value)} placeholder="Reading dashboard" autoComplete="off" aria-describedby="api-key-name-help" />
            </label>
            <p id="api-key-name-help" className="sp-muted">Name the app or script that will use it, so you know which key to revoke.</p>
            <fieldset className={styles.access}>
              <legend>Access</legend>
              <label>
                <input type="radio" name="api-key-access" checked={!canWrite} onChange={() => setCanWrite(false)} />
                <span><strong>Read-only</strong><small>Read documents, tasks, memory and projects, search, and export.</small></span>
              </label>
              {writable && (
                <label>
                  <input type="radio" name="api-key-access" checked={canWrite} onChange={() => setCanWrite(true)} />
                  <span><strong>Read and write</strong><small>Also create, edit and delete them. Changes are saved as yours.</small></span>
                </label>
              )}
            </fieldset>
            {!writable && <p className="sp-muted">You can view this Space without editing it, so your keys are read-only.</p>}
            <label className="sp-field">Expires
              <select value={expiry} onChange={event => setExpiry(event.target.value)}>
                {apiKeyExpiries.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
              </select>
            </label>
            <button className="sp-primary" disabled={!name.trim()}>Create key</button>
          </fieldset>
        </form>
        {full && <p className="sp-muted">You have {API_KEY_LIMIT} active keys in this Space. Revoke one to create another.</p>}
      </section>
      <section aria-busy={loading}>
        <div className={styles.heading}>
          <h2>Your keys</h2>
          {keys === null && !loading && <button type="button" disabled={disabled} onClick={() => void run("Loading API keys…", load)}>Try again</button>}
        </div>
        {!loading && keys?.length === 0 && <p className={styles.empty}><Icon name="code" size={16} /><span><strong>No API keys yet.</strong> Keys you create in {space.name} appear here.</span></p>}
        {!loading && keys === null && <p>API keys could not be loaded. Use Try again to reload them.</p>}
        <ul className={styles.list}>
          {keys?.map(key => (
            <li key={key.id} aria-label={`API key ${key.name}`}>
              <div className={styles.heading}>
                <h3>{key.name}</h3>
                <span className="sp-tag">{key.can_write ? "Read and write" : "Read-only"}</span>
              </div>
              <p><code className={styles.prefix}>{key.prefix}…</code></p>
              <p>
                Created {formatDay(key.created_at)}
                {" · "}{key.expires_at ? `${apiKeyExpired(key) ? "Expired" : "Expires"} ${formatDay(key.expires_at)}` : "Never expires"}
                {" · "}{key.last_used_at ? `Last used ${lastUsed(key.last_used_at)}` : "Never used"}
              </p>
              <button type="button" disabled={disabled} onClick={() => setRevoking(key)}>Revoke</button>
            </li>
          ))}
        </ul>
      </section>
      {baseUrl && (
        <section>
          <h2>Connection details</h2>
          <dl className="sp-properties">
            <div><dt>API URL</dt><dd><code>{baseUrl}</code></dd></div>
            <div><dt>Space ID</dt><dd><code>{space.id}</code></dd></div>
          </dl>
          <button type="button" onClick={() => void copy(baseUrl)}>Copy API URL</button>
        </section>
      )}
      {revoking && (
        <ConfirmModal
          title="Revoke this API key?"
          confirmLabel="Revoke key"
          busyLabel="Revoking…"
          busy={!!busy}
          onClose={() => setRevoking(null)}
          onConfirm={() => void run("Revoking API key…", async signal => {
            try {
              await api<{ revoked: boolean }>(apiKeyPath(space.id, revoking.id), "DELETE", undefined, signal);
              setNotice("API key revoked.");
            } catch (error) {
              // Already revoked elsewhere: the key is gone either way.
              if (!(error instanceof ApiError && error.status === 404)) throw error;
              setNotice("This key was already revoked.");
            }
            signal.throwIfAborted();
            setKeys(items => items?.filter(item => item.id !== revoking.id) ?? null);
            setCreated(current => current?.id === revoking.id ? null : current);
            setRevoking(null);
          })}
        >
          <p>{revoking.name} ({revoking.prefix}…) stops working on its next request. Anything already saved with it stays saved.</p>
        </ConfirmModal>
      )}
    </>
  );
}
