import test from "node:test";
import assert from "node:assert/strict";
import { deleteAgentWithConnections } from "../lib/space/agent-deletion.ts";

const agent = { id: "one", revision: 2, name: "Codexx", harness: "codex" };
const grants = [
  { client_id: "active", agent_id: "one", revoked_at: null },
  { client_id: "revoked", agent_id: "one", revoked_at: "2026-09-20" },
  { client_id: "unrelated", agent_id: "two" },
];

test("deletion revokes every connection of the agent before deleting its identity", async () => {
  const calls = [];
  await deleteAgentWithConnections(agent, grants, async (path, method) => {
    calls.push([path, method]);
    return path.startsWith("agents/")
      ? { revoked: true }
      : { deleted: true };
  });
  assert.deepEqual(calls, [
    ["agents/active", "DELETE"],
    ["agents/revoked", "DELETE"],
    ["agent-identities/one?expected_revision=2", "DELETE"],
  ]);
});

test("failed or unconfirmed Space revocation prevents deleting the identity", async () => {
  for (const result of [{ revoked: false }, {}, null]) {
    const calls = [];
    await assert.rejects(deleteAgentWithConnections(agent, grants, async (path) => {
      calls.push(path);
      if (result === null) throw new Error("Network unavailable");
      return result;
    }));
    assert.deepEqual(calls, ["agents/active"]);
  }
});

test("backend rejection and unconfirmed deletion remain errors", async () => {
  await assert.rejects(deleteAgentWithConnections(agent, [], async () => {
    throw new Error("Revoke all connections before deleting this agent.");
  }), /Revoke all/);
  await assert.rejects(deleteAgentWithConnections(agent, [], async () => ({})), /Could not delete/);
});
