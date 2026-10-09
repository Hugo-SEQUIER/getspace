import type { Resource, ResourceView } from "./types";

/** The task or document status an agent sets when it cannot continue without the person. */
export const NEEDS_YOU = "needs_you";

export type Attention = {
  /** Items an agent flagged as waiting on the person, newest first. */
  needsYou: Resource[];
  /** Items whose latest revision was written by someone else and not opened since, newest first. */
  unseen: Resource[];
};

// Parameters: the seen marks the API returned for the owner.
// What it does: indexes them by resource for cheap lookups.
// Output: resource ID to the revision last opened.
export function seenRevisions(items: ResourceView[]): Record<string, number> {
  const result: Record<string, number> = {};
  for (const item of items) result[item.resource_id] = Math.max(result[item.resource_id] ?? 0, item.seen_revision);
  return result;
}

// Parameters: existing marks and items just opened or marked.
// What it does: raises each mark, never lowering one.
// Output: the merged marks.
export function mergeSeen(seen: Record<string, number>, items: { id: string; revision: number }[]): Record<string, number> {
  const result = { ...seen };
  for (const item of items) result[item.id] = Math.max(result[item.id] ?? 0, item.revision);
  return result;
}

// Parameters: an item and the person's seen marks.
// What it does: an item is unseen when its latest author is not the person and the current
// revision is newer than the one they last opened. The person's own writes are always seen.
// Output: whether the item should be shown as new since they looked.
export function isUnseen(resource: Resource, seen: Record<string, number>): boolean {
  return resource.author.toLowerCase() !== "you" && resource.revision > (seen[resource.id] ?? 0);
}

const newestFirst = (a: Resource, b: Resource) => b.updated_at.localeCompare(a.updated_at);

// Parameters: every loaded document and task, and the person's seen marks.
// What it does: splits out what waits on the person and what changed without them. Generated
// memory never appears here, and a flagged item is listed once, under "needs you".
// Output: both lists, newest first.
export function attentionItems(resources: Resource[], seen: Record<string, number>): Attention {
  const visible = resources.filter(resource => resource.recall?.category !== "memory");
  const needsYou = visible.filter(resource => resource.status === NEEDS_YOU).sort(newestFirst);
  const unseen = visible
    .filter(resource => resource.status !== NEEDS_YOU && isUnseen(resource, seen))
    .sort(newestFirst);
  return { needsYou, unseen };
}

// Parameters: unseen items and the project names of the Space.
// What it does: counts unseen items per project; items without a project count as "No project".
// Output: labelled counts, largest first.
export function unseenByProject(unseen: Resource[], projectNames: Map<string, string>): { label: string; count: number }[] {
  const counts = new Map<string, number>();
  for (const resource of unseen) {
    const label = (resource.project && projectNames.get(resource.project)) || "No project";
    counts.set(label, (counts.get(label) ?? 0) + 1);
  }
  const unassigned = (label: string) => Number(label === "No project");
  return [...counts].map(([label, count]) => ({ label, count }))
    .sort((a, b) => b.count - a.count || unassigned(a.label) - unassigned(b.label) || a.label.localeCompare(b.label));
}
