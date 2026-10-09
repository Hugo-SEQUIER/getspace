import {
  checkImportFile,
  cleanDocumentText,
  decodeImportText,
  parseTableText,
  tableFromRows,
  type ImportTable,
} from "./import-data";

import { documentImageExtensions, readDocumentImage, insertDocumentImage } from "./document-content";

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
