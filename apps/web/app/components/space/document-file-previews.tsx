import { useEffect, useMemo, useRef, useState } from "react";
import type { PDFDocumentProxy, RenderTask } from "pdfjs-dist";
import { checkArchiveText, officeXml } from "../../../lib/space/document-files";

// Visual previews of original files, loaded only when such a file is opened. Office markup
// comes from the file itself, so Word and PowerPoint render inside a sandboxed frame that
// runs no scripts, loads nothing from the network and cannot restyle the app.

const FRAME_POLICY = "default-src 'none'; img-src data: blob:; media-src data: blob:; font-src data: blob:; style-src 'unsafe-inline'";

function frameDocument(style: string, body = "") {
  return `<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="${FRAME_POLICY}"><style>${style}</style></head><body>${body}</body></html>`;
}

const frameStyle = `html,body{margin:0;background:#f2f5f2;}body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;}`;

/** External links open in a new tab from the app; nothing navigates the frame itself. */
function guardLinks(document: Document) {
  for (const link of document.querySelectorAll<HTMLAnchorElement>("a[href]")) {
    const href = link.getAttribute("href") ?? "";
    let safe = false;
    try { safe = ["http:", "https:", "mailto:"].includes(new URL(href).protocol); } catch {}
    if (!safe && !href.includes("#")) link.removeAttribute("href");
    link.target = "_blank";
    link.rel = "noopener noreferrer";
  }
  document.addEventListener("click", (event) => {
    const link = (event.target as Element | null)?.closest?.("a");
    if (!link) return;
    event.preventDefault();
    const href = link.getAttribute("href") ?? "";
    const anchor = href.includes("#") ? href.slice(href.indexOf("#") + 1) : "";
    try {
      const url = new URL(href);
      if (["http:", "https:", "mailto:"].includes(url.protocol)) {
        window.open(url.href, "_blank", "noopener,noreferrer");
        return;
      }
    } catch {}
    if (anchor) document.getElementById(decodeURIComponent(anchor))?.scrollIntoView({ block: "start" });
  });
}

// Parameters: a sandboxed frame whose document is rendered at its natural size.
// What it does: sizes the frame to its content and scales it down to the available width,
// following the container as it resizes.
// Output: a cleanup function.
function fitFrame(box: HTMLElement, frame: HTMLIFrameElement, naturalWidth: () => number) {
  let width = 0;
  const fit = () => {
    const document = frame.contentDocument;
    if (!document?.documentElement) return;
    width = Math.max(1, Math.ceil(naturalWidth()));
    frame.style.width = `${width}px`;
    const height = Math.ceil(document.documentElement.scrollHeight);
    frame.style.height = `${height}px`;
    const scale = Math.min(1, box.clientWidth / width);
    frame.style.transform = scale < 1 ? `scale(${scale})` : "";
    box.style.height = `${Math.ceil(height * scale)}px`;
  };
  fit();
  const observer = new ResizeObserver(() => fit());
  observer.observe(box);
  const later = window.setTimeout(fit, 400);
  return () => { observer.disconnect(); window.clearTimeout(later); };
}

function PreviewProblem({ children }: { children: string }) {
  return <p className="sp-file-problem" role="status">{children}</p>;
}

