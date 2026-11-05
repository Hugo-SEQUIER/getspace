import "../../document-files.css";

import dynamic from "@web/lib/lazy";
import { useEffect, useState } from "react";
import { message } from "../../../lib/client";
import { fileDetails, fileKind, fileLabel, formatFileSize, previewMode } from "../../../lib/space/document-files";
import type { Resource } from "../../../lib/space/types";
import { useSpace } from "./provider";
import { Icon } from "./ui-icon";

const PdfPreview = dynamic(() => import("./document-file-previews").then((m) => m.PdfPreview));
const WordPreview = dynamic(() => import("./document-file-previews").then((m) => m.WordPreview));
const SlidesPreview = dynamic(() => import("./document-file-previews").then((m) => m.SlidesPreview));
const SheetPreview = dynamic(() => import("./document-file-previews").then((m) => m.SheetPreview));
const TextPreview = dynamic(() => import("./document-file-previews").then((m) => m.TextPreview));

// Types the browser may display from an object URL. Anything else is only ever downloaded,
// so a file can never run as a page of this site.
const displayTypes: Partial<Record<string, string>> = {
  png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg", gif: "image/gif", webp: "image/webp",
  avif: "image/avif", bmp: "image/bmp", mp3: "audio/mpeg", wav: "audio/wav", ogg: "audio/ogg",
  oga: "audio/ogg", m4a: "audio/mp4", aac: "audio/aac", flac: "audio/flac", mp4: "video/mp4",
  webm: "video/webm", mov: "video/quicktime", m4v: "video/mp4", ogv: "video/ogg", pdf: "application/pdf",
};

function displayType(name: string) {
  return displayTypes[/\.([a-z0-9]+)$/i.exec(name)?.[1].toLowerCase() ?? ""] ?? "application/octet-stream";
}

function saveBlob(blob: Blob, name: string) {
  // An octet stream is always downloaded, whatever the file claims to be.
  const url = URL.createObjectURL(new Blob([blob], { type: "application/octet-stream" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.rel = "noopener";
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

// Parameters: a document that keeps an original file, and the revision being read.
// What it does: shows the file's name, type and size with Download (and Open for PDFs and
// pictures), then a preview for the types Space can display: at once for files up to 25 MB,
// on request above that, since a preview downloads the whole file.
// Output: the original-file section of the document reader.
export function DocumentFileView({ resource, revision }: { resource: Resource; revision?: number }) {
  const data = useSpace();
  const file = resource.file!;
  const kind = fileKind(file);
  const mode = previewMode(file);
  const [requested, setRequested] = useState(false);
  const wanted = mode === "auto" || (mode === "on-request" && requested);
  const [blob, setBlob] = useState<Blob | null>(null);
  const [progress, setProgress] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [pages, setPages] = useState(0);
  const [url, setUrl] = useState("");
  useEffect(() => setRequested(false), [resource.id, file.id]);
  useEffect(() => {
    setBlob(null);
    setError("");
    setPages(0);
    setProgress(0);
    // A read cancelled by the previous file never clears its own loading state.
    setLoading(wanted);
    if (!wanted) return;
    const controller = new AbortController();
    data.readFile(resource, revision, { signal: controller.signal, onProgress: setProgress })
      .then((value) => { if (!controller.signal.aborted) setBlob(value); })
      .catch((reason) => { if (!controller.signal.aborted) setError(message(reason)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
    // The file's ID fixes its bytes; a new revision with the same file needs no new read.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resource.id, file.id, revision, attempt, kind, wanted]);
  useEffect(() => {
    if (!blob || !["image", "audio", "video"].includes(kind)) { setUrl(""); return; }
    const value = URL.createObjectURL(new Blob([blob], { type: displayType(file.name) }));
    setUrl(value);
    return () => URL.revokeObjectURL(value);
  }, [blob, kind, file.name]);

  async function fileBlob() {
    if (blob) return blob;
    setLoading(true);
    setError("");
    try {
      return await data.readFile(resource, revision, { onProgress: setProgress });
    } finally {
      setLoading(false);
    }
  }
  async function download() {
    try { saveBlob(await fileBlob(), file.name); }
    catch (reason) { setError(message(reason)); }
  }
  function open() {
    if (!blob) return;
    const value = URL.createObjectURL(new Blob([blob], { type: displayType(file.name) }));
    window.open(value, "_blank", "noopener");
    window.setTimeout(() => URL.revokeObjectURL(value), 60_000);
  }

  const openable = blob && (kind === "pdf" || kind === "image");
  const detail = [fileDetails(file), pages ? `${pages} ${kind === "slides" ? (pages === 1 ? "slide" : "slides") : pages === 1 ? "page" : "pages"}` : ""]
    .filter(Boolean).join(" · ");
  return (
    <section className="sp-file-view" aria-label="Original file">
      <div className="sp-file-card">
        <span className={`sp-file-badge is-${kind}`} aria-hidden="true">{fileLabel(file).slice(0, 4)}</span>
        <span className="sp-file-name">
          <strong title={file.name}>{file.name}</strong>
          <small>{detail}</small>
        </span>
        <span className="sp-file-actions">
          {openable && (
            <button type="button" onClick={open}>
              <Icon name="external" size={15} />
              <span>Open</span>
            </button>
          )}
          <button type="button" onClick={() => void download()} disabled={loading && !blob}>
            <Icon name="download" size={15} />
            <span>Download</span>
          </button>
        </span>
      </div>
      {loading && !blob && (
        <div className="sp-file-loading" role="status" aria-label="Loading file">
          <progress max={1} value={progress} />
          <span>Loading file… {Math.round(progress * 100)}%</span>
        </div>
      )}
      {error && (
        <div className="sp-file-problem" role="alert">
          <span>{error}</span>
          <button type="button" className="sp-text-button" onClick={() => setAttempt((value) => value + 1)}>Try again</button>
        </div>
      )}
      {blob && (
        <div className="sp-file-preview">
          {kind === "pdf" ? <PdfPreview blob={blob} onPages={setPages} />
            : kind === "word" ? <WordPreview blob={blob} />
              : kind === "slides" ? <SlidesPreview blob={blob} onPages={setPages} />
                : kind === "sheet" ? <SheetPreview blob={blob} />
                  : kind === "text" ? <TextPreview blob={blob} />
                    : kind === "image" ? url && <img src={url} alt={resource.title} />
                      : kind === "audio" ? url && <audio controls src={url} aria-label={file.name} />
                        : kind === "video" ? url && <video controls src={url} aria-label={file.name} />
                          : null}
        </div>
      )}
      {mode === "on-request" && !requested && !blob && (
        <div className="sp-file-on-request">
          <span>This file is {formatFileSize(file.bytes)}, so its preview loads when you ask.</span>
          <button type="button" onClick={() => setRequested(true)}>Load preview</button>
        </div>
      )}
      {mode === "none" && (
        <p className="sp-file-note">
          {kind === "other"
            ? "Space can’t show this type of file here. Download it to open it."
            : "This file is too large to preview here. Download it to open it."}
        </p>
      )}
    </section>
  );
}
