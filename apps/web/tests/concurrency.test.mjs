import test from "node:test";
import assert from "node:assert/strict";
import { mapConcurrent } from "../lib/space/concurrency.ts";

test("export preserves order and never exceeds four simultaneous reads", async () => {
  let active = 0, peak = 0;
  const input = Array.from({ length: 30 }, (_, i) => i);
  const output = await mapConcurrent(input, async value => {
    active++; peak = Math.max(peak, active);
    await new Promise(resolve => setTimeout(resolve, (value % 3) + 1));
    active--; return value;
  });
  assert.equal(peak, 4);
  assert.equal(active, 0);
  assert.deepEqual(output, input);
});

test("a failed read stops scheduling and prevents a partial successful export", async () => {
  const called = [];
  await assert.rejects(mapConcurrent([0, 1, 2, 3, 4, 5], async value => {
    called.push(value);
    if (value === 0) throw new Error("denied");
    await new Promise(resolve => setTimeout(resolve, 2));
    return value;
  }, 2), /denied/);
  assert.deepEqual(called, [0, 1]);
});
