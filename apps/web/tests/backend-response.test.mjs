import assert from "node:assert/strict";
import test from "node:test";
import { backendResponseJson } from "../lib/backend-fetch.ts";

test("JSON success and error bodies remain unchanged", async () => {
  for (const status of [200, 400, 404, 429, 503]) {
    const body = status === 200 ? { items: [] } : { error: "Specific API error" };
    assert.deepEqual(await backendResponseJson(Response.json(body, { status })), body);
  }
});

test("non-JSON failures retain their status and retry metadata without exposing upstream content", async t => {
  const warnings = [];
  t.mock.method(console, "warn", (...args) => warnings.push(args));
  for (const status of [404, 429, 500, 502, 503, 504]) {
    for (const body of ["", "<html>private upstream details</html>"]) {
      const response = new Response(body, {
        status, headers: { "Content-Type": "text/html", "Retry-After": "5" },
      });
      const value = await backendResponseJson(response);
      assert.equal(response.status, status);
      assert.equal(response.headers.get("Retry-After"), "5");
      assert.match(value.error, new RegExp(`HTTP ${status}`));
      assert.ok(!JSON.stringify(value).includes("private"));
      assert.deepEqual(warnings.at(-1), ["space_backend_invalid_error_body", { status }]);
    }
  }
});

test("malformed successful responses and body transport failures are not treated as valid data", async () => {
  await assert.rejects(backendResponseJson(new Response("not JSON")), SyntaxError);
  const failure = new TypeError("interrupted body");
  const response = new Response(new ReadableStream({ start(controller) { controller.error(failure); } }), { status: 503 });
  await assert.rejects(backendResponseJson(response), error => error === failure);
});
