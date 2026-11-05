import "../../document-library.css";

import Link from "@web/lib/navigation";
import { useRouter, useSearchParams } from "@web/lib/navigation";
import { useMemo, useState, type ReactNode } from "react";
import { message } from "../../../lib/client";
import { useFeedback } from "../notifications";
import { isUnseen } from "../../../lib/space/attention";
import {
  DOCUMENT_PAGE_SIZE, applyFacet, defaultDocumentSort, documentFacets, documentsInLocation, facetCounts,
  folderGroups, folderPath, latestActivity, matchesProject, matchesSearch, recentlyOpened, resolveLocation,
  sortDocuments, toggleSort, unreadDocuments,
  type DocumentFacet, type DocumentLocation, type DocumentSort, type DocumentSortKey, type FolderGroup, type FolderSummary,
} from "../../../lib/space/document-library";
import type { DocumentFolder, OrganizationItem, Resource } from "../../../lib/space/types";
import { ActionMenu, type ActionMenuItem } from "./action-menu";
import { DocumentFolderDropTarget, useDocumentDragState } from "./document-drag";
import { DocumentRecentCard, DocumentRow, RecentlyOpenedCard } from "./document-list-items";
import { FileGrid } from "./file-grid";
import type { OrganizationDialogs } from "./document-organization";
import { ListPageControls, useListPage } from "./list-page";
import { useSpace } from "./provider";
import { PageOptions } from "./sidebar-navigation";
import { useSidebarPreferences, type SidebarPage } from "./sidebar-preferences";
import { Empty, Modal, authorName, relativeDate, useConfirm } from "./ui";
import { Icon } from "./ui-icon";
import { localEntries, useWorkspaceSearch } from "./workspace-search";

type LibraryProps = {
  base: string;
  /** The documents the directory lists: never generated memory. */
  documents: Resource[];
  folders: DocumentFolder[];
  sections: OrganizationItem[];
  projectFilter: string;
  folderFilter: string;
  sectionFilter: string;
  unreadFilter: boolean;
  dialogs: OrganizationDialogs;
  treeOpen: boolean;
  onToggleTree: () => void;
  onCreateDocument: (folderId?: string) => void;
  /** Opens the Upload dialog, with the files dropped on the directory when there are any. */
  onUpload: (files?: File[]) => void;
};

const plural = (count: number, word: string) => `${count} ${word}${count === 1 ? "" : "s"}`;
/** Whether a drag over the directory carries files from outside the page, not a document card. */
const carriesFiles = (transfer: DataTransfer | null) => !!transfer && Array.from(transfer.types ?? []).includes("Files");
const facetValues = documentFacets.map((item) => item.value);
/** "today", "yesterday" or "on Oct 5", to follow "Updated" or "Opened". */
const activityLabel = (value: string) => {
  const when = relativeDate(value);
  return when === "Today" || when === "Yesterday" ? when.toLowerCase() : `on ${when}`;
};

