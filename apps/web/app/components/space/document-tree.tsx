
import Link from "@web/lib/navigation";
import { useRouter } from "@web/lib/navigation";
import { useState } from "react";
import { message } from "../../../lib/client";
import { useFeedback } from "../notifications";
import type { DocumentFolder, OrganizationItem, Resource } from "../../../lib/space/types";
import { useSpace } from "./provider";
import { Modal, ConfirmModal } from "./ui";
import { Icon } from "./ui-icon";
import { useSidebarPreferences } from "./sidebar-preferences";
import { DocumentFolderDropTarget, useDocumentDragState } from "./document-drag";
import { DocumentTreePage } from "./document-list-items";

type TreeProps = {
  base: string;
  documents: Resource[];
  selectedId?: string;
  projectFilter: string;
  folderFilter: string;
  sectionFilter: string;
  query: string;
  onQueryChange: (value: string) => void;
  onNewDocument: (folderId?: string) => void;
  onFolderSelect: (folderId: string) => void;
  onNavigate: () => void;
};

export function DocumentTree({
  base, documents, selectedId, projectFilter, folderFilter, sectionFilter, query, onQueryChange,
  onNewDocument, onFolderSelect, onNavigate,
}: TreeProps) {
  const data = useSpace();
  const { dragging } = useDocumentDragState();
  const router = useRouter();
  const { preferences, update } = useSidebarPreferences();
  const [sectionDraft, setSectionDraft] = useState<OrganizationItem | null>(null);
  const [deletingItem, setDeletingItem] = useState<OrganizationItem | DocumentFolder | null>(null);
  const [folderDraft, setFolderDraft] = useState<DocumentFolder | null>(null);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useFeedback({ error, loading: busy ? "Updating document organization…" : null, clearError: () => setError("") });
  useFeedback({ error: data.sectionError || data.folderError, loading: !data.sectionReady && !data.sectionError || !data.folderReady && !data.folderError ? "Loading folders…" : null });
  const sections = data.organizationItems
    .filter((item) => item.kind === "section" && !item.deleted)
    .sort((a, b) => a.name.localeCompare(b.name));
  const folders = data.folders.filter((folder) => !folder.deleted).sort((a, b) => a.name.localeCompare(b.name));
  const folderIds = new Set(folders.map((folder) => folder.id));
  const sectionIds = new Set(sections.map((section) => section.id));
  const loose = documents.filter((document) => !document.folder || !folderIds.has(document.folder));
  const deletingKind = deletingItem?.kind === "section" ? "section" : "folder";
  const search = query.trim().toLowerCase();
  const matches = (value: string) => value.toLowerCase().includes(search);
  const matchingDocuments = (items: Resource[], parentMatches = false) =>
    [...items].filter((item) => !search || dragging || parentMatches || matches(item.title))
      .sort((a, b) => a.title.localeCompare(b.title));
  const isExpanded = (key: string, defaultOpen: boolean) =>
    !!search || (preferences.collapsed[key] === undefined ? defaultOpen : !preferences.collapsed[key]);
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

  const editingSection = !!sectionDraft && sections.some((section) => section.id === sectionDraft.id);
  function openSection(item?: OrganizationItem) {
    setSectionDraft(item ?? { id: crypto.randomUUID(), kind: "section", name: "", description: "" });
    setName(item?.name ?? "");
    setError("");
  }
  function openFolder(sectionId: string | null, folder?: DocumentFolder) {
    setFolderDraft(folder ?? { id: crypto.randomUUID(), kind: "folder", name: "", description: "", section_id: sectionId });
    setName(folder?.name ?? "");
    setError("");
  }
  function pages(items: Resource[]) {
    return items.map((document) => (
      <DocumentTreePage key={document.id} document={document} base={base} selected={selectedId === document.id} onNavigate={onNavigate} />
    ));
  }
  function folderNode(folder: DocumentFolder, parentMatches = false) {
    const folderDocuments = matchingDocuments(documents.filter((document) => document.folder === folder.id), parentMatches || matches(folder.name));
    if (search && !dragging && !parentMatches && !matches(folder.name) && !folderDocuments.length) return null;
    const key = `document-tree:folder:${folder.id}`;
    const open = isExpanded(key, folder.id === activeFolder);
    return (
      <li className="sp-doc-tree-project" key={folder.id}>
        <DocumentFolderDropTarget folder={folder.id} className={`sp-doc-tree-row ${!selectedId && folderFilter === folder.id ? "selected" : ""}`}>
          <button
            className="sp-doc-tree-toggle"
            aria-label={`${open ? "Collapse" : "Expand"} ${folder.name}`}
            aria-expanded={open}
            aria-controls={`doc-tree-folder-${folder.id}`}
            disabled={!!search}
            onClick={() => toggle(key, folder.id === activeFolder)}
          >
            <Icon name={open ? "down" : "chevron"} size={12} />
          </button>
          <Link href={`${base}/documents?folder=${encodeURIComponent(folder.id)}`} aria-current={!selectedId && folderFilter === folder.id ? "page" : undefined} onClick={onNavigate} title={folder.name}>
            <Icon name="folder" size={15} />
            <span>{folder.name}</span>
            <small>{folderDocuments.length}</small>
          </Link>
          {data.canWrite && (
            <>
              <button className="sp-doc-tree-add" aria-label={`Folder options for ${folder.name}`} title="Folder options" onClick={() => openFolder(folder.section_id ?? null, folder)}><Icon name="more" size={14} /></button>
              <button className="sp-doc-tree-add" aria-label={`New document in ${folder.name}`} title="New document" onClick={() => onNewDocument(folder.id)}><Icon name="plus" size={14} /></button>
            </>
          )}
        </DocumentFolderDropTarget>
        <ul id={`doc-tree-folder-${folder.id}`} hidden={!open}>
          {pages(folderDocuments)}
          {!folderDocuments.length && <li className="sp-doc-tree-empty">No pages yet</li>}
        </ul>
      </li>
    );
  }
  function sectionNode(section: OrganizationItem) {
    const children = folders.filter((folder) => folder.section_id === section.id);
    const sectionMatches = matches(section.name);
    const shown = children.map((folder) => folderNode(folder, sectionMatches)).filter(Boolean);
    if (search && !dragging && !sectionMatches && !shown.length) return null;
    const key = `document-tree:section:${section.id}`;
    const open = dragging || isExpanded(key, true);
    const selected = !selectedId && !folderFilter && sectionFilter === section.id;
    return (
      <li className="sp-doc-tree-section" key={section.id}>
        <div className={`sp-doc-tree-row sp-doc-tree-section-row ${selected ? "selected" : ""}`}>
          <button className="sp-doc-tree-toggle" aria-label={`${open ? "Collapse" : "Expand"} ${section.name}`} aria-expanded={open} aria-controls={`doc-tree-section-${section.id}`} disabled={!!search} onClick={() => toggle(key, true)}>
            <Icon name={open ? "down" : "chevron"} size={12} />
          </button>
          <Link className="sp-doc-tree-section-name" href={`${base}/documents?section=${encodeURIComponent(section.id)}`} aria-current={selected ? "page" : undefined} title={section.name} onClick={onNavigate}>
            <span>{section.name}</span>
          </Link>
          {data.canWrite && (
            <>
              <button className="sp-doc-tree-add" aria-label={`Section options for ${section.name}`} title="Section options" onClick={() => openSection(section)}><Icon name="more" size={14} /></button>
              <button className="sp-doc-tree-add" aria-label={`New folder in ${section.name}`} title="New folder" onClick={() => openFolder(section.id)}><Icon name="plus" size={14} /></button>
            </>
          )}
        </div>
        <ul id={`doc-tree-section-${section.id}`} hidden={!open}>
          {shown}
          {!shown.length && <li className="sp-doc-tree-empty">No folders yet</li>}
        </ul>
      </li>
    );
  }
  const unsectioned = folders.filter((folder) => !folder.section_id || !sectionIds.has(folder.section_id));
  const shownUnsectioned = unsectioned.map((folder) => folderNode(folder)).filter(Boolean);
  const shownLoose = matchingDocuments(loose);
  const shownSections = sections.map(sectionNode).filter(Boolean);
  const foldersOpen = dragging || isExpanded("document-tree:folders", true);
  const unfiledOpen = dragging || isExpanded("document-tree:unfiled", true);

  return (
    <>
      <nav className="sp-doc-tree" aria-label="Document sections">
        <div className="sp-doc-tree-heading">
          <strong>Pages</strong>
          {data.canWrite && (
            <div className="sp-actions">
              <button className="sp-doc-tree-add" aria-label="New folder" title="New folder" onClick={() => openFolder(null)}><Icon name="folder" size={15} /></button>
              <button className="sp-doc-tree-add" aria-label="New section" title="New section" onClick={() => openSection()}><Icon name="plus" size={15} /></button>
            </div>
          )}
        </div>
        <label className="sp-doc-tree-search">
          <Icon name="search" size={15} />
          <input aria-label="Search document tree" placeholder="Find a page…" value={query} onChange={(event) => onQueryChange(event.target.value)} />
        </label>
        <Link className={`sp-doc-tree-all ${!selectedId && !projectFilter && !folderFilter && !sectionFilter ? "selected" : ""}`} href={`${base}/documents`} aria-current={!selectedId && !projectFilter && !folderFilter && !sectionFilter ? "page" : undefined} onClick={onNavigate}>
          <Icon name="document" size={15} /><span>All documents</span><small>{documents.length}</small>
        </Link>
        {data.canWrite && <p className="sp-doc-drag-hint">Drag pages to a folder or Unfiled.</p>}
        {!data.sectionReady || !data.folderReady ? (
          <div className="sp-doc-tree-status" aria-busy={!data.sectionError && !data.folderError}>
            {data.sectionError || data.folderError ? (
              <button onClick={() => { data.retrySections(); data.retryFolders(); }}>Retry folders</button>
            ) : <div className="sp-skeleton" aria-label="Loading folders" />}
          </div>
        ) : <ul className="sp-doc-tree-groups">
          {shownSections}
          {(shownUnsectioned.length > 0 || (!search && !sections.length)) && (
            <li className="sp-doc-tree-section">
              <div className="sp-doc-tree-row sp-doc-tree-section-row">
                <button className="sp-doc-tree-toggle" aria-label={`${foldersOpen ? "Collapse" : "Expand"} Folders`} aria-expanded={foldersOpen} aria-controls="doc-tree-folders" disabled={!!search} onClick={() => toggle("document-tree:folders", true)}>
                  <Icon name={foldersOpen ? "down" : "chevron"} size={12} />
                </button>
                <span className="sp-doc-tree-section-name">Folders</span>
              </div>
              <ul id="doc-tree-folders" hidden={!foldersOpen}>{shownUnsectioned}</ul>
            </li>
          )}
          {(!search || dragging || shownLoose.length > 0) && (
            <li className="sp-doc-tree-section">
              <DocumentFolderDropTarget className={`sp-doc-tree-row sp-doc-tree-section-row ${!selectedId && folderFilter === "unfiled" ? "selected" : ""}`}>
                <button className="sp-doc-tree-toggle" aria-label={`${unfiledOpen ? "Collapse" : "Expand"} Unfiled`} aria-expanded={unfiledOpen} aria-controls="doc-tree-unfiled" disabled={!!search} onClick={() => toggle("document-tree:unfiled", true)}>
                  <Icon name={unfiledOpen ? "down" : "chevron"} size={12} />
                </button>
                <Link className="sp-doc-tree-section-name" href={`${base}/documents?folder=unfiled`} aria-current={!selectedId && folderFilter === "unfiled" ? "page" : undefined} onClick={onNavigate}>Unfiled</Link>
                <small>{shownLoose.length}</small>
              </DocumentFolderDropTarget>
              <ul id="doc-tree-unfiled" hidden={!unfiledOpen}>{pages(shownLoose)}{!shownLoose.length && <li className="sp-doc-tree-empty">No pages yet</li>}</ul>
            </li>
          )}
          {search && !shownSections.length && !shownUnsectioned.length && !shownLoose.length && <li className="sp-doc-tree-empty">No matching pages</li>}
        </ul>}
      </nav>
      {deletingItem && (
        <ConfirmModal
          title={`Delete ${deletingKind}?`}
          confirmLabel={`Delete ${deletingKind}`}
          busyLabel="Deleting…"
          busy={busy}
          error={error}
          onClose={() => setDeletingItem(null)}
          onConfirm={async () => {
            if (busy) return;
            setBusy(true); setError("");
            try {
              if (deletingItem.kind === "folder") await data.archiveFolder(deletingItem, true);
              else await data.archiveOrganizationItem(deletingItem, true);
              data.setNotice(`${deletingKind === "section" ? "Section" : "Folder"} deleted. Documents are still in All documents.`);
              setDeletingItem(null);
              if (!selectedId && (folderFilter === deletingItem.id || sectionFilter === deletingItem.id)) router.replace(`${base}/documents`);
            } catch (e) { setError(message(e)); }
            finally { setBusy(false); }
          }}
        >
          <p>Delete “{deletingItem.name}”? Its documents will stay in All documents. {deletingKind === "section" ? "Its folders will be kept in Folders." : "Its documents will become Unfiled."} No documents will be archived or deleted.</p>
        </ConfirmModal>
      )}
      {sectionDraft && (
        <Modal title={editingSection ? "Edit section" : "New section"} onClose={() => { if (!busy) setSectionDraft(null); }}>
          <form className="sp-modal-content" onSubmit={async (event) => {
            event.preventDefault();
            if (busy || !name.trim()) return;
            setBusy(true); setError("");
            try {
              await data.saveOrganizationItem({ ...sectionDraft, name: name.trim() });
              setSectionDraft(null);
            } catch (e) { setError(message(e)); }
            finally { setBusy(false); }
          }}>
            <label className="sp-field">Section name<input data-autofocus required maxLength={200} value={name} onChange={(event) => setName(event.target.value)} disabled={busy} /></label>
            <div className="sp-modal-footer">
              {editingSection && <button type="button" className="sp-danger-text" disabled={busy} onClick={() => { const section = sectionDraft; setSectionDraft(null); setError(""); setDeletingItem(section); }}>Delete section</button>}
              <div className="sp-actions"><button type="button" disabled={busy} onClick={() => setSectionDraft(null)}>Cancel</button><button className="sp-primary" disabled={busy || !name.trim()}>{busy ? "Saving…" : editingSection ? "Save section" : "Create section"}</button></div>
            </div>
          </form>
        </Modal>
      )}
      {folderDraft && (
        <Modal title={folderDraft.revision ? "Edit folder" : "New folder"} onClose={() => { if (!busy) setFolderDraft(null); }}>
          <form className="sp-modal-content" onSubmit={async (event) => {
            event.preventDefault();
            if (busy || !name.trim()) return;
            setBusy(true); setError("");
            try {
              const saved = await data.saveFolder({ ...folderDraft, name: name.trim(), section_id: folderDraft.section_id && sectionIds.has(folderDraft.section_id) ? folderDraft.section_id : null });
              setFolderDraft(null);
              onFolderSelect(saved.id);
            } catch (e) { setError(message(e)); }
            finally { setBusy(false); }
          }}>
            <label className="sp-field">Folder name<input data-autofocus required maxLength={200} value={name} onChange={(event) => setName(event.target.value)} disabled={busy} /></label>
            <label className="sp-field">Section<select value={folderDraft.section_id && sectionIds.has(folderDraft.section_id) ? folderDraft.section_id : ""} onChange={(event) => setFolderDraft({ ...folderDraft, section_id: event.target.value || null })} disabled={busy}>
              <option value="">No section</option>
              {sections.map((section) => <option value={section.id} key={section.id}>{section.name}</option>)}
            </select></label>
            <div className="sp-modal-footer">
              {folderDraft.revision && <button type="button" className="sp-danger-text" disabled={busy} onClick={() => { setFolderDraft(null); setError(""); setDeletingItem(folderDraft); }}>Delete folder</button>}
              <div className="sp-actions"><button type="button" disabled={busy} onClick={() => setFolderDraft(null)}>Cancel</button><button className="sp-primary" disabled={busy || !name.trim()}>{busy ? "Saving…" : folderDraft.revision ? "Save folder" : "Create folder"}</button></div>
            </div>
          </form>
        </Modal>
      )}
    </>
  );
}
