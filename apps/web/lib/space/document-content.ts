export type DocumentBlock =
  | { type: "heading"; text: string; level: number; id: string }
  | { type: "paragraph" | "quote"; text: string }
  | { type: "code"; text: string; language: string }
  | { type: "list"; items: string[]; ordered: boolean }
  | {
      type: "table";
      header: string[];
      align: ("left" | "center" | "right" | null)[];
      rows: string[][];
    }
  | { type: "rule" }
  | { type: "plain"; text: string };

export const MAX_MARKDOWN_NODES = 4000;

export const MAX_DOCUMENT_BODY_LENGTH = 700000;
export const MAX_DOCUMENT_IMAGE_LENGTH = 300000;
export const documentImageExtensions = ["png", "jpg", "jpeg", "webp", "gif"];

// Parameters: the first bytes of a file or an embedded image.
// What it does: recognizes supported raster signatures, without trusting a filename or MIME label.
// Output: the detected image MIME type, or null for unsupported content.
export function documentImageMimeType(bytes: Uint8Array) {
  if ([137, 80, 78, 71, 13, 10, 26, 10].every((byte, i) => bytes[i] === byte))
    return "image/png";
  if (bytes[0] === 255 && bytes[1] === 216 && bytes[2] === 255)
    return "image/jpeg";
  const header = String.fromCharCode(...bytes.slice(0, 12));
  if (header.startsWith("RIFF") && header.slice(8) === "WEBP") return "image/webp";
  if (header.startsWith("GIF87a") || header.startsWith("GIF89a")) return "image/gif";
  return null;
}

// Parameters: a Markdown image destination.
// What it does: allows only bounded, embedded raster images with matching signatures; never remote requests or SVG.
// Output: whether the destination can be rendered as an image.
export function safeDocumentImage(src: string) {
  if (src.length > MAX_DOCUMENT_IMAGE_LENGTH) return false;
  const match = src.match(/^data:image\/(png|jpeg|webp|gif);base64,([A-Za-z0-9+/]+={0,2})$/);
  if (!match || match[2].length % 4) return false;
  try {
    const header = Uint8Array.from(atob(match[2].slice(0, 32)), char => char.charCodeAt(0));
    return documentImageMimeType(header) === `image/${match[1]}`;
  } catch {
    return false;
  }
}

// Parameters: document Markdown used for excerpts and word counts.
// What it does: substitutes embedded images with their descriptions, leaving the saved body untouched.
// Output: text without encoded image payloads.
export function documentText(text: string) {
  return text.replace(/!\[([^\][\r\n]*)\]\(data:image\/(?:png|jpeg|webp|gif);base64,[A-Za-z0-9+/=]+\)/g, "$1");
}

// Parameters: a supported local raster image file, up to 10 MB.
// What it does: validates, decodes and re-encodes on the device, stripping metadata and reducing dimensions to fit a document.
// Output: a bounded data URL; animated images become a still frame.
export async function readDocumentImage(file: File): Promise<string> {
  const extension = file.name.split(".").pop()?.toLowerCase() ?? "";
  if (!documentImageExtensions.includes(extension))
    throw new Error("Choose a PNG, JPEG, WebP or GIF image.");
  if (!file.size) throw new Error("This image is empty.");
  if (file.size > 10 * 1024 * 1024) throw new Error("Choose an image smaller than 10 MB.");
  const buffer = await file.arrayBuffer();
  const mime = documentImageMimeType(new Uint8Array(buffer));
  if (!mime) throw new Error("This file is not a supported raster image.");
  const url = URL.createObjectURL(new Blob([buffer], { type: mime }));
  try {
    const image = new Image();
    image.src = url;
    try {
      await image.decode();
    } catch {
      throw new Error("This image could not be read. Try exporting a new copy.");
    }
    if (!image.naturalWidth || !image.naturalHeight
      || image.naturalWidth * image.naturalHeight > 40000000)
      throw new Error("Choose an image with no more than 40 million pixels.");
    const canvas = document.createElement("canvas");
    const context = canvas.getContext("2d");
    if (!context) throw new Error("Image processing is unavailable in this browser.");
    let scale = Math.min(1, 2048 / Math.max(image.naturalWidth, image.naturalHeight));
    for (let attempt = 0; attempt < 8; attempt++) {
      canvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
      canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
      context.drawImage(image, 0, 0, canvas.width, canvas.height);
      const src = canvas.toDataURL("image/webp", 0.85);
      if (safeDocumentImage(src)) return src;
      scale *= 0.75;
    }
    throw new Error("This image is too large to embed. Try a smaller image.");
  } finally {
    URL.revokeObjectURL(url);
  }
}

