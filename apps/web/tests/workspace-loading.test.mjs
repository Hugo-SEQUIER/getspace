import assert from "node:assert/strict";
import test from "node:test";
import { workspaceReadPath, workspaceReadPlan } from "../lib/space/loading.ts";

test("memory loads categories and projects without retired section catalog", () => {
  const plan = workspaceReadPlan("/app/memory");
  assert.ok(plan.supporting.includes("category"));
  assert.ok(plan.supporting.includes("project"));
  assert.ok(![...plan.required, ...plan.supporting].includes("section"));
});

test("document and project views load sections without blocking their content", () => {
  for (const path of ["/app/projects", "/app/projects/legacy-project", "/app/documents", "/app/documents/example"]) {
    const plan = workspaceReadPlan(path);
    assert.ok(plan.supporting.includes("section"));
    assert.ok(!plan.required.includes("section"));
  }
});


test("folders load as supporting data for Documents and the global document editor", () => {
  for (const path of ["/app/documents", "/app/documents/example", "/app/home", "/app/tasks", "/preview/documents"]) {
    const plan = workspaceReadPlan(path);
    assert.ok(plan.supporting.includes("folder"), path);
    assert.ok(!plan.required.includes("folder"), path);
    assert.equal(plan.supporting.filter(kind => kind === "folder").length, 1, path);
  }
  assert.equal(workspaceReadPath("one", "folder"), "spaces/one/items?kind=folder&deleted=false");
});
