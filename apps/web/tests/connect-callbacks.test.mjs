import assert from "node:assert/strict";
import test from "node:test";
import {
  agentCallback,
  authorizationId,
  callbackHost,
  loopbackCallback,
} from "../lib/device-flow.ts";

test("a callback keeps the app's code, state and registered query", () => {
  const value = "https://claude.example/callback?client=desktop&code=a%2Bb%2Fc&state=state%26value";
  assert.equal(agentCallback(value), value);
  assert.equal(callbackHost(value), "claude.example");
});

test("desktop apps return to loopback listeners or their own scheme", () => {
  for (const host of ["localhost", "127.0.0.1", "[::1]"]) {
    const value = `http://${host}:1455/callback?error=access_denied&state=preserved`;
    assert.equal(agentCallback(value), value);
    assert.equal(callbackHost(value), `${host}:1455`);
  }
  const app = "cursor://anysphere.cursor-mcp/oauth/callback?code=x&state=y";
  assert.equal(agentCallback(app), app);
  assert.equal(callbackHost(app), "anysphere.cursor-mcp");
  assert.equal(callbackHost("com.example.app:/oauth2redirect?code=x"), "com.example.app");
});

test("unsafe and relative callbacks never become navigation targets", () => {
  for (const value of [
    "/connect/done",
    "//claude.example/callback",
    "http://claude.example/callback",
    "http://localhost.evil.example/",
    "javascript:alert(1)",
    "JavaScript:alert(1)",
    "data:text/html,test",
    "vbscript:msgbox",
    "file:///etc/passwd",
    "https://user:password@claude.example/callback",
    "not a url",
  ]) {
    assert.throws(() => agentCallback(value), undefined, value);
    assert.equal(callbackHost(value), "your app");
  }
});

test("only a request ID shaped like a UUID reaches the consent API", () => {
  const id = "77777777-7777-4777-8777-777777777777";
  assert.equal(authorizationId(`?authorization_id=${id}`), id);
  assert.equal(authorizationId(`?authorization_id=${id.toUpperCase()}`), id);
  for (const search of ["", "?authorization_id=", "?authorization_id=../other", `?authorization_id=${id}x`, "?redirect_url=https://claude.example"])
    assert.equal(authorizationId(search), null, search);
});

test("loopback callbacks are flagged for finishing on the app's computer", () => {
  assert.equal(loopbackCallback("http://localhost:8787/callback?code=x&state=y"), true);
  assert.equal(loopbackCallback("http://[::1]:8787/callback"), true);
  assert.equal(loopbackCallback("https://claude.example/api/mcp/auth_callback?code=x"), false);
  assert.equal(loopbackCallback("cursor://anysphere.cursor-mcp/oauth/callback"), false);
  for (const value of ["http://claude.example/callback", "not a url", "javascript:alert(1)"])
    assert.equal(loopbackCallback(value), false);
});
