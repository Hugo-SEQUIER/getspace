// Original files kept by documents: their limits and names, and the transfer that moves
// them through the JSON-only /api proxy as base64 parts of at most 1 MiB.

/** Every part except the last carries exactly this many bytes. */
export const FILE_PART_BYTES = 1_048_576;
export const MAX_FILE_BYTES = 262_144_000;
export const MAX_FILE_PARTS = MAX_FILE_BYTES / FILE_PART_BYTES;
/** Searchable text saved beside a file, in characters. */
export const MAX_FILE_TEXT = 500_000;
/** The text's JSON-encoded size: the backend refuses a document over 1 MiB, the proxy a request over 1.5 MB. */
export const MAX_FILE_TEXT_BYTES = 900_000;
/** A preview downloads the whole file, so larger files wait for the person to ask for it. */
export const AUTO_PREVIEW_BYTES = 26_214_400;
/** Word, PowerPoint, Excel and picture previews hold the whole file in memory several times. */
export const HEAVY_PREVIEW_BYTES = 52_428_800;

export type DocumentFile = {
  id: string;
  name: string;
  type: string;
  bytes: number;
  sha256: string;
  parts: number;
};

export type FileKind = "pdf" | "word" | "slides" | "sheet" | "image" | "text" | "audio" | "video" | "other";

const imageExtensions = ["png", "jpg", "jpeg", "gif", "webp", "avif", "bmp"];
const audioExtensions = ["mp3", "wav", "ogg", "oga", "m4a", "aac", "flac"];
const videoExtensions = ["mp4", "webm", "mov", "m4v", "ogv"];
export const textFileExtensions = [
  "txt", "md", "markdown", "csv", "tsv", "json", "jsonl", "ndjson", "xml", "yaml", "yml",
  "log", "html", "htm", "css", "js", "ts", "py", "sql", "ini", "toml", "rtf", "tex",
];
const knownTypes: Record<string, string> = {
  pdf: "application/pdf",
  docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  pptx: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  doc: "application/msword",
  ppt: "application/vnd.ms-powerpoint",
  xls: "application/vnd.ms-excel",
  csv: "text/csv",
  md: "text/markdown",
  txt: "text/plain",
  json: "application/json",
  png: "image/png",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  gif: "image/gif",
  webp: "image/webp",
  mp3: "audio/mpeg",
  mp4: "video/mp4",
  zip: "application/zip",
};

export function fileExtension(name: string) {
  return /\.([a-z0-9]{1,12})$/i.exec(name)?.[1].toLowerCase() ?? "";
}

// Parameters: the name the browser reports for a chosen file.
// What it does: replaces path separators, control and direction-changing characters, which
// the backend refuses and which could disguise an extension, and shortens it to 255
// characters while keeping the extension.
// Output: a name the backend accepts.
export function cleanFileName(name: string) {
  const cleaned = name
    .replace(/[\u0000-\u001f\u007f-\u009f\u200b-\u200f\u202a-\u202e\u2066-\u2069/\\]/g, "-")
    .trim()
    .replace(/[. ]+$/, "");
  const characters = [...cleaned];
  if (!characters.length) return "file";
  if (characters.length <= 255) return cleaned;
  const extension = fileExtension(cleaned);
  const suffix = extension ? `.${extension}` : "";
  return characters.slice(0, 255 - suffix.length).join("") + suffix;
}

// Parameters: the MIME type the browser reports and the file name.
// What it does: drops parameters such as charset, lowercases the type, and fills in a known
// type when the browser reports none.
// Output: a type the backend accepts; application/octet-stream when nothing better is known.
export function cleanFileType(type: string, name = "") {
  const value = type.split(";")[0].trim().toLowerCase();
  if (value && value !== "application/octet-stream" && value.length <= 255
    && /^[a-z0-9!#$&^_.+-]+\/[a-z0-9!#$&^_.+-]+$/.test(value)) return value;
  return knownTypes[fileExtension(name)] ?? "application/octet-stream";
}

export function fileParts(bytes: number) {
  return Math.ceil(bytes / FILE_PART_BYTES);
}

/** The [start, end) byte range of one part. */
export function partRange(part: number, bytes: number): [number, number] {
  const start = part * FILE_PART_BYTES;
  return [start, Math.min(bytes, start + FILE_PART_BYTES)];
}

export function bytesToBase64(bytes: Uint8Array) {
  let binary = "";
  // Spread in slices: one call with a million arguments overflows the stack.
  for (let index = 0; index < bytes.length; index += 0x8000)
    binary += String.fromCharCode(...bytes.subarray(index, index + 0x8000));
  return btoa(binary);
}

export function base64ToBytes(text: string) {
  const binary = atob(text);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index++) bytes[index] = binary.charCodeAt(index);
  return bytes;
}

export async function sha256Hex(data: ArrayBuffer | Uint8Array) {
  const digest = await crypto.subtle.digest("SHA-256", data as BufferSource);
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export function formatFileSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB"];
  let value = bytes / 1024, unit = 0;
  while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit++; }
  return `${value >= 10 ? Math.round(value) : Math.round(value * 10) / 10} ${units[unit]}`;
}

