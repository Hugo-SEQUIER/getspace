import "../../document-files.css";

import Link from "@web/lib/navigation";
import { useEffect, useRef, useState } from "react";
import { fileDetails, fileKind, fileLabel } from "../../../lib/space/document-files";
import type { Resource } from "../../../lib/space/types";
import { useDocumentDraggable } from "./document-drag";
import { useSpace } from "./provider";
import { relativeDate } from "./ui";
import { Icon } from "./ui-icon";

// The Files tab of the Documents library: every document that keeps a file, as a grid of
// tiles. A tile shows the file's thumbnail once it scrolls near the viewport, or its type
// badge when it has none, then the title and the file's details. Tiles open the document
// and can be dragged into a folder like the library's cards and rows.
export function FileGrid({ documents, base }: { documents: Resource[]; base: string }) {
  return (
    <div className="sp-file-grid" role="list" aria-label="Files">
      {documents.map((document) => <FileTile key={document.id} document={document} base={base} />)}
    </div>
  );
}

function FileTile({ document, base }: { document: Resource; base: string }) {
  const data = useSpace();
  const drag = useDocumentDraggable(document, "files");
  const file = document.file!;
  const kind = fileKind(file);
  const thumbnail = file.thumbnail;
  const media = useRef<HTMLSpanElement>(null);
  const [near, setNear] = useState(false);
  const [url, setUrl] = useState("");
  useEffect(() => {
    const element = media.current;
    if (!element || !thumbnail || near) return;
    if (typeof IntersectionObserver === "undefined") { setNear(true); return; }
    const observer = new IntersectionObserver(([entry]) => { if (entry.isIntersecting) setNear(true); }, { rootMargin: "200px 0px" });
    observer.observe(element);
    return () => observer.disconnect();
  }, [thumbnail, near]);
  useEffect(() => {
    if (!near || !thumbnail) { setUrl(""); return; }
    const controller = new AbortController();
    let value = "";
    data.readThumbnail(document, undefined, controller.signal)
      .then((blob) => { if (controller.signal.aborted) return; value = URL.createObjectURL(blob); setUrl(value); })
      .catch(() => { /* The badge stays; the document itself still opens. */ });
    return () => { controller.abort(); if (value) URL.revokeObjectURL(value); setUrl(""); };
    // The thumbnail's ID fixes its bytes; a new revision with the same thumbnail needs no new read.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [near, document.id, thumbnail?.id, data.space?.id]);
  return (
    <div
      ref={drag.setNodeRef}
      onPointerDown={drag.onPointerDown}
      role="listitem"
      className={`sp-file-tile-wrap ${drag.handle ? "has-drag-handle" : ""} ${drag.isDragging ? "is-dragging" : ""}`}
    >
      <Link className="sp-file-tile" href={`${base}/documents/${document.id}`} draggable={false} title={file.name}>
        <span ref={media} className={`sp-file-tile-media is-${kind}`}>
          {url ? <img src={url} alt="" /> : <span className="sp-file-badge" aria-hidden="true">{fileLabel(file).slice(0, 4)}</span>}
          {kind === "video" && (
            <span className="sp-file-tile-play" aria-hidden="true"><Icon name="play" size={18} /></span>
          )}
        </span>
        <span className="sp-file-tile-body">
          <strong>{document.title}</strong>
          <small>{fileDetails(file)} · {relativeDate(document.updated_at)}</small>
        </span>
      </Link>
      {drag.handle}
    </div>
  );
}
