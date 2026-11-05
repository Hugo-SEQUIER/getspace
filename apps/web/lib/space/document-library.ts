import { isUnseen } from "./attention.ts";
import type { DocumentFolder, OrganizationItem, Project, Resource } from "./types";

/** The directory lists 50 documents at a time, like the other long lists. */
export const DOCUMENT_PAGE_SIZE = 50;
/** How many recently opened documents the directory shows above the folders. */
export const RECENTLY_OPENED_LIMIT = 4;

export type DocumentKind = "page" | "file" | "handoff";
export type DocumentFacet = "all" | "pages" | "files" | "handoffs";
export type DocumentSortKey = "updated" | "name" | "author";
export type DocumentSort = { key: DocumentSortKey; descending: boolean };

export const documentFacets: { value: DocumentFacet; label: string }[] = [
  { value: "all", label: "All" },
  { value: "pages", label: "Pages" },
  { value: "files", label: "Files" },
  { value: "handoffs", label: "Handoffs" },
];

export const defaultDocumentSort: DocumentSort = { key: "updated", descending: true };

// Parameters: a document summary.
// What it does: tells a session handoff, a document that keeps an original file, and a
//   written page apart; the directory shows a different glyph and facet for each.
// Output: the kind.
export function documentKind(document: Resource): DocumentKind {
  if (document.recall?.category === "handoff") return "handoff";
  if (document.file) return "file";
  return "page";
}

// Parameters: every loaded resource.
// What it does: keeps the documents the directory lists; generated memory has its own page.
// Output: the listed documents.
export function listedDocuments(resources: readonly Resource[]): Resource[] {
  return resources.filter((resource) => resource.kind === "document" && resource.recall?.category !== "memory");
}

/** Where the directory is: the whole Space, the documents without a folder, one folder or one section. */
export type DocumentLocation =
  | { kind: "all" }
  | { kind: "unfiled" }
  | { kind: "folder"; folder: DocumentFolder; section: OrganizationItem | null }
  | { kind: "section"; section: OrganizationItem; folders: DocumentFolder[] }
  | { kind: "missing" };

// Parameters: the folder and section the address names, and the active folders and sections.
// What it does: resolves the address to a location; an archived or unknown container is "missing".
// Output: the location.
export function resolveLocation(
  folderId: string,
  sectionId: string,
  folders: readonly DocumentFolder[],
  sections: readonly OrganizationItem[],
): DocumentLocation {
  if (folderId === "unfiled") return { kind: "unfiled" };
  if (folderId) {
    const folder = folders.find((item) => item.id === folderId);
    if (!folder) return { kind: "missing" };
    return { kind: "folder", folder, section: sections.find((item) => item.id === folder.section_id) ?? null };
  }
  if (sectionId) {
    const section = sections.find((item) => item.id === sectionId);
    if (!section) return { kind: "missing" };
    return { kind: "section", section, folders: folders.filter((item) => item.section_id === section.id) };
  }
  return { kind: "all" };
}

// Parameters: a document and the active folders.
// What it does: a document whose folder was archived counts as unfiled.
// Output: the document's active folder, if any.
export function activeFolder(document: Resource, folders: readonly DocumentFolder[]): DocumentFolder | undefined {
  return document.folder ? folders.find((folder) => folder.id === document.folder) : undefined;
}

// Parameters: the listed documents, a location and the active folders.
// What it does: keeps the documents the location holds.
// Output: the documents in that location.
export function documentsInLocation(
  documents: readonly Resource[],
  location: DocumentLocation,
  folders: readonly DocumentFolder[],
): Resource[] {
  switch (location.kind) {
    case "all":
      return [...documents];
    case "unfiled":
      return documents.filter((document) => !activeFolder(document, folders));
    case "folder":
      return documents.filter((document) => activeFolder(document, folders)?.id === location.folder.id);
    case "section": {
      const ids = new Set(location.folders.map((folder) => folder.id));
      return documents.filter((document) => {
        const folder = activeFolder(document, folders);
        return !!folder && ids.has(folder.id);
      });
    }
    case "missing":
      return [];
  }
}

// Parameters: a document, the project filter ("" for every project, "unfiled" for none) and the projects.
// What it does: the optional project filter applies on top of the location.
// Output: whether the document passes the filter.
export function matchesProject(document: Resource, projectFilter: string, projects: readonly Project[]): boolean {
  if (projectFilter === "unfiled") return !(document.project && projects.some((project) => project.id === document.project));
  if (projectFilter) return document.project === projectFilter;
  return true;
}

// Parameters: documents in the current location.
// What it does: counts each facet, so the tabs show what a facet would list.
// Output: the counts.
export function facetCounts(documents: readonly Resource[]): Record<DocumentFacet, number> {
  const counts = { all: documents.length, pages: 0, files: 0, handoffs: 0 };
  for (const document of documents) {
    const kind = documentKind(document);
    if (kind === "page") counts.pages++;
    else if (kind === "file") counts.files++;
    else counts.handoffs++;
  }
  return counts;
}

export function applyFacet(documents: readonly Resource[], facet: DocumentFacet): Resource[] {
  if (facet === "all") return [...documents];
  const kind: DocumentKind = facet === "pages" ? "page" : facet === "files" ? "file" : "handoff";
  return documents.filter((document) => documentKind(document) === kind);
}

