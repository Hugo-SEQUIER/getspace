
import { useEffect, useRef, useState } from "react";
import { useFeedback } from "@web/app/components/notifications";
import { ACCESS_RESET_EVENT, api, ApiError, invalidateReads, message } from "../../../lib/client";
import {
  canRedeliver, webhookEndpoint, webhookPath, webhookTransition,
  type WebhookAction, type WebhookDelivery, type WebhookResult, type WebhookSubscription,
} from "../../../lib/space/webhooks";
import { useSpace } from "./provider";
import { Empty, Modal } from "./ui";
import styles from "./webhook-settings.module.css";

export function WebhookSettings() {
  const data = useSpace();
  const [accessEpoch, setAccessEpoch] = useState(0);
  useEffect(() => {
    const reset = () => setAccessEpoch(value => value + 1);
    const restored = (event: PageTransitionEvent) => { if (event.persisted) reset(); };
    window.addEventListener(ACCESS_RESET_EVENT, reset);
    window.addEventListener("pageshow", restored);
    return () => {
      window.removeEventListener(ACCESS_RESET_EVENT, reset);
      window.removeEventListener("pageshow", restored);
    };
  }, []);
  return (
    <div className={styles.settings}>
      <h1>Webhooks</h1>
      <p className="sp-subtitle">Send database change events to a receiver you control.</p>
      {!data.space ? null : data.space.role !== "owner" ? (
        <p className={styles.callout}>Only the Space owner can manage webhooks and view delivery receipts.</p>
      ) : (
        <OwnerWebhooks key={`${data.accountId}:${data.space.id}:${data.space.role}:${accessEpoch}`} spaceId={data.space.id} />
      )}
    </div>
  );
}

type Confirmation = {
  kind: "remove" | "rotate" | "redeliver";
  subscriptionId: string;
  deliveryId?: string;
};

