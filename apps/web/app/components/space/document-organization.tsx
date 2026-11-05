import { useRouter } from "@web/lib/navigation";
import { useState, type ReactNode } from "react";
import { message } from "../../../lib/client";
import { useFeedback } from "../notifications";
import type { DocumentFolder, OrganizationItem } from "../../../lib/space/types";
import { useSpace } from "./provider";
import { ConfirmModal, Modal } from "./ui";

export type OrganizationDialogs = {
  /** Opens the section dialog: a new section, or the given section's name and delete action. */
  openSection: (section?: OrganizationItem) => void;
  /** Opens the folder dialog: a new folder in the given section, or the given folder's details. */
  openFolder: (sectionId: string | null, folder?: DocumentFolder) => void;
  /** Asks to delete a section or folder; documents are never deleted with their container. */
  deleteItem: (item: OrganizationItem | DocumentFolder) => void;
  /** The dialogs to render once, wherever the hook is used. */
  element: ReactNode;
  busy: boolean;
};

// Parameters: the directory's address, the open folder or section, and what to do with a folder
//   once it is saved (the tree and the directory both open it).
// What it does: owns the section and folder dialogs so the tree rail and the folder tiles share
//   them: create, rename, move a folder between sections, and delete a container, which keeps
//   every document (in All documents, and in Unfiled for a deleted folder).
// Output: the dialog openers and the dialogs themselves.
export function useOrganizationDialogs({
  base,
  selectedId,
  folderFilter,
  sectionFilter,
  onFolderSaved,
}: {
  base: string;
  selectedId?: string;
  folderFilter: string;
  sectionFilter: string;
  onFolderSaved: (folder: DocumentFolder) => void;
}): OrganizationDialogs {
  const data = useSpace();
  const router = useRouter();
  const [sectionDraft, setSectionDraft] = useState<OrganizationItem | null>(null);
  const [deletingItem, setDeletingItem] = useState<OrganizationItem | DocumentFolder | null>(null);
  const [folderDraft, setFolderDraft] = useState<DocumentFolder | null>(null);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useFeedback({ error, loading: busy ? "Updating document organization…" : null, clearError: () => setError("") });
  const sections = data.organizationItems
    .filter((item) => item.kind === "section" && !item.deleted)
    .sort((a, b) => a.name.localeCompare(b.name));
  const sectionIds = new Set(sections.map((section) => section.id));
  const editingSection = !!sectionDraft && sections.some((section) => section.id === sectionDraft.id);
  const deletingKind = deletingItem?.kind === "section" ? "section" : "folder";

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
  function deleteItem(item: OrganizationItem | DocumentFolder) {
    setError("");
    setDeletingItem(item);
  }

  const element = (
    <>
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
              {editingSection && <button type="button" className="sp-danger-text" disabled={busy} onClick={() => { const section = sectionDraft; setSectionDraft(null); deleteItem(section); }}>Delete section</button>}
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
              const saved = await data.saveFolder({
                ...folderDraft,
                name: name.trim(),
                description: folderDraft.description.trim(),
                section_id: folderDraft.section_id && sectionIds.has(folderDraft.section_id) ? folderDraft.section_id : null,
              });
              setFolderDraft(null);
              onFolderSaved(saved);
            } catch (e) { setError(message(e)); }
            finally { setBusy(false); }
          }}>
            <label className="sp-field">Folder name<input data-autofocus required maxLength={200} value={name} onChange={(event) => setName(event.target.value)} disabled={busy} /></label>
            <label className="sp-field">Description<textarea rows={2} maxLength={500} placeholder="What goes in this folder" value={folderDraft.description} onChange={(event) => setFolderDraft({ ...folderDraft, description: event.target.value })} disabled={busy} /></label>
            <label className="sp-field">Section<select value={folderDraft.section_id && sectionIds.has(folderDraft.section_id) ? folderDraft.section_id : ""} onChange={(event) => setFolderDraft({ ...folderDraft, section_id: event.target.value || null })} disabled={busy}>
              <option value="">No section</option>
              {sections.map((section) => <option value={section.id} key={section.id}>{section.name}</option>)}
            </select></label>
            <div className="sp-modal-footer">
              {folderDraft.revision && <button type="button" className="sp-danger-text" disabled={busy} onClick={() => { const folder = folderDraft; setFolderDraft(null); deleteItem(folder); }}>Delete folder</button>}
              <div className="sp-actions"><button type="button" disabled={busy} onClick={() => setFolderDraft(null)}>Cancel</button><button className="sp-primary" disabled={busy || !name.trim()}>{busy ? "Saving…" : folderDraft.revision ? "Save folder" : "Create folder"}</button></div>
            </div>
          </form>
        </Modal>
      )}
    </>
  );

  return { openSection, openFolder, deleteItem, element, busy };
}
