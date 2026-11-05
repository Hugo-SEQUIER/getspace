
import Link from "@web/lib/navigation";
import type { Resource } from "../../../lib/space/types";
import { documentText } from "../../../lib/space/document-content";
import { fileDetails, fileKind } from "../../../lib/space/document-files";
import { documentKind } from "../../../lib/space/document-library";
import { ActionMenu, type ActionMenuItem } from "./action-menu";
import { useDocumentDraggable } from "./document-drag";
import { PageIcon } from "./sidebar-preferences";
import { Avatar, authorName, relativeDate } from "./ui";
import { Icon, type IconName } from "./ui-icon";

/** The glyph a document falls back to when the person chose no icon for it. */
export function documentGlyph(document: Resource): IconName {
  const kind = documentKind(document);
  if (kind === "handoff") return "arrow";
  if (kind !== "file" || !document.file) return "document";
  const media = fileKind(document.file);
  return media === "image" ? "image" : media === "video" ? "play" : media === "audio" ? "headphones" : "document";
}

export function DocumentTreePage({ document, base, selected, onNavigate }: {
  document: Resource;
  base: string;
  selected: boolean;
  onNavigate: () => void;
}) {
  const drag = useDocumentDraggable(document, "tree");
  return (
    <li>
      <div ref={drag.setNodeRef} onPointerDown={drag.onPointerDown} className={`sp-doc-tree-page-row ${selected ? "selected" : ""} ${drag.isDragging ? "is-dragging" : ""}`}>
        <Link
          className={`sp-doc-tree-page ${selected ? "selected" : ""}`}
          href={`${base}/documents/${document.id}`}
          draggable={false}
          aria-current={selected ? "page" : undefined}
          title={document.title}
          onClick={onNavigate}
        >
          <PageIcon pageKey={`document:${document.id}`} fallback={documentGlyph(document)} size={15} />
          <span>{document.title}</span>
        </Link>
        {drag.handle}
      </div>
    </li>
  );
}

// Parameters: a document, where it is filed (empty for Unfiled), the project it links to, whether
//   someone else changed it since the person opened it, and the actions of its ··· menu.
// What it does: one 48px row of the directory's list: a drag grip, the icon, the title with the
//   file's type and size or the folder path under it, then the project, the author and the date.
// Output: a table row.
export function DocumentRow({ document, base, path, projectName, showProject, unread, actions }: {
  document: Resource;
  base: string;
  path: string[];
  projectName?: string;
  showProject: boolean;
  unread: boolean;
  actions: ActionMenuItem[];
}) {
  const drag = useDocumentDraggable(document, "table");
  const author = authorName(document.author, document.author_agent);
  return (
    <tr ref={drag.setNodeRef} onPointerDown={drag.onPointerDown} className={`${unread ? "is-unread" : ""} ${drag.isDragging ? "is-dragging" : ""}`}>
      <td>
        <div className="sp-doc-title-cell">
          {drag.handle}
          <Link className="sp-document-link" href={`${base}/documents/${document.id}`} draggable={false}>
            <span className={`sp-doc-row-icon ${documentKind(document) === "handoff" ? "is-handoff" : ""}`}>
              <PageIcon pageKey={`document:${document.id}`} fallback={documentGlyph(document)} size={17} />
            </span>
            <span className="sp-doc-row-text">
              <span className="sp-doc-row-title">
                {unread && <span className="sp-doc-unread-dot" role="img" aria-label="New since you looked" />}
                {document.title}
                {document.file && <small className="sp-doc-row-file">{fileDetails(document.file)}</small>}
              </span>
              {path.length > 0 && <small className="sp-doc-row-path">{path.join(" / ")}</small>}
              {documentKind(document) === "handoff" && <small className="sp-doc-row-path">Session handoff</small>}
            </span>
          </Link>
        </div>
      </td>
      {showProject && <td className="sp-muted sp-doc-row-project">{projectName ?? "—"}</td>}
      <td><span className="sp-with-avatar"><Avatar name={author} harness={document.author_agent?.harness} kind={document.author === "agent" ? "agent" : "person"} small />{author}</span></td>
      <td className="sp-muted"><time dateTime={document.updated_at}>{relativeDate(document.updated_at)}</time></td>
      <td className="sp-doc-row-actions">{actions.length > 0 && <ActionMenu label={`Actions for ${document.title}`} items={actions} />}</td>
    </tr>
  );
}

// Parameters: a document and when the person last opened it.
// What it does: a small card for the "Recently opened" row: the icon, the title, the opening time.
// Output: a link card.
export function RecentlyOpenedCard({ document, base, openedAt }: { document: Resource; base: string; openedAt: string }) {
  const when = relativeDate(openedAt);
  return (
    <Link className="sp-doc-card sp-doc-card-compact" href={`${base}/documents/${document.id}`}>
      <span className={`sp-doc-file ${documentKind(document) === "handoff" ? "is-handoff" : ""}`}>
        <PageIcon pageKey={`document:${document.id}`} fallback={documentGlyph(document)} size={18} />
      </span>
      <span className="sp-doc-card-compact-text">
        <h3>{document.title}</h3>
        <small>Opened {when === "Today" || when === "Yesterday" ? when.toLowerCase() : `on ${when}`}</small>
      </span>
    </Link>
  );
}

// Parameters: a document and whether someone else changed it since the person opened it.
// What it does: the card of the directory's grid view: icon, title, an excerpt or the file's
//   details, then the author, the date and the revision.
// Output: a draggable card.
export function DocumentRecentCard({ document, base, unread = false }: { document: Resource; base: string; unread?: boolean }) {
  const drag = useDocumentDraggable(document, "recent");
  const isHandoff = documentKind(document) === "handoff";
  const author = authorName(document.author, document.author_agent);
  const excerpt = documentText(document.body ?? "").replace(/^#+\s+.*$/gm, "").replace(/[*`>]/g, "").replace(/\s+/g, " ").trim().slice(0, 160);
  return (
    <div ref={drag.setNodeRef} onPointerDown={drag.onPointerDown} className={`sp-doc-card-wrap ${drag.handle ? "has-drag-handle" : ""} ${drag.isDragging ? "is-dragging" : ""}`}>
      <Link className={`sp-doc-card ${unread ? "is-unread" : ""}`} href={`${base}/documents/${document.id}`} draggable={false}>
        <div className="sp-doc-card-top">
          <span className={`sp-doc-file ${isHandoff ? "is-handoff" : ""}`}>
            <PageIcon pageKey={`document:${document.id}`} fallback={documentGlyph(document)} size={18} />
          </span>
          {unread && <span className="sp-doc-unread-dot" role="img" aria-label="New since you looked" />}
        </div>
        <h3>{document.title}</h3>
        <p>{document.file ? `${fileDetails(document.file)} · Original file` : isHandoff ? "Session handoff" : excerpt || "Open the latest saved version."}</p>
        <div className="sp-doc-card-footer">
          <span><Avatar name={author} harness={document.author_agent?.harness} kind={document.author === "agent" ? "agent" : "person"} small />{author}</span>
          <span>{relativeDate(document.updated_at)} · v{document.revision}</span>
        </div>
      </Link>
      {drag.handle}
    </div>
  );
}

/** Keeps the old export name for the reader's related lists. */
export { Icon as DocumentListIcon };
