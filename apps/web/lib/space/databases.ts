export const fieldTypes = [
  { type: "text", label: "Text", symbol: "Aa" },
  { type: "number", label: "Number", symbol: "#" },
  { type: "select", label: "Select", symbol: "◉" },
  { type: "date", label: "Date", symbol: "▦" },
  { type: "email", label: "Email", symbol: "@" },
  { type: "phone", label: "Phone", symbol: "☎" },
  { type: "url", label: "URL", symbol: "↗" },
  { type: "checkbox", label: "Checkbox", symbol: "✓" },
] as const;
export type FieldType = (typeof fieldTypes)[number]["type"];
export type FieldValue = string | number | boolean | null;
export type DatabaseField = {
  id: string;
  name: string;
  type: FieldType;
  options?: string[];
  optionColors?: Record<string, string>;
};
export type DatabaseRecord = {
  id: string;
  name: string;
  values: Record<string, FieldValue>;
  notes: string;
  createdAt?: string | null;
  updatedAt: string;
};
/**
 * Marks a database as a task list. Fields are named by ID so a rename keeps the setting:
 * status is a select field, done lists its closing options, planned is a date field.
 */
export type TaskTracking = { status: string; done: string[]; planned: string };
export type WorkspaceDatabase = {
  revision?: number;
  deleted?: boolean;
  id: string;
  name: string;
  description: string;
  fields: DatabaseField[];
  records: DatabaseRecord[];
  view: "table" | "board";
  groupBy: string | null;
  tasks?: TaskTracking | null;
  /** Placement in the Space map, set by agents; saves must carry it unchanged. */
  project_id?: string | null;
  section_id?: string | null;
};
export const optionColors = [
  "#62675c",
  "#456a9c",
  "#86692e",
  "#76549b",
  "#3d806b",
  "#9a536b",
];
export const optionColorNames = ["Gray", "Blue", "Yellow", "Purple", "Green", "Pink"];

export function optionColor(field: DatabaseField, value: FieldValue): string {
  const label = String(value);
  const configured = field.optionColors?.[label];
  return configured && optionColors.includes(configured)
    ? configured
    : optionColors[Math.max(0, field.options?.indexOf(label) ?? 0) % optionColors.length];
}

export type DatabaseTemplate = "blank" | "clients" | "tasks";

export function createDatabase(
  template: DatabaseTemplate,
  name: string,
  description: string,
  id: string = crypto.randomUUID(),
): WorkspaceDatabase {
  const fields: DatabaseField[] =
    template === "clients"
      ? [
          { id: `${id}-contact`, name: "Contact", type: "text" },
          { id: `${id}-email`, name: "Email", type: "email" },
          {
            id: `${id}-stage`,
            name: "Stage",
            type: "select",
            options: [
              "New",
              "Contacted",
              "Qualified",
              "Proposal",
              "Won",
              "Lost",
            ],
          },
          { id: `${id}-value`, name: "Value", type: "number" },
          { id: `${id}-followup`, name: "Next follow-up", type: "date" },
        ]
      : template === "tasks"
      ? [
          {
            id: `${id}-status`,
            name: "Status",
            type: "select",
            options: ["To do", "In progress", "Done"],
            optionColors: { "In progress": "#456a9c", Done: "#3d806b" },
          },
          { id: `${id}-planned`, name: "Planned for", type: "date" },
          { id: `${id}-due`, name: "Due", type: "date" },
        ]
      : [];
  return {
    id,
    name: name.trim(),
    description: description.trim(),
    fields,
    records: [],
    view: template === "tasks" ? "board" : "table",
    groupBy: fields.find((f) => f.type === "select")?.id ?? null,
    ...(template === "tasks"
      ? { tasks: { status: `${id}-status`, done: ["Done"], planned: `${id}-planned` } }
      : {}),
  };
}

/**
 * Parameters: a database and a proposed task setting.
 * What it does: checks the setting names a select status, known done options and a date field.
 * Output: nothing, or an error that says what to fix.
 */
export function validateTaskTracking(database: WorkspaceDatabase, tracking: TaskTracking) {
  const status = database.fields.find((field) => field.id === tracking.status);
  if (status?.type !== "select")
    throw new Error("Choose a select property for the task status.");
  if (
    !tracking.done.length ||
    new Set(tracking.done).size !== tracking.done.length ||
    tracking.done.some((option) => !status.options?.includes(option))
  )
    throw new Error("Choose at least one status option that means done.");
  if (database.fields.find((field) => field.id === tracking.planned)?.type !== "date")
    throw new Error("Choose a date property for the planned day.");
}

