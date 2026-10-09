export type ImportPreview = {
  records: number;
  valid_records: number;
  errors: { row: number; reason: string }[];
  namespaces: { namespace: string; records: number }[];
  previously_imported: number;
  conflict_rows: number[];
};

export type MemoryImportJob = {
  id: string;
  status: "queued" | "processing" | "ready" | "failed";
  total: number;
  completed: number;
  created_at: string;
  error_code: string | null;
};

// Stored embeddings stay in the user's file. Only memory text and provenance
// travel to the normal authenticated API for authoritative validation.
export function prepareMemoraExport(text: string) {
  if (new TextEncoder().encode(text).length > 25 * 1024 * 1024)
    throw new Error("Choose a Memora JSON export smaller than 25 MB.");
  let data;
  try {
    data = JSON.parse(text.replace(/^\uFEFF/, ""), (_key, value) => {
      if (typeof value === "number" && !Number.isFinite(value)) throw new Error("Non-finite number");
      return value;
    });
  } catch {
    throw new Error("This JSON could not be read. Choose a Memora export.");
  }
  // JSON.parse discards duplicate keys. Check the original tokens before
  // serializing so ambiguous source records cannot silently lose provenance.
  const stack: { keys: Set<string> | null; key: boolean }[] = [];
  for (const match of text.matchAll(/"(?:[^"\\]|\\.)*"|[{}\[\]:,]/g)) {
    const token = match[0];
    if (token === "{" || token === "[") {
      stack.push({ keys: token === "{" ? new Set() : null, key: true });
      if (stack.length > 33) throw new Error("This export contains too many nested levels.");
    } else if (token === "}" || token === "]") {
      stack.pop();
    } else {
      const parent = stack[stack.length - 1];
      if (token === "," && parent) parent.key = true;
      if (token.startsWith('"') && parent?.keys && parent.key) {
        const key = JSON.parse(token) as string;
        if (parent.keys.has(key)) throw new Error("This export contains duplicate JSON keys.");
        parent.keys.add(key);
        parent.key = false;
      }
    }
  }
  if (
    !data || !Array.isArray(data.records) || !Number.isInteger(data.count) ||
    data.count !== data.records.length || data.count < 1 || data.count > 10000
  ) throw new Error("Choose a Memora full-export or memories JSON file with matching count and records.");
  const records = data.records.map((record: unknown) => {
    if (!record || typeof record !== "object" || Array.isArray(record))
      throw new Error("Every memory record must be an object.");
    return Object.fromEntries(Object.entries(record).filter(([key]) => key !== "vector_f32_b64"));
  });
  const result = JSON.stringify({ count: records.length, records });
  if (new TextEncoder().encode(result).length > 700000)
    throw new Error("Memory metadata exceeds 700 KB. Split the export into smaller files.");
  return result;
}

export function importDestinations(preview: ImportPreview, mapping: Record<string, string>) {
  const groups = new Map<string, string[]>();
  for (const { namespace } of preview.namespaces) {
    const target = mapping[namespace];
    if (!target) throw new Error("Choose a destination Space for every namespace.");
    groups.set(target, [...(groups.get(target) ?? []), namespace]);
  }
  return groups;
}

export function importFailure(code: string | null) {
  if (code === "access_revoked") return "Access changed. Restore access before resuming.";
  if (code === "source_or_revision_changed") return "A source changed or conflicts with an earlier import. Check it before resuming.";
  return "Import stopped. Saved memories are kept; you can resume the remaining records.";
}
