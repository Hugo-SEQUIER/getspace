
import Link from "@web/lib/navigation";
import type { Resource } from "../../../lib/space/types";
import { documentText } from "../../../lib/space/document-content";
import { useDocumentDraggable } from "./document-drag";
import { PageIcon } from "./sidebar-preferences";
import { Avatar, authorName, relativeDate } from "./ui";
import { Icon } from "./ui-icon";

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
          <PageIcon pageKey={`document:${document.id}`} fallback={document.recall?.category === "handoff" ? "arrow" : "document"} size={15} />
          <span>{document.title}</span>
        </Link>
        {drag.handle}
      </div>
    </li>
  );
}

export function DocumentTableRow({ document, base, folderName }: {
  document: Resource;
  base: string;
  folderName?: string;
}) {
  const drag = useDocumentDraggable(document, "table");
  const isHandoff = document.recall?.category === "handoff";
  const author = authorName(document.author, document.author_agent);
  return (
    <tr ref={drag.setNodeRef} onPointerDown={drag.onPointerDown} className={drag.isDragging ? "is-dragging" : ""}>
      <td>
        <div className="sp-doc-title-cell">
          {drag.handle}
          <Link className="sp-document-link" href={`${base}/documents/${document.id}`} draggable={false}>
            <span className={`sp-doc-row-icon ${isHandoff ? "is-handoff" : ""}`}>
              <PageIcon pageKey={`document:${document.id}`} fallback={isHandoff ? "arrow" : "document"} size={17} />
            </span>
            <span>{document.title}{isHandoff && <small>Session handoff</small>}</span>
          </Link>
        </div>
      </td>
      <td><span className="sp-doc-project-label">{folderName && <Icon name="folder" size={12} />}{folderName ?? "Unfiled"}</span></td>
      <td><span className="sp-with-avatar"><Avatar name={author} harness={document.author_agent?.harness} kind={document.author === "agent" ? "agent" : "person"} small />{author}</span></td>
      <td className="sp-muted">{relativeDate(document.updated_at)}</td>
      <td><span className="sp-doc-version">v{document.revision}</span></td>
    </tr>
  );
}

export function DocumentRecentCard({ document, base }: { document: Resource; base: string }) {
  const drag = useDocumentDraggable(document, "recent");
  const isHandoff = document.recall?.category === "handoff";
  const author = authorName(document.author, document.author_agent);
  const excerpt = documentText(document.body ?? "").replace(/^#+\s+.*$/gm, "").replace(/[*`>]/g, "").replace(/\s+/g, " ").trim().slice(0, 160);
  return (
    <div ref={drag.setNodeRef} onPointerDown={drag.onPointerDown} className={`sp-doc-card-wrap ${drag.handle ? "has-drag-handle" : ""} ${drag.isDragging ? "is-dragging" : ""}`}>
      <Link className="sp-doc-card" href={`${base}/documents/${document.id}`} draggable={false}>
        <div className="sp-doc-card-top">
          <span className={`sp-doc-file ${isHandoff ? "is-handoff" : ""}`}>
            <PageIcon pageKey={`document:${document.id}`} fallback={isHandoff ? "arrow" : "document"} size={18} />
          </span>
        </div>
        <h3>{document.title}</h3>
        <p>{excerpt || "Open the latest saved version."}</p>
        <div className="sp-doc-card-footer">
          <span><Avatar name={author} harness={document.author_agent?.harness} kind={document.author === "agent" ? "agent" : "person"} small />{author}</span>
          <span>{relativeDate(document.updated_at)} · v{document.revision}</span>
        </div>
      </Link>
      {drag.handle}
    </div>
  );
}