export function fileKind(file: { name: string; type: string }): FileKind {
  const extension = fileExtension(file.name), type = file.type;
  if (extension === "pdf" || type === "application/pdf") return "pdf";
  if (extension === "docx") return "word";
  if (extension === "pptx") return "slides";
  if (extension === "xlsx") return "sheet";
  // SVG can carry scripts, so it is only ever downloaded.
  if (extension === "svg" || type === "image/svg+xml") return "other";
  if (imageExtensions.includes(extension) || (!extension && /^image\/(png|jpeg|gif|webp|avif|bmp)$/.test(type))) return "image";
  if (audioExtensions.includes(extension)) return "audio";
  if (videoExtensions.includes(extension)) return "video";
  if (textFileExtensions.includes(extension) || (!extension && type.startsWith("text/"))) return "text";
  return "other";
}

export type PreviewMode = "auto" | "on-request" | "none";

const heavyKinds: FileKind[] = ["word", "slides", "sheet", "image"];

// Parameters: a document's file.
// What it does: decides how the reader previews it. Files over AUTO_PREVIEW_BYTES load only
// when asked; Word, PowerPoint, Excel and pictures over HEAVY_PREVIEW_BYTES, and types Space
// cannot display, are download only.
// Output: "auto", "on-request" or "none".
export function previewMode(file: { name: string; type: string; bytes: number }): PreviewMode {
  const kind = fileKind(file);
  if (kind === "other" || (heavyKinds.includes(kind) && file.bytes > HEAVY_PREVIEW_BYTES)) return "none";
  return file.bytes > AUTO_PREVIEW_BYTES ? "on-request" : "auto";
}

/** A short label such as PDF or PPTX for a file badge. */
export function fileLabel(file: { name: string; type: string }) {
  const extension = fileExtension(file.name);
  if (extension) return extension.toUpperCase();
  return fileKind(file) === "pdf" ? "PDF" : "File";
}

export function fileSummary(file: { name: string; type: string; bytes: number }) {
  return `${fileLabel(file)} · ${formatFileSize(file.bytes)}`;
}

const encoder = new TextEncoder();
const encodedSize = (text: string) => encoder.encode(JSON.stringify(text)).length;

/** Cuts text to `length` UTF-16 units without splitting a surrogate pair. */
function cut(text: string, length: number) {
  const code = text.charCodeAt(length - 1);
  return text.slice(0, code >= 0xd800 && code <= 0xdbff ? length - 1 : length);
}

// Parameters: text read from a file.
// What it does: keeps what fits the searchable text bounds, both in characters and in its
// JSON-encoded size, cut at a character boundary and at a line break where one is near.
// Output: the text to save and whether it was shortened.
export function boundFileText(text: string) {
  let value = text
    .replace(/^\uFEFF/, "")
    .replace(/\r\n?/g, "\n")
    .replace(/\0/g, "")
    // A lone surrogate cannot be stored as UTF-8; keep its place visible instead.
    .replace(/[\ud800-\udbff](?![\udc00-\udfff])|(?<![\ud800-\udbff])[\udc00-\udfff]/g, "\ufffd")
    .trim();
  let shortened = false;
  if (value.length > MAX_FILE_TEXT) { value = cut(value, MAX_FILE_TEXT); shortened = true; }
  if (encodedSize(value) > MAX_FILE_TEXT_BYTES) {
    // The longest prefix whose encoded size fits, found by halving.
    let low = 0, high = value.length;
    while (low < high) {
      const middle = Math.ceil((low + high) / 2);
      if (encodedSize(value.slice(0, middle)) <= MAX_FILE_TEXT_BYTES) low = middle;
      else high = middle - 1;
    }
    value = cut(value, low);
    shortened = true;
  }
  if (shortened) {
    const lineEnd = value.lastIndexOf("\n");
    if (lineEnd > value.length * 0.8) value = value.slice(0, lineEnd);
    value = value.trimEnd();
  }
  return { text: value, shortened };
}

const entities: Record<string, string> = { amp: "&", lt: "<", gt: ">", quot: "\"", apos: "'" };