// Parameters: the listed documents, the active folders and sections, the address's filters, the
//   shared folder dialogs and the heading's actions.
// What it does: the Documents directory. At the root it shows the documents the person opened
//   last, the folders of the Space as tiles grouped by section, then every document in one list.
//   Inside a folder or section it shows that container's header and documents. The list has type
//   facets, one search over titles and bodies, an Unread filter, sortable headers, a List or Grid
//   view and the shared pager; each row has a ··· menu.
// Output: the directory below the page heading.
export function DocumentLibrary({
  base, documents, folders, sections, projectFilter, folderFilter, sectionFilter, unreadFilter, dialogs,
  treeOpen, onToggleTree, onCreateDocument, onUpload,
}: LibraryProps) {
  const data = useSpace();
  const router = useRouter();
  const params = useSearchParams();
  const confirm = useConfirm();
  const { preferences, update } = useSidebarPreferences();
  const { move } = useDocumentDragState();
  const [query, setQuery] = useState("");
  const [dropping, setDropping] = useState(false);
  const [sort, setSort] = useState<DocumentSort>(defaultDocumentSort);
  const [moving, setMoving] = useState<Resource | null>(null);
  const [iconFor, setIconFor] = useState<Resource | null>(null);
  const [error, setError] = useState("");
  const [loadingMore, setLoadingMore] = useState(false);
  const view = preferences.documentsView;
  const typeParam = params.get("type") as DocumentFacet | null;
  const facet: DocumentFacet = typeParam && facetValues.includes(typeParam) ? typeParam : "all";
  const needle = query.trim().toLowerCase();

  // Known titles answer at once; bodies come from the search index the palette already uses.
  const known = useMemo(() => localEntries(data), [data.resources, data.projects, data.databases, data.memories]);
  const searching = !!needle;
  const search = useWorkspaceSearch(data.space?.id, base, query, searching, undefined, known);
  const bodyMatches = useMemo(() => {
    if (!needle) return undefined;
    return new Set(search.items.filter((item) => item.kind === "document").map((item) => item.id));
  }, [needle, search.items]);
  useFeedback({
    error: error || (searching ? search.error : ""),
    loading: loadingMore ? "Loading more documents…" : searching && search.loading ? "Searching your documents…" : null,
    clearError: () => setError(""),
  });

  const location = resolveLocation(folderFilter, sectionFilter, folders, sections);
  const inLocation = documentsInLocation(documents, location, folders).filter((document) => matchesProject(document, projectFilter, data.projects));
  const scoped = unreadFilter ? unreadDocuments(inLocation, data.views) : inLocation;
  const searched = scoped.filter((document) => matchesSearch(document, query, bodyMatches));
  const counts = facetCounts(searched);
  const author = (document: Resource) => authorName(document.author, document.author_agent);
  const listed = sortDocuments(applyFacet(searched, facet), sort, author);
  const page = useListPage(listed, JSON.stringify([folderFilter, sectionFilter, projectFilter, unreadFilter, needle, facet, sort, view]), DOCUMENT_PAGE_SIZE);
  const filtered = !!needle || facet !== "all" || !!projectFilter || unreadFilter;
  const atRoot = location.kind === "all" && !filtered;
  const groups = useMemo(() => folderGroups(sections, folders, documents, data.views), [sections, folders, documents, data.views]);
  const recent = useMemo(() => recentlyOpened(documents, data.viewTimes), [documents, data.viewTimes]);
  const unreadTotal = unreadDocuments(documents, data.views).length;
  const projectNames = new Map(data.projects.map((project) => [project.id, project.name]));
  const showProject = data.projects.length > 0;

  function go(changes: Record<string, string | null>) {
    const search = new URLSearchParams(params.toString());
    search.delete("revision");
    for (const [key, value] of Object.entries(changes)) value ? search.set(key, value) : search.delete(key);
    router.push(`${base}/documents${search.size ? `?${search}` : ""}`);
  }
  function clearFilters() {
    setQuery("");
    router.replace(`${base}/documents${location.kind === "folder" ? `?folder=${encodeURIComponent(location.folder.id)}` : location.kind === "section" ? `?section=${encodeURIComponent(location.section.id)}` : location.kind === "unfiled" ? "?folder=unfiled" : ""}`);
  }
  function pageFor(document: Resource): SidebarPage {
    return { key: `document:${document.id}`, title: document.title, href: `${base}/documents/${document.id}`, icon: document.recall?.category === "handoff" ? "arrow" : "document", type: "Document" };
  }
  function rowActions(document: Resource): ActionMenuItem[] {
    const key = `document:${document.id}`;
    const favorite = preferences.favorites.includes(key);
    const items: ActionMenuItem[] = [
      { label: "Open", icon: "external", onSelect: () => router.push(`${base}/documents/${document.id}`) },
      {
        label: favorite ? "Remove from favorites" : "Add to favorites", icon: "star",
        onSelect: () => update((p) => ({ ...p, favorites: favorite ? p.favorites.filter((item) => item !== key) : [...p.favorites, key], collapsed: { ...p.collapsed, favorites: false } })),
      },
      { label: "Change icon…", icon: "palette", onSelect: () => setIconFor(document) },
    ];
    if (data.canWrite) {
      items.splice(1, 0, { label: "Move to folder…", icon: "folder", onSelect: () => setMoving(document) });
      items.push({
        label: "Delete…", icon: "trash", danger: true,
        onSelect: async () => {
          if (!await confirm({ title: "Delete document?", message: `Delete “${document.title}” and all its saved versions? This cannot be undone. Separately saved memories will remain.`, confirmLabel: "Delete document" })) return;
          try { await data.deleteResource(document); } catch (e) { setError(message(e)); }
        },
      });
    }
    return items;
  }
  function containerActions(kind: "folder" | "section", item: DocumentFolder | OrganizationItem): ActionMenuItem[] {
    if (!data.canWrite) return [];
    return kind === "folder" ? [
      { label: "Edit folder…", icon: "edit", onSelect: () => dialogs.openFolder((item as DocumentFolder).section_id ?? null, item as DocumentFolder) },
      { label: "New document here", icon: "plus", onSelect: () => onCreateDocument(item.id) },
      { label: "Delete folder…", icon: "trash", danger: true, onSelect: () => dialogs.deleteItem(item) },
    ] : [
      { label: "Edit section…", icon: "edit", onSelect: () => dialogs.openSection(item as OrganizationItem) },
      { label: "New folder in section", icon: "folder", onSelect: () => dialogs.openFolder(item.id) },
      { label: "Delete section…", icon: "trash", danger: true, onSelect: () => dialogs.deleteItem(item) },
    ];
  }
  const sortHeader = (key: DocumentSortKey, label: string) => (
    <th aria-sort={sort.key === key ? (sort.descending ? "descending" : "ascending") : "none"}>
      <button type="button" className={`sp-doc-sort ${sort.key === key ? "is-active" : ""}`} onClick={() => setSort(toggleSort(sort, key))}>
        {label}
        {sort.key === key && <Icon name={sort.descending ? "down" : "chevron"} size={12} className={sort.descending ? "" : "is-up"} />}
      </button>
    </th>
  );
  const sectionOf = (folder: DocumentFolder) => sections.find((section) => section.id === folder.section_id) ?? null;

  function folderTile(summary: FolderSummary) {
    const { folder, count, unread, activity } = summary;
    return (
      <DocumentFolderDropTarget key={folder.id} folder={folder.id} surface="tile" className="sp-doc-folder-card-wrap">
        <article className="sp-db-library-card sp-doc-folder-card">
          <div className="sp-db-card-top">
            <span className="sp-db-large-icon"><Icon name="folder" size={22} /></span>
            {data.canWrite && <ActionMenu label={`Folder options for ${folder.name}`} items={containerActions("folder", folder)} />}
          </div>
          <h3><Link className="sp-doc-folder-link" href={`${base}/documents?folder=${encodeURIComponent(folder.id)}`}>{folder.name}</Link></h3>
          <p>{folder.description || "No description yet."}</p>
          <div className="sp-db-card-footer">
            <span>{plural(count, "document")}{unread > 0 && <> · <b>{unread} unread</b></>}</span>
            <span>{activity ? relativeDate(activity) : "Empty"}</span>
          </div>
        </article>
      </DocumentFolderDropTarget>
    );
  }
  function folderGroupView(group: FolderGroup) {
    const key = group.section ? `document-folders:section:${group.section.id}` : "document-folders:folders";
    const open = preferences.collapsed[key] !== true;
    const name = group.section?.name ?? "Folders";
    const id = `doc-folders-${group.section?.id ?? "loose"}`;
    return (
      <section className="sp-doc-folder-group" aria-label={name} key={key}>
        <div className="sp-doc-folder-group-head">
          <button type="button" className="sp-doc-tree-toggle" aria-label={`${open ? "Collapse" : "Expand"} ${name}`} aria-expanded={open} aria-controls={id} onClick={() => update((p) => ({ ...p, collapsed: { ...p.collapsed, [key]: open } }))}>
            <Icon name={open ? "down" : "chevron"} size={12} />
          </button>
          <h2>{group.section ? <Link href={`${base}/documents?section=${encodeURIComponent(group.section.id)}`}>{group.section.name}</Link> : "Folders"}</h2>
          <span className="sp-doc-folder-group-meta">{plural(group.folders.length, "folder")} · {plural(group.documents, "document")}{!group.section && " · outside a section"}</span>
          {group.section && data.canWrite && <ActionMenu label={`Section options for ${group.section.name}`} items={containerActions("section", group.section)} />}
        </div>
        <div id={id} className="sp-db-library-grid sp-doc-folder-grid" hidden={!open}>
          {group.folders.map(folderTile)}
          {data.canWrite && (
            <button type="button" className="sp-db-add-card sp-doc-add-folder" onClick={() => dialogs.openFolder(group.section?.id ?? null)}>
              <span><Icon name="plus" size={20} /></span>
              <strong>{group.section ? `New folder in ${group.section.name}` : "New folder"}</strong>
            </button>
          )}
        </div>
      </section>
    );
  }

  const heading = (
    <div className="sp-work-heading sp-doc-library-heading">
      <div>
        <h1>Documents</h1>
        <p>Pages, files and session handoffs, written by you and your agents.</p>
      </div>
      <div className="sp-actions sp-doc-library-actions">
        <button className="sp-doc-browse" aria-label="Browse folders" aria-expanded={treeOpen} aria-controls="document-tree-pane" onClick={onToggleTree}>
          <Icon name="folder" size={16} /><span>Browse</span>
        </button>
        <button className="sp-doc-upload" aria-label="Upload files" disabled={!data.canWrite} onClick={() => onUpload()}>
          <Icon name="upload" size={16} /><span>Upload</span>
        </button>
        <div className="sp-doc-new">
          <button className="sp-primary" disabled={!data.canWrite} onClick={() => onCreateDocument(location.kind === "folder" ? location.folder.id : "")}>
            <Icon name="plus" size={16} />New document
          </button>
          <ActionMenu label="More ways to create" className="sp-doc-new-more" disabled={!data.canWrite} items={[
            { label: "New folder", icon: "folder", onSelect: () => dialogs.openFolder(location.kind === "folder" ? location.folder.section_id ?? null : location.kind === "section" ? location.section.id : null) },
            { label: "New section", icon: "plus", onSelect: () => dialogs.openSection() },
            { label: "New database", icon: "database", onSelect: () => router.push(`${base}/databases?new=1`) },
          ]}><Icon name="down" size={14} /></ActionMenu>
        </div>
      </div>
    </div>
  );

  if (location.kind === "missing") return (
    <div className="sp-doc-library">
      {heading}
      <Empty icon="folder" title="This folder is no longer here">
        <p>It may have been deleted. Its documents are still in All documents.</p>
        <Link className="sp-button" href={`${base}/documents`}>All documents</Link>
      </Empty>
    </div>
  );

  const crumbs: ReactNode[] = [];
  if (location.kind !== "all") {
    crumbs.push(<Link key="all" href={`${base}/documents`}>All documents</Link>);
    const section = location.kind === "section" ? location.section : location.kind === "folder" ? location.section : null;
    if (section) crumbs.push(<Icon key="s-sep" name="chevron" size={12} />, location.kind === "section"
      ? <span key="section" aria-current="page">{section.name}</span>
      : <Link key="section" href={`${base}/documents?section=${encodeURIComponent(section.id)}`}>{section.name}</Link>);
    if (location.kind === "folder") crumbs.push(<Icon key="f-sep" name="chevron" size={12} />, <span key="folder" aria-current="page">{location.folder.name}</span>);
    if (location.kind === "unfiled") crumbs.push(<Icon key="u-sep" name="chevron" size={12} />, <span key="unfiled" aria-current="page">Unfiled</span>);
  }

  const container = location.kind === "folder" ? location.folder : location.kind === "section" ? location.section : null;
  const containerDocuments = documentsInLocation(documents, location, folders);
  const containerUnread = unreadDocuments(containerDocuments, data.views).length;
  const header = location.kind === "all" ? null : (
    <div className="sp-doc-folder-head" aria-label={location.kind === "unfiled" ? "Unfiled" : container!.name}>
      <span className="sp-doc-folder-head-icon"><Icon name={location.kind === "section" ? "memory" : "folder"} size={20} /></span>
      <div className="sp-doc-folder-head-copy">
        <h2>{location.kind === "unfiled" ? "Unfiled" : container!.name}</h2>
        <p>{location.kind === "unfiled" ? "Documents that are not in a folder." : container!.description || (location.kind === "section" ? "A section groups folders." : "No description yet.")}</p>
        <div className="sp-doc-folder-head-meta">
          {location.kind === "section" && <span>{plural(location.folders.length, "folder")}</span>}
          <span>{plural(containerDocuments.length, "document")}</span>
          {latestActivity(containerDocuments) && <span>Updated {activityLabel(latestActivity(containerDocuments)!)}</span>}
          {location.kind === "folder" && location.section && <span>Section: {location.section.name}</span>}
          {containerUnread > 0 && <span>{containerUnread} unread</span>}
        </div>
      </div>
      {container && data.canWrite && <ActionMenu label={`${location.kind === "section" ? "Section" : "Folder"} options for ${container.name}`} items={containerActions(location.kind === "section" ? "section" : "folder", container)} />}
    </div>
  );

  const sectionGroups = location.kind === "section" ? groups.filter((group) => group.section?.id === location.section.id) : [];

  return (
    <div
      className="sp-doc-library"
      onDragEnter={(event) => {
        if (!data.canWrite || !carriesFiles(event.dataTransfer)) return;
        event.preventDefault();
        setDropping(true);
      }}
      onDragOver={(event) => {
        if (!data.canWrite || !carriesFiles(event.dataTransfer)) return;
        event.preventDefault();
        event.dataTransfer.dropEffect = "copy";
        if (!dropping) setDropping(true);
      }}
      onDragLeave={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDropping(false);
      }}
      onDrop={(event) => {
        if (!carriesFiles(event.dataTransfer)) return;
        event.preventDefault();
        setDropping(false);
        const dropped = [...event.dataTransfer.files];
        if (!data.canWrite || !dropped.length) return;
        onUpload(dropped);
      }}
    >
      {dropping && (
        <div className="sp-doc-drop-overlay" aria-hidden="true">
          <Icon name="upload" size={28} />
          <strong>Drop to upload</strong>
          <span>Pictures, videos, PDFs and any other file, saved in {location.kind === "folder" ? location.folder.name : location.kind === "section" ? location.section.name : "Documents"}</span>
        </div>
      )}
      {heading}
      {crumbs.length > 0 && <nav className="sp-doc-scope" aria-label="Location">{crumbs}</nav>}
      {header}
      {atRoot && recent.length > 0 && (
        <section className="sp-doc-recents" aria-label="Recently opened">
          <div className="sp-doc-section-title"><h2>Recently opened<span>by you, on any device</span></h2></div>
          <div className="sp-doc-recent-grid sp-doc-recent-compact">
            {recent.map((item) => <RecentlyOpenedCard key={item.document.id} document={item.document} base={base} openedAt={item.openedAt} />)}
          </div>
        </section>
      )}
      {(atRoot ? groups : sectionGroups).length > 0 && (
        <div className="sp-doc-folders">
          {(atRoot ? groups : sectionGroups).map(folderGroupView)}
        </div>
      )}
      {atRoot && groups.length === 0 && data.canWrite && documents.length > 0 && (
        <p className="sp-doc-folders-hint">
          <Icon name="folder" size={15} />
          Folders keep documents together.
          <button type="button" className="sp-text-button" onClick={() => dialogs.openFolder(null)}>Create the first folder</button>
        </p>
      )}
      <section className="sp-doc-collection" aria-label={location.kind === "all" ? "All documents" : `Documents in ${location.kind === "unfiled" ? "Unfiled" : container!.name}`}>
        {atRoot && <div className="sp-doc-section-title sp-doc-collection-title"><h2>All documents<span>{plural(documents.length, "document")}</span></h2></div>}
        <div className="sp-doc-library-toolbar">
          <div className="sp-doc-tabs" role="group" aria-label="Document type">
            {documentFacets.map((item) => (
              <button key={item.value} type="button" aria-pressed={facet === item.value} onClick={() => go({ type: item.value === "all" ? null : item.value })}>
                {item.label}<span>{counts[item.value]}</span>
              </button>
            ))}
          </div>
          <div className="sp-doc-library-controls">
            {(unreadTotal > 0 || unreadFilter) && (
              <button type="button" className={`sp-doc-chip ${unreadFilter ? "is-on" : ""}`} aria-pressed={unreadFilter} onClick={() => go({ unread: unreadFilter ? null : "1" })}>
                <span className="sp-doc-unread-dot" aria-hidden="true" />Unread<span>{unreadFilter ? scoped.length : unreadDocuments(inLocation, data.views).length}</span>
              </button>
            )}
            <label className="sp-search">
              <Icon name="search" size={15} />
              <input aria-label="Search documents" placeholder={location.kind === "all" ? "Search titles and text…" : "Search in this folder…"} value={query} onChange={(event) => setQuery(event.target.value)} />
            </label>
            {showProject && (
              <select aria-label="Filter documents by project" value={projectFilter} onChange={(event) => go({ project: event.target.value || null })}>
                <option value="">All projects</option>
                <option value="unfiled">No project</option>
                {data.projects.map((project) => <option value={project.id} key={project.id}>{project.name}</option>)}
              </select>
            )}
            {facet !== "files" && <div className="sp-segment sp-doc-view-switch" role="group" aria-label="View">
              <button type="button" className={view === "list" ? "selected" : ""} aria-pressed={view === "list"} aria-label="List view" title="List" onClick={() => update((p) => ({ ...p, documentsView: "list" }))}><Icon name="list" size={16} /></button>
              <button type="button" className={view === "grid" ? "selected" : ""} aria-pressed={view === "grid"} aria-label="Grid view" title="Grid" onClick={() => update((p) => ({ ...p, documentsView: "grid" }))}><Icon name="board" size={16} /></button>
            </div>}
          </div>
        </div>
        {listed.length === 0 ? (
          facet === "files" ? (
            <Empty icon="image" title={needle || unreadFilter || projectFilter ? "No files found" : "No files yet"}>
              <p>{needle || unreadFilter || projectFilter ? "Try another name or broaden the filters." : "Drop pictures, videos, PDFs and any other file here, or use Upload. Each is kept as it is, with a preview."}</p>
              {needle || unreadFilter || projectFilter
                ? <button onClick={clearFilters}>Clear filters</button>
                : <button disabled={!data.canWrite} onClick={() => onUpload()}>Upload files</button>}
            </Empty>
          ) : filtered ? (
            <Empty icon="search" title="No documents found">
              <p>Try another word or broaden the filters.</p>
              <button onClick={clearFilters}>Clear filters</button>
            </Empty>
          ) : location.kind === "all" ? (
            <Empty valley title="A place for your first idea">
              <p>Write a note, save a plan, or leave context for your next session.</p>
              <button disabled={!data.canWrite} onClick={() => onCreateDocument()}>Create a document</button>
            </Empty>
          ) : (
            <Empty icon="folder" title={location.kind === "unfiled" ? "Nothing is unfiled" : `Nothing in ${container!.name} yet`}>
              <p>{location.kind === "unfiled" ? "Every document has a folder." : "Documents created or moved here will appear in this list."}</p>
              {location.kind === "folder" && <button disabled={!data.canWrite} onClick={() => onCreateDocument(location.folder.id)}>New document here</button>}
            </Empty>
          )
        ) : facet === "files" ? (
          <FileGrid documents={page.items} base={base} />
        ) : view === "grid" ? (
          <div className="sp-doc-recent-grid sp-doc-grid" role="list" aria-label="Documents">
            {page.items.map((document) => (
              <div role="listitem" key={document.id}>
                <DocumentRecentCard document={document} base={base} unread={isUnseen(document, data.views)} />
              </div>
            ))}
          </div>
        ) : (
          <div className="sp-table-wrap sp-doc-list-wrap">
            <table className="sp-table sp-doc-table">
              <thead>
                <tr>
                  {sortHeader("name", "Name")}
                  {showProject && <th>Project</th>}
                  {sortHeader("author", "Updated by")}
                  {sortHeader("updated", "Updated")}
                  <th><span className="sr-only">Actions</span></th>
                </tr>
              </thead>
              <tbody>
                {page.items.map((document) => (
                  <DocumentRow
                    key={document.id}
                    document={document}
                    base={base}
                    path={location.kind === "folder" ? [] : folderPath(document, folders, sections)}
                    projectName={document.project ? projectNames.get(document.project) : undefined}
                    showProject={showProject}
                    unread={isUnseen(document, data.views)}
                    actions={rowActions(document)}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
        <ListPageControls {...page} />
        <div className="sp-doc-library-footer">
          <span>{plural(listed.length, facet === "files" ? "file" : "document")}{data.cursors.document ? " loaded" : ""}</span>
          {filtered && <button className="sp-text-button" onClick={clearFilters}>Clear filters</button>}
          <span><Icon name="lock" size={11} />Only you and your authorized agents</span>
        </div>
        {data.cursors.document && (
          <button
            className="sp-load-more"
            disabled={loadingMore}
            onClick={async () => {
              setLoadingMore(true);
              try { await data.loadMore("document"); } finally { setLoadingMore(false); }
            }}
          >
            {loadingMore ? "Loading…" : "Load more documents"}
          </button>
        )}
      </section>
      {moving && (
        <MoveDialog
          document={moving}
          folders={folders}
          sections={sections}
          sectionOf={sectionOf}
          onClose={() => setMoving(null)}
          onMove={async (folderId) => { await move(moving, folderId); setMoving(null); }}
        />
      )}
      {iconFor && <PageOptions page={pageFor(iconFor)} onClose={() => setIconFor(null)} />}
    </div>
  );
}

// Parameters: the document to move, the active folders with their sections, and the move action.
// What it does: the keyboard and touch alternative to dragging: choose a folder or Unfiled.
// Output: a small dialog.
function MoveDialog({ document, folders, sections, sectionOf, onClose, onMove }: {
  document: Resource;
  folders: DocumentFolder[];
  sections: OrganizationItem[];
  sectionOf: (folder: DocumentFolder) => OrganizationItem | null;
  onClose: () => void;
  onMove: (folderId: string) => Promise<void>;
}) {
  const [choice, setChoice] = useState(document.folder && folders.some((folder) => folder.id === document.folder) ? document.folder : "");
  const [busy, setBusy] = useState(false);
  const byName = (a: { name: string }, b: { name: string }) => a.name.localeCompare(b.name);
  const grouped = [
    ...[...sections].sort(byName).map((section) => ({ label: section.name, folders: folders.filter((folder) => sectionOf(folder)?.id === section.id).sort(byName) })),
    { label: "Folders", folders: folders.filter((folder) => !sectionOf(folder)).sort(byName) },
  ].filter((group) => group.folders.length);
  return (
    <Modal title="Move to folder" onClose={() => { if (!busy) onClose(); }}>
      <form className="sp-modal-content" onSubmit={async (event) => {
        event.preventDefault();
        if (busy) return;
        setBusy(true);
        try { await onMove(choice); } finally { setBusy(false); }
      }}>
        <p className="sp-doc-move-note">Move “{document.title}” to a folder or Unfiled. Its project link does not change.</p>
        <label className="sp-field">Folder<select data-autofocus value={choice} onChange={(event) => setChoice(event.target.value)} disabled={busy}>
          <option value="">Unfiled</option>
          {grouped.map((group) => (
            <optgroup label={group.label} key={group.label}>
              {group.folders.map((folder) => <option value={folder.id} key={folder.id}>{folder.name}</option>)}
            </optgroup>
          ))}
        </select></label>
        <div className="sp-modal-footer">
          <div className="sp-actions">
            <button type="button" disabled={busy} onClick={onClose}>Cancel</button>
            <button className="sp-primary" disabled={busy || (document.folder ?? "") === choice}>{busy ? "Moving…" : "Move document"}</button>
          </div>
        </div>
      </form>
    </Modal>
  );
}
