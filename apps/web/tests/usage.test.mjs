import assert from "node:assert/strict";
import test from "node:test";
import { capacityRows, formatBytes, usageRows } from "../lib/space/usage.ts";

const used = { retained_bytes: 3 * 1048576, items: 12, model_requests: 7 };

test("an installation without limits shows each amount used and no quota", () => {
  const rows = usageRows({ ...used, storage_limit_bytes: null, item_limit: null, model_request_limit: null });
  assert.deepEqual(rows.map(row => [row.label, row.value]), [
    ["Retained content", "3.0 MiB · No limit"],
    ["Workspace items", "12 · No limit"],
    ["Model requests this month", "7 · No limit"],
  ]);
  for (const row of rows) assert.doesNotMatch(row.value, /%|plan/i);
});

test("a limit set by the operator is shown beside the amount used", () => {
  const rows = usageRows({ ...used, storage_limit_bytes: 2 * 1073741824, item_limit: 500, model_request_limit: null });
  assert.deepEqual(rows.map(row => row.value), ["3.0 MiB / 2 GiB", "12 / 500", "7 · No limit"]);
});

test("sizes switch to GiB from one gibibyte", () => {
  assert.equal(formatBytes(1048576 * 512), "512.0 MiB");
  assert.equal(formatBytes(1073741824 * 1.5), "1.5 GiB");
});

test("the overview capacity shows no meter for a limit the installation does not set", () => {
  const rows = capacityRows({ retained_bytes: 2.5 * 1048576, storage_limit_bytes: null, model_requests: 4, model_request_limit: null });
  assert.deepEqual(rows.map(row => [row.label, row.limit, row.text]), [
    ["Storage", null, "2.5 MiB · No limit"],
    ["Model requests this month", null, "4 · No limit"],
  ]);
  const limited = capacityRows({ retained_bytes: 0, storage_limit_bytes: 512 * 1048576, model_requests: 4, model_request_limit: 200 });
  assert.deepEqual(limited.map(row => [row.limit, row.text]), [[512 * 1048576, "0.0 / 512 MiB"], [200, "4 / 200"]]);
});