export function cellLink(field: DatabaseField, value: FieldValue): string | null {
  if ((field.type !== "url" && field.type !== "text") || typeof value !== "string")
    return null;
  try {
    const url = new URL(value.trim());
    return ["http:", "https:"].includes(url.protocol) ? url.href : null;
  } catch {
    return null;
  }
}

export function fieldValue(
  field: DatabaseField,
  value: FieldValue,
): FieldValue {
  if (value === null || value === "")
    return field.type === "checkbox" ? false : null;
  if (field.type === "checkbox") {
    if (typeof value !== "boolean")
      throw new Error(`${field.name} must be a checkbox value.`);
    return value;
  }
  if (field.type === "number") {
    if (typeof value === "boolean" || !Number.isFinite(Number(value)))
      throw new Error(`${field.name} must be a number.`);
    return Number(value);
  }
  if (typeof value !== "string" || value.length > 2000)
    throw new Error(`${field.name} must be text of at most 2,000 characters.`);
  if (field.type === "select" && !field.options?.includes(value))
    throw new Error(`Choose an option for ${field.name}.`);
  if (field.type === "email" && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value))
    throw new Error(`Enter a valid email for ${field.name}.`);
  if (field.type === "url") {
    try {
      if (!["http:", "https:"].includes(new URL(value).protocol))
        throw new Error();
    } catch {
      throw new Error(`${field.name} needs an http or https URL.`);
    }
  }
  if (field.type === "date") {
    const parsed = new Date(`${value}T00:00:00Z`);
    if (
      !/^\d{4}-\d{2}-\d{2}$/.test(value) ||
      !Number.isFinite(parsed.getTime()) ||
      parsed.toISOString().slice(0, 10) !== value
    )
      throw new Error(`Enter a valid date for ${field.name}.`);
  }
  return value;
}

export function validateField(
  database: WorkspaceDatabase,
  field: DatabaseField,
) {
  if (!field.name.trim() || field.name.trim().length > 80)
    throw new Error("Use a property name between 1 and 80 characters.");
  if (
    field.name.trim().toLowerCase() === "name" ||
    database.fields.some(
      (f) =>
        f.id !== field.id &&
        f.name.toLowerCase() === field.name.trim().toLowerCase(),
    )
  )
    throw new Error("A property with this name already exists.");
  if (!fieldTypes.some((f) => f.type === field.type))
    throw new Error("Choose a supported property type.");
  if (
    field.type === "select" &&
    (!field.options?.length ||
      field.options.length > 30 ||
      field.options.some((o) => !o.trim() || o.length > 80) ||
      new Set(field.options.map((o) => o.toLowerCase())).size !==
        field.options.length)
  )
    throw new Error(
      "Add between 1 and 30 distinct select options (up to 80 characters each).",
    );
  const colors = Object.entries(field.optionColors ?? {});
  if (colors.length > 30 || colors.some(([label, color]) =>
    field.type !== "select" || !field.options?.includes(label) || !optionColors.includes(color)
  ))
    throw new Error("Choose a supported color for an existing select option.");
}

/**
 * Parameters: database snapshot and the property ID.
 * What it does: derives exact option labels, refusing values the select schema cannot represent.
 * Output: distinct labels, or an error without changing the snapshot.
 */
export function selectOptionsFromRecords(database: WorkspaceDatabase, fieldId: string): string[] {
  const options = [...new Set(database.records.flatMap((record) => {
    const value = record.values[fieldId];
    return value === null || value === undefined || value === "" ? [] : [String(value)];
  }))];
  if (options.length > 30)
    throw new Error(`This property has ${options.length} distinct values. Select supports at most 30 options. No values will be changed.`);
  if (options.some((label) => !label.trim() || label.length > 80))
    throw new Error("Some values are blank labels or exceed 80 characters. Edit those values before converting. No values will be changed.");
  if (new Set(options.map((label) => label.toLowerCase())).size !== options.length)
    throw new Error("Some values differ only by letter case. Make their spelling consistent before converting. No values will be changed.");
  return options;
}

/**
 * Parameters: latest snapshot, edited field, original field and explicit option renames.
 * What it does: validates a complete conversion before returning it and detects stale schema edits.
 * Output: a new snapshot, or an error with the original data untouched.
 */
