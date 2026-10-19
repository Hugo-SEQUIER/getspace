import { fieldValue, type DatabaseRecord, type FieldValue, type WorkspaceDatabase } from "./databases.ts";

export function duplicateDatabaseRecord(
  database: WorkspaceDatabase,
  sourceId: string,
  duplicateId: string = crypto.randomUUID(),
  now = new Date().toISOString(),
): WorkspaceDatabase {
  const source = database.records.find((record) => record.id === sourceId);
  if (!source)
    throw new Error("This record no longer exists. Reload the database.");
  if (!duplicateId.trim())
    throw new Error("A duplicate record needs a new ID.");
  if (database.records.some((record) => record.id === duplicateId))
    throw new Error("This record ID already exists. Try duplicating again.");
  if (database.records.length >= 1000)
    throw new Error("A database supports at most 1,000 records.");
  const suffix = " (copy)";
  let name = "";
  for (const point of source.name) {
    if (name.length + point.length > 200 - suffix.length) break;
    name += point;
  }
  const duplicate: DatabaseRecord = {
    ...source,
    id: duplicateId,
    name: name + suffix,
    values: { ...source.values },
    createdAt: now,
    updatedAt: now,
  };
  return { ...database, records: [...database.records, duplicate] };
}

export function duplicateDatabaseRecords(
  database: WorkspaceDatabase,
  sourceIds: readonly string[],
  duplicateIds: readonly string[] = [...new Set(sourceIds)].map(() => crypto.randomUUID()),
  now = new Date().toISOString(),
): WorkspaceDatabase {
  const ids = [...new Set(sourceIds)];
  if (duplicateIds.length !== ids.length)
    throw new Error("Each selected record needs a new ID.");
  if (database.records.length + ids.length > 1000)
    throw new Error("A database supports at most 1,000 records.");
  const available = new Set(database.records.map((record) => record.id));
  if (ids.some((id) => !available.has(id)))
    throw new Error("A selected record no longer exists. Reload the database before duplicating.");
  return ids.reduce((current, id, index) => duplicateDatabaseRecord(current, id, duplicateIds[index], now), database);
}

function sameRecord(current: DatabaseRecord, original: DatabaseRecord): boolean {
  const keys = Object.keys(current.values);
  return current.name === original.name &&
    current.notes === original.notes &&
    current.createdAt === original.createdAt &&
    current.updatedAt === original.updatedAt &&
    keys.length === Object.keys(original.values).length &&
    keys.every((key) => Object.hasOwn(original.values, key) && current.values[key] === original.values[key]);
}

export function deleteDatabaseRecords(
  database: WorkspaceDatabase,
  originals: readonly DatabaseRecord[],
): WorkspaceDatabase {
  const currentById = new Map(database.records.map((record) => [record.id, record]));
  const ids = new Set<string>();
  for (const original of originals) {
    const current = currentById.get(original.id);
    if (!current)
      throw new Error("A selected record no longer exists. Reload the database before deleting.");
    if (!sameRecord(current, original))
      throw new Error("A selected record changed. Review it before deleting.");
    ids.add(original.id);
  }
  return { ...database, records: database.records.filter((record) => !ids.has(record.id)) };
}

// Sets one select property on every selected record in a single snapshot; only select properties qualify.
// Records that already hold the value keep their last-edited time.
export function setDatabaseRecordsOption(
  database: WorkspaceDatabase,
  recordIds: readonly string[],
  fieldId: string,
  value: string | null,
  now = new Date().toISOString(),
): WorkspaceDatabase {
  const field = database.fields.find((item) => item.id === fieldId);
  if (field?.type !== "select")
    throw new Error("This property changed. Reload the database before editing it.");
  const next = fieldValue(field, value);
  const ids = new Set(recordIds);
  const available = new Set(database.records.map((record) => record.id));
  if ([...ids].some((id) => !available.has(id)))
    throw new Error("A selected record no longer exists. Reload the database before changing it.");
  return {
    ...database,
    records: database.records.map((record) => !ids.has(record.id) || (record.values[field.id] ?? null) === next ? record
      : { ...record, values: { ...record.values, [field.id]: next }, updatedAt: now }),
  };
}

function tsvCell(value: FieldValue | undefined): string {
  let text = value == null ? "" : String(value);
  if (typeof value === "string" && /^(?:[=+\-@\t\r\n]|\s+[=+\-@])/.test(text))
    text = "'" + text;
  return /["\t\r\n]/.test(text) ? '"' + text.replace(/"/g, '""') + '"' : text;
}

export function databaseRecordsTSV(
  database: WorkspaceDatabase,
  orderedIds: readonly string[],
): string {
  const currentById = new Map(database.records.map((record) => [record.id, record]));
  const rows = [["Name", ...database.fields.map((field) => field.name), "Record notes"]];
  for (const id of new Set(orderedIds)) {
    const record = currentById.get(id);
    if (!record)
      throw new Error("A selected record no longer exists. Reload the database before copying.");
    rows.push([record.name, ...database.fields.map((field) => record.values[field.id]), record.notes].map(tsvCell));
  }
  rows[0] = rows[0].map(tsvCell);
  return rows.map((row) => row.join("\t")).join("\n");
}