export function decodeXmlText(value: string) {
  return value.replace(/&(#x[0-9a-f]+|#[0-9]+|amp|lt|gt|quot|apos);/gi, (match, entity: string) => {
    if (entity[0] !== "#") return entities[entity.toLowerCase()] ?? match;
    const code = entity[1].toLowerCase() === "x" ? parseInt(entity.slice(2), 16) : parseInt(entity.slice(1), 10);
    return Number.isInteger(code) && code > 0 && code <= 0x10ffff ? String.fromCodePoint(code) : match;
  });
}

function attribute(attributes: string, name: string) {
  return new RegExp(`(?:^|\\s)${name}="([^"]*)"`).exec(attributes)?.[1];
}

// Parameters: the paths of the slide parts in a PPTX package, ppt/presentation.xml and its
// relationships part.
// What it does: follows the presentation's slide list to its slide parts, which is the order
// people see; slides it cannot place follow in the order of their file numbers.
// Output: slide part paths in presentation order.
export function slideOrder(slidePaths: string[], presentationXml = "", relationshipsXml = "") {
  const targets = new Map<string, string>();
  for (const [, attributes] of relationshipsXml.matchAll(/<(?:[\w.-]+:)?Relationship\b([^>]*)>/g)) {
    const id = attribute(attributes, "Id"), target = attribute(attributes, "Target");
    if (!id || !target) continue;
    const segments: string[] = [];
    for (const segment of (target.startsWith("/") ? target.slice(1) : `ppt/${target}`).split("/")) {
      if (segment === "..") segments.pop();
      else if (segment && segment !== ".") segments.push(segment);
    }
    targets.set(id, segments.join("/"));
  }
  const available = new Set(slidePaths);
  const ordered: string[] = [];
  for (const [, attributes] of presentationXml.matchAll(/<(?:[\w.-]+:)?sldId\b([^>]*)>/g)) {
    const relationship = /(?:^|\s)[\w.-]+:id="([^"]+)"/.exec(attributes)?.[1];
    const path = relationship && targets.get(relationship);
    if (path && available.has(path) && !ordered.includes(path)) ordered.push(path);
  }
  const number = (path: string) => Number(/(\d+)\.xml$/.exec(path)?.[1] ?? 0);
  const rest = slidePaths.filter((path) => !ordered.includes(path)).sort((a, b) => number(a) - number(b));
  return [...ordered, ...rest];
}

/** The text of one slide part: one line per paragraph, runs joined, line breaks kept. */
export function slideText(xml: string) {
  const paragraphs: string[] = [];
  for (const [paragraph] of xml.matchAll(/<a:p\b[\s\S]*?<\/a:p>/g)) {
    let line = "";
    for (const [, br, text] of paragraph.matchAll(/(<a:br\b[^>]*\/>)|<a:t(?:\s[^>]*)?>([^<]*)<\/a:t>/g))
      line += br ? "\n" : decodeXmlText(text ?? "");
    if (line.trim()) paragraphs.push(line.trim());
  }
  return paragraphs.join("\n");
}

// Parameters: an Office package and which of its entries a reader will decompress.
// What it does: bounds the expanded size of those entries before anything inflates them;
// pictures and other media a text reader skips do not count.
// Output: nothing; throws when the package is unreadable or too large to read safely.
export async function checkArchiveText(buffer: ArrayBuffer, read: (name: string) => boolean) {
  const { unzipSync } = await import("fflate");
  let size = 0, entries = 0;
  unzipSync(new Uint8Array(buffer), {
    filter(entry) {
      if (++entries > 20000) throw new Error("Too many entries");
      if (read(entry.name)) size += entry.originalSize;
      if (size > 60 * 1024 * 1024) throw new Error("Too much text");
      return false;
    },
  });
}

export const officeXml = (name: string) => name.endsWith(".xml") || name.endsWith(".rels");

// ---- Transfer ----

export type FileRequest = <T>(path: string, method: string, body?: unknown, signal?: AbortSignal) => Promise<T>;

export const filePartPath = (spaceId: string, fileId: string, part: number) =>
  `spaces/${spaceId}/files/${fileId}/parts/${part}`;

export const fileDiscardPath = (spaceId: string, fileId: string) => `spaces/${spaceId}/files/${fileId}`;

export const fileReadPath = (spaceId: string, resourceId: string, part: number, revision?: number) =>
  `spaces/${spaceId}/resources/${resourceId}/file/${part}${revision ? `?revision=${revision}` : ""}`;

