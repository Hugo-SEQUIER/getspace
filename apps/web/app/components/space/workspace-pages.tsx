
import { TeamSettings, SpaceAccessSettings } from "./team-settings";
import { WorkspaceUsage } from "./workspace-usage";
import { WebhookSettings } from "./webhook-settings";
import { ApiKeySettings } from "./api-key-settings";
import { WorkspaceRestore } from "./workspace-restore";
import { WorkspaceExport } from "./workspace-export";
import { teamsAvailable } from "../../../lib/features";
import { docsLinks, sourceCode } from "../../../lib/docs-links";
import { profilePhoto } from "../../../lib/space/profile-photo";

import Link from "@web/lib/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "@web/lib/navigation";
import { message } from "../../../lib/client";
import { useFeedback } from "../notifications";
import { statuses, type Project, type Status } from "../../../lib/space/types";
import { useSpace } from "./provider";
import { localEntries, useWorkspaceSearch } from "./workspace-search";
import { ItemTrash } from "./item-trash";
import { Icon } from "./ui-icon";
import {
  Avatar,
  Empty,
  Modal,
  StatusDot,
  relativeDate,
  useConfirm,
} from "./ui";
import { ResourceForm } from "./resource-views";
import { DocumentMarkdown } from "./document-markdown";
import { ProjectDatabases } from "./project-databases";
import "./work-views.css";

export { HomeView } from "./home-view";

