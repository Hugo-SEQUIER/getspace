import assert from "node:assert/strict";
import test from "node:test";
import { serverTiming } from "../lib/server-timing.ts";

const stages = [
  "db_transaction", "recall_total", "recall_sources", "recall_vector", "recall_lexical",
  "recall_context", "recall_assessment", "recall_auth", "embedding", "jev", "chat",
];

test("forwards numeric recall and provider timings without flattening overlapping stages", () => {
  const upstream = stages.map((stage, index) => `${stage};dur=${index + 1}.25`).join(", ");
  const expected = stages.map((stage, index) => `${stage};dur=${index + 1}.3`).join(", ");
  assert.equal(serverTiming(1, 2, upstream), `auth;dur=1.0, backend;dur=2.0, ${expected}`);
});

test("new stages still reject descriptions, malformed values and header injection", () => {
  for (const stage of stages) {
    for (const invalid of [
      `${stage};dur=3;desc="PRIVATE_QUERY"`,
      `${stage};dur=-1`, `${stage};dur=NaN`, `${stage};dur=Infinity`,
      `${stage};dur=1e3`, `${stage};dur=1.2.3`, `${stage};dur=3ms`,
      `${stage};dur=3\r\nX-Private: CREDENTIAL_CANARY`,
      `${stage};dur=${"9".repeat(400)}`,
    ]) {
      assert.equal(serverTiming(1, 2, invalid), "auth;dur=1.0, backend;dur=2.0");
    }
    assert.equal(serverTiming(1, 2, `${stage};dur=3, ${stage};dur=99, PRIVATE_QUERY;dur=1`),
      `auth;dur=1.0, backend;dur=2.0, ${stage};dur=3.0`);
  }
});
