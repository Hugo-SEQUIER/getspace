import Link, { useRouter, useSearchParams } from "@web/lib/navigation";
import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { useFeedback } from "@web/app/components/notifications";
import { message } from "../../../lib/client";
import { createFirstPage, initialOnboarding, onboardingKey, readOnboarding, shouldOfferOnboarding, writeOnboarding, type OnboardingFocus, type OnboardingProgress } from "../../../lib/space/onboarding";
import { useSpace } from "./provider";
import { Icon, type IconName } from "./ui-icon";
import { Modal } from "./ui";
import "./onboarding.css";

const choices: { id: OnboardingFocus; title: string; description: string; icon: IconName; placeholder: string }[] = [
  { id: "notes", title: "Personal notes", description: "Ideas, plans, and things worth keeping.", icon: "document", placeholder: "A few things on my mind" },
  { id: "projects", title: "Project work", description: "Plans, tasks, and decisions for a project.", icon: "folder", placeholder: "My next project" },
  { id: "research", title: "Research", description: "Sources and what you learn from them.", icon: "book", placeholder: "Something I want to explore" },
];
const headings = ["Welcome to Space", "What will you use Space for?", "Name your first page"];
function browserStorage() {
  try { return window.localStorage; } catch { return undefined; }
}

export function Onboarding({ base }: { base: string }) {
  const data = useSpace();
  const key = onboardingKey(data.accountId, data.space?.id);
  return key ? <OnboardingState key={key} storageKey={key} base={base} /> : null;
}

