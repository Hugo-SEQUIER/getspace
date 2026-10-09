import test from "node:test";
import assert from "node:assert/strict";
import {
  createGraphLayout,
  createGraphGeometry,
  placeGraphGroups,
  hitGraphNode,
  nodeScreenPoint,
  fitCamera,
  limitCameraPan,
  minimumGraphScale,
  worldPoint,
  zoomAt,
} from "../lib/space/graph-layout.ts";
import { memoryNeighborhood, vectorEdges } from "../lib/space/memory-model.ts";
import { makeDemoVectors } from "./support/sample-workspace.ts";

const items = [
  { id: "a", classification: { category_ids: ["abc"] }, creator: "Agent 1" },
  { id: "b", classification: { category_ids: ["abc"] }, creator: "Agent 2" },
  { id: "c", classification: { category_ids: ["abc"], project_ids: ["cd"] } },
  { id: "d", classification: { category_ids: ["de"], project_ids: ["cd"] } },
  { id: "e", classification: { category_ids: ["de"] } },
];

test("project anchors separate grouped nodes without changing the default layout", () => {
  const groups = placeGraphGroups([
    { id: "a", name: "Alpha", count: 2, ids: ["a1", "a2"] },
    { id: "b", name: "Beta", count: 1, ids: ["b1"] },
  ]);
  assert.ok(groups[0].x + groups[0].radius < groups[1].x - groups[1].radius);
  const model = createGraphLayout([{ id: "a1" }, { id: "a2" }, { id: "b1" }], [], new Map(), undefined, groups);
  try {
    model.simulation.tick(100);
    assert.equal(model.byId.get("a1").group, "a");
    assert.equal(model.byId.get("b1").group, "b");
    assert.ok(model.byId.get("a1").x < model.byId.get("b1").x);
  } finally {
    model.simulation.stop();
  }
  assert.deepEqual(createGraphGeometry([{ id: "plain" }], []).groups, []);
});

test("isolated memories remain visible, finite and selectable in mixed and edgeless layouts", () => {
  for (const memories of [[...items, { id: "orphan" }], [{ id: "orphan" }], [{ id: "orphan" }, { id: "other" }]]) {
    const model = createGraphLayout(memories, vectorEdges(memories));
    try {
      model.simulation.tick(180);
      assert.equal(model.nodes.length, memories.length);
      const orphan = model.byId.get("orphan");
      assert.equal(orphan.degree, 0);
      assert.ok(Number.isFinite(orphan.x) && Number.isFinite(orphan.y));
      const camera = fitCamera(model.nodes, 1200, 800);
      const point = nodeScreenPoint(orphan, camera, 1200, 800);
      assert.equal(hitGraphNode(model.nodes, point, camera, 1200, 800)?.id, "orphan");
    } finally {
      model.simulation.stop();
    }
  }
});

test("unlinked memories stay near the connected network", () => {
  const memories = makeDemoVectors();
  const model = createGraphLayout(memories, vectorEdges(memories));
  try {
    model.simulation.tick(180);
    const isolated = model.nodes.filter((node) => node.degree === 0);
    const linked = model.nodes.filter((node) => node.degree > 0);
    assert.ok(isolated.length > 0 && linked.length > 0);
    const radius = (node) => Math.hypot(node.x, node.y);
    const outerLinked = Math.max(...linked.map(radius));
    assert.ok(Math.max(...isolated.map(radius)) < outerLinked * 1.2);
  } finally {
    model.simulation.stop();
  }
});

test("Canvas hit testing follows pan and zoom, picks the nearest memory, and ignores empty space", () => {
  const geometry = createGraphGeometry(
    [{ id: "a" }, { id: "b" }],
    [],
    new Map([
      ["a", { x: -160, y: 130 }],
      ["b", { x: 85, y: -50 }],
    ]),
  );
  for (const k of [0.12, 0.5, 1, 5]) {
    const camera = { x: 240, y: -120, k };
    const point = nodeScreenPoint(geometry.nodes[0], camera, 1200, 800);
    assert.equal(
      hitGraphNode(geometry.nodes, point, camera, 1200, 800)?.id,
      "a",
    );
    assert.equal(
      hitGraphNode(
        geometry.nodes,
        { x: point.x + 4, y: point.y },
        camera,
        1200,
        800,
      )?.id,
      "a",
    );
    assert.equal(
      hitGraphNode(geometry.nodes, { x: -1000, y: -1000 }, camera, 1200, 800),
      undefined,
    );
  }
  const close = createGraphGeometry(
    [{ id: "a" }, { id: "b" }],
    [],
    new Map([
      ["a", { x: 0, y: 0 }],
      ["b", { x: 3, y: 0 }],
    ]),
  );
  assert.equal(
    hitGraphNode(
      close.nodes,
      { x: 603, y: 400 },
      { x: 0, y: 0, k: 1 },
      1200,
      800,
    )?.id,
    "b",
  );
});

