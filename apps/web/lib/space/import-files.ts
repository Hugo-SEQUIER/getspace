import {
  checkImportFile,
  cleanDocumentText,
  decodeImportText,
  parseTableText,
  tableFromRows,
  type ImportTable,
} from "./import-data";

import { documentImageExtensions, readDocumentImage, insertDocumentImage } from "./document-content";
import { HEAVY_PREVIEW_BYTES, boundFileText, checkArchiveText, fileExtension, fileKind, officeXml, slideOrder, slideText } from "./document-files";

export const databaseExtensions = [
  "csv",
  "tsv",
  "xlsx",
  "json",
  "jsonl",
  "ndjson",
];
export const documentExtensions = ["pdf", "docx", "md", "markdown", "txt", ...documentImageExtensions];

async function checkArchive(buffer: ArrayBuffer) {
  const { unzipSync } = await import("fflate");
  let size = 0,
    entries = 0;
  // Inspect ZIP directory sizes before a parser expands an Office document.
  try {
    unzipSync(new Uint8Array(buffer), {
      filter(entry) {
        size += entry.originalSize;
        if (++entries > 2000 || size > 40 * 1024 * 1024)
          throw new Error(
            "This Office file is too complex. Export a smaller file or use CSV / text.",
          );
        return false;
      },
    });
  } catch (error) {
    if (error instanceof Error && error.message.startsWith("This Office"))
      throw error;
    throw new Error(
      "This Office file could not be read. Use an unencrypted .xlsx or .docx file, or export it as text.",
    );
  }
}

export async function readDatabaseFile(file: File): Promise<ImportTable[]> {
  const extension = checkImportFile(file, databaseExtensions);
  if (extension !== "xlsx")
    return [
      parseTableText(
        decodeImportText(new Uint8Array(await file.arrayBuffer())),
        extension,
        file.name,
      ),
    ];
  const buffer = await file.arrayBuffer();
  await checkArchive(buffer);
  const { default: readXlsx } = await import("read-excel-file/browser");
  let sheets;
  try {
    sheets = await readXlsx(buffer, {
      trim: false,
      parseNumber: (value) => value,
    });
  } catch {
    throw new Error(
      "This spreadsheet could not be read. Use an unencrypted .xlsx file, or export it as CSV.",
    );
  }
  const nonempty = sheets.filter((sheet) =>
    sheet.data.some((row) =>
      row.some((value) => value !== null && value !== ""),
    ),
  );
  if (!nonempty.length)
    throw new Error(
      "This workbook has no data. Add a header row and records first.",
    );
  return nonempty.map((sheet) =>
    tableFromRows(`${file.name} · ${sheet.sheet}`, sheet.data),
  );
}

