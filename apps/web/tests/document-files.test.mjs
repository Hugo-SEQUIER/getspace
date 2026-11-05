import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { test } from "node:test";
import {
  AUTO_PREVIEW_BYTES,
  FILE_PART_BYTES,
  HEAVY_PREVIEW_BYTES,
  MAX_FILE_BYTES,
  MAX_FILE_PARTS,
  MAX_FILE_TEXT,
  MAX_FILE_TEXT_BYTES,
  base64ToBytes,
  boundFileText,
  bytesToBase64,
  cleanFileName,
  cleanFileType,
  downloadFileParts,
  THUMBNAIL_BYTES,
  THUMBNAIL_SIDE,
  downloadThumbnail,
  eachPart,
  fileDetails,
  fileKind,
  fileLabel,
  fileParts,
  filePartPath,
  fileReadPath,
  fileDiscardPath,
  fileThumbnailPath,
  fitThumbnail,
  formatDuration,
  formatFileSize,
  mediaDetail,
  partRange,
  previewMode,
  sha256Hex,
  slideOrder,
  slideText,
  thumbnailKinds,
  uploadFileParts,
} from "../lib/space/document-files.ts";

const status = (code, message = "failed") => Object.assign(new Error(message), { name: "ApiError", status: code });

test("file names lose path separators, control and direction characters but keep their extension", () => {
  assert.equal(cleanFileName("../reports\\Q3.pdf"), "..-reports-Q3.pdf");
  assert.equal(cleanFileName("in\u0000voice‮fdp.exe"), "in-voice-fdp.exe");
  assert.equal(cleanFileName("  notes.txt. "), "notes.txt");
  assert.equal(cleanFileName("///"), "---");
  assert.equal(cleanFileName("   "), "file");
  const long = cleanFileName(`${"é".repeat(400)}.pptx`);
  assert.equal([...long].length, 255);
  assert.ok(long.endsWith(".pptx"));
});

test("file types drop parameters, fall back by extension and refuse malformed values", () => {
  assert.equal(cleanFileType("Text/Plain; charset=UTF-8", "a.txt"), "text/plain");
  assert.equal(cleanFileType("", "deck.pptx"), "application/vnd.openxmlformats-officedocument.presentationml.presentation");
  assert.equal(cleanFileType("application/octet-stream", "scan.pdf"), "application/pdf");
  assert.equal(cleanFileType("not a type", "archive.unknown"), "application/octet-stream");
  assert.equal(cleanFileType("image/png\r\nX: y", "a.png"), "image/png");
});

test("parts are 1 MiB except the last, and cover the file exactly", () => {
  assert.equal(fileParts(1), 1);
  assert.equal(fileParts(FILE_PART_BYTES), 1);
  assert.equal(fileParts(FILE_PART_BYTES + 1), 2);
  assert.deepEqual(partRange(0, FILE_PART_BYTES + 5), [0, FILE_PART_BYTES]);
  assert.deepEqual(partRange(1, FILE_PART_BYTES + 5), [FILE_PART_BYTES, FILE_PART_BYTES + 5]);
  assert.equal(filePartPath("s", "f", 3), "spaces/s/files/f/parts/3");
  assert.equal(fileReadPath("s", "r", 0), "spaces/s/resources/r/file/0");
  assert.equal(fileReadPath("s", "r", 2, 7), "spaces/s/resources/r/file/2?revision=7");
});

test("base64 round-trips a full part without overflowing the stack", async () => {
  const bytes = new Uint8Array(FILE_PART_BYTES);
  for (let index = 0; index < bytes.length; index++) bytes[index] = (index * 31 + 7) & 0xff;
  const encoded = bytesToBase64(bytes);
  assert.equal(encoded, Buffer.from(bytes).toString("base64"));
  assert.deepEqual(base64ToBytes(encoded), bytes);
  assert.equal(await sha256Hex(bytes), createHash("sha256").update(bytes).digest("hex"));
});

