import "../../document-design.css";

import Link from "@web/lib/navigation";
import dynamic from "@web/lib/lazy";
import { useRouter, useSearchParams } from "@web/lib/navigation";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { message } from "../../../lib/client";
import { useFeedback } from "../notifications";
import { documentBlocks, documentText } from "../../../lib/space/document-content";
import {
  categoryColors,
  statuses,
  type Resource,
} from "../../../lib/space/types";
import { useSpace } from "./provider";
import { ResourceForm } from "./resource-views";
import { DocumentMarkdown } from "./document-markdown";
import { Avatar, Empty, Modal, StatusDot, authorName, relativeDate, ConfirmModal } from "./ui";
import { Icon } from "./ui-icon";
import { PageIcon } from "./sidebar-preferences";
import { DocumentIconButton } from "./sidebar-navigation";
import { DocumentTree } from "./document-tree";
import { DocumentDragProvider } from "./document-drag";
import { DocumentRecentCard, DocumentTableRow } from "./document-list-items";

function DocumentRail({
  panel,
  close,
  children,
}: {
  panel: string;
  close: () => void;
  children: ReactNode;
}) {
  const [compact, setCompact] = useState(false);
  useEffect(() => {
    const media = window.matchMedia("(max-width: 1200px)");
    const update = () => setCompact(media.matches);
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  if (compact && panel !== "outline")
    return (
      <Modal
        title={panel === "history" ? "Version history" : "Document context"}
        onClose={close}
        className="sp-doc-mobile-panel"
      >
        {children}
      </Modal>
    );
  return children;
}

const ImportDocumentDialog = dynamic(() =>
  import("./import-dialogs").then((m) => m.ImportDocumentDialog),
);

export function DocumentsView({ base, id }: { base: string; id?: string }) {
  const data = useSpace(),
    router = useRouter(),
    params = useSearchParams();
  const revisionValue = params.get("revision") ?? "";
  const revision =
    /^[1-9][0-9]*$/.test(revisionValue) &&
    Number.isSafeInteger(Number(revisionValue))
      ? Number(revisionValue)
      : undefined;
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState("updated");
  const projectFilter = params.get("project") ?? "";
  const folderFilter = params.get("folder") ?? "";
  const sectionFilter = params.get("section") ?? "";
  const [tab, setTab] = useState("all");
  const [editing, setEditing] = useState<"new" | "edit" | null>(null);
  const [newProject, setNewProject] = useState("");
  const [newFolder, setNewFolder] = useState("");
  const [treeOpen, setTreeOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleteError, setDeleteError] = useState("");
  const [importing, setImporting] = useState(false);
  const [selected, setSelected] = useState<Resource | null>(null);
  const [current, setCurrent] = useState<Resource | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [panel, setPanel] = useState<"outline" | "context" | "history">(
    "outline",
  );
  const [historyLimit, setHistoryLimit] = useState(50);
  const [memoryLimit, setMemoryLimit] = useState(8);
  const [loadingMore, setLoadingMore] = useState(false);
  useFeedback({
    error: error || deleteError || (panel === "context" ? data.memoryError : ""),
    loading: deleting ? "Deleting document…" : loading ? "Loading document…" : loadingMore ? "Loading more documents…" : panel === "context" && data.memoryLoading ? "Loading linked memory…" : null,
    clearError: () => { setError(""); setDeleteError(""); },
  });
  const documents = data.resources.filter(
    (r) => r.kind === "document" && r.recall?.category !== "memory",
  );
  const folders = data.folders.filter((folder) => !folder.deleted);
  const locationDocuments = documents.filter((r) => {
    if (projectFilter === "unfiled") {
      if (r.project && data.projects.some((project) => project.id === r.project)) return false;
    } else if (projectFilter && r.project !== projectFilter) return false;
    const folder = folders.find((item) => item.id === r.folder);
    if (folderFilter === "unfiled") return !folder;
    if (folderFilter) return folder?.id === folderFilter;
    return !sectionFilter || folder?.section_id === sectionFilter;
  });
  const matchingDocuments = locationDocuments.filter((r) =>
    r.title.toLowerCase().includes(query.toLowerCase()),
  );
  const recent = [...matchingDocuments]
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at))
    .slice(0, 3);
  const handoffs = matchingDocuments.filter((r) => r.recall?.category === "handoff");
  const items = matchingDocuments
    .filter((r) => tab !== "handoffs" || r.recall?.category === "handoff")
    .sort((a, b) =>
      sort === "name"
        ? a.title.localeCompare(b.title)
        : b.updated_at.localeCompare(a.updated_at),
    );
  const filtered = !!query || !!projectFilter || !!folderFilter || !!sectionFilter || tab !== "all";
  const locationName = folderFilter
    ? folderFilter === "unfiled" ? "Unfiled" : folders.find((folder) => folder.id === folderFilter)?.name
    : sectionFilter
      ? data.organizationItems.find((item) => item.kind === "section" && !item.deleted && item.id === sectionFilter)?.name
      : projectFilter === "unfiled" ? "No project" : data.projects.find((project) => project.id === projectFilter)?.name;
  const project = data.projects.find((p) => p.id === selected?.project);
  const linkedMemories = useMemo(() => {
    const matches = data.memories.filter((m) => m.sourceId === selected?.id);
    const seen = new Set<string>();
    const first: typeof matches = [],
      repeated: typeof matches = [];
    // Preview distinct claims first; keep every separately addressable memory.
    for (const memory of matches) {
      const key = JSON.stringify([memory.category, memory.value]);
      (seen.has(key) ? repeated : first).push(memory);
      seen.add(key);
    }
    return [...first, ...repeated];
  }, [data.memories, selected?.id]);
  const knownRevision = current?.revision ?? selected?.revision ?? 1;
  const projectTasks = selected?.project
    ? data.resources.filter(
        (r) => r.kind === "task" && r.project === selected.project,
      )
    : [];
  const headings = useMemo(
    () =>
      documentBlocks(selected?.body ?? "").filter((b) => b.type === "heading"),
    [selected?.body],
  );
  const readableText = documentText(selected?.body ?? "").trim();
  const words = readableText ? readableText.split(/\s+/).length : 0;

  useEffect(() => {
    setPanel("outline");
    setTreeOpen(false);
    setHistoryLimit(50);
    setMemoryLimit(8);
  }, [id]);
  useEffect(() => {
    if (!id) {
      setSelected(null);
      setCurrent(null);
      return;
    }
    setLoading(true);
    setError("");
    setSelected(null);
    setCurrent(null);
    const stop = data.watchResource(id, revision, saved => {
      setSelected(saved);
      if (!revision) setCurrent(saved);
      setLoading(false);
      setError("");
    }, e => { setError(message(e)); setLoading(false); });
    const stopLatest = revision ? data.watchResource(id, undefined, setCurrent, () => setCurrent(null)) : () => {};
    return () => { stop(); stopLatest(); };
    // Observe the selected resource; editor drafts remain in their own form state.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, revision, data.space?.id]);
  useEffect(() => {
    if (panel === "context") return data.watchMemories();
    // Retry is explicit after a request failure.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [panel, data.watchMemories]);

  function openRevision(next?: number) {
    const search = new URLSearchParams(params.toString());
    if (next) search.set("revision", String(next));
    else search.delete("revision");
    router.push(`${base}/documents/${id}${search.size ? `?${search}` : ""}`, {
      scroll: false,
    });
  }
  function selectProject(projectId: string) {
    setTreeOpen(false);
    const search = new URLSearchParams(params.toString());
    search.delete("revision");
    if (projectId) search.set("project", projectId);
    else search.delete("project");
    router.push(`${base}/documents${search.size ? `?${search}` : ""}`);
  }
  function selectFolder(folderId: string) {
    setTreeOpen(false);
    router.push(`${base}/documents?folder=${encodeURIComponent(folderId)}`);
  }
  function clearFilters() {
    setQuery("");
    setTab("all");
    router.replace(`${base}/documents`);
  }
  function createDocument(folderId = "") {
    setNewFolder(folders.some((folder) => folder.id === folderId) ? folderId : "");
    setNewProject(projectFilter === "unfiled" ? "" : projectFilter);
    setEditing("new");
    setTreeOpen(false);
  }

  return (
    <>
      {id && (
        <div className="sp-topbar sp-doc-topbar">
          <div className="sp-breadcrumbs">
            <Link href={`${base}/documents`}>Documents</Link>
            <Icon name="chevron" size={13} />
            <span>{selected?.id === id ? selected.title : "Document"}</span>
          </div>
          <div className="sp-actions">
            <button className="sp-doc-browse" aria-label="Browse pages" aria-expanded={treeOpen} aria-controls="document-tree-pane" onClick={() => setTreeOpen(!treeOpen)}>
              <Icon name="folder" size={15} /><span>Browse</span>
            </button>
            {!loading && selected && (
              <span className="sp-saved">
                <Icon name={revision ? "clock" : "check"} size={13} />
                {revision ? `Version ${revision}` : "Saved"}
              </span>
            )}
            <button
              className={`sp-doc-panel-button ${panel === "context" ? "selected" : ""}`}
              aria-label="Context"
              aria-pressed={panel === "context"}
              onClick={() =>
                setPanel(panel === "context" ? "outline" : "context")
              }
              disabled={!selected || loading}
            >
              <Icon name="graph" size={15} />
              <span>Context</span>
            </button>
            <button
              className={`sp-icon-button ${panel === "history" ? "selected" : ""}`}
              aria-label="Document history"
              title="Version history"
              aria-pressed={panel === "history"}
              onClick={() =>
                setPanel(panel === "history" ? "outline" : "history")
              }
              disabled={!selected || loading}
            >
              <Icon name="clock" />
            </button>
            {selected?.recall?.category === "memory" ? (
              <Link className="sp-button" href={`${base}/memory`}>
                Open memory
              </Link>
            ) : (
              <button
                onClick={() => setEditing("edit")}
                disabled={!data.canWrite || !selected || loading || !!revision}
              >
                <Icon name="edit" size={15} />
                Edit
              </button>
            )}
            {data.canWrite && selected?.recall?.category !== "memory" && (
              <button
                className="sp-icon-button sp-danger-text"
                aria-label="Delete document"
                title="Delete document"
                disabled={!selected || loading || !!revision}
                onClick={() => { setDeleteError(""); setDeleteOpen(true); }}
              >
                <Icon name="trash" size={16} />
              </button>
            )}
          </div>
        </div>
      )}

      <DocumentDragProvider
        key={data.space?.id}
        disabled={!!revision || !!editing || deleting}
        onDragStart={() => setTreeOpen(true)}
        onMoved={(saved) => {
          if (saved.id === id) { setSelected(saved); setCurrent(saved); }
        }}
      >
      <div className="sp-doc-workspace">
        <div id="document-tree-pane" className={`sp-doc-tree-pane ${treeOpen ? "is-open" : ""}`}>
          <DocumentTree
            base={base}
            documents={documents}
            selectedId={id}
            projectFilter={projectFilter}
            folderFilter={folderFilter}
            sectionFilter={sectionFilter}
            query={query}
            onQueryChange={setQuery}
            onNewDocument={createDocument}
            onFolderSelect={selectFolder}
            onNavigate={() => setTreeOpen(false)}
          />
        </div>
        <div className="sp-doc-main">
      {!id ? (
        <div className="sp-doc-library">
          <header className="sp-doc-library-heading">
            <div>
              <h1>{locationName ?? "Documents"}</h1>
              <p>A home for your notes, plans, and ideas.</p>
            </div>
            <div className="sp-doc-library-actions">
              <button className="sp-doc-browse" aria-label="Browse pages" aria-expanded={treeOpen} aria-controls="document-tree-pane" onClick={() => setTreeOpen(!treeOpen)}>
                <Icon name="folder" size={16} /><span>Browse</span>
              </button>
              <button
                className="sp-doc-upload"
                aria-label="Upload document"
                disabled={!data.canWrite}
                onClick={() => setImporting(true)}
              >
                <Icon name="upload" size={15} />
                <span>Upload</span>
              </button>
              <Link
                className="sp-button sp-doc-new-database"
                aria-label="New database"
                href={`${base}/databases?new=1`}
              >
                <Icon name="database" size={15} />
                <span>New database</span>
              </Link>
              <button className="sp-primary" disabled={!data.canWrite} onClick={() => createDocument(folderFilter)}>
                <Icon name="plus" size={15} />
                New document
              </button>
            </div>
          </header>
          {!filtered && recent.length > 0 && (
            <section
              className="sp-doc-recents"
              aria-label="Recently updated documents"
            >
              <div className="sp-doc-section-title">
                <h2>Recently updated</h2>
                <span className="sp-doc-private"><Icon name="lock" size={12} />{data.space?.name}</span>
              </div>
              <div className="sp-doc-recent-grid">
                {recent.map((doc) => (
                  <DocumentRecentCard key={doc.id} document={doc} base={base} />
                ))}
              </div>
            </section>
          )}
          <section className="sp-doc-collection" aria-label="Document library">
            <div className="sp-doc-library-toolbar">
              <div className="sp-doc-tabs" aria-label="Document type">
                <button
                  aria-pressed={tab === "all"}
                  onClick={() => setTab("all")}
                >
                  All documents<span>{matchingDocuments.length}</span>
                </button>
                <button
                  aria-pressed={tab === "handoffs"}
                  onClick={() => setTab("handoffs")}
                >
                  Handoffs<span>{handoffs.length}</span>
                </button>
              </div>
              <div className="sp-doc-library-controls">
                <label className="sp-search">
                  <Icon name="search" size={15} />
                  <input
                    aria-label="Search documents"
                    placeholder="Search documents…"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                  />
                </label>
                {data.projects.length > 0 && (
                  <select
                    aria-label="Filter documents by project"
                    value={projectFilter}
                    onChange={(e) => selectProject(e.target.value)}
                  >
                    <option value="">All projects</option>
                    <option value="unfiled">No project</option>
                    {data.projects.map((p) => (
                      <option value={p.id} key={p.id}>
                        {p.name}
                      </option>
                    ))}
                  </select>
                )}
                <select
                  aria-label="Sort documents"
                  value={sort}
                  onChange={(e) => setSort(e.target.value)}
                >
                  <option value="updated">Last updated</option>
                  <option value="name">Name A–Z</option>
                </select>
              </div>
            </div>
            {items.length ? (
              <div className="sp-table-wrap">
                <table className="sp-table sp-doc-table">
                  <thead>
                    <tr>
                      <th>Document</th>
                      <th>Folder</th>
                      <th>Updated by</th>
                      <th>Last updated</th>
                      <th>
                        <span className="sr-only">Version</span>
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((item) => (
                      <DocumentTableRow key={item.id} document={item} base={base} folderName={folders.find(folder => folder.id === item.folder)?.name} />
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <Empty
                title={
                  filtered
                    ? "No documents found"
                    : "A place for your first idea"
                }
              >
                <p>
                  {filtered
                    ? "Try another title or broaden your filters."
                    : "Write a note, save a plan, or leave context for your next session."}
                </p>
                <button
                  onClick={filtered ? clearFilters : () => createDocument()}
                >
                  {filtered ? "Clear filters" : "Create a document"}
                </button>
              </Empty>
            )}
            <div className="sp-doc-library-footer">
              <span>
                {items.length} {items.length === 1 ? "document" : "documents"}
                {data.cursors.document ? " loaded" : ""}
              </span>
              {filtered && (
                <button className="sp-text-button" onClick={clearFilters}>
                  Clear filters
                </button>
              )}
              <span>
                <Icon name="lock" size={11} />
                Only you and your authorized agents
              </span>
            </div>
            {data.cursors.document && (
              <button
                className="sp-load-more"
                disabled={loadingMore}
                onClick={async () => {
                  setLoadingMore(true);
                  try {
                    await data.loadMore("document");
                  } finally {
                    setLoadingMore(false);
                  }
                }}
              >
                {loadingMore ? "Loading…" : "Load more documents"}
              </button>
            )}
          </section>
        </div>
      ) : (
        <div
          className={`sp-doc-reading-layout ${panel !== "outline" ? "has-panel" : ""}`}
        >
          <article className="sp-document-reader sp-doc-reader">
            {loading ? (
              <div
                className="sp-doc-loading"
                role="status"
                aria-label="Loading document"
              >
                <span />
                <span />
                <span />
                <span />
              </div>
            ) : error ? (
              <Empty title="This page is unavailable">
                <Link className="sp-button" href={`${base}/documents`}>
                  Back to documents
                </Link>
              </Empty>
            ) : (
              selected && (
                <>
                  {revision && (
                    <div className="sp-revision-banner">
                      <span>
                        <Icon name="clock" size={14} />
                        Viewing revision {revision}
                      </span>
                      <button onClick={() => openRevision()}>
                        Back to current
                      </button>
                    </div>
                  )}
                  <div className="sp-doc-reader-kicker">
                    <span>
                      {selected.recall?.category === "handoff"
                        ? "Session handoff"
                        : "Document"}
                    </span>
                    {project && (
                      <>
                        <span>/</span>
                        <Link href={`${base}/projects/${project.id}`}>
                          <Icon name="folder" size={13} />
                          {project.name}
                        </Link>
                      </>
                    )}
                  </div>
                  <div className="sp-doc-title-block">
                    <DocumentIconButton
                      id={selected.id}
                      title={selected.title}
                      fallback={
                        selected.recall?.category === "handoff"
                          ? "arrow"
                          : "document"
                      }
                    />
                    <h1>{selected.title}</h1>
                  </div>
                  <div className="sp-document-meta">
                    <span className="sp-with-avatar">
                      <Avatar name={authorName(selected.author, selected.author_agent)} harness={selected.author_agent?.harness} kind={selected.author === "agent" ? "agent" : "person"} small />
                      {authorName(selected.author, selected.author_agent)}
                    </span>
                    <span>·</span>
                    <span>{relativeDate(selected.updated_at)}</span>
                    <span>·</span>
                    <button onClick={() => setPanel("history")}>
                      Revision {selected.revision}
                    </button>
                    {words > 0 && (
                      <>
                        <span>·</span>
                        <span>
                          {Math.max(1, Math.ceil(words / 220))} min read
                        </span>
                      </>
                    )}
                  </div>
                  {selected.body?.trim() ? (
                    <DocumentMarkdown text={selected.body} anchors />
                  ) : (
                    <div className="sp-doc-empty-body">
                      <p>This document is a blank page.</p>
                      {!revision && (
                        <button disabled={!data.canWrite} onClick={() => setEditing("edit")}>
                          Start writing
                          <Icon name="edit" size={13} />
                        </button>
                      )}
                    </div>
                  )}
                  {projectTasks.length > 0 && (
                    <section className="sp-doc-related">
                      <div className="sp-doc-section-title">
                        <h2>
                          In this project<span>{projectTasks.length}</span>
                        </h2>
                        <Link href={`${base}/projects/${selected.project}`}>
                          View project
                          <Icon name="arrow" size={13} />
                        </Link>
                      </div>
                      {projectTasks.slice(0, 3).map((task) => (
                        <Link
                          className="sp-related-task"
                          key={task.id}
                          href={`${base}/tasks/${task.id}`}
                        >
                          <span>
                            <StatusDot status={task.status} />
                            {task.title}
                          </span>
                          <span className="sp-muted">
                            {
                              statuses.find((s) => s.value === task.status)
                                ?.label
                            }
                          </span>
                        </Link>
                      ))}
                    </section>
                  )}
                  <button
                    className="sp-doc-memory-entry"
                    onClick={() => setPanel("context")}
                  >
                    <span className="sp-doc-memory-symbol">
                      <Icon name="graph" size={21} />
                    </span>
                    <span>
                      <strong>Connected to your memory</strong>
                      <small>
                        {data.memoryLoaded
                          ? `${linkedMemories.length} memories reference this source`
                          : "Explore the memories that reference this source"}
                      </small>
                    </span>
                    <Icon name="arrow" size={16} />
                  </button>
                  <div className="sp-document-end">
                    <button
                      className="sp-text-button"
                      onClick={() => setPanel("history")}
                    >
                      View source history
                    </button>
                    <span>
                      {words.toLocaleString()} words<span> · </span>Saved in{" "}
                      {data.space?.name}
                    </span>
                  </div>
                </>
              )
            )}
          </article>

          {!loading && selected && (
            <DocumentRail panel={panel} close={() => setPanel("outline")}>
              <aside
                className={`sp-doc-rail sp-doc-rail-${panel}`}
                aria-label={
                  panel === "history"
                    ? "Document revisions"
                    : panel === "context"
                      ? "Document context"
                      : "Document outline"
                }
              >
                <div className="sp-doc-rail-inner">
                  {panel === "outline" ? (
                    <>
                      <nav aria-label="On this page">
                        <h2>On this page</h2>
                        {headings.length ? (
                          headings.map((heading) => (
                            <a
                              key={heading.id}
                              href={`#${heading.id}`}
                              className={heading.level > 2 ? "is-nested" : ""}
                            >
                              {heading.text.replace(/[*`]/g, "")}
                            </a>
                          ))
                        ) : (
                          <p className="sp-doc-rail-note">
                            Headings will appear here as your document grows.
                          </p>
                        )}
                      </nav>
                      <div className="sp-doc-rail-bottom">
                        <Icon name="graph" size={19} />
                        <h3>The context carries forward.</h3>
                        <p>
                          Follow this document into the memories built from it.
                        </p>
                        <button
                          className="sp-text-button"
                          onClick={() => setPanel("context")}
                        >
                          Explore context
                          <Icon name="arrow" size={13} />
                        </button>
                      </div>
                    </>
                  ) : (
                    <>
                      <div className="sp-inspector-head">
                        <h2>
                          {panel === "history"
                            ? "Version history"
                            : "Document context"}
                        </h2>
                        <button
                          className="sp-icon-button"
                          aria-label={
                            panel === "history"
                              ? "Close history"
                              : "Close context"
                          }
                          onClick={() => setPanel("outline")}
                        >
                          <Icon name="close" size={16} />
                        </button>
                      </div>
                      {panel === "history" ? (
                        <>
                          <p className="sp-doc-rail-note">
                            Open a saved version without changing the current
                            document.
                          </p>
                          <div className="sp-doc-revisions">
                            {Array.from(
                              {
                                length: Math.min(knownRevision, historyLimit),
                              },
                              (_, i) => knownRevision - i,
                            ).map((v) => (
                              <button
                                key={v}
                                aria-label={`Revision ${v} ${v === current?.revision ? "Current version" : "Saved version"}`}
                                aria-pressed={
                                  (revision ?? current?.revision) === v
                                }
                                className={`sp-revision-row ${(revision ?? current?.revision) === v ? "selected" : ""}`}
                                onClick={() =>
                                  openRevision(
                                    v === current?.revision ? undefined : v,
                                  )
                                }
                              >
                                <span className="sp-doc-timeline-dot">
                                  <Icon
                                    name={
                                      v === current?.revision
                                        ? "check"
                                        : "clock"
                                    }
                                    size={13}
                                  />
                                </span>
                                <span>
                                  <strong>Revision {v}</strong>
                                  <small>
                                    {v === current?.revision
                                      ? "Current version"
                                      : "Saved version"}
                                  </small>
                                </span>
                                {(revision ?? current?.revision) === v && (
                                  <span className="sp-doc-revision-viewing">
                                    Viewing
                                  </span>
                                )}
                              </button>
                            ))}
                          </div>
                          {knownRevision > historyLimit && (
                            <button
                              className="sp-text-button"
                              onClick={() =>
                                setHistoryLimit((limit) => limit + 50)
                              }
                            >
                              Show older versions
                            </button>
                          )}
                          <div className="sp-doc-version-info">
                            <span className="sp-doc-eyebrow">
                              Viewing revision {selected.revision}
                            </span>
                            <span className="sp-with-avatar">
                              <Avatar
                                name={authorName(selected.author, selected.author_agent)}
                                harness={selected.author_agent?.harness}
                                kind={selected.author === "agent" ? "agent" : "person"}
                                small
                              />
                              {authorName(selected.author, selected.author_agent)}
                            </span>
                            <time dateTime={selected.updated_at}>
                              {new Date(selected.updated_at).toLocaleString(
                                "en",
                                {
                                  month: "short",
                                  day: "numeric",
                                  year: "numeric",
                                  hour: "numeric",
                                  minute: "2-digit",
                                },
                              )}
                            </time>
                            <p>
                              {revision
                                ? "This saved version is read only."
                                : "Your next edit will create a new revision."}
                            </p>
                          </div>
                        </>
                      ) : (
                        <>
                          <div className="sp-doc-context-source">
                            <span className="sp-doc-file">
                              <PageIcon
                                pageKey={`document:${selected.id}`}
                                size={19}
                              />
                            </span>
                            <div>
                              <strong>{selected.title}</strong>
                              <small>
                                Source · revision {selected.revision}
                              </small>
                            </div>
                          </div>
                          <div className="sp-doc-context-heading">
                            <Icon name="memory" size={15} />
                            <h3>Memory from this source</h3>
                            {data.memoryLoaded && (
                              <span className="sp-count">
                                {linkedMemories.length}
                              </span>
                            )}
                          </div>
                          <p className="sp-doc-rail-note">
                            Each memory keeps the revision it was built from.
                          </p>
                          {data.memoryLoading ? (
                            <p role="status" className="sp-doc-rail-note">
                              Loading linked memory…
                            </p>
                          ) : data.memoryError ? (
                            <div>
                              <button onClick={() => void data.loadMemories()}>
                                Retry
                              </button>
                            </div>
                          ) : linkedMemories.length ? (
                            <div className="sp-doc-memory-list">
                              {linkedMemories
                                .slice(0, memoryLimit)
                                .map((memory) => (
                                  <Link
                                    key={memory.id}
                                    className="sp-doc-memory-card"
                                    href={`${base}/memory/${encodeURIComponent(memory.id)}`}
                                  >
                                    <span
                                      style={{
                                        color:
                                          categoryColors[memory.category] ??
                                          "#7b7890",
                                      }}
                                    >
                                      <i />
                                      {memory.category}
                                      <small>
                                        {memory.sourceRevision
                                          ? `v${memory.sourceRevision}`
                                          : "Revision not recorded"}
                                      </small>
                                    </span>
                                    <p>{memory.value}</p>
                                    <small>
                                      {memory.creator
                                        ? `Created by ${memory.creator}`
                                        : "Creator not recorded"}
                                      {memory.stale && <em>Source updated</em>}
                                    </small>
                                  </Link>
                                ))}
                            </div>
                          ) : (
                            <p className="sp-doc-context-empty">
                              No linked memories yet. You can build memory from
                              this document in Memory.
                            </p>
                          )}
                          {linkedMemories.length > memoryLimit && (
                            <button
                              className="sp-doc-more-memory"
                              onClick={() =>
                                setMemoryLimit((limit) => limit + 8)
                              }
                            >
                              Show more memories
                              <span>
                                {memoryLimit} of {linkedMemories.length}
                              </span>
                            </button>
                          )}
                          <Link
                            className="sp-button sp-doc-open-memory"
                            href={`${base}/memory`}
                          >
                            Explore memory
                            <Icon name="arrow" size={14} />
                          </Link>
                        </>
                      )}
                    </>
                  )}
                </div>
              </aside>
            </DocumentRail>
          )}
        </div>
      )}
        </div>
      </div>
      </DocumentDragProvider>
      {deleteOpen && selected && (
        <ConfirmModal
          title="Delete document?"
          confirmLabel="Delete document"
          busyLabel="Deleting…"
          busy={deleting}
          error={deleteError}
          onClose={() => setDeleteOpen(false)}
          onConfirm={async () => {
            setDeleting(true);
            setDeleteError("");
            try {
              await data.deleteResource(selected);
              setDeleteOpen(false);
              router.replace(`${base}/documents`);
            } catch (e) {
              setDeleteError(message(e));
            } finally {
              setDeleting(false);
            }
          }}
        >
          <p>Delete “{selected.title}” and all its saved versions? This cannot be undone. Separately saved memories will remain.</p>
        </ConfirmModal>
      )}
      {importing && (
        <ImportDocumentDialog
          initialProject={projectFilter === "unfiled" ? "" : projectFilter}
          initialFolder={folders.some(folder => folder.id === folderFilter) ? folderFilter : ""}
          close={() => setImporting(false)}
          created={(saved) => {
            setImporting(false);
            router.push(`${base}/documents/${saved.id}`);
          }}
        />
      )}
      {editing && (
        <ResourceForm
          kind="document"
          resource={editing === "edit" ? (selected ?? undefined) : undefined}
          initialProject={editing === "new" ? newProject : undefined}
          initialFolder={editing === "new" ? newFolder : undefined}
          onClose={() => setEditing(null)}
          onSaved={(saved) => {
            setSelected(saved);
            setCurrent(saved);
            if (editing === "new") router.push(`${base}/documents/${saved.id}`);
          }}
        />
      )}
    </>
  );
}
