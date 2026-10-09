import type { Resource } from "./types";
import type { ResourceDraft } from "./resource-model";

export type OnboardingFocus = "notes" | "projects" | "research";
export type OnboardingProgress = {
  version: 1;
  step: 0 | 1 | 2;
  focus: OnboardingFocus;
  status: "active" | "skipped" | "completed";
};
type ProgressStorage = Pick<Storage, "getItem" | "setItem">;
const sessionProgress = new Map<string, OnboardingProgress>();

export function initialOnboarding(): OnboardingProgress {
  return { version: 1, step: 0, focus: "notes", status: "active" };
}

export function onboardingKey(accountId: string | null | undefined, spaceId: string | undefined) {
  if (!spaceId || !accountId) return null;
  return `space-onboarding-v1:account:${encodeURIComponent(accountId)}:${encodeURIComponent(spaceId)}`;
}

export function parseOnboarding(raw: string | null): OnboardingProgress | null {
  try {
    const value = JSON.parse(raw ?? "null");
    if (value?.version !== 1 || ![0, 1, 2].includes(value.step)
      || !["notes", "projects", "research"].includes(value.focus)
      || !["active", "skipped", "completed"].includes(value.status)) return null;
    // Persist only preferences and progress, never document drafts or credentials.
    return { version: 1, step: value.step, focus: value.focus, status: value.status };
  } catch { return null; }
}

export function readOnboarding(key: string, storage?: ProgressStorage): OnboardingProgress | null {
  if (sessionProgress.has(key)) return { ...sessionProgress.get(key)! };
  try { return parseOnboarding(storage?.getItem(key) ?? null); }
  catch { return null; }
}

export function writeOnboarding(key: string, progress: OnboardingProgress, storage?: ProgressStorage): boolean {
  const safe = parseOnboarding(JSON.stringify(progress));
  if (!safe) return false;
  sessionProgress.set(key, safe);
  try {
    if (!storage) return false;
    storage.setItem(key, JSON.stringify(safe));
    return true;
  } catch { return false; }
}

export function shouldOfferOnboarding({ accountId, kind, canWrite, empty, ready }: {
  accountId: string | null | undefined; kind?: string;
  canWrite: boolean; empty: boolean; ready: boolean;
}) {
  return !!accountId && kind === "personal" && canWrite && empty && ready;
}

export async function createFirstPage(
  save: (draft: ResourceDraft) => Promise<Resource>,
  input: { title: string; notes: string },
  progress: OnboardingProgress,
) {
  const title = input.title.trim();
  if (!title || title.length > 300) throw new Error("Give your page a name of 1–300 characters.");
  if (input.notes.length > 10000) throw new Error("Keep these first notes under 10,000 characters. You can add more in your document.");
  const resource = await save({ kind: "document", title, body: input.notes, status: "todo" });
  return { resource, progress: { ...progress, step: 2, status: "completed" } as OnboardingProgress };
}

export type ChecklistStep = { id: "page" | "task" | "agent"; done: boolean };

// Parameters: saved workspace resources and the number of active agent connections.
// What it does: derives first-run progress from stored data, never from a local flag.
// Output: the three getting-started steps in display order.
export function gettingStartedSteps(resources: Pick<Resource, "kind" | "recall">[], activeConnections: number): ChecklistStep[] {
  return [
    { id: "page", done: resources.some(resource => resource.kind === "document" && resource.recall?.category !== "memory") },
    { id: "task", done: resources.some(resource => resource.kind === "task") },
    { id: "agent", done: activeConnections > 0 },
  ];
}

export function checklistHiddenKey(onboarding: string) {
  return onboarding.replace("space-onboarding-v1:", "space-checklist-hidden-v1:");
}