export async function readDocumentFile(
  file: File,
): Promise<{ text: string; note: string; image?: string }> {
  const extension = checkImportFile(file, documentExtensions);
  if (documentImageExtensions.includes(extension)) {
    const image = await readDocumentImage(file);
    return {
      text: insertDocumentImage("", 0, 0, image, file.name.replace(/\.[^.]+$/, "")).text,
      image,
      note: "Saved as a still image inside your document. Images may be resized and compressed; metadata is removed. Text in images is not extracted.",
    };
  }
  if (!["pdf", "docx"].includes(extension))
    return {
      text: cleanDocumentText(
        decodeImportText(new Uint8Array(await file.arrayBuffer())),
      ),
      note: "Text and Markdown are imported as editable content.",
    };
  const buffer = await file.arrayBuffer();
  if (extension === "docx") {
    await checkArchive(buffer);
    const mammoth = await import("mammoth/mammoth.browser");
    let result;
    try {
      result = await mammoth.extractRawText({ arrayBuffer: buffer });
    } catch {
      throw new Error(
        "This Word document could not be read. Use an unencrypted .docx file, or export it as text.",
      );
    }
    return {
      text: cleanDocumentText(result.value),
      note: "Imported as editable text. Images, layout, and the original file are not kept.",
    };
  }
  const pdfjs = await import("pdfjs-dist");
  pdfjs.GlobalWorkerOptions.workerSrc = `/vendor/pdfjs/pdf.worker.min.mjs?v=${pdfjs.version}`;
  const loading = pdfjs.getDocument({ data: buffer, useSystemFonts: true });
  let pdf;
  try {
    pdf = await loading.promise;
  } catch (error) {
    await loading.destroy();
    if ((error as Error).name === "PasswordException")
      throw new Error(
        "This PDF is password protected. Upload an unlocked copy.",
      );
    throw new Error(
      "This PDF could not be read. Try exporting a new PDF or a text file.",
    );
  }
  try {
    if (pdf.numPages > 100)
      throw new Error(
        "Import up to 100 PDF pages at a time. Split this document first.",
      );
    const pages: string[] = [];
    let empty = 0,
      total = 0;
    for (let pageIndex = 1; pageIndex <= pdf.numPages; pageIndex++) {
      const page = await pdf.getPage(pageIndex);
      const content = await page.getTextContent();
      const text = content.items
        .map((item) =>
          "str" in item ? item.str + (item.hasEOL ? "\n" : " ") : "",
        )
        .join("")
        .trim();
      if (!text) empty++;
      total += text.length;
      if (total > 500000)
        throw new Error(
          "Import up to 500,000 characters at a time. Split this PDF first.",
        );
      pages.push(text);
      page.cleanup();
    }
    return {
      text: cleanDocumentText(pages.join("\n\n")),
      note: `${empty ? `${empty} page(s) had no readable text and were skipped. ` : ""}Imported as editable text. Images, layout, and the original PDF are not kept.`,
    };
  } finally {
    await loading.destroy();
  }
}

async function pdfText(buffer: ArrayBuffer) {
  const pdfjs = await import("pdfjs-dist");
  pdfjs.GlobalWorkerOptions.workerSrc = `/vendor/pdfjs/pdf.worker.min.mjs?v=${pdfjs.version}`;
  // pdf.js may transfer the buffer to its worker; the caller still needs the original bytes.
  const loading = pdfjs.getDocument({ data: new Uint8Array(buffer.slice(0)), useSystemFonts: true });
  try {
    const pdf = await loading.promise;
    const pages: string[] = [];
    let total = 0, read = 0;
    for (let pageIndex = 1; pageIndex <= pdf.numPages && total <= 500_000; pageIndex++) {
      const page = await pdf.getPage(pageIndex);
      const content = await page.getTextContent();
      const text = content.items
        .map((item) => ("str" in item ? item.str + (item.hasEOL ? "\n" : " ") : ""))
        .join("")
        .trim();
      total += text.length;
      read = pageIndex;
      if (text) pages.push(text);
      page.cleanup();
    }
    return {
      text: pages.join("\n\n"),
      detail: read < pdf.numPages ? `Text from the first ${read} of ${pdf.numPages} pages is searchable.` : "",
    };
  } finally {
    await loading.destroy();
  }
}

async function wordText(buffer: ArrayBuffer) {
  await checkArchiveText(buffer, officeXml);
  const mammoth = await import("mammoth/mammoth.browser");
  return (await mammoth.extractRawText({ arrayBuffer: buffer })).value;
}

async function slidesText(buffer: ArrayBuffer) {
  const { unzipSync, strFromU8 } = await import("fflate");
  const wanted = (name: string) =>
    name === "ppt/presentation.xml" || name === "ppt/_rels/presentation.xml.rels" || /^ppt\/slides\/slide\d+\.xml$/.test(name);
  await checkArchiveText(buffer, wanted);
  const files = unzipSync(new Uint8Array(buffer), { filter: (entry) => wanted(entry.name) });
  const text = (name: string) => (files[name] ? strFromU8(files[name]) : "");
  const slides = slideOrder(
    Object.keys(files).filter((name) => name.startsWith("ppt/slides/")),
    text("ppt/presentation.xml"),
    text("ppt/_rels/presentation.xml.rels"),
  );
  return slides
    .map((name, index) => [index + 1, slideText(text(name))] as const)
    .filter(([, value]) => value)
    .map(([number, value]) => `Slide ${number}\n${value}`)
    .join("\n\n");
}

