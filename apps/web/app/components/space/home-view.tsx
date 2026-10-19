import Link, { useRouter } from "@web/lib/navigation";
import { lazy, Suspense, useEffect, useState } from "react";
import { message } from "../../../lib/client";
import { groupAgentConnections } from "../../../lib/space/agents";
import { attentionItems, unseenByProject } from "../../../lib/space/attention";
import type { Resource } from "../../../lib/space/types";
import { checklistHiddenKey, gettingStartedSteps, onboardingKey, type ChecklistStep } from "../../../lib/space/onboarding";
import { useSpace } from "./provider";
import { Icon, type IconName } from "./ui-icon";
import { Avatar, Modal, StatusDot, authorName, relativeDate } from "./ui";
import { Onboarding } from "./onboarding";
import { PageIcon } from "./sidebar-preferences";
import "./onboarding.css";

const ResourceForm = lazy(() => import("./resource-views").then(module => ({ default: module.ResourceForm })));

function greeting(date: Date) {
  const hour = date.getHours();
  return hour < 5 ? "Good evening" : hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
}

function browserStorage() {
  try { return window.localStorage; } catch { return undefined; }
}

export function HomeView({ base, onSearch, searchShortcut }: {
  base: string;
  onSearch: () => void;
  searchShortcut: string;
}) {
  const data = useSpace();
  const router = useRouter();
  useEffect(() => data.watchAgents(), [data.watchAgents]);
  useEffect(() => data.watchViews(), [data.watchViews]);
  const [create, setCreate] = useState<"document" | "task" | null>(null);
  const projectNames = new Map(data.projects.map(project => [project.id, project.name]));
  const attention = attentionItems(data.resources, data.views);
  const documents = data.resources
    .filter(resource => resource.kind === "document" && resource.recall?.category !== "memory")
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at)).slice(0, 4);
  const tasks = data.resources.filter(resource => resource.kind === "task")
    .sort((a, b) => Number(a.status === "done") - Number(b.status === "done") || b.updated_at.localeCompare(a.updated_at))
    .slice(0, 5);
  const connections = groupAgentConnections(data.agents, data.grants.filter(grant => !grant.revoked_at))
    .filter(group => group.connections.length);
  const name = data.profile.displayName.split(" ")[0];
  const now = new Date();
  // The checklist depends on agent connections and the "new" list on seen marks. Wait for both,
  // so the page opens in its final layout instead of reshuffling as they arrive.
  const ready = data.resourcesReady && data.agentsReady && data.viewsSettled;
  return <div className="sp-home-v2">
    <div className="sp-home-band" aria-hidden="true" />
    <header className="sp-home-v2-heading">
      <p className="sp-home-v2-date">{now.toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" })}</p>
      <h1>{greeting(now)}{name ? `, ${name}` : ""}</h1>
    </header>
    <div className="sp-home-v2-actions" role="group" aria-label="Quick actions">
      <button type="button" disabled={!data.canWrite} onClick={() => setCreate("document")}><Icon name="document" size={16} />New page</button>
      <button type="button" disabled={!data.canWrite} onClick={() => setCreate("task")}><Icon name="tasks" size={16} />New task</button>
      <button type="button" onClick={onSearch}><Icon name="search" size={16} />Search<kbd aria-hidden="true">{searchShortcut}</kbd></button>
    </div>
    {!ready ? <div role="status" aria-label="Loading home"><div className="sp-skeleton" /></div> : <>
    <Checklist base={base} steps={gettingStartedSteps(data.resources, connections.length)} onCreate={setCreate} />
    {attention.needsYou.length > 0 && <section className="sp-home-v2-section sp-home-attention" aria-label="Needs you">
      <div className="sp-home-v2-section-head">
        <h2 id="home-needs-you-title">Needs you<span className="sp-home-attention-count">{attention.needsYou.length}</span></h2>
        <Link href={`${base}/tasks`}>All tasks</Link>
      </div>
      <p className="sp-home-attention-hint">Agents flagged these as waiting on your decision, approval or merge. Change the status once you have answered.</p>
      <AttentionList base={base} items={attention.needsYou} projectNames={projectNames} />
    </section>}
    {data.viewsReady && data.resourcesReady && attention.unseen.length > 0 && <section className="sp-home-v2-section sp-home-attention" aria-label="New since you looked">
      <div className="sp-home-v2-section-head">
        <h2 id="home-unseen-title">New since you looked<span className="sp-home-attention-count">{attention.unseen.length}</span></h2>
        <button type="button" className="sp-text-button" onClick={() => void data.markSeen(attention.unseen.map(item => ({ id: item.id, revision: item.revision })))}>
          <Icon name="check" size={15} />Mark all as seen
        </button>
      </div>
      <p className="sp-home-attention-projects">
        {unseenByProject(attention.unseen, projectNames).map(group => <span key={group.label}>{group.label} <strong>{group.count}</strong></span>)}
      </p>
      <AttentionList base={base} items={attention.unseen.slice(0, 6)} projectNames={projectNames} />
      {attention.unseen.length > 6 && <p className="sp-home-attention-more">And {attention.unseen.length - 6} more. Opening an item marks it as seen.</p>}
    </section>}
    <section className="sp-home-v2-section" aria-labelledby="home-documents-title">
      <div className="sp-home-v2-section-head">
        <h2 id="home-documents-title">Jump back in</h2>
        <Link href={`${base}/documents`}>All pages</Link>
      </div>
      {documents.length ? <ul className="sp-home-v2-documents">
        {documents.map(document => <li key={document.id}>
          <Link href={`${base}/documents/${document.id}`}>
            <span className="sp-home-v2-cover" aria-hidden="true" />
            <span className="sp-home-v2-file"><PageIcon pageKey={`document:${document.id}`} fallback="document" size={18} /></span>
            <strong>{document.title}</strong>
            <small>Edited {relativeDate(document.updated_at)}</small>
          </Link>
        </li>)}
      </ul> : <div className="sp-home-v2-empty">
        <p>Pages you write or open will show up here.</p>
        {data.canWrite && <button className="sp-text-button" onClick={() => setCreate("document")}><Icon name="plus" size={15} />New page</button>}
      </div>}
    </section>
    <div className="sp-home-v2-columns">
      <section className="sp-home-v2-section" aria-labelledby="home-tasks-title">
        <div className="sp-home-v2-section-head">
          <h2 id="home-tasks-title">Tasks</h2>
          <Link href={`${base}/tasks`}>All tasks</Link>
        </div>
        {tasks.length ? <ul className="sp-home-v2-tasks">
          {tasks.map(task => <li key={task.id} className={task.status === "done" ? "is-done" : ""}>
            <button type="button" role="checkbox" aria-checked={task.status === "done"}
              aria-label={`Complete ${task.title}`} className="sp-home-v2-task-check"
              disabled={!data.canWrite}
              onClick={() => void data.changeTaskStatus(task, task.status === "done" ? "todo" : "done").catch(error => data.setError(message(error)))}>
              {task.status === "done" && <Icon name="check" size={12} />}
            </button>
            <Link href={`${base}/tasks/${task.id}`}>{task.title}</Link>
            {task.status === "in_progress" && <StatusDot status={task.status} />}
          </li>)}
        </ul> : <div className="sp-home-v2-empty">
          <p>Nothing on your list yet.</p>
          {data.canWrite && <button className="sp-text-button" onClick={() => setCreate("task")}><Icon name="plus" size={15} />New task</button>}
        </div>}
      </section>
      <section className="sp-home-v2-section" aria-labelledby="home-agent-title">
        <div className="sp-home-v2-section-head">
          <h2 id="home-agent-title">Agents</h2>
          <Link href={`${base}/agents`}>{connections.length ? "Manage" : "Set up"}</Link>
        </div>
        {connections.length ? <ul className="sp-home-v2-agents">
          {connections.slice(0, 4).map(group => <li key={group.id}>
            <Avatar name={group.agent?.name ?? "Unidentified agent"} harness={group.agent?.harness} kind="agent" small />
            <span className="sp-home-v2-agent-name">{group.agent?.name ?? "Unidentified agent"}</span>
            <small>{group.connections.length} {group.connections.length === 1 ? "connection" : "connections"}</small>
          </li>)}
        </ul> : <div className="sp-home-v2-empty">
          <p>Connect an agent so it can save context here and pick up where you left off.</p>
          <Link className="sp-text-button" href={`${base}/agents`}><Icon name="agents" size={15} />Connect an agent</Link>
        </div>}
      </section>
    </div>
    </>}
    <Onboarding base={base} />
    {create && <Suspense fallback={<Modal title={`New ${create}`} onClose={() => setCreate(null)}><p role="status" className="sp-modal-content">Opening editor…</p></Modal>}>
      <ResourceForm kind={create} onClose={() => setCreate(null)} onSaved={resource => {
        setCreate(null);
        router.push(`${base}/${resource.kind === "task" ? "tasks" : "documents"}/${resource.id}`);
      }} />
    </Suspense>}
  </div>;
}

