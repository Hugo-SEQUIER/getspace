import assert from "node:assert/strict";
import test from "node:test";
import {
  applyFacet, documentKind, documentsInLocation, facetCounts, folderGroups, folderPath, listedDocuments,
  matchesProject, matchesSearch, recentlyOpened, resolveLocation, sortDocuments, toggleSort, unreadDocuments,
} from "../lib/space/document-library.ts";

const sections = [
  { id: "library", kind: "section", name: "Library", description: "" },
  { id: "work", kind: "section", name: "Work", description: "" },
];
const folders = [
  { id: "billing", kind: "folder", name: "Billing", description: "Pricing and plans.", section_id: "work", revision: 1 },
  { id: "notes", kind: "folder", name: "Website notes", description: "", section_id: "work", revision: 1 },
  { id: "research", kind: "folder", name: "Research", description: "", section_id: "library", revision: 1 },
  { id: "meetings", kind: "folder", name: "Meeting notes", description: "", section_id: null, revision: 1 },
  { id: "orphan", kind: "folder", name: "Orphan", description: "", section_id: "gone", revision: 1 },
];
const document = (id, extra = {}) => ({
  id, kind: "document", title: id, body: "", status: "todo", revision: 1, updated_at: "2026-10-20T10:00:00Z", author: "you", ...extra,
});
const documents = [
  document("plan", { folder: "notes", updated_at: "2026-10-22T09:00:00Z", author: "agent", author_agent: { name: "Codex" }, revision: 3 }),
  document("pricing.xlsx", { folder: "billing", file: { id: "f", name: "pricing.xlsx", type: "xlsx", bytes: 10, sha256: "", parts: 1 } }),
  document("handoff", { folder: "billing", recall: { category: "handoff" }, author: "agent", author_agent: { name: "AIC Agent" }, updated_at: "2026-10-21T08:00:00Z", revision: 2 }),
  document("interview", {}),
  document("archived-folder-doc", { folder: "deleted-folder" }),
  document("memory", { recall: { category: "memory" } }),
];
const listed = listedDocuments(documents);

test("the directory lists documents but never generated memory", () => {
  assert.deepEqual(listed.map((item) => item.id), ["plan", "pricing.xlsx", "handoff", "interview", "archived-folder-doc"]);
  assert.equal(documentKind(listed[0]), "page");
  assert.equal(documentKind(listed[1]), "file");
  assert.equal(documentKind(listed[2]), "handoff");
});

test("an address resolves to the whole Space, Unfiled, a folder with its section, or a section with its folders", () => {
  assert.deepEqual(resolveLocation("", "", folders, sections), { kind: "all" });
  assert.deepEqual(resolveLocation("unfiled", "", folders, sections), { kind: "unfiled" });
  const folder = resolveLocation("billing", "", folders, sections);
  assert.equal(folder.kind, "folder");
  assert.equal(folder.section.name, "Work");
  const section = resolveLocation("", "work", folders, sections);
  assert.equal(section.kind, "section");
  assert.deepEqual(section.folders.map((item) => item.id), ["billing", "notes"]);
  assert.deepEqual(resolveLocation("missing", "", folders, sections), { kind: "missing" });
  assert.deepEqual(resolveLocation("", "missing", folders, sections), { kind: "missing" });
});

test("a location keeps its own documents, and an archived folder counts as unfiled", () => {
  const ids = (location) => documentsInLocation(listed, location, folders).map((item) => item.id);
  assert.deepEqual(ids({ kind: "all" }), listed.map((item) => item.id));
  assert.deepEqual(ids({ kind: "unfiled" }), ["interview", "archived-folder-doc"]);
  assert.deepEqual(ids(resolveLocation("billing", "", folders, sections)), ["pricing.xlsx", "handoff"]);
  assert.deepEqual(ids(resolveLocation("", "work", folders, sections)), ["plan", "pricing.xlsx", "handoff"]);
  assert.deepEqual(ids({ kind: "missing" }), []);
});

test("the project filter applies on top of the location", () => {
  const projects = [{ id: "launch", name: "Launch", description: "", status: "todo" }];
  const inProject = document("a", { project: "launch" });
  const inDeletedProject = document("b", { project: "old" });
  assert.equal(matchesProject(inProject, "", projects), true);
  assert.equal(matchesProject(inProject, "launch", projects), true);
  assert.equal(matchesProject(inProject, "unfiled", projects), false);
  assert.equal(matchesProject(inDeletedProject, "unfiled", projects), true);
  assert.equal(matchesProject(document("c"), "launch", projects), false);
});