test("file kinds pick a preview by extension, and SVG is never displayed", () => {
  assert.equal(fileKind({ name: "a.PDF", type: "" }), "pdf");
  assert.equal(fileKind({ name: "a.docx", type: "" }), "word");
  assert.equal(fileKind({ name: "a.pptx", type: "" }), "slides");
  assert.equal(fileKind({ name: "a.xlsx", type: "" }), "sheet");
  assert.equal(fileKind({ name: "a.webp", type: "image/webp" }), "image");
  assert.equal(fileKind({ name: "a.svg", type: "image/svg+xml" }), "other");
  assert.equal(fileKind({ name: "logo", type: "image/svg+xml" }), "other");
  assert.equal(fileKind({ name: "a.csv", type: "text/csv" }), "text");
  assert.equal(fileKind({ name: "a.mp4", type: "video/mp4" }), "video");
  assert.equal(fileKind({ name: "a.zip", type: "application/zip" }), "other");
  assert.equal(fileLabel({ name: "deck.pptx", type: "" }), "PPTX");
  assert.equal(fileLabel({ name: "README", type: "" }), "File");
  assert.equal(formatFileSize(900), "900 B");
  assert.equal(formatFileSize(2_516_582), "2.4 MB");
  assert.equal(formatFileSize(52_428_800), "50 MB");
});

test("a file holds at most 250 parts of 1 MiB, the backend's cap", () => {
  assert.equal(MAX_FILE_PARTS, 250);
  assert.equal(MAX_FILE_BYTES, 250 * FILE_PART_BYTES);
  assert.equal(fileParts(MAX_FILE_BYTES), 250);
  assert.equal(fileDiscardPath("s", "f"), "spaces/s/files/f");
});

test("big files preview on request and heavy previews stop at their size", () => {
  const file = (name, bytes) => ({ name, type: "", bytes });
  assert.equal(previewMode(file("plan.pdf", AUTO_PREVIEW_BYTES)), "auto");
  assert.equal(previewMode(file("plan.pdf", AUTO_PREVIEW_BYTES + 1)), "on-request");
  assert.equal(previewMode(file("plan.pdf", MAX_FILE_BYTES)), "on-request");
  assert.equal(previewMode(file("deck.pptx", HEAVY_PREVIEW_BYTES)), "on-request");
  assert.equal(previewMode(file("deck.pptx", HEAVY_PREVIEW_BYTES + 1)), "none");
  assert.equal(previewMode(file("photo.png", HEAVY_PREVIEW_BYTES + 1)), "none");
  assert.equal(previewMode(file("talk.mp4", MAX_FILE_BYTES)), "on-request");
  assert.equal(previewMode(file("archive.zip", 10)), "none");
});

test("searchable text stays within the character and encoded-size bounds", () => {
  const short = boundFileText("﻿Line one\r\nLine two\0");
  assert.deepEqual(short, { text: "Line one\nLine two", shortened: false });
  const long = boundFileText("word ".repeat(MAX_FILE_TEXT));
  assert.ok(long.shortened);
  assert.ok(long.text.length <= MAX_FILE_TEXT);
  // Three bytes a character: the encoded bound, not the character bound, decides.
  const wide = boundFileText("界".repeat(400_000));
  assert.ok(wide.shortened);
  assert.ok(Buffer.byteLength(JSON.stringify(wide.text)) <= MAX_FILE_TEXT_BYTES);
  // Newlines double in JSON; the bound counts the escaped form.
  const lines = boundFileText("\n".repeat(10).concat("x\n".repeat(600_000)));
  assert.ok(Buffer.byteLength(JSON.stringify(lines.text)) <= MAX_FILE_TEXT_BYTES);
  // Cutting never leaves half of a surrogate pair, and lone halves are replaced.
  const emoji = boundFileText("😀".repeat(MAX_FILE_TEXT));
  assert.ok(!/[\ud800-\udbff]$/.test(emoji.text));
  assert.equal(boundFileText("a\ud800b").text, "a�b");
});