// Parameters: documents or tasks that need the person's attention and the project names.
// What it does: lists each item with who last wrote it, its project and when.
// Output: links into the task or document views.
function AttentionList({ base, items, projectNames }: { base: string; items: Resource[]; projectNames: Map<string, string> }) {
  return <ul className="sp-home-attention-list">
    {items.map(item => <li key={item.id}>
      <span className="sp-home-attention-kind" aria-hidden="true"><Icon name={item.kind === "task" ? "tasks" : "document"} size={15} /></span>
      <Link href={`${base}/${item.kind === "task" ? "tasks" : "documents"}/${item.id}`}>{item.title}</Link>
      <small>
        {authorName(item.author, item.author_agent)}
        {item.project && projectNames.get(item.project) ? ` · ${projectNames.get(item.project)}` : ""}
        {` · ${relativeDate(item.updated_at)}`}
      </small>
    </li>)}
  </ul>;
}

const checklistCopy: Record<ChecklistStep["id"], { title: string; detail: string; icon: IconName }> = {
  page: { title: "Write your first page", detail: "A note, a plan, or a draft.", icon: "document" },
  task: { title: "Add a task", detail: "Something you want to get done.", icon: "tasks" },
  agent: { title: "Connect an agent", detail: "Let your AI tools recall and save context.", icon: "agents" },
};

