import assert from "node:assert/strict";
import test from "node:test";
import { resourceSaveCommand } from "../lib/space/resource-model.ts";

const draft = { kind: "document", title: "Notes", body: "Updated content", status: "todo" };
const previous = { ...draft, id: "document-id", revision: 4, project: "project-id", folder: "folder-id" };

test("document folder changes do not change the project relationship", () => {
  const command = resourceSaveCommand({ ...draft, folder: "other-folder" }, previous);
  assert.equal(command.folder, "other-folder");
  assert.equal(Object.hasOwn(command, "project"), false);
  assert.equal(command.resource_id, "document-id");
  assert.equal(command.expected_revision, 4);
  assert.equal(resourceSaveCommand({ ...draft, folder: "folder-id", project: "other-project" }, previous).project, "other-project");
});

test("content and project edits omit folder so existing or archived membership is preserved", () => {
  for (const change of [draft, { ...draft, project: "other-project" }, { ...draft, folder: undefined }]) {
    const command = resourceSaveCommand(change, previous);
    assert.equal(Object.hasOwn(command, "folder"), false);
    assert.equal(command.body, "Updated content");
  }
});

test("Unfiled explicitly clears folder while leaving project alone", () => {
  const command = resourceSaveCommand({ ...draft, folder: "" }, previous);
  assert.equal(command.folder, null);
  assert.equal(Object.hasOwn(command, "project"), false);
  assert.equal(resourceSaveCommand({ ...draft, folder: "folder-id", project: "" }, previous).project, null);
});

test("new documents can select a folder without creating or requiring a project", () => {
  const command = resourceSaveCommand({ ...draft, folder: "folder-id" });
  assert.equal(command.resource_id, null);
  assert.equal(command.expected_revision, 0);
  assert.equal(command.folder, "folder-id");
  assert.equal(Object.hasOwn(command, "project"), false);
});

test("task save commands do not contain the document-only folder field", () => {
  const command = resourceSaveCommand({ ...draft, kind: "task", folder: "folder-id" });
  assert.equal(Object.hasOwn(command, "folder"), false);
});