export function PdfPreview({ blob, onPages }: { blob: Blob; onPages?: (pages: number) => void }) {
  const box = useRef<HTMLDivElement>(null);
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null);
  const [ratio, setRatio] = useState(1.294);
  const [width, setWidth] = useState(0);
  const [error, setError] = useState("");
  useEffect(() => {
    let cancelled = false;
    let destroy: (() => Promise<void>) | undefined;
    setPdf(null);
    setError("");
    (async () => {
      const pdfjs = await import("pdfjs-dist");
      pdfjs.GlobalWorkerOptions.workerSrc = `/vendor/pdfjs/pdf.worker.min.mjs?v=${pdfjs.version}`;
      const loading = pdfjs.getDocument({ data: new Uint8Array(await blob.arrayBuffer()), useSystemFonts: true, enableXfa: false });
      destroy = () => loading.destroy();
      const document = await loading.promise;
      const first = (await document.getPage(1)).getViewport({ scale: 1 });
      if (cancelled) return;
      setRatio(first.height / first.width);
      setPdf(document);
      onPages?.(document.numPages);
    })().catch((error) => {
      if (!cancelled)
        setError(error?.name === "PasswordException"
          ? "This PDF is password protected. Download it to open it."
          : "This PDF could not be shown. Download it to open it.");
    });
    return () => { cancelled = true; void destroy?.(); };
    // onPages only reports the count; a new callback must not reload the file.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [blob]);
  useEffect(() => {
    const element = box.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.floor(entry.contentRect.width)));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  if (error) return <PreviewProblem>{error}</PreviewProblem>;
  return (
    <div ref={box} className="sp-file-pdf" aria-busy={!pdf}>
      {pdf && Array.from({ length: pdf.numPages }, (_, index) => (
        <PdfPage key={index} pdf={pdf} number={index + 1} width={width} ratio={ratio} />
      ))}
    </div>
  );
}

// Each page renders when it comes near the viewport and releases its canvas when it moves
// far away, so long documents keep a bounded number of page bitmaps.
function PdfPage({ pdf, number, width, ratio }: { pdf: PDFDocumentProxy; number: number; width: number; ratio: number }) {
  const frame = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const [near, setNear] = useState(false);
  const [pageRatio, setPageRatio] = useState(ratio);
  useEffect(() => {
    const element = frame.current;
    if (!element) return;
    const observer = new IntersectionObserver(([entry]) => setNear(entry.isIntersecting), { rootMargin: "150% 0px" });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    const element = canvas.current;
    if (!element) return;
    if (!near || !width) {
      element.width = 0;
      element.height = 0;
      return;
    }
    let task: RenderTask | undefined, cancelled = false;
    (async () => {
      const page = await pdf.getPage(number);
      if (cancelled) return;
      const base = page.getViewport({ scale: 1 });
      setPageRatio(base.height / base.width);
      const scale = width / base.width;
      let pixels = Math.min(window.devicePixelRatio || 1, 2);
      // Keep one page bitmap under about 16 million pixels.
      const area = base.width * scale * pixels * base.height * scale * pixels;
      if (area > 16_000_000) pixels *= Math.sqrt(16_000_000 / area);
      const viewport = page.getViewport({ scale: scale * pixels });
      element.width = Math.floor(viewport.width);
      element.height = Math.floor(viewport.height);
      task = page.render({ canvas: element, viewport });
      await task.promise;
    })().catch(() => {});
    return () => { cancelled = true; task?.cancel(); };
  }, [near, width, pdf, number]);
  return (
    <div ref={frame} className="sp-file-page" style={{ aspectRatio: `1 / ${pageRatio}` }}>
      <canvas ref={canvas} role="img" aria-label={`Page ${number}`} />
    </div>
  );
}

const docxStyle = `${frameStyle}
.docx-wrapper{background:#f2f5f2!important;padding:20px 16px 4px!important;}
.docx-wrapper>section.docx{box-shadow:0 1px 3px rgba(32,37,34,.14)!important;margin-bottom:20px!important;}`;

export function WordPreview({ blob }: { blob: Blob }) {
  const box = useRef<HTMLDivElement>(null);
  const frame = useRef<HTMLIFrameElement>(null);
  const [error, setError] = useState("");
  const [ready, setReady] = useState(false);
  useEffect(() => {
    const element = frame.current, container = box.current;
    if (!element || !container) return;
    let cancelled = false, stop = () => {};
    setReady(false);
    setError("");
    const render = async () => {
      const document = element.contentDocument;
      const body = document?.getElementById("body"), styles = document?.getElementById("styles");
      if (!document || !body || !styles) throw new Error("Preview frame unavailable");
      await checkArchiveText(await blob.arrayBuffer(), officeXml);
      const { renderAsync } = await import("docx-preview");
      await renderAsync(blob, body, styles, {
        className: "docx",
        inWrapper: true,
        breakPages: true,
        ignoreLastRenderedPageBreak: true,
        experimental: false,
        renderHeaders: true,
        renderFooters: true,
        renderFootnotes: true,
        renderEndnotes: true,
        renderComments: false,
        renderChanges: false,
        // An altChunk embeds HTML from the file; never render it.
        renderAltChunks: false,
        useBase64URL: true,
        debug: false,
      });
      if (cancelled) return;
      guardLinks(document);
      stop = fitFrame(container, element, () => {
        const pages = [...document.querySelectorAll<HTMLElement>(".docx-wrapper > section")];
        return Math.max(...pages.map((page) => page.offsetWidth), 320) + 32;
      });
      setReady(true);
    };
    const start = () => { render().catch(() => { if (!cancelled) setError("This Word document could not be shown. Download it to open it."); }); };
    element.addEventListener("load", start, { once: true });
    element.srcdoc = frameDocument(docxStyle, '<div id="styles"></div><div id="body"></div>');
    return () => { cancelled = true; stop(); element.removeEventListener("load", start); };
  }, [blob]);
  return (
    <>
      {error && <PreviewProblem>{error}</PreviewProblem>}
      <div ref={box} className="sp-file-frame" aria-busy={!ready} hidden={!!error}>
        <iframe ref={frame} title="Word document preview" sandbox="allow-same-origin" referrerPolicy="no-referrer" />
      </div>
    </>
  );
}

const slidesStyle = `${frameStyle}
body{display:flex;flex-direction:column;align-items:center;gap:16px;padding:16px;width:max-content;min-width:100%;box-sizing:border-box;}
body>.slide,body>.slide-container{flex:none;box-shadow:0 1px 3px rgba(32,37,34,.14);}`;

export function SlidesPreview({ blob, onPages }: { blob: Blob; onPages?: (pages: number) => void }) {
  const box = useRef<HTMLDivElement>(null);
  const frame = useRef<HTMLIFrameElement>(null);
  const [error, setError] = useState("");
  const [ready, setReady] = useState(false);
  useEffect(() => {
    const element = frame.current, container = box.current;
    if (!element || !container) return;
    let cancelled = false, stop = () => {};
    setReady(false);
    setError("");
    (async () => {
      const buffer = await blob.arrayBuffer();
      await checkArchiveText(buffer, officeXml);
      const { pptxToHtml } = await import("@jvmr/pptx-to-html");
      const slides = await pptxToHtml(buffer);
      if (cancelled) return;
      onPages?.(slides.length);
      element.addEventListener("load", () => {
        const document = element.contentDocument;
        if (cancelled || !document) return;
        guardLinks(document);
        stop = fitFrame(container, element, () => document.body.scrollWidth);
        setReady(true);
      }, { once: true });
      // The markup is the file's own; the sandbox keeps it from running or loading anything.
      element.srcdoc = frameDocument(slidesStyle, slides.join(""));
    })().catch(() => { if (!cancelled) setError("This presentation could not be shown. Download it to open it."); });
    return () => { cancelled = true; stop(); };
    // onPages only reports the count; a new callback must not reload the file.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [blob]);
  return (
    <>
      {error && <PreviewProblem>{error}</PreviewProblem>}
      <div ref={box} className="sp-file-frame" aria-busy={!ready} hidden={!!error}>
        <iframe ref={frame} title="Presentation preview" sandbox="allow-same-origin" referrerPolicy="no-referrer" />
      </div>
    </>
  );
}

const SHEET_ROWS = 200, SHEET_COLUMNS = 30;

export function SheetPreview({ blob }: { blob: Blob }) {
  const [sheets, setSheets] = useState<{ sheet: string; data: unknown[][] }[] | null>(null);
  const [selected, setSelected] = useState(0);
  const [error, setError] = useState("");
  useEffect(() => {
    let cancelled = false;
    setSheets(null);
    setError("");
    (async () => {
      await checkArchiveText(await blob.arrayBuffer(), officeXml);
      const { default: readXlsx } = await import("read-excel-file/browser");
      const result = await readXlsx(blob, { trim: false, parseNumber: (value) => value });
      if (!cancelled) { setSheets(result); setSelected(0); }
    })().catch(() => { if (!cancelled) setError("This spreadsheet could not be shown. Download it to open it."); });
    return () => { cancelled = true; };
  }, [blob]);
  const sheet = sheets?.[selected];
  const rows = useMemo(() => sheet?.data.slice(0, SHEET_ROWS) ?? [], [sheet]);
  if (error) return <PreviewProblem>{error}</PreviewProblem>;
  if (!sheets) return <div className="sp-file-sheet" aria-busy="true" />;
  const width = Math.min(SHEET_COLUMNS, Math.max(0, ...rows.map((row) => row.length)));
  const cut = (sheet?.data.length ?? 0) > SHEET_ROWS || (sheet?.data.some((row) => row.length > SHEET_COLUMNS) ?? false);
  const cell = (value: unknown) => value instanceof Date ? value.toISOString().slice(0, 10) : value === null || value === undefined ? "" : String(value);
  return (
    <div className="sp-file-sheet">
      {sheets.length > 1 && (
        <label className="sp-file-sheet-picker">
          Sheet
          <select value={selected} onChange={(event) => setSelected(Number(event.target.value))}>
            {sheets.map((item, index) => <option key={index} value={index}>{item.sheet}</option>)}
          </select>
        </label>
      )}
      {rows.length ? (
        <div className="sp-import-table-scroll sp-file-sheet-scroll">
          <table className="sp-import-table">
            <tbody>
              {rows.map((row, rowIndex) => (
                <tr key={rowIndex}>
                  {Array.from({ length: width }, (_, column) => <td key={column}>{cell(row[column])}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : <PreviewProblem>This sheet is empty.</PreviewProblem>}
      {cut && <p className="sp-file-note">Showing the first {SHEET_ROWS} rows and {SHEET_COLUMNS} columns. Download the file to see everything.</p>}
    </div>
  );
}

const TEXT_PREVIEW_BYTES = 200 * 1024;

export function TextPreview({ blob }: { blob: Blob }) {
  const [text, setText] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    void blob.slice(0, TEXT_PREVIEW_BYTES).arrayBuffer().then((buffer) => {
      const bytes = new Uint8Array(buffer);
      const encoding = bytes[0] === 0xff && bytes[1] === 0xfe ? "utf-16le" : bytes[0] === 0xfe && bytes[1] === 0xff ? "utf-16be" : "utf-8";
      if (!cancelled) setText(new TextDecoder(encoding).decode(bytes));
    });
    return () => { cancelled = true; };
  }, [blob]);
  if (text === null) return <div className="sp-file-text" aria-busy="true" />;
  return (
    <>
      <pre className="sp-file-text" tabIndex={0}>{text}</pre>
      {blob.size > TEXT_PREVIEW_BYTES && <p className="sp-file-note">Showing the first 200 KB. Download the file to see everything.</p>}
    </>
  );
}