// Parameters: the current body, selection, encoded image and its description.
// What it does: inserts safe image Markdown at the selection without exceeding the saved document limit.
// Output: the updated body and description selection for keyboard editing.
export function insertDocumentImage(text: string, start: number, end: number, src: string, description: string) {
  if (!safeDocumentImage(src)) throw new Error("This image cannot be embedded.");
  const label = description.replace(/[\\[\]\r\n]/g, " ").trim().slice(0, 300) || "Image";
  const prefix = (start > 0 ? "\n\n" : "") + "![";
  const suffix = `](${src})` + (end < text.length ? "\n\n" : "");
  const body = text.slice(0, start) + prefix + label + suffix + text.slice(end);
  if (body.length > MAX_DOCUMENT_BODY_LENGTH)
    throw new Error("This document has no room for another image. Use a smaller image or a separate document.");
  return { text: body, start: start + prefix.length, end: start + prefix.length + label.length };
}


// Escaped pipes stay in their cell, even inside inline code or emphasis.
function tableCells(line: string): string[] {
  const text = line.trim();
  const cells: string[] = [];
  let cell = "", trailingPipe = false;
  for (let i = 0; i < text.length; i++) {
    trailingPipe = false;
    if (text[i] === "\\" && i + 1 < text.length) {
      cell += text[i + 1] === "|" ? "|" : text[i] + text[i + 1];
      i++;
    } else if (text[i] === "|") {
      cells.push(cell.trim());
      cell = "";
      trailingPipe = true;
    } else {
      cell += text[i];
    }
  }
  cells.push(cell.trim());
  if (text.startsWith("|")) cells.shift();
  if (trailingPipe) cells.pop();
  return cells;
}

function tableHeader(line: string, delimiter: string | undefined) {
  if (!delimiter || !/^[\t :|\-]+$/.test(delimiter)
    || !(line.includes("|") || delimiter.includes("|"))) return null;
  const separators = tableCells(delimiter);
  if (!separators.length || !separators.every(cell => /^:?-+:?$/.test(cell)))
    return null;
  const header = tableCells(line);
  if (header.length !== separators.length) return null;
  const align = separators.map(cell => cell.endsWith(":")
    ? cell.startsWith(":") ? "center" as const : "right" as const
    : cell.startsWith(":") ? "left" as const : null);
  return { header, align };
}

