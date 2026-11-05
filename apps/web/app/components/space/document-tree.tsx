
import Link from "@web/lib/navigation";
import type { DocumentFolder, OrganizationItem, Resource } from "../../../lib/space/types";
import { useSpace } from "./provider";
import { Icon } from "./ui-icon";
import { useSidebarPreferences } from "./sidebar-preferences";
import { DocumentFolderDropTarget, useDocumentDragState } from "./document-drag";
import { DocumentTreePage } from "./document-list-items";
import type { OrganizationDialogs } from "./document-organization";

type TreeProps = {
  base: string;
  documents: Resource[];
  selectedId?: string;
  projectFilter: string;
  folderFilter: string;
  sectionFilter: string;
  /** Whether the directory shows only the documents changed since the person looked. */
  unreadFilter: boolean;
  unreadCount: number;
  dialogs: OrganizationDialogs;
  onNewDocument: (folderId?: string) => void;
  onNavigate: () => void;
};

// Parameters: the listed documents, the open document or filter, the unread count, the shared
//   section and folder dialogs, and what happens on a choice.
// What it does: the rail beside the directory and the reader: every section, folder and Unfiled
//   with counts, each a link and a drop target. Disclosure state is kept per person in the browser.
// Output: the navigation rail.
export function DocumentTree({
  base, documents, selectedId, projectFilter, folderFilter, sectionFilter, unreadFilter, unreadCount, dialogs,
  onNewDocument, onNavigate,
}: TreeProps) {
  const data = useSpace();
  const { dragging } = useDocumentDragState();
  const { preferences, update } = useSidebarPreferences();
  const sections = data.organizationItems
    .filter((item) => item.kind === "section" && !item.deleted)
    .sort((a, b) => a.name.localeCompare(b.name));
  const folders = data.folders.filter((folder) => !folder.deleted).sort((a, b) => a.name.localeCompare(b.name));
  const folderIds = new Set(folders.map((folder) => folder.id));
  const sectionIds = new Set(sections.map((section) => section.id));
  const loose = documents.filter((document) => !document.folder || !folderIds.has(document.folder));
  const byTitle = (items: Resource[]) => [...items].sort((a, b) => a.title.localeCompare(b.title));
  const isExpanded = (key: string, defaultOpen: boolean) =>
    preferences.collapsed[key] === undefined ? defaultOpen : !preferences.collapsed[key];
  const toggle = (key: string, defaultOpen: boolean) =>
    update((old) => ({
      ...old,
      collapsed: {
        ...old.collapsed,
        [key]: old.collapsed[key] === undefined ? defaultOpen : !old.collapsed[key],
      },
    }));
  const activeFolder = selectedId
    ? documents.find((document) => document.id === selectedId)?.folder
    : folderFilter;
  const atRoot = !selectedId && !projectFilter && !folderFilter && !sectionFilter && !unreadFilter;

  function pages(items: Resource[]) {
    return items.map((document) => (
      <DocumentTreePage key={document.id} document={document} base={base} selected={selectedId === document.id} onNavigate={onNavigate} />
    ));
  }
  function folderNode(folder: DocumentFolder) {
    const folderDocuments = byTitle(documents.filter((document) => document.folder === folder.id));
    const key = `document-tree:folder:${folder.id}`;
    const open = isExpanded(key, folder.id === activeFolder);
    const selected = !selectedId && folderFilter === folder.id;
    return (
      <li className="sp-doc-tree-project" key={folder.id}>
        <DocumentFolderDropTarget folder={folder.id} className={`sp-doc-tree-row ${selected ? "selected" : ""}`}>
          <button
            className="sp-doc-tree-toggle"
            aria-label={`${open ? "Collapse" : "Expand"} ${folder.name}`}
            aria-expanded={open}
            aria-controls={`doc-tree-folder-${folder.id}`}
            onClick={() => toggle(key, folder.id === activeFolder)}
          >
            <Icon name={open ? "down" : "chevron"} size={12} />
          </button>
          <Link href={`${base}/documents?folder=${encodeURIComponent(folder.id)}`} aria-current={selected ? "page" : undefined} onClick={onNavigate} title={folder.name}>
            <Icon name="folder" size={15} />
            <span>{folder.name}</span>
            <small>{folderDocuments.length}</small>
          </Link>
          {data.canWrite && (
            <>
              <button className="sp-doc-tree-add" aria-label={`Folder options for ${folder.name}`} title="Folder options" onClick={() => dialogs.openFolder(folder.section_id ?? null, folder)}><Icon name="more" size={14} /></button>
              <button className="sp-doc-tree-add" aria-label={`New document in ${folder.name}`} title="New document" onClick={() => onNewDocument(folder.id)}><Icon name="plus" size={14} /></button>
            </>
          )}
        </DocumentFolderDropTarget>
        <ul id={`doc-tree-folder-${folder.id}`} hidden={!open}>
          {pages(folderDocuments)}
          {!folderDocuments.length && <li className="sp-doc-tree-empty">No documents yet</li>}
        </ul>
      </li>
    );
  }
  function sectionNode(section: OrganizationItem) {
    const children = folders.filter((folder) => folder.section_id === section.id);
    const count = documents.filter((document) => children.some((folder) => folder.id === document.folder)).length;
    const key = `document-tree:section:${section.id}`;
    const open = dragging || isExpanded(key, true);
    const selected = !selectedId && !folderFilter && sectionFilter === section.id;
    return (
      <li className="sp-doc-tree-section" key={section.id}>
        <div className={`sp-doc-tree-row sp-doc-tree-section-row ${selected ? "selected" : ""}`}>
          <button className="sp-doc-tree-toggle" aria-label={`${open ? "Collapse" : "Expand"} ${section.name}`} aria-expanded={open} aria-controls={`doc-tree-section-${section.id}`} onClick={() => toggle(key, true)}>
            <Icon name={open ? "down" : "chevron"} size={12} />
          </button>
          <Link className="sp-doc-tree-section-name" href={`${base}/documents?section=${encodeURIComponent(section.id)}`} aria-current={selected ? "page" : undefined} title={section.name} onClick={onNavigate}>
            <span>{section.name}</span>
          </Link>
          <small>{count}</small>
          {data.canWrite && (
            <>
              <button className="sp-doc-tree-add" aria-label={`Section options for ${section.name}`} title="Section options" onClick={() => dialogs.openSection(section)}><Icon name="more" size={14} /></button>
              <button className="sp-doc-tree-add" aria-label={`New folder in ${section.name}`} title="New folder" onClick={() => dialogs.openFolder(section.id)}><Icon name="plus" size={14} /></button>
            </>
          )}
        </div>
        <ul id={`doc-tree-section-${section.id}`} hidden={!open}>
          {children.map((folder) => folderNode(folder))}
          {!children.length && <li className="sp-doc-tree-empty">No folders yet</li>}
        </ul>
      </li>
    );
  }
  const unsectioned = folders.filter((folder) => !folder.section_id || !sectionIds.has(folder.section_id));
  const shownLoose = byTitle(loose);
  const foldersOpen = dragging || isExpanded("document-tree:folders", true);
  const unfiledOpen = dragging || isExpanded("document-tree:unfiled", true);

  return (
    <nav className="sp-doc-tree" aria-label="Document sections">
      <div className="sp-doc-tree-heading">
        <strong>Documents</strong>
        {data.canWrite && (
          <div className="sp-actions">
            <button className="sp-doc-tree-add" aria-label="New folder" title="New folder" onClick={() => dialogs.openFolder(null)}><Icon name="folder" size={15} /></button>
            <button className="sp-doc-tree-add" aria-label="New section" title="New section" onClick={() => dialogs.openSection()}><Icon name="plus" size={15} /></button>
          </div>
        )}
      </div>
      <Link className={`sp-doc-tree-all ${atRoot ? "selected" : ""}`} href={`${base}/documents`} aria-current={atRoot ? "page" : undefined} onClick={onNavigate}>
        <Icon name="document" size={15} /><span>All documents</span><small>{documents.length}</small>
      </Link>
      {unreadCount > 0 && (
        <Link className={`sp-doc-tree-all sp-doc-tree-unread ${unreadFilter ? "selected" : ""}`} href={`${base}/documents?unread=1`} aria-current={unreadFilter ? "page" : undefined} onClick={onNavigate}>
          <span className="sp-doc-unread-dot" aria-hidden="true" /><span>New since you looked</span><small>{unreadCount}</small>
        </Link>
      )}
      {!data.sectionReady || !data.folderReady ? (
        <div className="sp-doc-tree-status" aria-busy={!data.sectionError && !data.folderError}>
          {data.sectionError || data.folderError ? (
            <button onClick={() => { data.retrySections(); data.retryFolders(); }}>Retry folders</button>
          ) : <div className="sp-skeleton" aria-label="Loading folders" />}
        </div>
      ) : <ul className="sp-doc-tree-groups">
        {sections.map(sectionNode)}
        {(unsectioned.length > 0 || !sections.length) && (
          <li className="sp-doc-tree-section">
            <div className="sp-doc-tree-row sp-doc-tree-section-row">
              <button className="sp-doc-tree-toggle" aria-label={`${foldersOpen ? "Collapse" : "Expand"} Folders`} aria-expanded={foldersOpen} aria-controls="doc-tree-folders" onClick={() => toggle("document-tree:folders", true)}>
                <Icon name={foldersOpen ? "down" : "chevron"} size={12} />
              </button>
              <span className="sp-doc-tree-section-name">Folders</span>
              <small>{documents.filter((document) => unsectioned.some((folder) => folder.id === document.folder)).length}</small>
            </div>
            <ul id="doc-tree-folders" hidden={!foldersOpen}>
              {unsectioned.map((folder) => folderNode(folder))}
              {!unsectioned.length && <li className="sp-doc-tree-empty">No folders yet</li>}
            </ul>
          </li>
        )}
        <li className="sp-doc-tree-section">
          <DocumentFolderDropTarget className={`sp-doc-tree-row sp-doc-tree-section-row ${!selectedId && folderFilter === "unfiled" ? "selected" : ""}`}>
            <button className="sp-doc-tree-toggle" aria-label={`${unfiledOpen ? "Collapse" : "Expand"} Unfiled`} aria-expanded={unfiledOpen} aria-controls="doc-tree-unfiled" onClick={() => toggle("document-tree:unfiled", true)}>
              <Icon name={unfiledOpen ? "down" : "chevron"} size={12} />
            </button>
            <Link className="sp-doc-tree-section-name" href={`${base}/documents?folder=unfiled`} aria-current={!selectedId && folderFilter === "unfiled" ? "page" : undefined} onClick={onNavigate}>Unfiled</Link>
            <small>{shownLoose.length}</small>
          </DocumentFolderDropTarget>
          <ul id="doc-tree-unfiled" hidden={!unfiledOpen}>{pages(shownLoose)}{!shownLoose.length && <li className="sp-doc-tree-empty">No documents yet</li>}</ul>
        </li>
      </ul>}
      {data.canWrite && <p className="sp-doc-drag-hint">Drag documents to a folder or Unfiled.</p>}
    </nav>
  );
}
