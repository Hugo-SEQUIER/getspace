// Thumbnails and media details made in the browser when a file is uploaded. A picture is
// decoded and scaled down, a video gives a frame near its start, and a PDF its first page;
// each becomes a WebP (or JPEG where the browser cannot write WebP) of at most
// THUMBNAIL_BYTES. Nothing here throws: a file the browser cannot decode gets no thumbnail,
// and whatever was read about it (its size in pixels, its length) is still reported.

import {
  THUMBNAIL_BYTES,
  THUMBNAIL_SIDE,
  type FileKind,
  type FileThumbnail,
  fitThumbnail,
  sha256Hex,
} from "./document-files";

/** A thumbnail ready to upload as a one-part file of its own. */
export type ThumbnailDraft = Omit<FileThumbnail, "parts"> & { bytes: number; data: Uint8Array };

export type MediaDetails = { width?: number; height?: number; duration?: number };

export type FileDescription = { media: MediaDetails; thumbnail: ThumbnailDraft | null };

/** Pictures past this many pixels are not decoded for a thumbnail; the tab's memory matters more. */
const MAX_PICTURE_PIXELS = 80_000_000;
/** How long a video or picture may take to decode before the file is saved without a thumbnail. */
const DECODE_TIMEOUT_MS = 8000;

function timeout<T>(work: Promise<T>, milliseconds: number): Promise<T> {
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(() => reject(new Error("Timed out")), milliseconds);
    work.then((value) => { window.clearTimeout(timer); resolve(value); }, (error) => { window.clearTimeout(timer); reject(error); });
  });
}

function once<K extends keyof HTMLMediaElementEventMap>(element: HTMLMediaElement, event: K) {
  return new Promise<void>((resolve, reject) => {
    const done = () => { cleanup(); resolve(); };
    const failed = () => { cleanup(); reject(new Error("Media could not be decoded")); };
    const cleanup = () => { element.removeEventListener(event, done); element.removeEventListener("error", failed); };
    element.addEventListener(event, done, { once: true });
    element.addEventListener("error", failed, { once: true });
  });
}

function toBlob(canvas: HTMLCanvasElement, type: string, quality: number) {
  return new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, type, quality));
}

// Parameters: a canvas holding the scaled picture.
// What it does: encodes it as WebP, or JPEG where the browser answers WebP with another type
// (Safari), lowering the quality and then the size until it fits THUMBNAIL_BYTES.
// Output: the encoded bytes with their type and size, or null when nothing fits.
async function encode(canvas: HTMLCanvasElement): Promise<Omit<ThumbnailDraft, "id" | "sha256"> | null> {
  let type: "image/webp" | "image/jpeg" = "image/webp";
  for (const quality of [0.82, 0.7, 0.55, 0.42]) {
    for (let shrink = 0; shrink < 3; shrink++) {
      const blob = await toBlob(canvas, type, quality);
      if (!blob) return null;
      if (blob.type !== type) {
        if (type === "image/jpeg") return null;
        type = "image/jpeg";
        shrink--;
        continue;
      }
      if (blob.size <= THUMBNAIL_BYTES)
        return { type, bytes: blob.size, width: canvas.width, height: canvas.height, data: new Uint8Array(await blob.arrayBuffer()) };
      if (shrink === 2) break;
      const smaller = document.createElement("canvas");
      smaller.width = Math.max(1, Math.round(canvas.width * 0.75));
      smaller.height = Math.max(1, Math.round(canvas.height * 0.75));
      smaller.getContext("2d")?.drawImage(canvas, 0, 0, smaller.width, smaller.height);
      canvas = smaller;
    }
  }
  return null;
}

function draw(source: CanvasImageSource, width: number, height: number) {
  const canvas = document.createElement("canvas");
  const size = fitThumbnail(width, height);
  canvas.width = size.width;
  canvas.height = size.height;
  const context = canvas.getContext("2d");
  if (!context) return null;
  context.drawImage(source, 0, 0, size.width, size.height);
  return canvas;
}

async function finish(canvas: HTMLCanvasElement | null): Promise<ThumbnailDraft | null> {
  if (!canvas) return null;
  const encoded = await encode(canvas);
  if (!encoded) return null;
  return { ...encoded, id: crypto.randomUUID(), sha256: await sha256Hex(encoded.data) };
}

