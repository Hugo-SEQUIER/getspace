import test from "node:test";
import assert from "node:assert/strict";
import { backendTimeoutMs, isTimeout, READ_TIMEOUT_MS, timeoutMessage } from "../lib/request-timeouts.ts";

test("all synchronous memory model operations have time for the backend's 180-second budget", () => {
  for (const operation of ["build", "remember", "claims", "change"]) {
    assert.equal(backendTimeoutMs(`spaces/id/memory/${operation}`, "POST"), 210_000);
  }
  assert.equal(backendTimeoutMs("spaces/id/context", "POST"), 210_000);
  assert.equal(backendTimeoutMs("spaces/id/restores/job/commit", "POST"), 210_000);
});

test("ordinary reads, writes and queued job submissions keep their shorter deadline", () => {
  for (const path of ["workspace", "spaces/id/resources", "spaces/id/memory/jobs", "spaces/id/memory/imports"]) {
    assert.equal(backendTimeoutMs(path, "GET"), READ_TIMEOUT_MS);
    assert.equal(backendTimeoutMs(path, "POST"), READ_TIMEOUT_MS);
  }
  assert.equal(backendTimeoutMs("spaces/id/memory/claims", "GET"), READ_TIMEOUT_MS);
});

test("timeout errors are distinct from cancellation and writes warn about uncertain completion", () => {
  assert.equal(isTimeout(new DOMException("signal timed out", "TimeoutError")), true);
  assert.equal(isTimeout(new DOMException("Account changed", "AbortError")), false);
  assert.equal(isTimeout(new Error("other")), false);
  assert.match(timeoutMessage("GET"), /Loading.*retry/);
  assert.match(timeoutMessage("POST"), /Check whether your changes were saved/);
});
