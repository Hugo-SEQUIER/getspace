import type { Resource } from "./types";

export type ResourceDraft = Pick<Resource, "kind" | "title" | "body" | "status"> &
  Partial<Pick<Resource, "project" | "folder" | "priority">>;

// Parameters: the edited fields and, for updates, the saved resource revision.
// What it does: builds a write while leaving omitted relationships unchanged.
// Output: the resource save command, with empty relationship selections cleared.
export function resourceSaveCommand(draft: ResourceDraft, previous?: Resource) {
  return {
    resource_id: previous?.id ?? null,
    expected_revision: previous?.revision ?? 0,
    kind: draft.kind,
    title: draft.title,
    body: draft.body ?? "",
    status: draft.status,
    ...(draft.project !== undefined ? { project: draft.project || null } : {}),
    ...(draft.kind === "document" && draft.folder !== undefined
      ? { folder: draft.folder || null }
      : {}),
    ...(draft.priority !== undefined ? { priority: draft.priority } : {}),
  };
}
