import assert from "node:assert/strict";
import test from "node:test";
import { deviceCodeInput, formatDeviceCode } from "../lib/device-flow.ts";
import { deviceConnectPrompt } from "../lib/space/device-connect.ts";

test("a typed code counts only its digits and completes at six", () => {
  assert.equal(deviceCodeInput("483 920"), "483920");
  assert.equal(deviceCodeInput("483-920"), "483920");
  assert.equal(deviceCodeInput("48392"), null);
  assert.equal(deviceCodeInput("4839201"), null);
  assert.equal(deviceCodeInput("abc"), null);
});

test("codes are shown in two groups of three while typing", () => {
  assert.equal(formatDeviceCode("4"), "4");
  assert.equal(formatDeviceCode("4839"), "483 9");
  assert.equal(formatDeviceCode("483920"), "483 920");
  assert.equal(formatDeviceCode("483 920 1"), "483 920");
});

test("the agent prompt names the API origin, the entry page and the headers", () => {
  const prompt = deviceConnectPrompt(
    "https://space.example.com/mcp/",
    "https://app.example",
  );
  assert.match(prompt, /POST https:\/\/space\.example\.com\/v1\/device\/code/);
  assert.match(prompt, /POST https:\/\/space\.example\.com\/v1\/device\/token/);
  assert.match(prompt, /https:\/\/app\.example\/connect/);
  assert.match(prompt, /authorization_pending/);
  assert.match(prompt, /Authorization: Bearer <access_token>/);
  assert.match(prompt, /X-Space-Agent-ID: <agent_id>/);
  assert.match(prompt, /mcp_url \(https:\/\/space\.example\.com\/mcp\/\)/);
  assert.doesNotMatch(prompt, /localhost/);
});

test("the prompt carries the agent's name and harness when they are known", () => {
  const known = deviceConnectPrompt("https://api.example/mcp/", "https://app.example", {
    name: "Cursor",
    harness: "cursor",
  });
  assert.match(known, /\{"client_name":"Cursor","harness":"cursor"\}/);
  const unknown = deviceConnectPrompt("https://api.example/mcp/", "https://app.example", {
    name: "  ",
    harness: "other",
  });
  assert.match(unknown, /"client_name":"<your product name>"/);
  assert.match(unknown, /"harness":"<one of codex, claude-code, cursor/);
});