/** A lost connection, an overloaded or restarting backend, or a gateway timeout is worth another try. */
function retryable(error: unknown) {
  if (!(error instanceof Error) || error.name === "AbortError") return false;
  const status = (error as Error & { status?: unknown }).status;
  if (typeof status === "number") return status === 502 || status === 503 || status === 504;
  return error.name === "BrowserRequestError" || error.name === "TypeError" || error.name === "TimeoutError";
}

function pause(milliseconds: number, signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    if (signal.aborted) return reject(signal.reason);
    const timer = setTimeout(() => { signal.removeEventListener("abort", stop); resolve(); }, milliseconds);
    const stop = () => { clearTimeout(timer); reject(signal.reason); };
    signal.addEventListener("abort", stop, { once: true });
  });
}

export async function withRetry<T>(work: () => Promise<T>, signal: AbortSignal, attempts = 3, delay = 1500): Promise<T> {
  for (let attempt = 1; ; attempt++) {
    try {
      return await work();
    } catch (error) {
      if (attempt >= attempts || signal.aborted || !retryable(error)) throw error;
      await pause(delay * attempt, signal);
    }
  }
}

// Parameters: the number of parts, how many may run at once, a cancellation signal and the
// work for one part.
// What it does: runs parts in order with bounded concurrency; the first failure cancels the
// parts still running and stops new ones.
// Output: resolves when every part succeeded.
export async function eachPart(
  parts: number,
  concurrency: number,
  signal: AbortSignal | undefined,
  work: (part: number, signal: AbortSignal) => Promise<void>,
) {
  const controller = new AbortController();
  const stop = () => controller.abort(signal?.reason);
  if (signal?.aborted) stop();
  signal?.addEventListener("abort", stop, { once: true });
  let next = 0;
  try {
    await Promise.all(Array.from({ length: Math.min(concurrency, parts) }, async () => {
      while (next < parts) {
        controller.signal.throwIfAborted();
        const part = next++;
        try {
          await work(part, controller.signal);
        } catch (error) {
          controller.abort(error);
          throw error;
        }
      }
    }));
  } finally {
    signal?.removeEventListener("abort", stop);
  }
}

export async function uploadFileParts(
  request: FileRequest,
  { spaceId, fileId, data, signal, onProgress }: {
    spaceId: string;
    fileId: string;
    data: Uint8Array;
    signal?: AbortSignal;
    onProgress?: (fraction: number) => void;
  },
) {
  const parts = fileParts(data.byteLength);
  if (!parts) throw new Error("This file is empty.");
  if (parts > MAX_FILE_PARTS) throw new Error("Choose a file up to 250 MB.");
  let done = 0;
  onProgress?.(0);
  await eachPart(parts, 3, signal, async (part, partSignal) => {
    const [start, end] = partRange(part, data.byteLength);
    const body = { data: bytesToBase64(data.subarray(start, end)) };
    await withRetry(() => request(filePartPath(spaceId, fileId, part), "POST", body, partSignal), partSignal);
    onProgress?.(++done / parts);
  });
}

export async function downloadFileParts(
  request: FileRequest,
  { spaceId, resourceId, file, revision, signal, onProgress }: {
    spaceId: string;
    resourceId: string;
    file: DocumentFile;
    revision?: number;
    signal?: AbortSignal;
    onProgress?: (fraction: number) => void;
  },
) {
  const chunks: Uint8Array[] = new Array(file.parts);
  let done = 0;
  onProgress?.(0);
  await eachPart(file.parts, 4, signal, async (part, partSignal) => {
    const value = await withRetry(
      () => request<{ data: string }>(fileReadPath(spaceId, resourceId, part, revision), "GET", undefined, partSignal),
      partSignal,
    );
    chunks[part] = base64ToBytes(value.data);
    onProgress?.(++done / file.parts);
  });
  const blob = new Blob(chunks as BlobPart[], { type: file.type });
  if (blob.size !== file.bytes || (await sha256Hex(await blob.arrayBuffer())) !== file.sha256)
    throw new Error("This file did not arrive intact. Try again.");
  return blob;
}

// Recently opened files, so returning to a document or switching revisions does not download
// the same bytes again. Bounded to a few files; never persisted.
const recentFiles = new Map<string, Promise<Blob>>();
const RECENT_FILES = 3;

export function rememberedFile(key: string, load: () => Promise<Blob>) {
  let value = recentFiles.get(key);
  if (value) recentFiles.delete(key);
  else {
    value = load();
    value.catch(() => { if (recentFiles.get(key) === value) recentFiles.delete(key); });
  }
  recentFiles.set(key, value);
  while (recentFiles.size > RECENT_FILES) recentFiles.delete(recentFiles.keys().next().value!);
  return value;
}

export function forgetFiles() {
  recentFiles.clear();
}