// Parameters: a document, the typed query and, when the search index answered, the ids whose
//   text matched the query.
// What it does: titles match at once; bodies match through the index.
// Output: whether the document matches.
export function matchesSearch(document: Resource, query: string, bodyMatches?: ReadonlySet<string>): boolean {
  const needle = query.trim().toLowerCase();
  if (!needle) return true;
  return document.title.toLowerCase().includes(needle) || !!bodyMatches?.has(document.id);
}

// Parameters: the current sort and the header the person pressed.
// What it does: pressing the active header flips its direction; another header starts with its
//   natural direction, newest first for dates and A to Z for names.
// Output: the next sort.
export function toggleSort(current: DocumentSort, key: DocumentSortKey): DocumentSort {
  if (current.key === key) return { key, descending: !current.descending };
  return { key, descending: key === "updated" };
}

// Parameters: documents, the sort and how an author is named.
// What it does: orders the list; ties fall back to the newest update, then the title.
// Output: a sorted copy.
export function sortDocuments(
  documents: readonly Resource[],
  sort: DocumentSort,
  author: (document: Resource) => string,
): Resource[] {
  const direction = sort.descending ? -1 : 1;
  const byUpdated = (a: Resource, b: Resource) => b.updated_at.localeCompare(a.updated_at);
  const byTitle = (a: Resource, b: Resource) => a.title.localeCompare(b.title, undefined, { sensitivity: "base" });
  return [...documents].sort((a, b) => {
    const primary = sort.key === "name" ? byTitle(a, b)
      : sort.key === "author" ? author(a).localeCompare(author(b), undefined, { sensitivity: "base" })
        : -byUpdated(a, b);
    return primary * direction || byUpdated(a, b) || byTitle(a, b);
  });
}

export type FolderSummary = {
  folder: DocumentFolder;
  count: number;
  unread: number;
  /** When a document in the folder last changed, or null for an empty folder. */
  activity: string | null;
};
export type FolderGroup = {
  /** Null for the folders outside every section. */
  section: OrganizationItem | null;
  folders: FolderSummary[];
  documents: number;
};

// Parameters: a folder, the listed documents, the active folders and the person's seen marks.
// What it does: counts the folder's documents, the unread ones, and its latest change.
// Output: the summary a tile shows.
export function folderSummary(
  folder: DocumentFolder,
  documents: readonly Resource[],
  seen: Record<string, number>,
): FolderSummary {
  const inside = documents.filter((document) => document.folder === folder.id);
  return {
    folder,
    count: inside.length,
    unread: inside.filter((document) => isUnseen(document, seen)).length,
    activity: latestActivity(inside),
  };
}

export function latestActivity(documents: readonly Resource[]): string | null {
  let latest: string | null = null;
  for (const document of documents) if (!latest || document.updated_at > latest) latest = document.updated_at;
  return latest;
}

// Parameters: the active sections and folders, the listed documents and the seen marks.
// What it does: groups folders by section, sections and folders by name, with the folders
//   outside every section last. A section with no folders is still a group, so it can be filled.
// Output: the groups the root of the directory shows.
export function folderGroups(
  sections: readonly OrganizationItem[],
  folders: readonly DocumentFolder[],
  documents: readonly Resource[],
  seen: Record<string, number>,
): FolderGroup[] {
  const byName = <T extends { name: string }>(a: T, b: T) => a.name.localeCompare(b.name, undefined, { sensitivity: "base" });
  const sectionIds = new Set(sections.map((section) => section.id));
  const summaries = (items: DocumentFolder[]) => [...items].sort(byName).map((folder) => folderSummary(folder, documents, seen));
  const groups: FolderGroup[] = [...sections].sort(byName).map((section) => ({
    section,
    folders: summaries(folders.filter((folder) => folder.section_id === section.id)),
    documents: 0,
  }));
  const loose = summaries(folders.filter((folder) => !folder.section_id || !sectionIds.has(folder.section_id)));
  if (loose.length) groups.push({ section: null, folders: loose, documents: 0 });
  for (const group of groups) group.documents = group.folders.reduce((total, item) => total + item.count, 0);
  return groups;
}

// Parameters: a document, the active folders and sections.
// What it does: names where the document is filed, section first.
// Output: the crumb parts, or none for an unfiled document.
export function folderPath(
  document: Resource,
  folders: readonly DocumentFolder[],
  sections: readonly OrganizationItem[],
): string[] {
  const folder = activeFolder(document, folders);
  if (!folder) return [];
  const section = sections.find((item) => item.id === folder.section_id);
  return section ? [section.name, folder.name] : [folder.name];
}

export type RecentlyOpened = { document: Resource; openedAt: string };

// Parameters: the listed documents, when the person last opened each resource, and a limit.
// What it does: picks the documents they came back to most recently, on any device.
// Output: newest opening first.
export function recentlyOpened(
  documents: readonly Resource[],
  openedAt: Record<string, string>,
  limit = RECENTLY_OPENED_LIMIT,
): RecentlyOpened[] {
  return documents
    .flatMap((document) => (openedAt[document.id] ? [{ document, openedAt: openedAt[document.id] }] : []))
    .sort((a, b) => b.openedAt.localeCompare(a.openedAt) || b.document.updated_at.localeCompare(a.document.updated_at))
    .slice(0, limit);
}

export function unreadDocuments(documents: readonly Resource[], seen: Record<string, number>): Resource[] {
  return documents.filter((document) => isUnseen(document, seen));
}
