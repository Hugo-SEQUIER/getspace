import assert from "node:assert/strict";
import test from "node:test";
import { canRedeliver, webhookEndpoint, webhookPath, webhookProxyAllowed, webhookTransition } from "../lib/space/webhooks.ts";

const space = "11111111-1111-4111-8111-111111111111";
const subscriptionId = "22222222-2222-4222-8222-222222222222";
const deliveryId = "33333333-3333-4333-8333-333333333333";
const path = webhookPath(space);

test("proxy allows only the scoped webhook routes and their supported methods", () => {
  const supported = [
    [path, "GET"], [path, "POST"],
    [`${path}/${subscriptionId}`, "POST"],
    [`${path}/${subscriptionId}/deliveries`, "GET"],
    [`${path}/${subscriptionId}/deliveries/${deliveryId}/redeliver`, "POST"],
  ];
  for (const [route, method] of supported) assert.equal(webhookProxyAllowed(route, method), true, `${method} ${route}`);
  for (const route of [path, `${path}/${subscriptionId}`, `${path}/${subscriptionId}/deliveries`, `${path}/${subscriptionId}/deliveries/${deliveryId}/redeliver`]) {
    for (const method of ["DELETE", "PATCH", "PUT", "HEAD"]) assert.equal(webhookProxyAllowed(route, method), false);
  }
  for (const [route, method] of [
    [`webhooks`, "GET"], [`spaces/${space}/webhooks/`, "GET"],
    [`spaces/${"-".repeat(36)}/webhooks`, "GET"],
    [`${path}/${subscriptionId}`, "GET"], [`${path}/${subscriptionId}/deliveries`, "POST"],
    [`${path}/${subscriptionId}/deliveries/${deliveryId}`, "GET"],
    [`${path}/${subscriptionId}/deliveries/${deliveryId}/redeliver`, "GET"],
    [`${path}/${subscriptionId}/deliveries/${deliveryId}/redeliver/extra`, "POST"],
    [`spaces/${space}/resources/${subscriptionId}/webhooks`, "POST"],
    [`${path}/not-a-uuid`, "POST"], [`${path}/${subscriptionId}/secret`, "GET"],
  ]) assert.equal(webhookProxyAllowed(route, method), false, `${method} ${route}`);
});

test("endpoint validation requires HTTPS on port 443 without credentials, queries or fragments", () => {
  assert.equal(webhookEndpoint(" https://receiver.example.com/events "), "https://receiver.example.com/events");
  assert.equal(webhookEndpoint("https://receiver.example.com:443/events"), "https://receiver.example.com/events");
  for (const endpoint of [
    "http://receiver.example.com/events", "javascript:alert(1)", "not a URL",
    "https://user:password@receiver.example.com", "https://receiver.example.com/#secret",
    "https://receiver.example.com/events?token=private", "https://receiver.example.com:8443/events",
  ]) assert.throws(() => webhookEndpoint(endpoint));
});

test("transitions use only current database select fields and their exact options", () => {
  const database = { fields: [
    { id: "stage", name: "Stage", type: "select", options: ["New", "Won"] },
    { id: "contact", name: "Contact", type: "text" },
    { id: "empty-select", name: "Empty", type: "select", options: [] },
  ] };
  assert.equal(webhookTransition(database, "", ""), null);
  assert.deepEqual(webhookTransition(database, "stage", "Won"), { field_id: "stage", to: "Won" });
  for (const [field, to] of [["contact", "Won"], ["missing", "Won"], ["stage", "won"], ["stage", ""], ["empty-select", "Won"]])
    assert.throws(() => webhookTransition(database, field, to), /current select field/);
});

test("redelivery requires an active subscription and a terminal delivery status", () => {
  for (const status of ["delivered", "failed", "queued", "processing"]) {
    assert.equal(canRedeliver({ paused: false }, { status }), ["delivered", "failed"].includes(status));
    assert.equal(canRedeliver({ paused: true }, { status }), false);
    assert.equal(canRedeliver({ paused: false, removed_at: "2026-09-30T12:00:00Z" }, { status }), false);
  }
});
