import Papa from "papaparse";
import type {
  DatabaseField,
  FieldType,
  FieldValue,
  WorkspaceDatabase,
} from "./databases";

export const importLimits = {
  bytes: 10 * 1024 * 1024,
  rows: 2000,
  columns: 50,
  text: 500000,
};
export type ImportTable = {
  name: string;
  headers: string[];
  rows: FieldValue[][];
};

export function checkImportFile(
  file: { name: string; size: number },
  extensions: string[],
) {
  const extension = file.name.split(".").pop()?.toLowerCase() ?? "";
  if (!extensions.includes(extension))
    throw new Error(
      `Choose ${extensions.map((e) => `.${e}`).join(", ")} files.`,
    );
  if (!file.size) throw new Error("This file is empty.");
  if (file.size > importLimits.bytes)
    throw new Error("Choose a file smaller than 10 MB.");
  return extension;
}

export function decodeImportText(bytes: Uint8Array) {
  const encoding =
    bytes[0] === 0xff && bytes[1] === 0xfe
      ? "utf-16le"
      : bytes[0] === 0xfe && bytes[1] === 0xff
        ? "utf-16be"
        : "utf-8";
  try {
    return new TextDecoder(encoding, { fatal: true }).decode(bytes);
  } catch {
    throw new Error(
      "This text encoding could not be read. Export the file as UTF-8 and try again.",
    );
  }
}

function cell(value: unknown): FieldValue {
  if (value === null || value === undefined) return null;
  if (value instanceof Date) return value.toISOString().slice(0, 10);
  if (typeof value === "boolean") return value;
  if (typeof value === "number") {
    if (
      !Number.isFinite(value) ||
      (Number.isInteger(value) && !Number.isSafeInteger(value))
    )
      throw new Error(
        "The file contains a number outside the supported range.",
      );
    return value;
  }
  const text = typeof value === "string" ? value : JSON.stringify(value);
  if (text.length > 5000)
    throw new Error(
      "A cell contains more than 5,000 characters. Shorten it before importing.",
    );
  return text;
}

export function tableFromRows(name: string, raw: unknown[][]): ImportTable {
  const rows = raw.filter((row) =>
    row.some((v) => v !== null && v !== undefined && String(v).trim() !== ""),
  );
  if (!rows.length)
    throw new Error("No columns found. Add a header row to your file.");
  if (rows.length - 1 > importLimits.rows)
    throw new Error(
      "Import up to 2,000 records at a time. Split this file into smaller files.",
    );
  const width = Math.max(...rows.map((row) => row.length));
  if (width > importLimits.columns)
    throw new Error("Import up to 50 columns at a time.");
  if (rows.length - 1 > importLimits.rows)
    throw new Error(
      "Import up to 2,000 records at a time. Split this file into smaller files.",
    );
  const used = new Set<string>();
  const headers = Array.from({ length: width }, (_, i) => {
    const original = String(rows[0][i] ?? "").trim() || `Column ${i + 1}`;
    if (original.length > 70)
      throw new Error("Shorten column names to 70 characters or fewer.");
    let title = original,
      suffix = 2;
    while (used.has(title.toLowerCase())) title = `${original} (${suffix++})`;
    used.add(title.toLowerCase());
    return title;
  });
  const result = {
    name,
    headers,
    rows: rows.slice(1).map((row) => headers.map((_, i) => cell(row[i]))),
  };
  if (JSON.stringify(result).length > 1000000)
    throw new Error(
      "The parsed table is too large. Split it into files with less than 1 MB of cell content.",
    );
  return result;
}

export function parseTableText(
  text: string,
  extension: string,
  name: string,
): ImportTable {
  text = text.replace(/^\uFEFF/, "");
  if (["json", "jsonl", "ndjson"].includes(extension)) {
    let value: unknown;
    try {
      value =
        extension === "json"
          ? JSON.parse(text)
          : text
              .split(/\r?\n/)
              .filter((line) => line.trim())
              .map((line) => JSON.parse(line));
    } catch {
      throw new Error(
        "This JSON could not be read. Check its syntax and try again.",
      );
    }
    if (value && !Array.isArray(value) && typeof value === "object") {
      const object = value as Record<string, unknown>;
      value =
        [object.records, object.rows, object.data].find(Array.isArray) ?? value;
    }
    if (!Array.isArray(value) || !value.length)
      throw new Error(
        "Use a JSON array of objects, or an object with a records, rows, or data array.",
      );
    if (value.length > importLimits.rows + 1)
      throw new Error(
        "Import up to 2,000 records at a time. Split this file into smaller files.",
      );
    if (value.every(Array.isArray)) return tableFromRows(name, value);
    if (
      !value.every(
        (row) => row && typeof row === "object" && !Array.isArray(row),
      )
    )
      throw new Error(
        "Each JSON record must be an object with named properties.",
      );
    const headers = [...new Set(value.flatMap((row) => Object.keys(row)))];
    return tableFromRows(name, [
      headers,
      ...value.map((row) =>
        headers.map((key) => (Object.hasOwn(row, key) ? row[key] : null)),
      ),
    ]);
  }
  const parsed = Papa.parse<string[]>(text, {
    delimiter: extension === "tsv" ? "\t" : "",
    skipEmptyLines: "greedy",
    dynamicTyping: false,
  });
  const error = parsed.errors.find((e) => e.code !== "UndetectableDelimiter");
  if (error)
    throw new Error(
      `Could not read row ${(error.row ?? 0) + 1}: ${error.message}`,
    );
  const width = parsed.data[0]?.length;
  if (parsed.data.some((row) => row.length !== width))
    throw new Error(
      "Some rows have a different number of columns. Check the delimiter and quoted values.",
    );
  return tableFromRows(name, parsed.data);
}

