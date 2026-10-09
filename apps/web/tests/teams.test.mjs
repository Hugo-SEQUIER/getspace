import assert from "node:assert/strict";
import test from "node:test";
import {
  invitationToken,
  scopedAgentPath,
  selectWorkspace,
} from "../lib/space/teams.ts";

test("accepts only complete invitation tokens and fragment links", () => {
  const token = "aB_".repeat(14) + "x";
  assert.equal(invitationToken(token), token);
  assert.equal(
    invitationToken(`https://space.example/app/settings/team#invite=${token}`),
    token,
  );
  assert.equal(
    invitationToken(`https://space.example/app/settings/team?invite=${token}`),
    null,
  );
  assert.equal(invitationToken("bad-token"), null);
  assert.equal(invitationToken(`${token}more`), null);
});

test("a stale selection falls back to the signed-in account's personal Space", () => {
  const personal = { id: "personal", name: "Personal", mcp_url: "" };
  const shared = { id: "shared", name: "Team", mcp_url: "" };
  assert.equal(selectWorkspace([personal, shared], "shared", personal), shared);
  assert.equal(
    selectWorkspace([personal], "removed-space", personal),
    personal,
  );
});

test("legacy agent routes follow the active Space without overriding explicit consent scope", () => {
  assert.equal(scopedAgentPath("agents", "team"), "agents?workspace_id=team");
  assert.equal(
    scopedAgentPath("agent-identities/agent?x=1", "team"),
    "agent-identities/agent?x=1&workspace_id=team",
  );
  assert.equal(
    scopedAgentPath("agent-identities?workspace_id=private", "team"),
    "agent-identities?workspace_id=private",
  );
  assert.equal(
    scopedAgentPath("spaces/private/resources", "team"),
    "spaces/private/resources",
  );
  assert.equal(scopedAgentPath("workspace", "team"), "workspace");
});