function OnboardingState({ storageKey, base }: { storageKey: string; base: string }) {
  const data = useSpace();
  const router = useRouter();
  const params = useSearchParams();
  const requested = params.get("setup") === "1";
  const [progress, setProgress] = useState(initialOnboarding);
  const progressRef = useRef(progress);
  const [ready, setReady] = useState(false);
  const [open, setOpen] = useState(false);
  const [storageUnavailable, setStorageUnavailable] = useState(false);
  const [title, setTitle] = useState("");
  const [notes, setNotes] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [createdId, setCreatedId] = useState<string | null>(null);
  const saving = useRef(false);
  const alive = useRef(true);
  const heading = useRef<HTMLHeadingElement>(null);
  const requestHandled = useRef(false);
  const currentScope = useRef(storageKey);
  currentScope.current = storageKey;
  const canAutoOffer = shouldOfferOnboarding({
    accountId: data.accountId, kind: data.space?.kind,
    canWrite: data.canWrite, empty: data.resources.length === 0,
    ready: !data.loading && !data.pageLoading && !data.pageError,
  });
  useFeedback({
    error,
    notice: open && storageUnavailable ? "Setup progress is kept for this session. Browser storage is unavailable." : null,
    loading: busy ? "Creating…" : null,
    clearError: () => setError(""),
  });

  const persist = useCallback((next: OnboardingProgress) => {
    progressRef.current = next;
    setProgress(next);
    setStorageUnavailable(!writeOnboarding(storageKey, next, browserStorage()));
  }, [storageKey]);
  const openSetup = useCallback(() => {
    const prior = progressRef.current;
    persist(prior.status === "completed" ? initialOnboarding() : { ...prior, status: "active" });
    setCreatedId(null);
    setError("");
    setOpen(true);
  }, [persist]);

  useEffect(() => {
    alive.current = true;
    const stored = readOnboarding(storageKey, browserStorage());
    const next = stored ?? initialOnboarding();
    progressRef.current = next;
    setProgress(next);
    setReady(true);
    if (canAutoOffer && (!stored || stored.status === "active")) setOpen(true);
    return () => { alive.current = false; };
    // The keyed component scopes one initialization to the verified account and Space.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [storageKey]);

  useEffect(() => {
    if (!requested) { requestHandled.current = false; return; }
    if (!ready || requestHandled.current) return;
    requestHandled.current = true;
    openSetup();
    const search = new URLSearchParams(window.location.search);
    search.delete("setup");
    router.replace(`${base}${search.size ? `?${search}` : ""}${window.location.hash}`, { scroll: false });
  }, [requested, ready, openSetup, base, router]);
  useEffect(() => {
    if (!ready) return;
    window.addEventListener("space:open-onboarding", openSetup);
    return () => window.removeEventListener("space:open-onboarding", openSetup);
  }, [ready, openSetup]);
  useEffect(() => {
    if (open) heading.current?.focus({ preventScroll: true });
  }, [open, progress.step, createdId]);

  function close() {
    if (saving.current) return;
    if (!createdId) persist({ ...progressRef.current, status: "skipped" });
    setOpen(false);
  }
  function go(step: OnboardingProgress["step"]) {
    persist({ ...progressRef.current, step, status: "active" });
    setError("");
  }
  async function create(event: FormEvent) {
    event.preventDefault();
    if (saving.current) return;
    if (!data.canWrite) { setError("You have view-only access to this Space."); return; }
    saving.current = true;
    setBusy(true);
    setError("");
    const scope = storageKey;
    try {
      const result = await createFirstPage(data.saveResource, { title, notes }, progressRef.current);
      if (!alive.current || currentScope.current !== scope) return;
      persist(result.progress);
      setCreatedId(result.resource.id);
      setTitle("");
      setNotes("");
    } catch (cause) {
      if (alive.current && currentScope.current === scope) setError(message(cause));
    } finally {
      saving.current = false;
      if (alive.current && currentScope.current === scope) setBusy(false);
    }
  }

  const focus = choices.find(choice => choice.id === progress.focus)!;
  if (!open) return null;
  return <Modal title="Get started with Space" className={`sp-onboarding${busy ? " is-saving" : ""}`} onClose={close}>
    <div className="sp-onboarding-content">
      <ol className="sp-onboarding-steps" aria-label="Setup progress">
        {["Welcome", "Your focus", "First page"].map((label, step) => <li key={label} className={step < progress.step || createdId ? "is-complete" : step === progress.step ? "is-current" : ""} aria-current={step === progress.step && !createdId ? "step" : undefined}>
          <span className="sp-onboarding-sr">{label}{step < progress.step || createdId ? ", done" : ""}</span>
        </li>)}
      </ol>
      <div className="sp-onboarding-heading">
        <h2 ref={heading} tabIndex={-1} data-autofocus>{createdId ? "Your first page is ready" : headings[progress.step]}</h2>
        <p>{createdId ? "Next, connect an agent so it can pick up where you left off."
          : progress.step === 0 ? "One calm place for your notes, your tasks, and the context your AI agents remember."
            : progress.step === 1 ? "Pick one to start. You can always explore the rest later."
              : "A thought, a plan, or a blank page. You can add more anytime."}</p>
      </div>
      {createdId ? <div className="sp-onboarding-success">
        <Link className="sp-button sp-primary" href={`${base}/documents/${createdId}`} onClick={() => setOpen(false)}>Open page</Link>
        <Link className="sp-button" href={`${base}/agents`} onClick={() => setOpen(false)}><Icon name="agents" size={16} />Connect an agent</Link>
        <button type="button" className="sp-onboarding-skip" onClick={close}>Done</button>
      </div> : <form onSubmit={progress.step === 2 ? create : event => { event.preventDefault(); go((progress.step + 1) as 1 | 2); }}>
        {progress.step === 0 && <ul className="sp-onboarding-welcome">
          <li><span><Icon name="document" size={18} /></span><div><strong>Write it down</strong><p>Pages for notes, plans, and research.</p></div></li>
          <li><span><Icon name="tasks" size={18} /></span><div><strong>Keep track</strong><p>Simple tasks on a board or a list.</p></div></li>
          <li><span><Icon name="agents" size={18} /></span><div><strong>Remember together</strong><p>Connected agents can recall and save context in your Space.</p></div></li>
        </ul>}
        {progress.step === 1 && <fieldset className="sp-onboarding-choices">
          <legend className="sp-onboarding-sr">What will you use Space for?</legend>
          {choices.map(choice => <label key={choice.id} className={progress.focus === choice.id ? "is-selected" : ""}>
            <span className="sp-onboarding-choice-icon"><Icon name={choice.icon} size={18} /></span>
            <span><strong>{choice.title}</strong><small>{choice.description}</small></span>
            <input type="radio" name="onboarding-focus" value={choice.id} checked={progress.focus === choice.id} onChange={() => persist({ ...progressRef.current, focus: choice.id })} />
          </label>)}
        </fieldset>}
        {progress.step === 2 && <div className="sp-onboarding-fields">
          <label htmlFor="onboarding-title">Page name</label>
          <input id="onboarding-title" value={title} onChange={event => setTitle(event.target.value)} placeholder={focus.placeholder} required maxLength={300} disabled={busy || !data.canWrite} autoComplete="off" />
          <label htmlFor="onboarding-notes">Notes <span>Optional</span></label>
          <textarea id="onboarding-notes" value={notes} onChange={event => setNotes(event.target.value)} placeholder="What's on your mind?" maxLength={10000} rows={3} disabled={busy || !data.canWrite} />
          {!data.canWrite && <p role="status">You have view-only access. Open a Space you can edit to create a page.</p>}
        </div>}
        <div className="sp-onboarding-footer">
          {progress.step === 0
            ? <button type="button" className="sp-onboarding-skip" onClick={close} disabled={busy}>Skip for now</button>
            : <button type="button" className="sp-onboarding-skip" onClick={() => go((progress.step - 1) as 0 | 1)} disabled={busy}>Back</button>}
          <span>Step {progress.step + 1} of 3</span>
          <button type="submit" className="sp-primary" disabled={busy || (progress.step === 2 && (!data.canWrite || !title.trim()))}>
            {busy ? "Creating…" : progress.step === 2 ? "Create page" : "Continue"}
          </button>
        </div>
      </form>}
    </div>
  </Modal>;
}