test("force layout moves connected memories without changing saved data or pinning them permanently", () => {
  const saved = structuredClone(items);
  const model = createGraphLayout(items, vectorEdges(items));
  try {
    model.simulation.tick(180);
    const a = model.byId.get("a"),
      b = model.byId.get("b");
    const before = { x: b.x, y: b.y };
    a.fx = a.x + 250;
    a.fy = a.y + 130;
    model.simulation.alpha(0.8).tick(100);
    assert.ok(
      Math.hypot(b.x - before.x, b.y - before.y) > 20,
      "Dragging a node pulls its neighbor",
    );
    assert.equal(a.x, a.fx);
    const heldX = a.x;
    a.fx = a.fy = null;
    model.simulation.alpha(0.5).tick(100);
    assert.ok(
      Math.abs(a.x - heldX) > 20,
      "Released nodes return to the simulation",
    );
    assert.ok(
      model.nodes.every(
        (node) => Number.isFinite(node.x) && Number.isFinite(node.y),
      ),
    );
    assert.deepEqual(items, saved);
    assert.equal(items[0].x, undefined);
    assert.ok(
      model.nodes.every((node) => items.every((item) => node !== item)),
    );
  } finally {
    model.simulation.stop();
  }
});

test("rebuilding after filtering reuses positions, and distance controls affect actual layout", () => {
  const previous = new Map([["a", { x: 215, y: -42 }]]);
  const model = createGraphLayout(items, vectorEdges(items), previous);
  try {
    assert.equal(model.byId.get("a").x, 215);
    assert.equal(model.byId.get("a").y, -42);
    const averageLength = () =>
      model.links.reduce(
        (sum, link) =>
          sum +
          Math.hypot(
            link.source.x - link.target.x,
            link.source.y - link.target.y,
          ),
        0,
      ) / model.links.length;
    model.configure({ repulsion: 20, distance: 20 });
    model.simulation.alpha(1).tick(200);
    const close = averageLength();
    model.configure({ repulsion: 20, distance: 110 });
    model.simulation.alpha(1).tick(200);
    assert.ok(averageLength() > close * 1.5);
  } finally {
    model.simulation.stop();
  }
});

test("zoom preserves the point under the cursor, including scale limits", () => {
  const camera = { x: 120, y: -70, k: 0.72 },
    point = { x: 241, y: 382 };
  const anchor = worldPoint(point, camera, 1200, 800);
  for (const scale of [0.01, 0.5, 2, 100]) {
    const zoomed = zoomAt(camera, point, scale, 1200, 800);
    const after = worldPoint(point, zoomed, 1200, 800);
    assert.ok(Math.abs(after.x - anchor.x) < 1e-9);
    assert.ok(Math.abs(after.y - anchor.y) < 1e-9);
    assert.ok(zoomed.k >= 0.12 && zoomed.k <= 5);
  }
});

test("fit keeps a normal graph within desktop and mobile viewports", () => {
  const points = [
    { x: -420, y: -630 },
    { x: 510, y: 580 },
  ];
  for (const [width, height] of [
    [1210, 940],
    [390, 700],
  ]) {
    const camera = fitCamera(points, width, height);
    for (const node of points) {
      const x = width / 2 + camera.x + node.x * camera.k;
      const y = height / 2 + camera.y + node.y * camera.k;
      assert.ok(x > 0 && x < width);
      assert.ok(y > 0 && y < height);
    }
  }
  assert.deepEqual(fitCamera([], 390, 700), { x: 0, y: 0, k: 1 });
});

test("camera keeps visible nodes in reach while panning and zooming out", () => {
  for (const [width, height] of [[1200, 800], [390, 700]]) {
    const nodes = [{ x: -160, y: -90 }, { x: 180, y: 120 }];
    const minimum = minimumGraphScale(nodes, width, height);
    const fitted = fitCamera(nodes, width, height);
    assert.ok(minimum >= 0.12 && minimum < fitted.k);
    assert.equal(zoomAt(fitted, { x: 25, y: 35 }, 0, width, height, minimum).k, minimum);
    for (const offset of [-100000, 100000]) {
      const bounded = limitCameraPan({ x: offset, y: offset, k: minimum }, nodes, width, height);
      assert.ok(bounded.x > -100000 && bounded.x < 100000);
      assert.ok(bounded.y > -100000 && bounded.y < 100000);
      const left = width / 2 + bounded.x + nodes[0].x * bounded.k;
      const right = width / 2 + bounded.x + nodes[1].x * bounded.k;
      const top = height / 2 + bounded.y + nodes[0].y * bounded.k;
      const bottom = height / 2 + bounded.y + nodes[1].y * bounded.k;
      assert.ok(right >= width / 4 - 1e-6 && left <= width * 3 / 4 + 1e-6);
      assert.ok(bottom >= height / 4 - 1e-6 && top <= height * 3 / 4 + 1e-6);
    }
    assert.deepEqual(limitCameraPan(fitted, [], width, height), fitted);
  }
});

test("local graph follows undirected connections by depth across creators, respecting current filters", () => {
  assert.deepEqual(
    memoryNeighborhood(items, "a", 0).map((item) => item.id),
    ["a"],
  );
  assert.deepEqual(
    memoryNeighborhood(items, "a", 1).map((item) => item.id),
    ["a", "b", "c"],
  );
  assert.deepEqual(
    memoryNeighborhood(items, "a", 2).map((item) => item.id),
    ["a", "b", "c", "d"],
  );
  assert.deepEqual(memoryNeighborhood(items, "a", 3), items);
  assert.deepEqual(
    memoryNeighborhood(
      items.filter((item) => item.id !== "c"),
      "a",
      3,
    ).map((item) => item.id),
    ["a", "b"],
  );
  assert.deepEqual(memoryNeighborhood(items, "missing", 2), []);
  assert.equal(memoryNeighborhood(items, "a", 1)[1], items[1]);
});