async function sheetText(buffer: ArrayBuffer) {
  await checkArchiveText(buffer, officeXml);
  const { default: readXlsx } = await import("read-excel-file/browser");
  const sheets = await readXlsx(buffer, { trim: false, parseNumber: (value) => value });
  let cut = false;
  const text = sheets.map(({ sheet, data }) => {
    const rows = data
      .slice(0, 5000)
      .map((row) => row.slice(0, 50).map((value) => (value instanceof Date ? value.toISOString().slice(0, 10) : value ?? "")).join("\t").trimEnd())
      .filter(Boolean);
    if (data.length > 5000 || data.some((row) => row.length > 50)) cut = true;
    return rows.length ? `${sheet}\n${rows.join("\n")}` : "";
  }).filter(Boolean).join("\n\n");
  return { text, detail: cut ? "Only the first 5,000 rows and 50 columns of each sheet are searchable." : "" };
}

function plainText(buffer: ArrayBuffer) {
  const bytes = new Uint8Array(buffer, 0, Math.min(buffer.byteLength, 4 * 1024 * 1024));
  const encoding = bytes[0] === 0xff && bytes[1] === 0xfe ? "utf-16le" : bytes[0] === 0xfe && bytes[1] === 0xff ? "utf-16be" : "utf-8";
  const text = new TextDecoder(encoding).decode(bytes);
  // Text that decodes to mostly replacement characters is binary under a text name.
  const replaced = text.slice(0, 10000).split("\uFFFD").length - 1;
  return replaced > 100 ? "" : text;
}

// Parameters: a file Space keeps as an original, and its bytes.
// What it does: reads the text search and agents can use, on this device. It never refuses
// the file: one without readable text is kept with an empty body.
// Output: the text (possibly empty) and a note on what was read.
export async function readOriginalFileText(file: File, buffer: ArrayBuffer): Promise<{ text: string; note: string }> {
  const kind = fileKind(file);
  if (!["pdf", "word", "slides", "sheet", "text"].includes(kind))
    return { text: "", note: "Agents and search see this file’s title, not its contents." };
  // A workbook's reader holds every sheet in memory; past this size that can stall the tab.
  if (kind === "sheet" && file.size > HEAVY_PREVIEW_BYTES)
    return { text: "", note: "This workbook is too large to read on this device, so agents and search see only the title." };
  try {
    const read = kind === "pdf" ? await pdfText(buffer)
      : kind === "sheet" ? await sheetText(buffer)
        : { text: kind === "word" ? await wordText(buffer) : kind === "slides" ? await slidesText(buffer) : plainText(buffer), detail: "" };
    const { text, shortened } = boundFileText(read.text);
    if (!text)
      return {
        text: "",
        note: kind === "pdf"
          ? "No readable text was found, so agents and search see only the title. Scanned PDFs need OCR."
          : "No readable text was found, so agents and search see only the title.",
      };
    const words = text.split(/\s+/).filter(Boolean).length;
    const detail = shortened ? "Only the first 500,000 characters are searchable." : read.detail;
    return { text, note: `Agents and search can read ${words.toLocaleString()} ${words === 1 ? "word" : "words"} from this file.${detail ? ` ${detail}` : ""}` };
  } catch (error) {
    if ((error as Error)?.name === "PasswordException")
      return { text: "", note: "This PDF is password protected, so its text can’t be read. Agents and search see only the title." };
    return { text: "", note: `The text of this ${fileExtension(file.name).toUpperCase() || "file"} could not be read, so agents and search see only the title.` };
  }
}
