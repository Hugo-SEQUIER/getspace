import { ListPageControls, useListPage } from "./list-page";

import Link from "@web/lib/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import type { MemoryVector } from "../../../lib/space/types";
import {
  filterVectors,
  filterByProjectVisibility,
  visualVectorGraph,
  memoryNeighborhood,
} from "../../../lib/space/memory-model";
import { message } from "../../../lib/client";
import { useFeedback } from "../notifications";
import { Icon } from "./ui-icon";
import { useSpace } from "./provider";
import { Empty, Modal } from "./ui";
import { MemoryOrganization, OrganizationCatalog } from "./memory-organization";
import { VectorGraph } from "./vector-graph";

// A short pause keeps one keystroke from issuing one request.
const SEARCH_DEBOUNCE_MS = 250;
// Stable identity for "nothing to show", so a memoized filter does not recompute each render.
const NO_MEMORIES: MemoryVector[] = [];

export function MemoryView({ base, id }: { base: string; id?: string }) {
  const data = useSpace();
  const [view, setView] = useState<"vectors" | "list">("vectors");
  const [catalogOpen, setCatalogOpen] = useState(false);
  const [label, setLabel] = useState("");
  const [hiddenProjectIds, setHiddenProjectIds] = useState<ReadonlySet<string>>(() => new Set());
  const [showGlobal, setShowGlobal] = useState(true);
  const [showUnassigned, setShowUnassigned] = useState(true);
  const [showUnavailable, setShowUnavailable] = useState(true);
  const [query, setQuery] = useState(""),
    [creator, setCreator] = useState("");
  const [colorBy, setColorBy] = useState("category"),
    [sourceFilter, setSourceFilter] = useState(""),
    [staleOnly, setStaleOnly] = useState(false),
    [filters, setFilters] = useState(false);
  const [selectedId, setSelectedId] = useState<string | undefined>(id);
  const [focusId, setFocusId] = useState<string | undefined>();
  const [depth, setDepth] = useState(1);
  const [dialog, setDialog] = useState<"edit" | "delete" | "build" | null>(
      null,
    ),
    [draft, setDraft] = useState(""),
    [source, setSource] = useState("");
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [results, setResults] = useState<MemoryVector[]>(NO_MEMORIES),
    [resultsTerm, setResultsTerm] = useState("");
  const [searching, setSearching] = useState(false),
    [searchError, setSearchError] = useState("");
  const [searchAttempt, setSearchAttempt] = useState(0);
  useFeedback({
    error: error || searchError || data.memoryError || data.jobError,
    loading: busy ? "Updating memory…" : searching ? "Searching memory…" : data.memoryLoading || data.memoryMoreLoading ? "Loading memory…" : null,
    clearError: () => setError(""),
  });
  const activeJobs = data.memoryJobs.filter(job => job.status === "queued" || job.status === "processing").length;
  const completedJobs = data.memoryJobs.filter(job => job.status === "ready")
    .map(job => `${job.id}:${job.result?.revision}`).sort().join(",");
  const searchRequest = useRef(0);
  const searchMemories = data.searchMemories;
  const latestSearch = useRef(searchMemories);
  useEffect(() => {
    latestSearch.current = searchMemories;
  }, [searchMemories]);
  useEffect(() => {
    return data.watchMemories();
  }, [data.watchMemories]);
  useEffect(() => {
    if (data.memoryLoaded && id && !data.memories.some(m => m.id === id)) {
      void data.loadSelectedMemory(id.split(":")[0]).catch(e => setError(message(e)));
    }
  }, [id, data.memoryLoaded, data.space?.id]);
  useEffect(() => {
    if (id) setSelectedId(id);
    setFocusId(undefined);
  }, [id]);
  // Parameters: none. It reacts to the text in the search box.
  // What it does: waits out the last keystroke, then asks the server to match every memory
  //   in the Space. Each keystroke invalidates the request before it, so a slow answer for an
  //   older term can never replace the results of a newer one.
  // Output: nothing. It fills the query results, or the search error this view reports.
  useEffect(() => {
    const current = ++searchRequest.current;
    const term = query.trim();
    if (!term) {
      setResults(NO_MEMORIES);
      setResultsTerm("");
      setSearching(false);
      setSearchError("");
      return;
    }
    setSearching(true);
    setSearchError("");
    const timer = setTimeout(() => {
      void latestSearch.current(term).then(
        (found) => {
          if (current !== searchRequest.current) return;
          setResults(found);
          setResultsTerm(term);
          setSearching(false);
        },
        (e: unknown) => {
          if (current !== searchRequest.current) return;
          setResults(NO_MEMORIES);
          setResultsTerm("");
          setSearchError(message(e));
          setSearching(false);
        },
      );
    }, SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [query, data.space?.id, searchAttempt, completedJobs]);
  // A server query answers for the whole Space, so its results are the only list to show.
  // The facets below then narrow what is on screen, never the Space.
  const term = query.trim();
  const queryActive = term.length > 0;
  const items = queryActive
    ? resultsTerm === term
      ? results
      : NO_MEMORIES
    : data.memories;
  const activeProjects = useMemo(() => data.projects.filter(project => !project.deleted), [data.projects]);
  const projectVisibility = useMemo(() => ({
    activeProjects: new Set(activeProjects.map(project => project.id)),
    visibleProjects: new Set(activeProjects.filter(project => !hiddenProjectIds.has(project.id)).map(project => project.id)),
    global: showGlobal,
    unassigned: showUnassigned,
    unavailable: showUnavailable,
  }), [activeProjects, hiddenProjectIds, showGlobal, showUnassigned, showUnavailable]);
  const filtered = useMemo(
    () => filterByProjectVisibility(
      filterVectors(items, "", creator, "").filter(
        (m) =>
          (!label || (label === "uncategorized" ? !(m.classification?.category_ids ?? []).some(id => data.organizationItems.some(i => i.kind === "category" && i.id === id && !i.deleted)) : m.classification?.category_ids.includes(label))) &&
          (!sourceFilter || m.sourceId === sourceFilter) &&
          (!staleOnly || m.stale),
      ), projectVisibility,
    ),
    [items, data.organizationItems, creator, sourceFilter, staleOnly, label, projectVisibility],
  );
  const selected = filtered.find((m) => m.id === selectedId);
  const focused = filtered.find((m) => m.id === focusId);
  const graphCatalog = useMemo(() => ({
    categoryIds: new Set(data.organizationItems.filter(i => i.kind === "category" && !i.deleted).map(i => i.id)),
    projectIds: new Set(data.projects.filter(p => !p.deleted).map(p => p.id)),
  }), [data.organizationItems, data.projects]);
  const visible = useMemo(
    () =>
      focused ? memoryNeighborhood(filtered, focused.id, depth, graphCatalog) : filtered,
    [filtered, focused, depth, graphCatalog],
  );
  const graph = useMemo(
    () => view === "vectors"
      ? visualVectorGraph(visible, graphCatalog)
      : { edges: [], representative: false },
    [view, visible, graphCatalog],
  );
  const memoryListPage = useListPage(visible, JSON.stringify([query, creator, sourceFilter, staleOnly, focusId, depth, label, [...hiddenProjectIds].sort(), showGlobal, showUnassigned, showUnavailable]));
  useEffect(() => {
    if (focusId && !focused) setFocusId(undefined);
  }, [focusId, focused]);
  function focusMemory(id: string) {
    setSelectedId(id);
    setFocusId(id);
  }
  function memoryRow(m: MemoryVector) {
    return <button
      className={`sp-memory-list-row ${selected?.id === m.id ? "selected" : ""}`}
      key={m.id}
      onClick={() => setSelectedId(m.id)}
    >
      <span>
        <i style={{ background: colors(m) }} />
        {m.value}
        {m.stale && <small>Source updated</small>}
      </span>
      <span>{m.creator ?? "Not recorded"}</span>
    </button>;
  }

  const creators = [
      ...new Set(data.memories.flatMap((m) => (m.creator ? [m.creator] : []))),
    ];
  const sources = data.resources.filter(
    (r) => r.kind === "document" && r.recall?.category !== "memory",
  );
  const sourceUrl = selected?.sourceId
    ? `/resources/${selected.sourceId}?space_id=${data.space?.id}&revision=${selected.sourceRevision}`
    : null;
  const creatorColor = (name: string | null) =>
    name === "Codex" ? "#5b6fb5" : name === "You" ? "#6f8a4a" : "#d9825a";
  // The evening valley: dusk blue, apricot, olive, plum, ochre, slate teal, lavender, umber.
  const categoryPalette = ["#5b6fb5", "#d9825a", "#6f8a4a", "#a8668f", "#b8913a", "#4f8a8b", "#8a7fc4", "#8a6a4f"];
  const liveCategories = data.organizationItems
    .filter(i => i.kind === "category" && !i.deleted)
    .sort((a, b) => a.id.localeCompare(b.id));
  const categoryColor = (id: string) => categoryPalette[
    liveCategories.findIndex(i => i.id === id) % categoryPalette.length
  ] ?? "#939a95";
  const colors = (m: MemoryVector) => {
    if (colorBy === "creator") return creatorColor(m.creator);
    const id = [...(m.classification?.category_ids ?? [])].sort().find(id => graphCatalog.categoryIds.has(id));
    return id ? categoryColor(id) : "#939a95";
  };
  const legend = colorBy === "creator"
    ? creators.map(name => ({ id: name, name, color: creatorColor(name) }))
    : liveCategories.filter(i => items.some(m => m.classification?.category_ids.includes(i.id)))
      .map(i => ({ id: i.id, name: i.name, color: categoryColor(i.id) }));
  const legendFilter = colorBy === "creator" ? creator : label;
  function filterLegend(id: string) {
    const next = legendFilter === id ? "" : id;
    if (colorBy === "creator") setCreator(next);
    else setLabel(next);
  }
  function clearFilters() {
    setQuery(""); setCreator(""); setLabel("");
    setHiddenProjectIds(new Set()); setShowGlobal(true); setShowUnassigned(true); setShowUnavailable(true);
    setSourceFilter(""); setStaleOnly(false);
  }
  function toggleProject(id: string, visible: boolean) {
    setHiddenProjectIds(current => {
      const next = new Set(current);
      if (visible) next.delete(id);
      else next.add(id);
      return next;
    });
  }
  const projectFilterActive = activeProjects.some(project => hiddenProjectIds.has(project.id)) || !showGlobal || !showUnassigned || !showUnavailable;
  function openDialog(next: typeof dialog) {
    setError("");
    setDialog(next);
    if (next === "edit") setDraft(selected?.value ?? "");
  }
  async function submit() {
    setBusy(true);
    setError("");
    try {
      if (dialog === "build") await data.buildMemory(source);
      else if (selected) {
        await data.changeMemory(
          selected,
          dialog === "delete" ? "forget" : "edit",
          draft,
        );
        if (query.trim())
          setSearchAttempt((attempt) => attempt + 1);
      }
      setDialog(null);
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      {catalogOpen && <OrganizationCatalog onClose={() => setCatalogOpen(false)} />}
      <div className="sp-topbar sp-memory-topbar">
        <div className="sp-actions">
          <h1 className="sp-topbar-title">Memory</h1>
          <div className="sp-segment">
            <button
              aria-pressed={view === "vectors"}
              className={view === "vectors" ? "selected" : ""}
              onClick={() => setView("vectors")}
            >
              <Icon name="graph" size={16} />
              Vectors
            </button>
            <button
              aria-pressed={view === "list"}
              className={view === "list" ? "selected" : ""}
              onClick={() => setView("list")}
            >
              <Icon name="list" size={16} />
              List
            </button>
          </div>
        </div>
        <div className="sp-actions">
          <button onClick={() => setCatalogOpen(true)}>Categories</button>
          <button onClick={() => openDialog("build")}>
            <Icon name="plus" size={14} />
            Build from source
          </button>
        </div>
      </div>
      {activeJobs > 0 && (
        <p className="sp-memory-jobs" role="status">
          <span className="sp-loading-orbit" aria-hidden="true" />
          Memory extraction · {activeJobs} in progress
        </p>
      )}
      {data.memoryError && (
        <div>
          <button onClick={() => void data.loadMemories()}>Retry</button>
        </div>
      )}
      <div
        className={`sp-memory-layout is-live ${view === "vectors" ? "is-graph" : "is-list"} ${selected ? "has-selection" : ""}`}
      >
        <div className="sp-memory-canvas">
          <div className="sp-canvas-toolbar">
            <div className="sp-memory-search-tools">
              <label className="sp-search">
                <Icon name="search" size={15} />
                <input
                  aria-label="Find a memory"
                  placeholder="Find a memory…"
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                />
              </label>
              <select
                aria-label="Filter by creator"
                value={creator}
                onChange={(event) => setCreator(event.target.value)}
              >
                <option value="">All creators</option>
                {creators.map((c) => (
                  <option key={c}>{c}</option>
                ))}
              </select>
              <select aria-label="Filter by category" value={label} onChange={e => setLabel(e.target.value)}>
                <option value="">All categories</option><option value="uncategorized">Uncategorized</option>
                {data.organizationItems.filter(i => i.kind === "category" && !i.deleted).map(i => <option key={i.id} value={i.id}>{i.name}</option>)}
              </select>
              <button
                aria-label="More memory filters"
                aria-expanded={filters}
                onClick={() => setFilters(!filters)}
              >
                <Icon name="filter" size={14} />
                {(sourceFilter || staleOnly || projectFilterActive) && (
                  <span className="sp-filter-active" />
                )}
              </button>
            </div>
          </div>
          {focused && (
            <div className="sp-local-graph-bar">
              <button onClick={() => setFocusId(undefined)}>
                <Icon name="arrow" size={13} />
                All memories
              </button>
              <span>{focused.title}</span>
              <label>
                Depth
                <select
                  aria-label="Connection depth"
                  value={depth}
                  onChange={(event) => setDepth(Number(event.target.value))}
                >
                  {[1, 2, 3].map((value) => (
                    <option key={value}>{value}</option>
                  ))}
                </select>
              </label>
            </div>
          )}
          {filters && (
            <div className="sp-filter-panel" id="sp-memory-filters">
              <fieldset className="sp-project-visibility">
                <legend>Show memories from</legend>
                <div className="sp-project-visibility-actions">
                  <button type="button" onClick={() => {
                    setHiddenProjectIds(new Set()); setShowGlobal(true); setShowUnassigned(true); setShowUnavailable(true);
                  }}>Show all</button>
                  <button type="button" onClick={() => {
                    setHiddenProjectIds(new Set(activeProjects.map(project => project.id)));
                    setShowGlobal(false); setShowUnassigned(false); setShowUnavailable(false);
                  }}>Hide all</button>
                </div>
                <div className="sp-project-visibility-options">
                  {activeProjects.map(project => <label key={project.id} className="sp-checkbox-label">
                    <input type="checkbox" checked={!hiddenProjectIds.has(project.id)} onChange={e => toggleProject(project.id, e.target.checked)} />
                    {project.name}
                  </label>)}
                  {!activeProjects.length && <p>No active projects yet.</p>}
                  <label className="sp-checkbox-label"><input type="checkbox" checked={showGlobal} onChange={e => setShowGlobal(e.target.checked)} />Global · across projects</label>
                  <label className="sp-checkbox-label"><input type="checkbox" checked={showUnassigned} onChange={e => setShowUnassigned(e.target.checked)} />Unassigned</label>
                  <label className="sp-checkbox-label"><input type="checkbox" checked={showUnavailable} onChange={e => setShowUnavailable(e.target.checked)} />Archived or unavailable project</label>
                </div>
                <p>Multi-project memories appear once. Global rules are not copies in each project. These switches change only this view, not agent recall.</p>
              </fieldset>
              <label>
                Source
                <select
                  value={sourceFilter}
                  onChange={(e) => setSourceFilter(e.target.value)}
                >
                  <option value="">All sources</option>
                  {sources.map((s) => (
                    <option value={s.id} key={s.id}>
                      {s.title}
                    </option>
                  ))}
                </select>
              </label>
              <label className="sp-checkbox-label">
                <input
                  type="checkbox"
                  checked={staleOnly}
                  onChange={(e) => setStaleOnly(e.target.checked)}
                />
                Source updated only
              </label>
              <button
                onClick={clearFilters}
              >
                Clear filters
              </button>
            </div>
          )}
          {data.memoryLoading ? (
            <div className="sp-memory-loading">
              <div className="sp-loading-orbit" />
              <p>Opening your saved memory…</p>
            </div>
          ) : !filtered.length ? (
            <Empty
              icon="memory"
              valley={!data.memories.length}
              title={
                data.memories.length
                  ? "No memories match"
                  : "A little more context, every session"
              }
            >
              <p>
                {data.memories.length
                  ? "Try another category, creator, or search."
                  : "Your saved memories will appear here as your agents work."}
              </p>
              {data.memories.length > 0 && (
                <button
                  onClick={clearFilters}
                >
                  Clear filters
                </button>
              )}
            </Empty>
          ) : view === "vectors" ? (
            <VectorGraph
              items={visible}
              edges={graph.edges}
              selected={selected?.id}
              onSelect={setSelectedId}
              color={colors}
              onFocus={focusMemory}
              colorBy={colorBy}
              onColorBy={setColorBy}
              projectsOpen={filters}
              onProjectsToggle={() => setFilters(!filters)}
            />
          ) : (
            <div className="sp-memory-list">
              <div className="sp-memory-list-head">
                <span>Memory</span>
                <span>Created by</span>
              </div>
              {memoryListPage.items.map(memoryRow)}
              <ListPageControls {...memoryListPage} />
            </div>
          )}
              {/* A page holds what the server sent. Older memory is one page away, and the
                  server only offers a cursor when no search is running. */}
              {data.memoryCursor && !query.trim() && (
                <div className="sp-memory-more">
                  <p className="sp-muted">Filters apply to loaded memories. Load older memory to include more.</p>
                  <button
                    onClick={() => void data.loadMoreMemories()}
                    disabled={data.memoryMoreLoading}
                  >
                    {data.memoryMoreLoading ? "Loading older memory…" : "Load older memory"}
                  </button>
                </div>
              )}

          <div className="sp-network-caption">
            <strong>{visible.length.toLocaleString()} memories</strong>
            <small>
              {graph.representative
                ? "Large groups show representative links"
                : "Links show shared projects and categories"}
            </small>
          </div>
          <div className="sp-graph-footer">
            <div className="sp-legend">
              {legend.map(entry => (
                <button
                  key={entry.id}
                  onClick={() => filterLegend(entry.id)}
                  aria-pressed={legendFilter === entry.id}
                >
                  <i style={{ background: entry.color }} />
                  {entry.name}
                </button>
              ))}
            </div>
            <span>Links show shared projects and categories</span>
          </div>
        </div>
        {selected && (
          <aside className="sp-memory-inspector">
            <div className="sp-inspector-head">
              <h2>Memory details</h2>
              <button
                className="sp-icon-button"
                aria-label="Close memory details"
                onClick={() => setSelectedId(undefined)}
              >
                <Icon name="close" size={17} />
              </button>
            </div>
            <div className="sp-inspector-body">
              <p className="sp-memory-content">{selected.value}</p>
              <MemoryOrganization key={selected.id} memory={selected} onUpdated={() => setSearchAttempt(attempt => attempt + 1)} />
              {selected.stale && (
                <div className="sp-stale">
                  <span>Source updated</span>
                  <p>A newer source revision is available.</p>
                </div>
              )}
              <dl className="sp-properties">
                <div>
                  <dt>Origin</dt>
                  <dd>
                    {selected.importOrigin === "memora" ? "Imported from Memora" : selected.sourceId ? "Source-derived" : "Directly saved"}
                  </dd>
                </div>
                <div>
                  <dt>Created by</dt>
                  <dd>{selected.creator ?? "Not recorded"}</dd>
                  {selected.revisionAgent && <>
                    <dt>Revision by</dt>
                    <dd>{selected.revisionAgent.name}</dd>
                  </>}
                </div>
                <div>
                  <dt>Source</dt>
                  <dd>
                    {selected.sourceId
                      ? `${selected.sourceTitle ?? "Source document"} · v${selected.sourceRevision}`
                      : "No source document"}
                  </dd>
                </div>
                {selected.edited && (
                  <div>
                    <dt>State</dt>
                    <dd>Edited</dd>
                  </div>
                )}
              </dl>
              {selected.evidence && (
                <div className="sp-evidence">
                  <h3>Evidence excerpt</h3>
                  <blockquote>“{selected.evidence}”</blockquote>
                </div>
              )}
              {view === "vectors" && (
                <button
                  className="sp-focus-connections"
                  onClick={() =>
                    focusId === selected.id
                      ? setFocusId(undefined)
                      : focusMemory(selected.id)
                  }
                >
                  <Icon name="graph" size={15} />
                  {focusId === selected.id
                    ? "Show all memories"
                    : "Focus connections"}
                  <Icon name="arrow" size={14} />
                </button>
              )}
              <div className="sp-memory-actions">
                {sourceUrl && (
                  <Link className="sp-button sp-primary" href={sourceUrl}>
                    Open source revision
                  </Link>
                )}
                <button onClick={() => openDialog("edit")}>Edit memory</button>
                <button
                  className="sp-danger-text"
                  onClick={() => openDialog("delete")}
                >
                  Delete
                </button>
              </div>
              <p className="sp-memory-sharing">
                Available across authorized agent sessions.
              </p>
            </div>
          </aside>
        )}
      </div>
      {dialog && (
        <Modal
          size={dialog === "delete" ? "small" : "default"}
          className={dialog === "delete" ? "sp-confirm-modal" : ""}
          title={
            dialog === "build"
              ? "Build memory from a source"
              : dialog === "edit"
                ? "Edit memory"
                : "Delete this memory?"
          }
          onClose={() => {
            if (!busy) setDialog(null);
          }}
        >
          <div className="sp-modal-content">
            {dialog === "build" ? (
              <>
                <p>
                  Choose a saved document. Space will queue extraction from its
                  current revision and show when the memory is ready to recall.
                </p>
                <label className="sp-field">
                  Source
                  <select
                    value={source}
                    onChange={(e) => setSource(e.target.value)}
                  >
                    <option value="">Select a document</option>
                    {sources.map((s) => (
                      <option value={s.id} key={s.id}>
                        {s.title} · v{s.revision}
                      </option>
                    ))}
                  </select>
                </label>
                <p className="sp-muted">
                  {data.provider?.enabled
                    ? "Source text is sent to your configured processor."
                    : "Memory processing is not configured for this installation."}
                </p>
              </>
            ) : dialog === "edit" ? (
              <>
                <label className="sp-field">
                  Memory
                  <textarea
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    maxLength={2000}
                    autoFocus
                  />
                </label>
                <p className="sp-muted">
                  Source references stay attached. Original creators are shown
                  only when recorded.
                </p>
              </>
            ) : (
              <>
                <p>
                  This removes the memory from active recall and the vector
                  index. It cannot be undone.
                </p>
                <blockquote>{selected?.value}</blockquote>
                <p className="sp-muted">
                  Earlier source revisions may still contain this text.
                </p>
              </>
            )}
            <div className="sp-modal-footer">
              <button onClick={() => setDialog(null)} disabled={busy}>
                Cancel
              </button>
              <button
                className={dialog === "delete" ? "sp-danger" : "sp-primary"}
                disabled={
                  busy ||
                  (dialog === "edit" && !draft.trim()) ||
                  (dialog === "build" &&
                    (!source || !data.provider?.enabled))
                }
                onClick={() => void submit()}
              >
                {busy
                  ? "Working…"
                  : dialog === "build"
                    ? "Start extraction"
                    : dialog === "delete"
                      ? "Delete memory"
                      : "Save changes"}
              </button>
            </div>
          </div>
        </Modal>
      )}
    </>
  );
}