test("slides are read in presentation order with their text runs and line breaks", () => {
  const presentation = `<p:presentation xmlns:p="p" xmlns:r="r"><p:sldIdLst>
    <p:sldId id="256" r:id="rId9"/><p:sldId id="257" r:id="rId2"/></p:sldIdLst></p:presentation>`;
  const relationships = `<Relationships><Relationship Id="rId2" Type="slide" Target="slides/slide1.xml"/>
    <Relationship Target="/ppt/slides/slide10.xml" Id="rId9" Type="slide"/></Relationships>`;
  assert.deepEqual(
    slideOrder(["ppt/slides/slide1.xml", "ppt/slides/slide10.xml", "ppt/slides/slide3.xml", "ppt/slides/slide2.xml"], presentation, relationships),
    ["ppt/slides/slide10.xml", "ppt/slides/slide1.xml", "ppt/slides/slide2.xml", "ppt/slides/slide3.xml"],
  );
  const slide = `<p:sld><a:p><a:r><a:t>Q3 </a:t></a:r><a:r><a:t xml:space="preserve">plan &amp; budget</a:t></a:r></a:p>
    <a:p><a:r><a:t>Line</a:t></a:r><a:br/><a:r><a:t>break &#x263A;</a:t></a:r></a:p><a:p><a:endParaRPr/></a:p></p:sld>`;
  assert.equal(slideText(slide), "Q3 plan & budget\nLine\nbreak ☺");
});

test("parts run with bounded concurrency and the first failure cancels the rest", async () => {
  let running = 0, peak = 0;
  const seen = [];
  await eachPart(7, 3, undefined, async (part) => {
    running++;
    peak = Math.max(peak, running);
    await new Promise((resolve) => setTimeout(resolve, 5));
    seen.push(part);
    running--;
  });
  assert.equal(peak, 3);
  assert.deepEqual(seen.sort((a, b) => a - b), [0, 1, 2, 3, 4, 5, 6]);

  const started = [];
  await assert.rejects(
    eachPart(10, 2, undefined, async (part, signal) => {
      started.push(part);
      if (part === 1) throw status(409, "This file part already holds different bytes.");
      await new Promise((resolve, reject) => {
        const timer = setTimeout(resolve, 50);
        signal.addEventListener("abort", () => { clearTimeout(timer); reject(signal.reason); }, { once: true });
      });
    }),
    /different bytes/,
  );
  assert.deepEqual(started, [0, 1]);
});

test("an upload sends every part once, retries an overloaded backend and reports progress", async () => {
  const data = new Uint8Array(FILE_PART_BYTES * 2 + 10).map((_, index) => index & 0xff);
  const calls = [], progress = [];
  let overloaded = true;
  const request = async (path, method, body) => {
    calls.push(path);
    assert.equal(method, "POST");
    if (path.endsWith("/parts/1") && overloaded) { overloaded = false; throw status(503, "Busy"); }
    const part = Number(path.split("/").pop());
    const [start, end] = partRange(part, data.length);
    assert.deepEqual(base64ToBytes(body.data), data.subarray(start, end));
    return { part };
  };
  await uploadFileParts(request, { spaceId: "s", fileId: "f", data, onProgress: (value) => progress.push(value) });
  assert.deepEqual([...calls].sort(), ["spaces/s/files/f/parts/0", "spaces/s/files/f/parts/1", "spaces/s/files/f/parts/1", "spaces/s/files/f/parts/2"]);
  assert.equal(progress.at(-1), 1);

  const quota = async () => { throw status(429, "Storage limit reached."); };
  let attempts = 0;
  await assert.rejects(
    uploadFileParts(async (...args) => { attempts++; return quota(...args); }, { spaceId: "s", fileId: "f", data: new Uint8Array(5) }),
    /Storage limit/,
  );
  assert.equal(attempts, 1, "a quota refusal is not retried");
});