// Parameters: getting-started steps derived from saved data, and the create action.
// What it does: shows a dismissible first-run checklist until every step is done.
// Output: a short list of next actions, or nothing once complete or hidden.
function Checklist({ base, steps, onCreate }: { base: string; steps: ChecklistStep[]; onCreate: (kind: "document" | "task") => void }) {
  const data = useSpace();
  const key = onboardingKey(data.accountId, data.space?.id);
  const storageKey = key ? checklistHiddenKey(key) : null;
  const [hidden, setHidden] = useState(true);
  useEffect(() => {
    try { setHidden(!!storageKey && browserStorage()?.getItem(storageKey) === "1"); }
    catch { setHidden(false); }
  }, [storageKey]);
  const done = steps.filter(step => step.done).length;
  if (hidden || !data.canWrite || done === steps.length) return null;
  function hide() {
    setHidden(true);
    try { if (storageKey) browserStorage()?.setItem(storageKey, "1"); } catch { /* Hidden for this visit only. */ }
  }
  return <section className="sp-home-checklist" aria-labelledby="home-checklist-title">
    <div className="sp-home-checklist-head">
      <div>
        <h2 id="home-checklist-title">Get started</h2>
        <p>{done} of {steps.length} done</p>
      </div>
      <div className="sp-home-checklist-tools">
        <button type="button" className="sp-text-button" onClick={() => window.dispatchEvent(new Event("space:open-onboarding"))}>Take the tour</button>
        <button type="button" className="sp-icon-button" aria-label="Hide getting started" title="Hide" onClick={hide}><Icon name="close" size={15} /></button>
      </div>
    </div>
    <div className="sp-home-checklist-bar" aria-hidden="true"><span style={{ width: `${(done / steps.length) * 100}%` }} /></div>
    <ul>
      {steps.map(step => {
        const copy = checklistCopy[step.id];
        const body = <>
          <span className="sp-home-checklist-check" aria-hidden="true">{step.done && <Icon name="check" size={12} />}</span>
          <span><strong>{step.done && <span className="sp-onboarding-sr">Done: </span>}{copy.title}</strong><small>{copy.detail}</small></span>
          {!step.done && <Icon name="arrow" size={15} />}
        </>;
        return <li key={step.id} className={step.done ? "is-done" : ""}>
          {step.done ? <div>{body}</div>
            : step.id === "agent" ? <Link href={`${base}/agents`}>{body}</Link>
              : <button type="button" onClick={() => onCreate(step.id === "page" ? "document" : "task")}>{body}</button>}
        </li>;
      })}
    </ul>
  </section>;
}