function OwnerWebhooks({ spaceId }: { spaceId: string }) {
  const data = useSpace();
  const [subscriptions, setSubscriptions] = useState<WebhookSubscription[] | null>(null);
  const [listLoading, setListLoading] = useState(true);
  const [databaseReady, setDatabaseReady] = useState(false);
  const [databaseError, setDatabaseError] = useState("");
  const [databaseId, setDatabaseId] = useState("");
  const [endpoint, setEndpoint] = useState("");
  const [fieldId, setFieldId] = useState("");
  const [to, setTo] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [secret, setSecret] = useState<{ subscriptionId: string; value: string } | null>(null);
  const [selectedId, setSelectedId] = useState("");
  const [deliveries, setDeliveries] = useState<WebhookDelivery[] | null>(null);
  const [receiptsLoading, setReceiptsLoading] = useState(false);
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null);
  const lifecycle = useRef<AbortController | null>(null);
  const running = useRef(false);
  const commandIds = useRef(new Map<string, string>());
  const databases = data.databases.filter(database => !database.deleted);
  const database = databases.find(database => database.id === databaseId);
  const selectFields = database?.fields.filter(field => field.type === "select" && field.options?.length) ?? [];
  const field = selectFields.find(field => field.id === fieldId);
  const selected = subscriptions?.find(subscription => subscription.id === selectedId);
  const target = subscriptions?.find(subscription => subscription.id === confirmation?.subscriptionId);
  const deliveryTarget = deliveries?.find(delivery => delivery.id === confirmation?.deliveryId);
  const disabled = !!busy || listLoading;
  useFeedback({
    error,
    notice,
    loading: busy || (listLoading ? "Loading webhooks…" : receiptsLoading ? "Loading delivery receipts…" : !databaseReady && !databaseError ? "Loading databases…" : undefined),
    clearError: () => setError(""),
    clearNotice: () => setNotice(""),
  });
  useFeedback({ error: databaseError });

  async function loadSubscriptions(signal: AbortSignal) {
    setListLoading(true);
    try {
      const response = await api<{ items: WebhookSubscription[] }>(webhookPath(spaceId), "GET", undefined, signal);
      signal.throwIfAborted();
      const items = response.items.filter(subscription => !subscription.removed_at);
      setSubscriptions(items);
      if (selectedId && !items.some(subscription => subscription.id === selectedId)) {
        setSelectedId("");
        setDeliveries(null);
      }
    } finally {
      if (!signal.aborted) setListLoading(false);
    }
  }

  useEffect(() => {
    const controller = new AbortController();
    lifecycle.current = controller;
    void loadSubscriptions(controller.signal).catch(error => {
      if (!controller.signal.aborted) setError(message(error));
    });
    const reset = () => {
      controller.abort();
      commandIds.current.clear();
      setSecret(null);
      setConfirmation(null);
      setSubscriptions(null);
      setDeliveries(null);
    };
    window.addEventListener(ACCESS_RESET_EVENT, reset);
    window.addEventListener("pagehide", reset);
    return () => {
      controller.abort();
      commandIds.current.clear();
      window.removeEventListener(ACCESS_RESET_EVENT, reset);
      window.removeEventListener("pagehide", reset);
    };
    // Parameters: the keyed Space scope. What it does: starts uncached reads and clears transient state on exit. Output: no retained secrets.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [spaceId]);

  useEffect(() => {
    setDatabaseReady(false);
    setDatabaseError("");
    return data.watchItems("database", false, () => {
      setDatabaseReady(true);
      setDatabaseError("");
    }, error => {
      setDatabaseReady(false);
      setDatabaseError(message(error));
    });
  }, [data.watchItems]);

  async function run(label: string, action: (signal: AbortSignal) => Promise<void>, mutation = false) {
    if (running.current || !lifecycle.current || lifecycle.current.signal.aborted) return;
    const signal = lifecycle.current.signal;
    running.current = true;
    setBusy(label);
    setError("");
    setNotice("");
    if (mutation) setSecret(null);
    try { await action(signal); }
    catch (error) {
      if (!signal.aborted) setError(error instanceof ApiError && error.status === 409
        ? `${message(error)} Refresh webhooks or receipts before trying again.` : message(error));
    } finally {
      if (!signal.aborted) {
        running.current = false;
        setBusy("");
      }
    }
  }

  async function command<T>(path: string, body: object, signal: AbortSignal, cycle = "") {
    const key = JSON.stringify({ path, body, cycle });
    const commandId = commandIds.current.get(key) ?? crypto.randomUUID();
    commandIds.current.set(key, commandId);
    const result = await api<T>(path, "POST", { ...body, command_id: commandId }, signal);
    signal.throwIfAborted();
    commandIds.current.delete(key);
    return result;
  }

  function saveSubscription(subscription: WebhookSubscription) {
    setSubscriptions(items => subscription.removed_at
      ? (items ?? []).filter(item => item.id !== subscription.id)
      : [subscription, ...(items ?? []).filter(item => item.id !== subscription.id)]);
  }

  async function change(subscription: WebhookSubscription, action: WebhookAction, signal: AbortSignal) {
    const result = await command<WebhookResult>(webhookPath(spaceId, subscription.id), {
      expected_revision: subscription.revision, action,
    }, signal);
    if (action === "remove") {
      setSubscriptions(items => (items ?? []).filter(item => item.id !== subscription.id));
      if (selectedId === subscription.id) { setSelectedId(""); setDeliveries(null); }
      setNotice("Webhook removed.");
    } else {
      saveSubscription(result.subscription);
      if (action === "rotate") {
        if (result.signing_secret) setSecret({ subscriptionId: subscription.id, value: result.signing_secret });
        setNotice(result.signing_secret ? "Signing secret rotated. Save the new secret now." : "Secret rotated, but the new secret was not returned. Rotate again to obtain one.");
      } else setNotice(action === "pause" ? "Webhook paused." : "Webhook resumed.");
    }
    setConfirmation(null);
  }

  async function loadDeliveries(subscriptionId: string, signal: AbortSignal) {
    setReceiptsLoading(true);
    try {
      const response = await api<{ items: WebhookDelivery[] }>(`${webhookPath(spaceId, subscriptionId)}/deliveries`, "GET", undefined, signal);
      signal.throwIfAborted();
      setDeliveries(response.items);
    } finally {
      if (!signal.aborted) setReceiptsLoading(false);
    }
  }

  function databaseName(subscription: WebhookSubscription) {
    return databases.find(database => database.id === subscription.database_id)?.name ?? `Database ${subscription.database_id}`;
  }

  function transitionName(subscription: WebhookSubscription) {
    if (!subscription.transition) return "No transition filter";
    const field = databases.find(database => database.id === subscription.database_id)?.fields.find(field => field.id === subscription.transition?.field_id);
    return `${field?.name ?? subscription.transition.field_id} → ${subscription.transition.to}`;
  }

  return (
    <>
      <p className={styles.callout}>Events disclose identifiers, event type and revisions, not whole records. A transition filter also discloses the selected field’s before and after values to your receiver. Record names, notes and other field values are not sent.</p>
      {secret && (
        <section className={styles.secret} aria-label="One-time signing secret">
          <h2>Save your signing secret</h2>
          <p className={styles.endpoint}>Webhook: {subscriptions?.find(subscription => subscription.id === secret.subscriptionId)?.endpoint_url ?? secret.subscriptionId}</p>
          <p>This secret is shown only after creation or rotation. Save it securely on your receiver. It cannot be retrieved later. Rotation replaces the previous secret.</p>
          <label className="sp-field">Signing secret
            <textarea readOnly value={secret.value} autoComplete="off" spellCheck={false} rows={2} onFocus={event => event.currentTarget.select()} />
          </label>
          <button type="button" disabled={!!busy} onClick={() => setSecret(null)}>I’ve saved it</button>
        </section>
      )}
      <section>
        <h2>Create webhook</h2>
        {databaseError && <button type="button" disabled={disabled} onClick={() => {
          setDatabaseError("");
          invalidateReads(`spaces/${spaceId}/items?kind=database&deleted=false`);
        }}>Retry databases</button>}
        {databaseReady && !databases.length && <p>No databases in this Space. Create a database before adding a webhook.</p>}
        <form onSubmit={event => {
          event.preventDefault();
          void run("Creating webhook…", async signal => {
            if (!database || database.deleted) throw new Error("Choose a current database in this Space.");
            const result = await command<WebhookResult>(webhookPath(spaceId), {
              database_id: database.id,
              endpoint_url: webhookEndpoint(endpoint),
              transition: webhookTransition(database, fieldId, to),
            }, signal);
            saveSubscription(result.subscription);
            if (result.signing_secret) setSecret({ subscriptionId: result.subscription.id, value: result.signing_secret });
            setEndpoint("");
            setNotice(result.signing_secret ? "Webhook created. Save the signing secret now." : "Webhook created, but no secret was returned. Rotate the secret to obtain one.");
          }, true);
        }}>
          <fieldset className={styles.form} disabled={disabled || !databaseReady || !databases.length || subscriptions === null}>
            <label className="sp-field">Database
              <select required value={databaseId} onChange={event => { setDatabaseId(event.target.value); setFieldId(""); setTo(""); }}>
                <option value="">Choose a database</option>
                {databases.map(database => <option key={database.id} value={database.id}>{database.name}</option>)}
              </select>
            </label>
            <label className="sp-field">HTTPS endpoint
              <input type="url" required maxLength={2048} value={endpoint} onChange={event => setEndpoint(event.target.value)} placeholder="https://receiver.example.com/webhook" autoComplete="off" aria-describedby="webhook-endpoint-help" />
            </label>
            <p id="webhook-endpoint-help" className="sp-muted">Use a public HTTPS receiver you control. Private network addresses are not allowed. Creating a webhook does not send a test event.</p>
            <label className="sp-field">Transition filter (optional)
              <select value={fieldId} onChange={event => { setFieldId(event.target.value); setTo(""); }} disabled={!database}>
                <option value="">No transition filter</option>
                {selectFields.map(field => <option key={field.id} value={field.id}>{field.name}</option>)}
              </select>
            </label>
            {fieldId && <label className="sp-field">Changes to
              <select required value={to} onChange={event => setTo(event.target.value)}>
                <option value="">Choose an option</option>
                {field?.options?.map(option => <option key={option} value={option}>{option}</option>)}
              </select>
            </label>}
            {database && !selectFields.length && <p className="sp-muted">This database has no select fields with options. You can create a webhook without a transition filter.</p>}
            {field && <p className="sp-muted">Send an event when {field.name} changes to {to || "the chosen option"}. Only this selected field’s before and after values are disclosed, with event metadata.</p>}
            <button className="sp-primary" disabled={!database || !endpoint.trim() || !!fieldId && !field?.options?.includes(to)}>Create webhook</button>
          </fieldset>
        </form>
      </section>
      <section aria-busy={listLoading}>
        <div className={styles.heading}>
          <h2>Saved webhooks</h2>
          <button type="button" disabled={disabled} onClick={() => void run("Refreshing webhooks…", loadSubscriptions)}>Refresh webhooks</button>
        </div>
        {!listLoading && subscriptions?.length === 0 && <Empty icon="external" title="No webhooks yet">Add a receiver for a database in this Space.</Empty>}
        {!listLoading && subscriptions === null && <p>Webhooks could not be loaded. Use Refresh webhooks to try again.</p>}
        <ul className={styles.list}>
          {subscriptions?.map(subscription => (
            <li key={subscription.id} aria-label={`Webhook for ${databaseName(subscription)} at ${subscription.endpoint_url}`}>
              <div className={styles.heading}>
                <h3>{databaseName(subscription)}</h3>
                <span className="sp-tag">{subscription.paused ? "Paused" : "Active"}</span>
              </div>
              <p className={styles.endpoint}>{subscription.endpoint_url}</p>
              <p>{transitionName(subscription)} · Revision {subscription.revision}</p>
              <div className={styles.actions}>
                <button type="button" disabled={disabled} onClick={() => void run(subscription.paused ? "Resuming webhook…" : "Pausing webhook…", signal => change(subscription, subscription.paused ? "resume" : "pause", signal), true)}>{subscription.paused ? "Resume" : "Pause"}</button>
                <button type="button" disabled={disabled} onClick={() => setConfirmation({ kind: "rotate", subscriptionId: subscription.id })}>Rotate secret</button>
                <button type="button" disabled={disabled} onClick={() => setConfirmation({ kind: "remove", subscriptionId: subscription.id })}>Remove</button>
                <button type="button" disabled={disabled} aria-pressed={selectedId === subscription.id} onClick={() => {
                  setSelectedId(subscription.id);
                  setDeliveries(null);
                  void run("Loading receipts…", signal => loadDeliveries(subscription.id, signal));
                }}>View receipts</button>
              </div>
            </li>
          ))}
        </ul>
      </section>
      {selected && (
        <section aria-busy={receiptsLoading} aria-label="Delivery receipts">
          <div className={styles.heading}>
            <h2>Delivery receipts</h2>
            <button type="button" disabled={disabled} onClick={() => void run("Refreshing receipts…", signal => loadDeliveries(selected.id, signal))}>Refresh receipts</button>
          </div>
          <p className={styles.endpoint}>{selected.endpoint_url}</p>
          <p>Latest 100 deliveries. Refresh manually to see new attempts. No receiver response bodies are stored here.</p>
          {!receiptsLoading && deliveries === null && <p>Receipts could not be loaded. Use Refresh receipts to try again.</p>}
          {deliveries?.length === 0 && <p>No deliveries yet.</p>}
          {selected.paused && <p className="sp-muted">Resume this webhook before redelivering an event.</p>}
          <ul className={styles.list}>
            {deliveries?.map(delivery => (
              <li key={delivery.id} aria-label={`Delivery ${delivery.event_id}`}>
                <div className={styles.heading}>
                  <h3>{delivery.event_type}</h3>
                  <span className="sp-tag">{delivery.status}</span>
                </div>
                <dl className={`sp-properties ${styles.properties}`}>
                  <div><dt>Event ID</dt><dd>{delivery.event_id}</dd></div>
                  <div><dt>Delivery ID</dt><dd>{delivery.id}</dd></div>
                  <div><dt>Record ID</dt><dd>{delivery.record_id}</dd></div>
                  <div><dt>Revision</dt><dd>{delivery.previous_revision ?? "None"} → {delivery.revision}</dd></div>
                  <div><dt>Attempts</dt><dd>{delivery.attempts} · Cycle {delivery.cycle}</dd></div>
                  <div><dt>Created</dt><dd>{delivery.created_at}</dd></div>
                  <div><dt>Updated</dt><dd>{delivery.updated_at}</dd></div>
                  {delivery.available_at && <div><dt>Available</dt><dd>{delivery.available_at}</dd></div>}
                  {delivery.status_code !== null && <div><dt>HTTP status</dt><dd>{delivery.status_code}</dd></div>}
                  {delivery.error_code && <div><dt>Error code</dt><dd>{delivery.error_code}</dd></div>}
                </dl>
                <details className={styles.receipts}>
                  <summary>Attempt receipts ({delivery.receipts.length})</summary>
                  {delivery.receipts.length === 0 ? <p>No attempt receipts yet.</p> : <ol>
                    {delivery.receipts.map(receipt => <li key={`${receipt.cycle}:${receipt.attempt}`}>
                      <strong>Cycle {receipt.cycle} · Attempt {receipt.attempt}</strong>
                      <span>Started: {receipt.started_at}</span>
                      <span>Finished: {receipt.finished_at ?? "In progress"}</span>
                      <span>HTTP status: {receipt.status_code ?? "No response"}</span>
                      {receipt.error_code && <span>Error code: {receipt.error_code}</span>}
                    </li>)}
                  </ol>}
                </details>
                <button type="button" disabled={disabled || !canRedeliver(selected, delivery)} onClick={() => setConfirmation({ kind: "redeliver", subscriptionId: selected.id, deliveryId: delivery.id })}>Redeliver</button>
                {(delivery.status === "queued" || delivery.status === "processing") && <p className="sp-muted">Wait for a terminal result before redelivery.</p>}
              </li>
            ))}
          </ul>
        </section>
      )}
      {confirmation && target && (
        <Modal size="small" className="sp-confirm-modal" title={confirmation.kind === "remove" ? "Remove webhook?" : confirmation.kind === "rotate" ? "Rotate signing secret?" : "Redeliver event?"} onClose={() => { if (!busy) setConfirmation(null); }}>
          <div className={`sp-modal-content ${styles.confirmation}`}>
            <p className={styles.endpoint}>{target.endpoint_url}</p>
            <p>{confirmation.kind === "remove" ? "This stops future deliveries for this webhook. An in-flight request may already have reached your receiver." : confirmation.kind === "rotate" ? "The previous signing secret will be replaced. Update your receiver with the new secret shown once after rotation." : "This sends the same event again and can repeat receiver side effects. Your receiver should deduplicate by event ID."}</p>
            {confirmation.kind === "redeliver" && <p>Event ID: {deliveryTarget?.event_id}</p>}
            <div className="sp-modal-footer">
              <button type="button" disabled={!!busy} onClick={() => setConfirmation(null)}>Cancel</button>
              <button type="button" className={confirmation.kind === "remove" ? "sp-danger" : "sp-primary"} disabled={disabled || confirmation.kind === "redeliver" && (!deliveryTarget || !canRedeliver(target, deliveryTarget))} onClick={() => {
                const action = confirmation.kind;
                void run(action === "remove" ? "Removing webhook…" : action === "rotate" ? "Rotating secret…" : "Redelivering event…", async signal => {
                  if (action !== "redeliver") return change(target, action, signal);
                  if (!deliveryTarget || !canRedeliver(target, deliveryTarget)) throw new Error("Only terminal deliveries for an active webhook can be redelivered.");
                  const result = await command<WebhookDelivery>(`${webhookPath(spaceId, target.id)}/deliveries/${encodeURIComponent(deliveryTarget.id)}/redeliver`, {}, signal, String(deliveryTarget.cycle));
                  setDeliveries(items => items?.map(item => item.id === result.id ? result : item) ?? [result]);
                  setConfirmation(null);
                  setNotice("Redelivery queued. Refresh receipts to see its result.");
                }, true);
              }}>{busy ? "Working…" : confirmation.kind === "remove" ? "Remove webhook" : confirmation.kind === "rotate" ? "Rotate secret" : "Redeliver event"}</button>
            </div>
          </div>
        </Modal>
      )}
    </>
  );
}