export function applyDatabaseField(
  database: WorkspaceDatabase,
  field: DatabaseField,
  original?: DatabaseField,
  optionRenames: Record<string, string> = {},
): WorkspaceDatabase {
  const current = database.fields.find((item) => item.id === field.id);
  if (original && (!current || JSON.stringify(current) !== JSON.stringify(original)))
    throw new Error("This property changed while you were editing. Reopen its editor before saving.");
  if (!original && current)
    throw new Error("This property already exists. Reopen its editor before saving.");
  validateField(database, field);
  const tracking = database.tasks;
  if (tracking && current && current.type !== field.type && [tracking.status, tracking.planned].includes(field.id))
    throw new Error(`${current.name} drives this task list. Turn off task tracking in database settings before changing its type.`);
  const next = {
    ...database,
    fields: current ? database.fields.map((item) => item.id === field.id ? field : item) : [...database.fields, field],
    groupBy: database.groupBy === field.id && field.type !== "select"
      ? null : database.groupBy ?? (field.type === "select" ? field.id : null),
    // Done statuses follow renamed options; a removed option stops counting as done.
    ...(tracking && field.id === tracking.status
      ? { tasks: { ...tracking, done: tracking.done.map((option) => optionRenames[option] ?? option).filter((option) => field.options?.includes(option)) } }
      : {}),
  };
  if (next.tasks && field.id === next.tasks.status && !next.tasks.done.length)
    throw new Error(`Keep at least one ${field.name} option that means done, or turn off task tracking first.`);
  const records = database.records.map((record) => {
    const value = record.values[field.id];
    let converted = value;
    try {
      if (typeof value === "string" && current?.type === "select" && field.type === "select" && Object.hasOwn(optionRenames, value))
        converted = optionRenames[value];
      if (current && current.type !== field.type && value !== undefined) {
        if (value === null || value === "") {
          converted = value === "" && ["number", "checkbox"].includes(field.type) ? null : value;
        } else {
          const input = ["text", "select"].includes(field.type) && typeof value !== "string" ? String(value) : value;
          converted = fieldValue(field, input);
          if (field.type === "number" && typeof value === "string" && String(converted) !== value)
            throw new Error("Use standard numeric values without extra formatting or precision before converting to number.");
        }
      }
      const updated = converted === value ? record : {
        ...record,
        values: { ...record.values, [field.id]: converted ?? null },
        updatedAt: new Date().toISOString(),
      };
      validateRecord(next, updated);
      return updated;
    } catch (error) {
      const reason = error instanceof Error ? error.message : "An incompatible value was found.";
      const guidance = field.type === "select" ? " Keep options that are still in use." : "";
      throw new Error(`Cannot save ${field.name}: ${record.name}: ${reason}${guidance} No values have been changed.`);
    }
  });
  return { ...next, records };
}

/**
 * Parameters: latest snapshot, record draft and its original saved record when editing.
 * What it does: merges only edited values and refuses same-value conflicts or duplicate creation.
 * Output: a validated snapshot that preserves unrelated concurrent edits.
 */
export function applyRecordChanges(
  database: WorkspaceDatabase,
  draft: DatabaseRecord,
  original?: DatabaseRecord,
): WorkspaceDatabase {
  const latest = database.records.find((record) => record.id === draft.id);
  if (original && !latest)
    throw new Error("This record no longer exists. Reload the database.");
  if (!original && latest)
    throw new Error("This record already exists. Open it before editing.");
  let merged = draft;
  if (original && latest) {
    merged = { ...latest, values: { ...latest.values }, updatedAt: draft.updatedAt };
    for (const key of ["name", "notes"] as const) {
      if (draft[key] === original[key]) continue;
      if (latest[key] !== original[key] && latest[key] !== draft[key])
        throw new Error(`The record ${key} changed while you were editing. Reopen the record before saving.`);
      merged[key] = draft[key];
    }
    for (const [key, value] of Object.entries(draft.values)) {
      if (value === original.values[key]) continue;
      if (!database.fields.some((field) => field.id === key))
        throw new Error("A property you edited no longer exists. Reopen the record before saving.");
      if (latest.values[key] !== original.values[key] && latest.values[key] !== value)
        throw new Error("A property value changed while you were editing. Reopen the record before saving.");
      merged.values[key] = value;
    }
  }
  const saved = validateRecord(database, merged);
  if (original && latest) {
    saved.values = { ...latest.values };
    for (const field of database.fields)
      if (Object.hasOwn(draft.values, field.id) && draft.values[field.id] !== original.values[field.id])
        saved.values[field.id] = fieldValue(field, draft.values[field.id]);
  }
  return {
    ...database,
    records: latest ? database.records.map((record) => record.id === saved.id ? saved : record) : [...database.records, saved],
  };
}