// Parameters: a document body.
// What it does: parses bounded Markdown with embedded raster images, but no HTML or remote media.
// Output: document blocks, or a lossless plain-text fallback when formatting exceeds the budget.
export function documentBlocks(text: string): DocumentBlock[] {
  const lines = text.replaceAll("\r\n", "\n").split("\n");
  const blocks: DocumentBlock[] = [];
  let i = 0,
    heading = 0,
    nodes = 0;
  const startsBlock = (line: string) =>
    /^(#{1,6} |```|> ?|[-*] |\d+\. |(?:---+|\*\*\*+)\s*$)/.test(line);
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) {
      i++;
      continue;
    }
    if (++nodes > MAX_MARKDOWN_NODES) return [{ type: "plain", text }];
    if (line.startsWith("```")) {
      const language = line.slice(3).trim();
      const content: string[] = [];
      i++;
      while (i < lines.length && !/^```\s*$/.test(lines[i]))
        content.push(lines[i++]);
      if (i < lines.length) i++;
      blocks.push({ type: "code", text: content.join("\n"), language });
      continue;
    }
    const title = line.match(/^(#{1,6}) (.+)$/);
    if (title) {
      blocks.push({
        type: "heading",
        text: title[2],
        level: title[1].length,
        id: `document-section-${heading++}`,
      });
      i++;
      continue;
    }
    if (/^(---+|\*\*\*+)\s*$/.test(line)) {
      blocks.push({ type: "rule" });
      i++;
      continue;
    }
    if (/^> ?/.test(line)) {
      const content: string[] = [];
      while (i < lines.length && /^> ?/.test(lines[i]))
        content.push(lines[i++].replace(/^> ?/, ""));
      blocks.push({ type: "quote", text: content.join("\n") });
      continue;
    }
    if (/^([-*] |\d+\. )/.test(line)) {
      const ordered = /^\d+\. /.test(line);
      const pattern = ordered ? /^\d+\. / : /^[-*] /;
      const items: string[] = [];
      while (i < lines.length && pattern.test(lines[i])) {
        if (++nodes > MAX_MARKDOWN_NODES) return [{ type: "plain", text }];
        items.push(lines[i++].replace(pattern, ""));
      }
      blocks.push({ type: "list", items, ordered });
      continue;
    }
    const table = tableHeader(line, lines[i + 1]);
    if (table) {
      // The block counts the scroll wrapper; also count table, thead, tr and cells.
      nodes += 3 + table.header.length;
      if (nodes > MAX_MARKDOWN_NODES) return [{ type: "plain", text }];
      const rows: string[][] = [];
      i += 2;
      while (i < lines.length && lines[i].trim() && !startsBlock(lines[i])) {
        nodes += 1 + table.header.length + (rows.length === 0 ? 1 : 0);
        if (nodes > MAX_MARKDOWN_NODES) return [{ type: "plain", text }];
        const cells = tableCells(lines[i++]);
        rows.push(table.header.map((_, column) => cells[column] ?? ""));
      }
      blocks.push({ type: "table", ...table, rows });
      continue;
    }
    const content = [line];
    i++;
    while (i < lines.length && lines[i].trim() && !startsBlock(lines[i])
      && !tableHeader(lines[i], lines[i + 1]))
      content.push(lines[i++]);
    blocks.push({ type: "paragraph", text: content.join("\n") });
  }
  // Include inline nodes in the same budget so the outline and rendered view
  // agree when the complete document needs a plain-text fallback.
  for (const block of blocks) {
    const values = block.type === "table"
      ? [...block.header, ...block.rows.flat()]
      : block.type === "list"
      ? block.items
      : block.type === "heading" || block.type === "paragraph" || block.type === "quote"
        ? [block.text]
        : [];
    for (const value of values) {
      const parts = documentInline(value, MAX_MARKDOWN_NODES - nodes);
      if (!parts) return [{ type: "plain", text }];
      nodes += parts.length;
    }
  }
  return blocks;
}

export type DocumentInline =
  | { type: "text" | "code" | "strong" | "em"; text: string }
  | { type: "link"; text: string; href: string }
  | { type: "image"; text: string; src: string };

// Each delimiter search advances monotonically, including failed link attempts.
// Malformed labels/destinations never rescan the same suffix for every opener.
export function documentInline(
  text: string,
  limit = MAX_MARKDOWN_NODES,
): DocumentInline[] | null {
  const next = (pattern: RegExp) => {
    let found = -1;
    return (start: number) => {
      if (found < start) {
        pattern.lastIndex = start;
        found = pattern.exec(text)?.index ?? text.length;
      }
      return found;
    };
  };
  const newline = next(/\n/g),
    tick = next(/`/g),
    star = next(/\*/g),
    strong = next(/\*/g),
    label = next(/\]/g),
    destination = next(/[\s)]/g);
  const parts: DocumentInline[] = [];
  let plain = 0;
  for (let i = 0; i < text.length; ) {
    let part: DocumentInline | undefined;
    let stop = i;
    if (text[i] === "`") {
      const end = tick(i + 1);
      if (end > i + 1 && end < newline(i + 1)) {
        part = { type: "code", text: text.slice(i + 1, end) };
        stop = end + 1;
      }
    } else if (text[i] === "*") {
      if (text[i + 1] === "*") {
        const end = strong(i + 2);
        if (end > i + 2 && end < newline(i + 1) && text[end + 1] === "*") {
          part = { type: "strong", text: text.slice(i + 2, end) };
          stop = end + 2;
        }
      }
      if (!part) {
        const end = star(i + 1);
        if (end > i + 1 && end < newline(i + 1)) {
          part = { type: "em", text: text.slice(i + 1, end) };
          stop = end + 1;
        }
      }
    } else if (text[i] === "[" || (text[i] === "!" && text[i + 1] === "[")) {
      const image = text[i] === "!";
      const start = i + (image ? 2 : 1);
      const end = label(start);
      if ((image ? end >= start : end > start) && end < newline(start) && text[end + 1] === "(") {
        const close = destination(end + 2);
        if (close > end + 2 && text[close] === ")") {
          const href = text.slice(end + 2, close);
          if (image ? safeDocumentImage(href) : safeDocumentLink(href))
            part = image
              ? { type: "image", text: text.slice(start, end), src: href }
              : { type: "link", text: text.slice(start, end), href };
          stop = close + 1;
        }
      }
    }
    if (part) {
      if (plain < i) parts.push({ type: "text", text: text.slice(plain, i) });
      parts.push(part);
      plain = stop;
      if (parts.length > limit) return null;
    }
    i = stop > i ? stop : i + 1;
  }
  if (plain < text.length) parts.push({ type: "text", text: text.slice(plain) });
  return parts.length <= limit ? parts : null;
}

