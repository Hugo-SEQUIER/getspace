import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { once } from "node:events";
import { fetchBackend } from "../lib/backend-fetch.ts";

test("backend transport preserves API responses and never forwards credentials through redirects", async t => {
  const requests = [];
  const server = createServer(async (request, response) => {
    let body = "";
    for await (const chunk of request) body += chunk;
    requests.push({ path: request.url, method: request.method, authorization: request.headers.authorization, body });
    if (request.url.startsWith("/redirect/")) {
      response.writeHead(Number(request.url.split("/")[2]), { Location: "/unexpected-target" });
      response.end("Do not forward credentials here");
    } else {
      response.writeHead(request.url === "/unauthorized" ? 401 : 200, { "Content-Type": "application/json" });
      response.end(JSON.stringify({ message: "API response" }));
    }
  });
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  t.after(async () => {
    server.closeAllConnections();
    await new Promise((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
  });
  const origin = `http://127.0.0.1:${server.address().port}`;
  const init = { method: "POST", headers: { Authorization: "Bearer synthetic-test-token", "Content-Type": "application/json" }, body: JSON.stringify({ example: true }), cache: "no-store" };

  for (const [path, status] of [["/workspace", 200], ["/unauthorized", 401]]) {
    await t.test(`preserves ${status} response, method, body, and authentication`, async () => {
      const response = await fetchBackend(new URL(path, origin), init);
      assert.equal(response.status, status);
      assert.deepEqual(await response.json(), { message: "API response" });
      assert.deepEqual(requests.at(-1), { path, method: "POST", authorization: "Bearer synthetic-test-token", body: init.body });
    });
  }
  for (const status of [301, 302, 303, 307, 308]) {
    await t.test(`rejects ${status} without requesting the redirect destination`, async () => {
      const count = requests.length;
      await assert.rejects(fetchBackend(new URL(`/redirect/${status}`, origin), init), /Backend redirects are not allowed/);
      assert.equal(requests.length, count + 1);
      assert.equal(requests.at(-1).path, `/redirect/${status}`);
      assert.ok(requests.every(request => request.path !== "/unexpected-target"));
    });
  }
});