export function validateRecord(
  database: WorkspaceDatabase,
  record: DatabaseRecord,
): DatabaseRecord {
  if (!record.name.trim() || record.name.trim().length > 200)
    throw new Error("A record needs a name of at most 200 characters.");
  if (record.notes.length > 20000)
    throw new Error("Keep record notes under 20,000 characters.");
  const values = Object.fromEntries(
    database.fields.map((field) => [
      field.id,
      fieldValue(field, record.values[field.id] ?? null),
    ]),
  );
  return { ...record, name: record.name.trim(), values };
}

export function queryRecords(
  database: WorkspaceDatabase,
  query: string,
  sort: string,
  filterId: string,
  filterValue: string,
  direction: "asc" | "desc" = "asc",
) {
  const needle = query.trim().toLowerCase();
  const field = database.fields.find((field) => sort === `property:${field.id}`);
  return database.records
    .filter(
      (record) =>
        (!needle ||
          [record.name, ...Object.values(record.values)].some((value) =>
            String(value ?? "")
              .toLowerCase()
              .includes(needle),
          )) &&
        (!filterId ||
          !filterValue ||
          String(record.values[filterId] ?? "") === filterValue),
    )
    .sort((a, b) => {
      if (sort !== "name" && sort !== "created" && !field)
        return b.updatedAt.localeCompare(a.updatedAt);
      const left = sort === "created" ? a.createdAt : field ? a.values[field.id] : a.name;
      const right = sort === "created" ? b.createdAt : field ? b.values[field.id] : b.name;
      const leftEmpty = left === null || left === undefined || left === "";
      const rightEmpty = right === null || right === undefined || right === "";
      if (leftEmpty || rightEmpty)
        return Number(leftEmpty) - Number(rightEmpty);
      const comparison = sort === "created"
        ? Date.parse(String(left)) - Date.parse(String(right))
        : field?.type === "number" || field?.type === "checkbox"
        ? Number(left) - Number(right)
        : String(left).localeCompare(String(right), "en", { numeric: true, sensitivity: "base" });
      return direction === "desc" ? -comparison : comparison;
    });
}

// A database read from the API is validated before its fields are rendered or saved.
export function isWorkspaceDatabase(
  value: unknown,
): value is WorkspaceDatabase {
  try {
    if (!value || typeof value !== "object") return false;
    const db = value as WorkspaceDatabase;
    if (
      typeof db.id !== "string" ||
      !db.id ||
      typeof db.name !== "string" ||
      !db.name.trim() ||
      db.name.length > 200 ||
      typeof db.description !== "string" ||
      !Array.isArray(db.fields) ||
      !Array.isArray(db.records) ||
      !["table", "board"].includes(db.view) ||
      !(db.groupBy === null || typeof db.groupBy === "string") ||
      !(db.tasks == null || (typeof db.tasks === "object" && Array.isArray(db.tasks.done)))
    )
      return false;
    if (
      new Set(db.fields.map((f) => f.id)).size !== db.fields.length ||
      new Set(db.records.map((r) => r.id)).size !== db.records.length
    )
      return false;
    for (const field of db.fields) {
      if (
        typeof field.id !== "string" ||
        !field.id ||
        typeof field.name !== "string"
      )
        return false;
      validateField(db, field);
    }
    if (
      db.groupBy &&
      !db.fields.some((f) => f.id === db.groupBy && f.type === "select")
    )
      return false;
    if (db.tasks) validateTaskTracking(db, db.tasks);
    for (const record of db.records) {
      if (
        typeof record.id !== "string" ||
        !record.id ||
        typeof record.name !== "string" ||
        typeof record.notes !== "string" ||
        typeof record.updatedAt !== "string" ||
        !Number.isFinite(Date.parse(record.updatedAt)) ||
        (record.createdAt != null &&
          (typeof record.createdAt !== "string" || !Number.isFinite(Date.parse(record.createdAt)))) ||
        !record.values ||
        typeof record.values !== "object"
      )
        return false;
      validateRecord(db, record);
    }
    return true;
  } catch {
    return false;
  }
}