export function safeDocumentLink(href: string) {
  return (
    /^(https?:\/\/|mailto:|\/(?!\/)|#)/i.test(href) &&
    !/[\u0000-\u0020\\]/.test(href)
  );
}


// Parameters: a saved document body containing embedded images.
// What it does: gives each embedded image a short editor-only reference, keeping encoded bytes out of the writing field.
// Output: editable Markdown and the images needed to restore the saved body.
export function documentImageDraft(body: string) {
  const images: Record<string, string> = {};
  const text = body.replace(/(!\[[^\][\r\n]*\]\()(data:image\/(?:png|jpeg|webp|gif);base64,[A-Za-z0-9+/=]+)(\))/g,
    (original, prefix: string, src: string, suffix: string) => {
      if (!safeDocumentImage(src)) return original;
      const reference = `space-image:${crypto.randomUUID()}`;
      images[reference] = src;
      return prefix + reference + suffix;
    });
  return { text, images };
}

// Parameters: editable Markdown and its local image references.
// What it does: restores embedded images for preview and saving, without changing unknown references.
// Output: canonical document Markdown, with no dependency on browser-local image storage.
export function documentImageBody(text: string, images: Record<string, string>) {
  return text.replace(/(!\[[^\][\r\n]*\]\()(space-image:[a-f0-9-]{36})(\))/g,
    (original, prefix: string, reference: string, suffix: string) =>
      Object.hasOwn(images, reference) ? prefix + images[reference] + suffix : original);
}

export type DocumentFormat =
  "bold" | "italic" | "heading" | "list" | "quote" | "code";
export function formatDocument(
  text: string,
  start: number,
  end: number,
  format: DocumentFormat,
) {
  const chosen = text.slice(start, end);
  if (format === "bold" || format === "italic") {
    const mark = format === "bold" ? "**" : "*";
    const content = chosen || "text";
    return {
      text: text.slice(0, start) + mark + content + mark + text.slice(end),
      start: start + mark.length,
      end: start + mark.length + content.length,
    };
  }
  if (format === "code") {
    const content = chosen || "code";
    const prefix = (start && text[start - 1] !== "\n" ? "\n" : "") + "```\n";
    const suffix =
      "\n```" + (end < text.length && text[end] !== "\n" ? "\n" : "");
    return {
      text: text.slice(0, start) + prefix + content + suffix + text.slice(end),
      start: start + prefix.length,
      end: start + prefix.length + content.length,
    };
  }
  const lineStart = start === 0 ? 0 : text.lastIndexOf("\n", start - 1) + 1;
  const lineEnd = text.indexOf("\n", Math.max(start, end - 1));
  const stop = lineEnd < 0 ? text.length : lineEnd;
  const prefix = format === "heading" ? "## " : format === "list" ? "- " : "> ";
  const content = text
    .slice(lineStart, stop)
    .split("\n")
    .map((line) => prefix + line)
    .join("\n");
  return {
    text: text.slice(0, lineStart) + content + text.slice(stop),
    start: lineStart,
    end: lineStart + content.length,
  };
}