test("facets count pages, files and handoffs and keep only one kind", () => {
  assert.deepEqual(facetCounts(listed), { all: 5, pages: 3, files: 1, handoffs: 1 });
  assert.deepEqual(applyFacet(listed, "files").map((item) => item.id), ["pricing.xlsx"]);
  assert.deepEqual(applyFacet(listed, "handoffs").map((item) => item.id), ["handoff"]);
  assert.equal(applyFacet(listed, "all").length, 5);
});

test("search matches titles at once and bodies through the index", () => {
  assert.equal(matchesSearch(listed[0], "  PLAN "), true);
  assert.equal(matchesSearch(listed[0], "budget"), false);
  assert.equal(matchesSearch(listed[0], "budget", new Set(["plan"])), true);
  assert.equal(matchesSearch(listed[0], ""), true);
});

test("sorting flips on the active header and starts each header in its natural direction", () => {
  const author = (item) => item.author_agent?.name ?? "You";
  assert.deepEqual(toggleSort({ key: "updated", descending: true }, "updated"), { key: "updated", descending: false });
  assert.deepEqual(toggleSort({ key: "updated", descending: true }, "name"), { key: "name", descending: false });
  assert.deepEqual(toggleSort({ key: "name", descending: false }, "updated"), { key: "updated", descending: true });
  assert.deepEqual(sortDocuments(listed, { key: "updated", descending: true }, author).map((item) => item.id).slice(0, 2), ["plan", "handoff"]);
  assert.deepEqual(sortDocuments(listed, { key: "name", descending: false }, author).map((item) => item.id),
    ["archived-folder-doc", "handoff", "interview", "plan", "pricing.xlsx"]);
  assert.deepEqual(sortDocuments(listed, { key: "author", descending: false }, author).map((item) => item.id).slice(0, 2), ["handoff", "plan"]);
});

test("folders group by section in name order, with loose folders last and counts per folder", () => {
  const groups = folderGroups(sections, folders, listed, {});
  assert.deepEqual(groups.map((group) => group.section?.name ?? null), ["Library", "Work", null]);
  const work = groups[1];
  assert.deepEqual(work.folders.map((item) => item.folder.name), ["Billing", "Website notes"]);
  assert.deepEqual(work.folders.map((item) => item.count), [2, 1]);
  assert.equal(work.documents, 3);
  assert.equal(work.folders[0].activity, "2026-10-21T08:00:00Z");
  assert.equal(work.folders[0].unread, 1, "the agent's handoff was never opened");
  assert.deepEqual(groups[2].folders.map((item) => item.folder.name), ["Meeting notes", "Orphan"]);
  assert.equal(groups[0].folders[0].activity, null);
});

test("a document's crumb names its section and folder, and nothing when unfiled", () => {
  assert.deepEqual(folderPath(listed[0], folders, sections), ["Work", "Website notes"]);
  assert.deepEqual(folderPath(document("x", { folder: "meetings" }), folders, sections), ["Meeting notes"]);
  assert.deepEqual(folderPath(listed[3], folders, sections), []);
  assert.deepEqual(folderPath(listed[4], folders, sections), []);
});

test("recently opened orders by the last opening and keeps to the limit", () => {
  const opened = { plan: "2026-10-22T10:00:00Z", interview: "2026-10-22T11:00:00Z", handoff: "2026-10-20T10:00:00Z" };
  assert.deepEqual(recentlyOpened(listed, opened).map((item) => item.document.id), ["interview", "plan", "handoff"]);
  assert.deepEqual(recentlyOpened(listed, opened, 2).map((item) => item.document.id), ["interview", "plan"]);
  assert.deepEqual(recentlyOpened(listed, {}), []);
});

test("unread documents are the ones someone else changed since the person opened them", () => {
  assert.deepEqual(unreadDocuments(listed, {}).map((item) => item.id), ["plan", "handoff"]);
  assert.deepEqual(unreadDocuments(listed, { plan: 3 }).map((item) => item.id), ["handoff"]);
  assert.deepEqual(unreadDocuments(listed, { plan: 2, handoff: 2 }).map((item) => item.id), ["plan"]);
});
