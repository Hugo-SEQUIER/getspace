import assert from "node:assert/strict";
import { test } from "node:test";
import { affectedReads } from "../lib/read-invalidation.ts";

test("task column commands only invalidate the same Space's column catalog", () => {
  const affected = affectedReads("spaces/one/tasks/columns");
  assert.equal(affected("spaces/one/tasks/columns"), true);
  for (const path of ["spaces/two/tasks/columns", "spaces/one/items?kind=project", "spaces/one/resources?kind=task", "spaces/one/overview", "agents"])
    assert.equal(affected(path), false, path);
});

test("status commands refresh resources, memory, overview and task columns without reloading catalogs", () => {
  const affected = affectedReads("spaces/one/tasks/task-id/status");
  // A new status becomes a task column, so the columns are read again.
  for (const path of ["spaces/one/resources?kind=task", "spaces/one/resources/task-id", "spaces/one/memories?limit=50", "spaces/one/overview", "spaces/one/tasks/columns"])
    assert.equal(affected(path), true, path);
  for (const path of ["spaces/two/resources?kind=task", "spaces/two/tasks/columns", "spaces/one/items?kind=project", "spaces/one/memory/jobs", "agents?workspace_id=one"])
    assert.equal(affected(path), false, path);
});

test("existing writes retain their invalidation boundaries", () => {
  assert.equal(affectedReads("spaces/one/items")("spaces/one/items?kind=project"), true);
  assert.equal(affectedReads("spaces/one/items")("spaces/two/items?kind=project"), false);
  assert.equal(affectedReads("spaces/one/memory/jobs")("spaces/one/memories"), false);
  assert.equal(affectedReads("spaces/one/resources")("spaces/one/resources/task-id"), true);
  assert.equal(affectedReads("agents?workspace_id=one")("agents?workspace_id=two"), false);
});


test("folder item saves and visibility changes refresh folder lists and workspace counts", () => {
  for (const command of ["spaces/one/items", "spaces/one/items/folder-id/visibility"]) {
    const affected = affectedReads(command);
    for (const read of ["spaces/one/items?kind=folder&deleted=false", "spaces/one/items?kind=folder&deleted=true", "spaces/one/overview"])
      assert.equal(affected(read), true, `${command}: ${read}`);
    for (const read of ["spaces/two/items?kind=folder&deleted=false", "spaces/two/overview", "spaces/one/tasks/columns"])
      assert.equal(affected(read), false, `${command}: ${read}`);
  }
});

test("API key commands leave cached workspace content untouched", () => {
  for (const command of ["spaces/one/api-keys", "spaces/one/api-keys/key-id"]) {
    const affected = affectedReads(command);
    for (const read of ["spaces/one/resources?kind=task", "spaces/one/items?kind=project", "spaces/one/overview", "spaces/one/memories"])
      assert.equal(affected(read), false, `${command}: ${read}`);
  }
});

test("a new connection token refreshes connections and identities, never Space content", () => {
  const affected = affectedReads("agent-tokens");
  for (const path of ["agents?workspace_id=one", "agent-identities?workspace_id=one", "agents"])
    assert.equal(affected(path), true, path);
  for (const path of ["spaces/one/resources?kind=task", "spaces/one/items?kind=project", "workspaces"])
    assert.equal(affected(path), false, path);
});