export function SearchView({ base }: { base: string }) {
  const data = useSpace();
  const [query, setQuery] = useState(""),
    [type, setType] = useState("All");
  // Titles the workspace already knows answer at once; bodies join when the index lands.
  const local = useMemo(() => localEntries(data), [data.resources, data.projects, data.databases, data.memories]);
  const search = useWorkspaceSearch(data.space?.id, base, query, true, undefined, local);
  useFeedback({ error: search.error, loading: search.loading ? "Loading full text…" : null });
  const results = search.items.filter((r) => type === "All" || r.type === type);
  const [limit, setLimit] = useState(80);
  return (
    <>
      <div className="sp-topbar">
        <strong>Search</strong>
        <span className="sp-muted">
          {data.space?.name}
        </span>
      </div>
      <div className="sp-search-page">
        <label className="sp-global-search">
          <Icon name="search" size={24} />
          <input
            autoFocus
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setLimit(80);
            }}
            placeholder="Find something in your space…"
            aria-label="Search this space"
          />
          <kbd>⌘ K</kbd>
        </label>
        <div className="sp-tabs">
          {["All", "Documents", "Databases", "Tasks", "Projects", "Memory"].map(
            (t) => (
              <button
                key={t}
                className={type === t ? "active" : ""}
                onClick={() => setType(t)}
              >
                {t}
              </button>
            ),
          )}
        </div>
        <p className="sp-search-caption">
          {results.length} results in your Space
          {search.loading && " · Loading full text…"}
        </p>
        {search.error && (
          <p>
            <button onClick={search.retry}>Restart search</button>
          </p>
        )}
        {results.slice(0, limit).map((r) => (
          <Link key={r.id} href={r.href} className="sp-search-result">
            <Icon
              name={
                r.type === "Memory"
                  ? "memory"
                  : r.type === "Databases"
                    ? "database"
                    : r.type === "Tasks"
                      ? "tasks"
                      : r.type === "Projects"
                        ? "folder"
                        : "document"
              }
              size={20}
            />
            <span>
              <small>{r.type}</small>
              <strong>{r.title}</strong>
              <p>{r.description.replace(/[#*]/g, "")}</p>
            </span>
            <Icon name="chevron" size={15} />
          </Link>
        ))}
        {results.length > limit && (
          <button onClick={() => setLimit((n) => n + 80)}>
            Show more results
          </button>
        )}
        {!results.length && !search.loading && !search.error && (
          <Empty icon="search" title="Nothing here just yet">
            <p>Try another word or explore your documents.</p>
          </Empty>
        )}
      </div>
    </>
  );
}

export function ProjectsView({ base, id }: { base: string; id?: string }) {
  const confirm = useConfirm();
  const data = useSpace(),
    router = useRouter();
  const [query, setQuery] = useState(""),
    [tab, setTab] = useState("Overview"),
    [create, setCreate] = useState(false);
  const [name, setName] = useState(""),
    [description, setDescription] = useState("");
  const [sectionId, setSectionId] = useState("");
  const [createDocument, setCreateDocument] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState<Project | null>(null);
  const [status, setStatus] = useState<Status>("todo");
  const newId = useRef(crypto.randomUUID());
  // The summaries already list each item's project, so the page fills before the index.
  const localResources = useMemo(() => localEntries({ resources: data.resources }), [data.resources]);
  const related = useWorkspaceSearch(
    data.space?.id,
    base,
    "",
    !!id,
    id,
    localResources,
  );
  useFeedback({
    error: error || related.error || (create ? data.sectionError : ""),
    loading: busy ? "Saving project…" : related.loading ? "Loading related documents and tasks…" : create && !data.sectionReady && !data.sectionError ? "Loading sections…" : null,
    clearError: () => setError(""),
  });
  const relatedResources = related.items;
  const project = data.projects.find((p) => p.id === id);
  const documents = relatedResources.filter((r) => r.kind === "document");
  const matchingProjects = data.projects.filter((project) =>
    project.name.toLowerCase().includes(query.toLowerCase()),
  );
  const tasks = relatedResources.filter((r) => r.kind === "task");
  const relatedReady = !related.loading && !related.error;
  // Parameters: whether the section sits in the overview's side column.
  // What it does: lists the project's documents; the side column uses a short empty line
  // instead of the full empty state.
  // Output: the documents section.
  function documentsSection(compact: boolean) {
    return (
      <section className="sp-home-section" aria-labelledby="project-documents-title">
        <h2 id="project-documents-title">{compact ? "Documents" : "Related documents"}</h2>
        {documents.map((r) => (
          <Link
            className="sp-project-resource"
            key={r.id}
            href={`${base}/documents/${r.id}`}
          >
            <Icon name="document" size={18} />
            {r.title}
            <span>
              {r.updated_at ? relativeDate(r.updated_at) : ""}
            </span>
          </Link>
        ))}
        {!documents.length && relatedReady && (compact ? (
          <p className="sp-muted sp-project-empty">
            No documents yet.
            {data.canWrite && (
              <button type="button" className="sp-text-button" onClick={() => setCreateDocument(true)}>
                <Icon name="plus" size={15} />
                New document
              </button>
            )}
          </p>
        ) : (
          <Empty icon="document" title="No documents yet">
            <p>Keep this project’s notes and plans here.</p>
            {data.canWrite && (
              <button
                className="sp-primary"
                onClick={() => setCreateDocument(true)}
              >
                <Icon name="plus" size={15} />
                New document
              </button>
            )}
          </Empty>
        ))}
      </section>
    );
  }
  // What it does: lists the project's tasks with their status.
  // Output: the tasks section.
  function tasksSection() {
    return (
      <section className="sp-home-section" aria-labelledby="project-tasks-title">
        <h2 id="project-tasks-title">Tasks</h2>
        {tasks.map((r) => (
          <Link
            className="sp-project-resource"
            key={r.id}
            href={`${base}/tasks/${r.id}`}
          >
            <StatusDot status={r.status ?? "todo"} />
            {r.title}
            <span>
              {
                statuses.find(
                  (s) => s.value === (r.status ?? "todo"),
                )?.label
              }
            </span>
          </Link>
        ))}
        {!tasks.length && relatedReady && <p className="sp-muted sp-project-empty">No tasks yet.</p>}
      </section>
    );
  }
  function newProject() {
    setEditing(null);
    setName("");
    setDescription("");
    setSectionId("");
    setStatus("todo");
    setError("");
    newId.current = crypto.randomUUID();
    setCreate(true);
  }
  return (
    <>
      <div className="sp-page sp-work-view sp-projects-view">
        {id && <div className="sp-project-breadcrumb sp-breadcrumbs">
          <Link href={`${base}/projects`}>Projects</Link>
          {project && (
            <>
              <Icon name="chevron" size={13} />
              <span>{project.name}</span>
            </>
          )}
        </div>}
        {project ? (
          <>
            <div className="sp-project-heading sp-work-heading">
              <span className="sp-file-tile">
                <Icon name="folder" size={28} />
              </span>
              <h1>{project.name}</h1>
              <span className="sp-with-status">
                <StatusDot status={project.status} />
                {statuses.find((s) => s.value === project.status)?.label}
              </span>
            </div>
            <div className="sp-actions sp-project-detail-actions">
              {data.canWrite && (
                <button
                  className="sp-primary"
                  onClick={() => setCreateDocument(true)}
                >
                  <Icon name="plus" size={15} />
                  New document
                </button>
              )}
              <button
                disabled={!data.canWrite}
                onClick={() => {
                  setEditing(project);
                  setName(project.name);
                  setDescription(project.description);
                  setSectionId(project.section_id ?? "");
                  setStatus(project.status);
                  setError("");
                  setCreate(true);
                }}
              >
                Edit project
              </button>
              <button
                className="sp-project-trash"
                disabled={busy || !data.canWrite}
                onClick={async () => {
                  if (
                    !(await confirm({
                      title: "Move project to trash?",
                      message: `Move “${project.name}” to trash? Its documents and tasks will stay available.`,
                      confirmLabel: "Move to trash",
                    }))
                  )
                    return;
                  setBusy(true);
                  setError("");
                  try {
                    await data.setItemDeleted(
                      { ...project, kind: "project" },
                      true,
                    );
                    router.push(`${base}/projects`);
                  } catch (e) {
                    setError(message(e));
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                Move to trash
              </button>
              <button disabled={!data.canWrite} onClick={newProject}>
                <Icon name="plus" size={15} />
                New project
              </button>
            </div>
            <div className="sp-tabs">
              {["Overview", "Documents", "Tasks", "Databases"].map((t) => (
                <button
                  key={t}
                  className={tab === t ? "active" : ""}
                  aria-pressed={tab === t}
                  onClick={() => setTab(t)}
                >
                  {t}
                </button>
              ))}
            </div>
            <div className="sp-project-body">
              {related.error && (
                <p>
                  <button onClick={related.retry}>Reload related items</button>
                </p>
              )}
              {tab === "Overview" && (
                <div className="sp-project-overview">
                  <article className="sp-project-overview-main" aria-labelledby="project-overview-title">
                    <h2 id="project-overview-title">Overview</h2>
                    <div className="sp-project-description">
                      {project.description.trim() ? (
                        <DocumentMarkdown text={project.description} />
                      ) : (
                        <p className="sp-muted">Add a description to give this project some context.</p>
                      )}
                    </div>
                  </article>
                  <aside className="sp-project-overview-side" aria-label="Project contents">
                    {documentsSection(true)}
                    {tasksSection()}
                    <ProjectDatabases base={base} projectId={project.id} />
                  </aside>
                </div>
              )}
              {tab === "Documents" && documentsSection(false)}
              {tab === "Tasks" && tasksSection()}
              {tab === "Databases" && <ProjectDatabases base={base} projectId={project.id} />}
            </div>
          </>
        ) : id ? (
          <Empty icon="folder" title="Project not found">
            <Link className="sp-button" href={`${base}/projects`}>
              All projects
            </Link>
          </Empty>
        ) : (
          <>
            <div className="sp-work-heading">
              <div>
                <h1>Projects</h1>
                <p>Manage your projects and track progress.</p>
              </div>
              <button className="sp-primary" disabled={!data.canWrite} onClick={newProject}>
                <Icon name="plus" size={17} />
                New project
              </button>
            </div>
            <div className="sp-project-library-toolbar">
              <span className="sp-muted">{matchingProjects.length} {matchingProjects.length === 1 ? "project" : "projects"}</span>
              <label className="sp-search">
                <Icon name="search" size={16} />
                <input
                  aria-label="Search projects"
                  placeholder="Search projects…"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                />
              </label>
            </div>
            {data.projects.length ? (
              <div className="sp-table-wrap">
                <table className="sp-table sp-project-table">
                  <thead>
                    <tr>
                      <th>Project</th>
                      <th>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {matchingProjects.map((p) => (
                        <tr key={p.id}>
                          <td>
                            <Link href={`${base}/projects/${p.id}`}>
                              <span className="sp-file-tile">
                                <Icon name="folder" size={21} />
                              </span>
                              <span className="sp-project-summary">
                                <strong>{p.name}</strong>
                                {p.description.trim() && <small>{p.description}</small>}
                              </span>
                            </Link>
                          </td>
                          <td>
                            <span className="sp-with-status">
                              <StatusDot status={p.status} />
                              {
                                statuses.find((s) => s.value === p.status)
                                  ?.label
                              }
                            </span>
                          </td>
                        </tr>
                      ))}
                    {!matchingProjects.length && (
                      <tr><td colSpan={2} className="sp-project-no-results">No projects match your search.</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
            ) : (
              <Empty icon="folder" title="A place for the bigger picture" valley>
                <p>Group related documents and tasks in a project.</p>
              </Empty>
            )}
          </>
        )}
      </div>
      {!id && <ItemTrash kind="project" />}
      {createDocument && project && data.canWrite && (
        <ResourceForm
          kind="document"
          initialProject={project.id}
          onClose={() => setCreateDocument(false)}
          onSaved={(resource) => {
            related.retry();
            router.push(`${base}/documents/${resource.id}`);
          }}
        />
      )}
      {create && (
        <Modal
          title={editing ? "Edit project" : "New project"}
          onClose={() => setCreate(false)}
        >
          <form
            className="sp-modal-content"
            onSubmit={async (e) => {
              e.preventDefault();
              if (busy) return;
              setBusy(true);
              setError("");
              try {
                const saved = await data.saveProject({
                  ...editing,
                  id: editing?.id ?? newId.current,
                  name: name.trim(),
                  description,
                  status,
                  section_id: sectionId || null,
                });
                setCreate(false);
                router.push(`${base}/projects/${saved.id}`);
              } catch (e) {
                setError(message(e));
              } finally {
                setBusy(false);
              }
            }}
          >
            <label className="sp-field">
              Name
              <input
                autoFocus
                value={name}
                onChange={(e) => setName(e.target.value)}
                maxLength={120}
                required
                placeholder="What are you working on?"
              />
            </label>
            <label className="sp-field">
              Description
              <textarea
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="A little context for this project…"
                maxLength={1000}
              />
            </label>
            <label className="sp-field">
              Section
              <select value={sectionId} onChange={(event) => setSectionId(event.target.value)} disabled={!data.sectionReady || busy}>
                <option value="">No section</option>
                {data.organizationItems.filter((item) => item.kind === "section" && !item.deleted).sort((a, b) => a.name.localeCompare(b.name)).map((section) => (
                  <option key={section.id} value={section.id}>{section.name}</option>
                ))}
              </select>
            </label>
            {data.sectionError && <button type="button" onClick={data.retrySections}>Retry loading sections</button>}
            <label className="sp-field">
              Status
              <select
                value={status}
                onChange={(e) => setStatus(e.target.value as Status)}
              >
                {statuses.map((s) => (
                  <option value={s.value} key={s.value}>
                    {s.label}
                  </option>
                ))}
              </select>
            </label>
            <div className="sp-modal-footer">
              <button type="button" onClick={() => setCreate(false)}>
                Cancel
              </button>
              <button className="sp-primary" disabled={busy || !name.trim()}>
                {busy ? "Saving…" : editing ? "Save project" : "Create project"}
              </button>
            </div>
          </form>
        </Modal>
      )}
    </>
  );
}

export { AgentsView } from "./agents-view";

// What it does: shows the owner's name and picture and lets them choose their own picture.
// The name comes from SPACE_OWNER_NAME, which the operator sets.
// Output: the Profile section of the Account settings.
function ProfileSettings() {
  const data = useSpace();
  const [photoBusy, setPhotoBusy] = useState(false);
  const [error, setError] = useState("");
  const photoInput = useRef<HTMLInputElement>(null);
  useFeedback({
    error,
    loading: photoBusy ? "Saving your picture…" : null,
    clearError: () => setError(""),
  });
  async function changePhoto(image: string | null | Promise<string>) {
    setPhotoBusy(true);
    setError("");
    try {
      await data.saveProfilePhoto(await image);
      data.setNotice(image ? "Picture saved" : "Picture removed");
    } catch (e) {
      setError(message(e));
    } finally {
      setPhotoBusy(false);
    }
  }
  return (
    <section aria-labelledby="settings-profile-title">
      <h2 id="settings-profile-title">Profile</h2>
      <div className="sp-profile">
        <Avatar name={data.profile.displayName || "You"} />
        <div>
          <strong>{data.profile.displayName || "Owner"}</strong>
          <p>Owner of this Space</p>
          <span className="sp-profile-caption">
            {data.ownPhoto
              ? "Picture you chose. The operator sets the name with SPACE_OWNER_NAME."
              : "The operator sets this name with SPACE_OWNER_NAME."}
          </span>
        </div>
        <div className="sp-profile-actions">
          <button type="button" disabled={photoBusy} onClick={() => photoInput.current?.click()}>
            Change picture
          </button>
          {data.ownPhoto && (
            <button type="button" className="sp-text-button" disabled={photoBusy} onClick={() => void changePhoto(null)}>
              Remove picture
            </button>
          )}
        </div>
        <input
          ref={photoInput}
          type="file"
          hidden
          aria-label="Choose a profile picture"
          accept="image/png,image/jpeg,image/webp"
          onChange={(event) => {
            const file = event.target.files?.[0];
            event.target.value = "";
            if (file) void changePhoto(profilePhoto(file));
          }}
        />
      </div>
    </section>
  );
}

export function SettingsView({
  base,
  tab = "account",
}: {
  base: string;
  tab?: string;
}) {
  const data = useSpace();
  const pages = [
    ["account", "Account", "user"],
    ["security", "Security", "lock"],
    // Space access and organizations only matter once teams launch.
    ...(teamsAvailable ? ([["space", "Space", "settings"]] as const) : []),
    ...(teamsAvailable ? ([["team", "Team & organization", "people"]] as const) : []),
    ["webhooks", "Webhooks", "link"],
    ["api-keys", "API keys", "code"],
    ["processing", "Memory processing", "memory"],
    ["data", "Data", "download"],
  ] as const;
  // A hidden or unknown tab's address opens Account instead.
  const shown = pages.some(([id]) => id === tab) ? tab : "account";
  const [providerError, setProviderError] = useState("");
  const [providerAttempt, setProviderAttempt] = useState(0);
  const { loadProvider } = data;
  useEffect(() => {
    if (shown !== "processing") return;
    setProviderError("");
    loadProvider().catch((e) => setProviderError(message(e)));
  }, [shown, loadProvider, providerAttempt]);
  useFeedback({
    error: shown === "processing" ? providerError : null,
    loading: shown === "processing" && !data.provider && !providerError ? "Loading processing settings…" : null,
    clearError: () => setProviderError(""),
  });
  return (
    <>
      <div className="sp-work-view sp-settings-view">
        <div className="sp-work-heading">
          <div>
            <h1>Settings</h1>
            <p>Manage your workspace and preferences.</p>
          </div>
        </div>
      <div className="sp-settings-layout">
        <nav aria-label="Settings">
          {pages.map(([id, name, icon]) => (
            <Link
              key={id}
              href={`${base}/settings/${id}`}
              className={shown === id ? "active" : ""}
              aria-current={shown === id ? "page" : undefined}
            >
              <Icon name={icon} size={16} />
              {name}
            </Link>
          ))}
        </nav>
        <div className="sp-settings-page">
          {shown === "team" && <TeamSettings />}
          {shown === "webhooks" && <WebhookSettings />}
          {shown === "api-keys" && <ApiKeySettings />}
          {shown === "account" && (
            <>
              <h1>Account</h1>
              <p className="sp-subtitle">Make yourself at home.</p>
              <ProfileSettings />
              <section>
                <h2>About</h2>
                <p className="sp-muted">
                  Space is free software under the GNU Affero General Public
                  License, version 3. Anyone who uses it can get its source.
                </p>
                <a className="sp-setting-link" href={sourceCode.href} target="_blank" rel="noreferrer">
                  {sourceCode.label}
                  <Icon name="external" size={16} />
                </a>
              </section>
            </>
          )}
          {shown === "security" && (
            <>
              <h1>Security</h1>
              <p className="sp-subtitle">How this browser reaches your Space.</p>
              <section>
                <h2>Access</h2>
                {data.accessMode === "private" ? (
                  <p className="sp-muted">
                    This installation runs without an access key: anyone who
                    can reach it on its private network or VPN can open it.
                    Its operator chose this with SPACE_ACCESS_MODE=private.
                  </p>
                ) : (
                  <>
                    <div className="sp-setting-row">
                      <div>
                        <strong>This browser</strong>
                        <p>
                          Unlocked with the access key. It stays unlocked for
                          30 days after unlocking, or until you lock it.
                        </p>
                      </div>
                      <span className="sp-tag">Current</span>
                    </div>
                    <button
                      onClick={() =>
                        void data
                          .lock()
                          .catch((e) => data.setError(message(e)))
                      }
                    >
                      Lock this browser
                    </button>
                    <p className="sp-muted">
                      To lock every browser at once, the operator changes
                      SPACE_ACCESS_KEY on the server.
                    </p>
                  </>
                )}
                <a className="sp-setting-link" href={docsLinks.selfHosting} target="_blank" rel="noreferrer">
                  Read the self-hosting guide
                  <Icon name="external" size={16} />
                </a>
              </section>
            </>
          )}
          {shown === "space" && (
            <>
              <h1>Space settings</h1>
              <p className="sp-subtitle">
                A home for your work and your authorized agents.
              </p>
              <section>
                <dl className="sp-properties">
                  <div>
                    <dt>Name</dt>
                    <dd>{data.space?.name}</dd>
                  </div>
                  <div>
                    <dt>Ownership</dt>
                    <dd>
                      {data.space?.kind === "team"
                        ? "Organization"
                        : "Personal"}
                    </dd>
                  </div>
                  <div>
                    <dt>Visibility</dt>
                    <dd>
                      <Icon name="lock" size={14} />
                      {data.space?.visibility === "shared"
                        ? "Organization members"
                        : data.space?.visibility === "restricted"
                          ? "Selected people"
                          : "Private"}
                    </dd>
                  </div>
                </dl>
                <p className="sp-muted">
                  {data.space?.kind === "team"
                    ? "Access follows this Space’s sharing settings."
                    : "Access is limited to you and the agents you authorize."}
                </p>
                <Link className="sp-button" href={`${base}/agents`}>
                  Manage agent access <Icon name="arrow" size={14} />
                </Link>
              </section>
            </>
          )}
          {shown === "space" && <SpaceAccessSettings />}
          {shown === "processing" && (
            <>
              <h1>Memory processing</h1>
              <p className="sp-subtitle">
                Source text may be sent to your configured processor to create
                searchable memory. Queries and candidate passages may also be
                sent to an assessor during recall.
              </p>
              <section>
                <dl className="sp-properties">
                  <div>
                    <dt>Processor</dt>
                    <dd>
                      {data.provider
                        ? data.provider.provider || "Not configured"
                        : providerError ? "Not available" : "Loading…"}
                    </dd>
                  </div>
                  <div>
                    <dt>Text model</dt>
                    <dd>{data.provider?.text_model ?? "Not available"}</dd>
                  </div>
                  <div>
                    <dt>Embedding model</dt>
                    <dd>{data.provider?.embedding_model ?? "Not available"}</dd>
                  </div>
                  <div>
                    <dt>Evidence assessment</dt>
                    <dd>
                      {data.provider?.relevance_provider === "typesafe"
                        ? `TypeSafe · ${data.provider.typesafe_model}`
                        : (data.provider?.relevance_model ??
                          data.provider?.text_model ?? "Not available")}
                    </dd>
                  </div>
                </dl>
                {data.provider?.classification_provider === "typesafe" && (
                  <p className="sp-muted">Memory organization sends claims and your category and project names and descriptions to TypeSafe {data.provider.typesafe_model}. Manual assignments stay pinned.</p>
                )}
                {data.provider?.relevance_provider === "typesafe" && (
                  <p className="sp-muted">
                    TypeSafe receives queries and short candidate passages.
                    Longer passages, uncertain decisions and provider failures
                    use {data.provider.relevance_model ?? data.provider.text_model}
                    {" "}through your configured processor.
                  </p>
                )}
                {providerError && (
                  <button onClick={() => setProviderAttempt((attempt) => attempt + 1)}>
                    Try again
                  </button>
                )}
                <p className="sp-muted">
                  Configuration is managed by your installation operator.
                </p>
              </section>
              <section>
                <h2>Source documents stay saved</h2>
                <p>Document storage is independent of memory processing.</p>
                <a className="sp-setting-link" href={docsLinks.memory} target="_blank" rel="noreferrer"
                  aria-label="Read the memory guide (opens in a new window)">
                  Read the memory guide
                  <Icon name="external" size={16} />
                </a>
              </section>
            </>
          )}
          {shown === "data" && (
            <>
              <h1>Data and portability</h1>
              <p className="sp-subtitle">
                Keep your work close, wherever you go next.
              </p>
              <WorkspaceUsage />
              <WorkspaceExport />
              {data.canWrite && <WorkspaceRestore />}
              <section>
                <h2>Memory removal</h2>
                <p className="sp-muted">
                  Deleting a memory removes it from active recall. Retained
                  source revisions may still contain earlier text.
                </p>
                <Link className="sp-button" href={`${base}/memory`}>
                  Manage memory <Icon name="arrow" size={14} />
                </Link>
              </section>
            </>
          )}
        </div>
      </div>
      </div>
    </>
  );
}