function numeric(value: FieldValue) {
  return (
    typeof value === "number" ||
    (typeof value === "string" &&
      /^-?(0|[1-9]\d*)(\.\d+)?$/.test(value) &&
      Number.isFinite(Number(value)) &&
      Math.abs(Number(value)) <= Number.MAX_SAFE_INTEGER)
  );
}
function validDate(value: string) {
  return (
    /^\d{4}-\d{2}-\d{2}$/.test(value) &&
    Number.isFinite(Date.parse(value)) &&
    new Date(value).toISOString().slice(0, 10) === value
  );
}
export function inferColumn(table: ImportTable, index: number): FieldType {
  const values = table.rows
    .map((row) => row[index])
    .filter((v) => v !== null && v !== "");
  if (!values.length) return "text";
  if (
    values.every((v) => typeof v === "boolean" || v === "true" || v === "false")
  )
    return "checkbox";
  // IDs, postal codes, and phone numbers should keep their original representation.
  if (
    !/id|phone|postal|zip|code/i.test(table.headers[index]) &&
    values.every(numeric)
  )
    return "number";
  if (values.every((v) => typeof v === "string" && validDate(v))) return "date";
  if (
    values.every(
      (v) => typeof v === "string" && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v),
    )
  )
    return "email";
  if (values.every((v) => typeof v === "string" && /^https?:\/\//.test(v)))
    return "url";
  if (
    /^(stage|status|category|priority)$/i.test(table.headers[index]) &&
    new Set(values).size <= 30 &&
    values.every((v) => String(v).length <= 80)
  )
    return "select";
  return "text";
}

export function convertImportValue(
  value: FieldValue,
  field: DatabaseField,
): FieldValue {
  if (value === null || value === "")
    return field.type === "checkbox" ? false : null;
  if (field.type === "number") {
    if (!numeric(value))
      throw new Error("needs a number (use Text to preserve this value)");
    return Number(value);
  }
  if (field.type === "checkbox") {
    if (value === true || value === "true") return true;
    if (value === false || value === "false") return false;
    throw new Error("needs true or false");
  }
  const text = String(value);
  if (field.type === "date" && !validDate(text))
    throw new Error("needs a date in YYYY-MM-DD format");
  if (field.type === "email" && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(text))
    throw new Error("needs a valid email");
  if (field.type === "url") {
    try {
      if (!["http:", "https:"].includes(new URL(text).protocol))
        throw new Error();
    } catch {
      throw new Error("needs an http or https URL");
    }
  }
  return text;
}

export function databaseFromImport(
  table: ImportTable,
  name: string,
  primary: number,
  types: FieldType[],
  id: string,
): WorkspaceDatabase {
  if (!name.trim() || name.trim().length > 200)
    throw new Error("Enter a database name of at most 200 characters.");
  const used = new Set(["name"]);
  const fields: DatabaseField[] = table.headers.flatMap((header, i) => {
    if (i === primary) return [];
    let title = header,
      suffix = 2;
    while (used.has(title.toLowerCase())) title = `${header} (${suffix++})`;
    used.add(title.toLowerCase());
    const type = types[i] ?? "text";
    const options =
      type === "select"
        ? [
            ...new Set(
              table.rows
                .map((row) => row[i])
                .filter((v) => v !== null && v !== "")
                .map(String),
            ),
          ]
        : undefined;
    if (
      options &&
      (!options.length ||
        options.length > 30 ||
        new Set(options.map((v) => v.toLowerCase())).size !== options.length ||
        options.some((v) => v.length > 80))
    )
      throw new Error(
        `${header}: Select needs 1–30 options of at most 80 characters. Choose Text instead.`,
      );
    return [
      {
        id: `${id}-field-${i}`,
        name: title,
        type,
        ...(options ? { options } : {}),
      },
    ];
  });
  const now = new Date().toISOString();
  const records = table.rows.map((row, index) => {
    const title = primary >= 0 ? String(row[primary] ?? "").trim() : "";
    if (title.length > 200)
      throw new Error(
        `Row ${index + 1}: record names must be 200 characters or fewer. Choose another name column or generate names.`,
      );
    return {
      id: `${id}-record-${index}`,
      name: title || `Record ${index + 1}`,
      notes: "",
      createdAt: now,
      updatedAt: now,
      values: Object.fromEntries(
        fields.map((field) => {
          const i = Number(field.id.split("-field-").pop());
          try {
            return [field.id, convertImportValue(row[i], field)];
          } catch (error) {
            throw new Error(
              `Row ${index + 1}, ${field.name}: ${(error as Error).message}.`,
            );
          }
        }),
      ),
    };
  });
  return {
    id,
    name: name.trim(),
    description: `Imported from ${table.name}`,
    fields,
    records,
    view: "table",
    groupBy: fields.find((f) => f.type === "select")?.id ?? null,
  };
}

export function cleanDocumentText(text: string) {
  text = text
    .replace(/^\uFEFF/, "")
    .replace(/\r\n?/g, "\n")
    .trim();
  if (text.includes("\0"))
    throw new Error(
      "This file does not contain readable text. Export it as UTF-8 text first.",
    );
  if (!text)
    throw new Error(
      "No readable text was found. Scanned PDFs need OCR before importing.",
    );
  if (text.length > importLimits.text)
    throw new Error(
      "Import up to 500,000 characters at a time. Split this document into smaller files.",
    );
  return text;
}
