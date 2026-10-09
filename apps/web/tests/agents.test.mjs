import test from "node:test";
import assert from "node:assert/strict";
import {
  groupAgentConnections,
  harnesses,
  harnessLogo,
} from "../lib/space/agents.ts";
import { existsSync } from "node:fs";

test("every named harness is selectable once and has a local logo", () => {
  const ids = harnesses.map((h) => h.id).filter((id) => id !== "other");
  assert.equal(ids.length, 21);
  assert.equal(new Set(harnesses.map((h) => h.id)).size, harnesses.length);
  for (const id of ids) {
    const logo = harnessLogo(id);
    assert.ok(logo, `Missing logo ${id}`);
    assert.ok(
      existsSync(new URL(`../public${logo}`, import.meta.url)),
      `Missing asset ${logo}`,
    );
  }
  assert.equal(harnessLogo("hermes"), "/integrations/hermes-portrait.png");
  assert.equal(harnessLogo("other"), null);
  assert.equal(harnessLogo("unknown"), null);
});

test("connections group by stable identity across reconnects and profile renames", () => {
  const agents = [
    { id: "builder", name: "Renamed builder", harness: "codex", revision: 2 },
  ];
  const grants = [
    {
      id: "old",
      client_id: "old-client",
      agent_id: "builder",
      revoked_at: "2026-09-15",
    },
    {
      id: "new",
      client_id: "new-client",
      agent_id: "builder",
      revoked_at: null,
    },
  ];
  const [group] = groupAgentConnections(agents, grants);
  assert.equal(group.id, "builder");
  assert.equal(group.agent.name, "Renamed builder");
  assert.deepEqual(group.connections, grants);
  assert.equal(groupAgentConnections(agents, grants).length, 1);
});

test("same harness does not merge distinct identities and legacy connections stay unassigned", () => {
  const agents = [
    { id: "one", name: "Builder", harness: "codex", revision: 1 },
    { id: "two", name: "Reviewer", harness: "codex", revision: 1 },
  ];
  const grants = [
    { id: "legacy", name: "Codex", agent_id: null },
    { id: "stale", name: "Codex", agent_id: "missing-profile" },
  ];
  const groups = groupAgentConnections(agents, grants);
  assert.equal(groups.length, 4);
  assert.deepEqual(
    groups.slice(0, 2).map((g) => g.id),
    ["one", "two"],
  );
  assert.ok(
    groups
      .slice(2)
      .every((g) => g.agent === null && g.connections.length === 1),
  );
});
