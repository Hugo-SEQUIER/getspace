import assert from "node:assert/strict";
import test from "node:test";
import { serverTiming } from "../lib/server-timing.ts";

test("retains proxy and detailed backend timings, including overlapping phases", () => {
  assert.equal(serverTiming(12.34, 420, "api_total;dur=390, space_key;dur=200, object_get;dur=180"),
    "auth;dur=12.3, backend;dur=420.0, api_total;dur=390.0, space_key;dur=200.0, object_get;dur=180.0");
  assert.equal(serverTiming(10000), "auth;dur=10000.0");
});

test("drops upstream identifiers, descriptions, unknown stages and duplicate metrics", () => {
  assert.equal(serverTiming(1, 2,
    'space_key;dur=3, space_key;dur=99, user_secret;dur=1, db_read;dur=4;desc="private", auth;dur=999, decode;dur=NaN'),
    "auth;dur=1.0, backend;dur=2.0, space_key;dur=3.0");
  assert.equal(serverTiming(1, 2, "space_key;dur=3,".repeat(300)), "auth;dur=1.0, backend;dur=2.0");
});