test("a download joins the parts in order and refuses bytes that do not match the hash", async () => {
  const data = new Uint8Array(FILE_PART_BYTES + 3).map((_, index) => (index * 7) & 0xff);
  const file = { id: "f", name: "a.pdf", type: "application/pdf", bytes: data.length, sha256: await sha256Hex(data), parts: 2 };
  const parts = [data.subarray(0, FILE_PART_BYTES), data.subarray(FILE_PART_BYTES)];
  const request = async (path) => {
    // The second part answers first: order comes from the part number.
    const part = Number(/file\/(\d+)/.exec(path)[1]);
    await new Promise((resolve) => setTimeout(resolve, part ? 1 : 10));
    return { data: bytesToBase64(parts[part]) };
  };
  const blob = await downloadFileParts(request, { spaceId: "s", resourceId: "r", file, revision: 3 });
  assert.equal(blob.type, "application/pdf");
  assert.deepEqual(new Uint8Array(await blob.arrayBuffer()), data);
  await assert.rejects(
    downloadFileParts(async () => ({ data: bytesToBase64(new Uint8Array(3)) }), { spaceId: "s", resourceId: "r", file: { ...file, parts: 1, bytes: 3 } }),
    /did not arrive intact/,
  );
});

test("thumbnails fit 480 pixels without growing, and pictures, videos and PDFs get one", () => {
  assert.equal(THUMBNAIL_BYTES, 65_536);
  assert.equal(THUMBNAIL_SIDE, 480);
  assert.deepEqual(fitThumbnail(4000, 3000), { width: 480, height: 360 });
  assert.deepEqual(fitThumbnail(1080, 1920), { width: 270, height: 480 });
  assert.deepEqual(fitThumbnail(64, 48), { width: 64, height: 48 });
  assert.deepEqual(fitThumbnail(10_000, 1), { width: 480, height: 1 });
  assert.deepEqual(fitThumbnail(0, 0), { width: 1, height: 1 });
  assert.deepEqual(thumbnailKinds, ["image", "video", "pdf"]);
  assert.equal(fileThumbnailPath("s", "r"), "spaces/s/resources/r/file/thumbnail");
  assert.equal(fileThumbnailPath("s", "r", 4), "spaces/s/resources/r/file/thumbnail?revision=4");
});

test("file details add a picture's size or a recording's length to the type and size", () => {
  assert.equal(formatDuration(0.4), "0:00");
  assert.equal(formatDuration(154.5), "2:35");
  assert.equal(formatDuration(3725), "1:02:05");
  assert.equal(mediaDetail({ width: 1920, height: 1080 }), "1920×1080");
  assert.equal(mediaDetail({ duration: 61 }), "1:01");
  assert.equal(mediaDetail({ width: 64, height: 48, duration: 2.4 }), "64×48 · 0:02");
  assert.equal(mediaDetail({}), "");
  assert.equal(fileDetails({ name: "garden.png", type: "image/png", bytes: 2_516_582, width: 3000, height: 2000 }), "PNG · 2.4 MB · 3000×2000");
  assert.equal(fileDetails({ name: "talk.mp4", type: "video/mp4", bytes: 900, duration: 154.5 }), "MP4 · 900 B · 2:35");
  assert.equal(fileDetails({ name: "plan.pdf", type: "application/pdf", bytes: 900 }), "PDF · 900 B");
});

test("a thumbnail download checks its size and hash and reports the backend's type", async () => {
  const data = new Uint8Array(3000).map((_, index) => (index * 13) & 0xff);
  const thumbnail = { id: "t", type: "image/webp", bytes: data.length, sha256: await sha256Hex(data), width: 480, height: 320, parts: 1 };
  const paths = [];
  const request = async (path) => { paths.push(path); return { data: bytesToBase64(data), type: "image/webp" }; };
  const blob = await downloadThumbnail(request, { spaceId: "s", resourceId: "r", thumbnail, revision: 2 });
  assert.equal(blob.type, "image/webp");
  assert.deepEqual(new Uint8Array(await blob.arrayBuffer()), data);
  assert.deepEqual(paths, ["spaces/s/resources/r/file/thumbnail?revision=2"]);
  await assert.rejects(
    downloadThumbnail(async () => ({ data: bytesToBase64(new Uint8Array(5)), type: "image/webp" }), { spaceId: "s", resourceId: "r", thumbnail }),
    /did not arrive intact/,
  );
});
