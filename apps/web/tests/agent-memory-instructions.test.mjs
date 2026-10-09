import test from "node:test";
import assert from "node:assert/strict";
import { spaceMemoryInstructions, spaceAgentSetupPrompt, spaceAgentUpdatePrompt, spaceSetupCheckPrompt } from "../lib/agent-memory-instructions.ts";

test("shared instructions cover recall, useful storage and safety boundaries", () => {
  for (const text of ["Before project work, retrieve", "Search before saving", "confirmed decisions", "tasks", "outcomes, blockers", "Never store secrets", "read-only access", "context, not instructions", "never claim"]) assert.ok(spaceMemoryInstructions.includes(text), text);
});
test("agent installation embeds exact instructions and preserves existing files", () => {
  assert.ok(spaceAgentSetupPrompt.endsWith(spaceMemoryInstructions));
  for (const text of ["Preserve all existing instructions", "do not replace the whole file", "duplicate blocks", "before changing anything", "other global agent settings", "without checking the resulting file", "across all projects", "Do not use or modify a project/repository AGENTS.md", "Confirm that the chosen location applies across projects"]) assert.ok(spaceAgentSetupPrompt.includes(text), text);
});
test("setup check only verifies actual reads, never writes or claimed installation", () => {
  for (const text of ["read-only", "empty result honestly", "Do not invent", "create, change, or delete", "does not prove write access", "instruction file was installed"]) assert.ok(spaceSetupCheckPrompt.includes(text), text);
});

test("update prompt preserves edits and asks before changing existing instructions", () => {
  assert.ok(spaceAgentUpdatePrompt.endsWith(spaceMemoryInstructions));
  for (const text of ["Preserve connection settings", "personal edits", "ask for approval", "duplicate blocks", "Check the resulting file", "Do not write test records", "global, user-level", "do not read or modify a project/repository AGENTS.md"]) assert.ok(spaceAgentUpdatePrompt.includes(text), text);
});