async function describePicture(blob: Blob): Promise<FileDescription> {
  const bitmap = await timeout(createImageBitmap(blob), DECODE_TIMEOUT_MS);
  try {
    const media = { width: bitmap.width, height: bitmap.height };
    if (bitmap.width * bitmap.height > MAX_PICTURE_PIXELS) return { media, thumbnail: null };
    return { media, thumbnail: await finish(draw(bitmap, bitmap.width, bitmap.height)) };
  } finally {
    bitmap.close();
  }
}

async function describeVideo(blob: Blob): Promise<FileDescription> {
  const url = URL.createObjectURL(blob);
  const video = document.createElement("video");
  video.muted = true;
  video.playsInline = true;
  video.preload = "auto";
  try {
    video.src = url;
    await timeout(once(video, "loadedmetadata"), DECODE_TIMEOUT_MS);
    const media: MediaDetails = {};
    if (Number.isFinite(video.duration) && video.duration > 0) media.duration = Math.round(video.duration * 1000) / 1000;
    if (video.videoWidth && video.videoHeight) { media.width = video.videoWidth; media.height = video.videoHeight; }
    if (!media.width) return { media, thumbnail: null };
    // A frame a moment in: the first frame of many videos is black.
    const at = Number.isFinite(video.duration) ? Math.min(1, video.duration * 0.1) : 0;
    try {
      const seeked = once(video, "seeked");
      video.currentTime = at;
      await timeout(seeked, DECODE_TIMEOUT_MS);
    } catch {
      return { media, thumbnail: null };
    }
    return { media, thumbnail: await finish(draw(video, video.videoWidth, video.videoHeight)) };
  } finally {
    video.removeAttribute("src");
    video.load();
    URL.revokeObjectURL(url);
  }
}

async function describeAudio(blob: Blob): Promise<FileDescription> {
  const url = URL.createObjectURL(blob);
  const audio = document.createElement("audio");
  audio.preload = "metadata";
  try {
    audio.src = url;
    await timeout(once(audio, "loadedmetadata"), DECODE_TIMEOUT_MS);
    const media: MediaDetails = {};
    if (Number.isFinite(audio.duration) && audio.duration > 0) media.duration = Math.round(audio.duration * 1000) / 1000;
    return { media, thumbnail: null };
  } finally {
    audio.removeAttribute("src");
    audio.load();
    URL.revokeObjectURL(url);
  }
}

async function describePdf(buffer: ArrayBuffer): Promise<FileDescription> {
  const pdfjs = await import("pdfjs-dist");
  pdfjs.GlobalWorkerOptions.workerSrc = `/vendor/pdfjs/pdf.worker.min.mjs?v=${pdfjs.version}`;
  // pdf.js may transfer the buffer to its worker; the caller still needs the original bytes.
  const loading = pdfjs.getDocument({ data: new Uint8Array(buffer.slice(0)), useSystemFonts: true, enableXfa: false });
  try {
    const pdf = await loading.promise;
    const page = await pdf.getPage(1);
    const base = page.getViewport({ scale: 1 });
    const size = fitThumbnail(base.width, base.height);
    const canvas = document.createElement("canvas");
    canvas.width = size.width;
    canvas.height = size.height;
    const viewport = page.getViewport({ scale: size.width / base.width });
    await page.render({ canvas, viewport }).promise;
    page.cleanup();
    return { media: {}, thumbnail: await finish(canvas) };
  } finally {
    await loading.destroy();
  }
}

// Parameters: a chosen file, its bytes and the kind Space gave it.
// What it does: reads what the browser can about a picture, a video or a recording and
// makes a thumbnail of a picture, a video frame or the first PDF page, on this device.
// Output: the media details and the thumbnail, or none when the browser cannot read them.
export async function describeFile(file: File, buffer: ArrayBuffer, kind: FileKind): Promise<FileDescription> {
  const nothing: FileDescription = { media: {}, thumbnail: null };
  if (typeof document === "undefined") return nothing;
  try {
    if (kind === "image") return await describePicture(new Blob([buffer], { type: file.type || "image/*" }));
    if (kind === "video") return await describeVideo(new Blob([buffer], { type: file.type || "video/*" }));
    if (kind === "audio") return await describeAudio(new Blob([buffer], { type: file.type || "audio/*" }));
    if (kind === "pdf") return await describePdf(buffer);
  } catch {
    // A file the browser cannot decode is kept all the same, without a thumbnail.
  }
  return nothing;
}
