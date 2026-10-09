import test from "node:test";
import assert from "node:assert/strict";
import {
  filterVectors,
  filterByProjectVisibility,
  groupVectorsByProject,
  projectVectorGraph,
  normalizeMemories,
  memoryNeighborhood,
  vectorEdges,
  visualVectorGraph,
} from "../lib/space/memory-model.ts";
import {
  makeDemoVectors,
  makeDemoHistories,
  demoDocuments,
} from "./support/sample-workspace.ts";

test("creator, category and search are intersecting filters on shared memory records", () => {
  const items = makeDemoVectors();
  const found = filterVectors(items, "  first release  ", "Codex", "Product");
  assert.ok(found.length > 0);
  assert.ok(
    found.every(
      (item) =>
        item.creator === "Codex" &&
        item.category === "Product" &&
        item.value.includes("first release"),
    ),
  );
  assert.ok(
    found.every((item) => items.includes(item)),
    "Filtering must not copy memory into agent-owned variants",
  );
  assert.equal(filterVectors(items, "", "", "").length, items.length);
  assert.equal(filterVectors(items, "", "missing agent", "").length, 0);
});

test("project checklist filters a union of projects without duplicating shared or global nodes", () => {
  const items = [
    { id: "alpha", classification: { scope: "project", project_ids: ["a"] } },
    { id: "beta", classification: { scope: "project", project_ids: ["b"] } },
    { id: "both", classification: { scope: "project", project_ids: ["a", "b"] } },
    { id: "old", classification: { scope: "project", project_ids: ["old"] } },
    { id: "global", classification: { scope: "global", project_ids: [] } },
    { id: "unassigned", classification: { scope: "unassigned", project_ids: [] } },
  ];
  const base = { activeProjects: new Set(["a", "b"]), global: true, unassigned: true, unavailable: true };
  const visible = visibleProjects => filterByProjectVisibility(items, { ...base, visibleProjects }).map(item => item.id);
  assert.deepEqual(visible(new Set(["a", "b"])), ["alpha", "beta", "both", "old", "global", "unassigned"]);
  assert.deepEqual(visible(new Set(["a"])), ["alpha", "both", "old", "global", "unassigned"]);
  assert.deepEqual(visible(new Set(["b"])), ["beta", "both", "old", "global", "unassigned"]);
  assert.deepEqual(filterByProjectVisibility(items, { ...base, visibleProjects: new Set(), global: false, unassigned: false, unavailable: false }), []);
  assert.deepEqual(filterByProjectVisibility(items, { ...base, visibleProjects: new Set(), global: true, unassigned: false, unavailable: false }).map(item => item.id), ["global"]);
  assert.equal(items[2].classification.project_ids.length, 2, "filtering never rewrites assignments");
});

test("project groups include each matching node, multi-project nodes, and unassigned nodes", () => {
  const projects = [
    { id: "b", name: "Beta" },
    { id: "a", name: "Alpha" },
    { id: "old", name: "Archived", deleted: true },
  ];
  const items = [
    { id: "both", classification: { project_ids: ["b", "a", "a"] } },
    { id: "alpha", classification: { project_ids: ["a"] } },
    { id: "archived", classification: { project_ids: ["old"] } },
    { id: "global", classification: { scope: "global", project_ids: [] } },
    { id: "missing" },
  ];
  const groups = groupVectorsByProject(items, projects);
  assert.deepEqual(groups.map(group => [group.name, group.items.map(item => item.id)]), [
    ["Alpha", ["both", "alpha"]],
    ["Beta", ["both"]],
    ["No active project", ["archived", "global", "missing"]],
  ]);
  assert.equal(groups[0].items[0], items[0], "groups keep the original nodes");
  assert.deepEqual(groupVectorsByProject(items.slice(1, 2), projects).map(group => group.name), ["Alpha"]);
});

test("project graph duplicates multi-project nodes without merging groups or changing original IDs", () => {
  const items = [
    { id: "shared", classification: { project_ids: ["a", "b"] } },
    { id: "a-only", classification: { project_ids: ["a"] } },
    { id: "none" },
  ];
  const graph = projectVectorGraph(items, [{ from: "shared", to: "a-only" }, { from: "shared", to: "none" }], [
    { id: "a", name: "Alpha" }, { id: "b", name: "Beta" },
  ]);
  assert.deepEqual(graph.groups.map(group => [group.name, group.count]), [["Alpha", 2], ["Beta", 1], ["No active project", 1]]);
  assert.equal(graph.nodes.length, 4);
  assert.equal(graph.edges.length, 1, "only connections within a project cluster remain");
  assert.deepEqual([...graph.originals.values()], ["shared", "a-only", "shared", "none"]);
  assert.deepEqual(items.map(item => item.id), ["shared", "a-only", "none"]);
});

test("live memory keeps exact source revision without inventing creators, coordinates, or evidence", () => {
  const memories = [
    {
      id: "memory-1",
      title: "Decisions",
      revision: 4,
      stale: true,
      author_client_id: "last-editor",
      author_agent: { id: "agent-1", name: "Build partner", harness: "codex", revision: 2 },
      recall: { sources: [{ resource_id: "source-1", revision: 2 }] },
      claims: [
        {
          id: "claim-1",
          index: "Personal use",
          value: "The first release is personal.",
          memory_type: "factual",
          edited: true,
          source: "source-1",
        },
      ],
    },
  ];
  const [item] = normalizeMemories(memories, [
    { id: "source-1", title: "Launch brief", revision: 8 },
  ]);
  assert.equal(item.id, "memory-1:claim-1");
  assert.equal(item.creator, null);
  assert.equal(item.revisionAgent.name, "Build partner");
  assert.equal(item.revisionAgent.id, "agent-1");
  assert.equal(item.sourceId, "source-1");
  assert.equal(item.sourceRevision, 2);
  assert.equal(item.sourceTitle, "Launch brief");
  assert.equal(item.stale, true);
  assert.equal(item.edited, true);
  assert.equal(item.x, undefined);
  assert.equal(item.y, undefined);
  assert.equal(item.evidence, undefined);
});

test("identical text remains separately addressable and unsourced memory remains unsourced", () => {
  const claim = {
    id: "claim",
    index: "Note",
    value: "Same text",
    memory_type: "factual",
    edited: false,
    source: "agent",
  };
  const result = normalizeMemories(
    ["a", "b"].map((id) => ({
      id,
      revision: 1,
      title: "Memory",
      stale: false,
      claims: [claim],
    })),
    [],
  );
  assert.equal(new Set(result.map((item) => item.id)).size, 2);
  assert.ok(
    result.every(
      (item) => item.sourceId === undefined && item.creator === null,
    ),
  );
});

test("creator metadata drives creator filtering independently of the last editor", () => {
  const rows = normalizeMemories([{
    id: "memory", revision: 3, title: "Oinride", stale: false,
    author_agent: { id: "codex", name: "Codex", harness: "codex", revision: 1 },
    claims: [{ id: "claim", index: "Oinride", value: "Exact source text", memory_type: "factual",
      creator: { kind: "agent", basis: "user_attributed",
        agent: { id: "hermes", name: "Hermes Agent", harness: "hermes", revision: 1 } } }],
  }], []);
  assert.equal(rows[0].creator, "Hermes Agent");
  assert.equal(rows[0].creatorBasis, "user_attributed");
  assert.equal(rows[0].revisionAgent.name, "Codex");
  assert.equal(filterVectors(rows, "", "Hermes Agent", "").length, 1);
  assert.equal(filterVectors(rows, "", "Codex", "").length, 0);
});

test("filtered graphs contain only visible memory nodes, without duplicate or self edges", () => {
  const items = [
    { id: "a", classification: { category_ids: ["shared", "shared"], project_ids: ["launch"] } },
    { id: "b", classification: { category_ids: ["shared"], project_ids: ["launch"] } },
  ];
  assert.equal(vectorEdges(items).length, 1);
  assert.deepEqual(vectorEdges(items.slice(0, 1)), []);
  const filtered = filterVectors(makeDemoVectors(), "", "Codex", "Product");
  const ids = new Set(filtered.map((item) => item.id));
  assert.ok(
    vectorEdges(filtered).every(
      (edge) => ids.has(edge.from) && ids.has(edge.to),
    ),
  );
});

test("shared creators, sources, types and legacy links never create relationships", () => {
  const items = ["a", "b", "c"].map(id => ({
    id, creator: "Codex", sourceId: "same-source", memoryId: "same-memory",
    category: "factual", title: "Same title", value: "Same content",
    parent: "a", connections: ["a", "b", "c", "missing"],
    classification: { category_ids: ["", " "], project_ids: [""], section_ids: ["shared-section"] },
  }));
  assert.deepEqual(vectorEdges(items), []);
  assert.deepEqual(filterVectors(items, "", "Codex", ""), items);
  assert.deepEqual(memoryNeighborhood(items, "b", 3), [items[1]]);
});

test("project/category links are independent of whether creators match", () => {
  const items = [
    { id: "a", creator: "Codex", classification: { category_ids: ["design"] } },
    { id: "b", creator: "Codex", classification: { category_ids: ["design"], project_ids: ["launch"] } },
    { id: "c", creator: "Hermes", classification: { project_ids: ["launch"] } },
    { id: "orphan", creator: "Codex" },
  ];
  const expected = [{ from: "a", to: "b" }, { from: "b", to: "c" }];
  assert.deepEqual(vectorEdges(items), expected);
  assert.deepEqual(vectorEdges(items.map(item => ({ ...item, creator: null }))), expected);
});

test("live graph connects shared projects and categories without inventing similarity", () => {
  const item = (id, category_ids = [], project_ids = []) => ({
    id, creator: "Codex", category: "factual", value: "Identical text",
    classification: { category_ids, project_ids },
  });
  const items = [item("a", ["sales"], ["oinride"]), item("b", ["sales"], ["oinride"]),
    item("c", ["sales"]), item("d", ["engineering"]), item("e", [], ["oinride"]), item("f")];
  const original = structuredClone(items);
  const edges = vectorEdges(items);
  assert.deepEqual(new Set(edges.map(e => `${e.from}:${e.to}`)),
    new Set(["a:b", "a:c", "b:c", "a:e", "b:e"]));
  assert.deepEqual(items, original, "graph must not mutate stored memory");
  assert.deepEqual(vectorEdges(items.filter(i => ["c", "e", "f"].includes(i.id))), []);
  assert.deepEqual(memoryNeighborhood(items, "c", 1).map(i => i.id), ["a", "b", "c"]);
  assert.deepEqual(memoryNeighborhood(items, "c", 2).map(i => i.id), ["a", "b", "c", "e"]);
  assert.deepEqual(memoryNeighborhood(items, "f", 1).map(i => i.id), ["f"]);
});

test("graph ignores removed catalog assignments and namespaces category/project IDs", () => {
  const items = [
    { id: "a", classification: { category_ids: ["shared"], project_ids: ["removed"] } },
    { id: "b", classification: { category_ids: ["shared"], project_ids: [] } },
    { id: "c", classification: { category_ids: [], project_ids: ["shared", "removed"] } },
  ];
  const edges = vectorEdges(items, { categoryIds: new Set(["shared"]), projectIds: new Set(["shared"]) });
  assert.deepEqual(edges, [{ from: "a", to: "b" }]);
  assert.deepEqual(memoryNeighborhood(items, "c", 3, edges), [items[2]]);
  assert.deepEqual(vectorEdges(items, { categoryIds: new Set(), projectIds: new Set() }), []);
});

test("visual graphs keep exact small-group links and respect the active catalog", () => {
  const items = [
    { id: "a", classification: { category_ids: ["shared", "shared"], project_ids: ["removed"] } },
    { id: "b", classification: { category_ids: ["shared"] } },
    { id: "c", classification: { category_ids: ["shared"] } },
    { id: "d", classification: { project_ids: ["shared", "removed"] } },
  ];
  const catalog = { categoryIds: new Set(["shared"]), projectIds: new Set(["shared"]) };
  const graph = visualVectorGraph(items, catalog);
  assert.equal(graph.representative, false);
  assert.deepEqual(graph.edges, vectorEdges(items, catalog));
  assert.equal(graph.edges.length, 3);
  assert.deepEqual(memoryNeighborhood(items, "d", 3, catalog), [items[3]]);
  assert.deepEqual(memoryNeighborhood(items, "a", 1, []), [items[0]], "explicit edge lists still control traversal");
});

test("1,200 claims sharing a project draw bounded links with every member represented", () => {
  const shared = Array.from({ length: 1_200 }, (_, index) => ({
    id: `claim-${index}`, classification: { project_ids: ["launch", "launch"], category_ids: ["shared"] },
  }));
  const items = [...shared, { id: "unassigned" }, { id: "removed", classification: { project_ids: ["archived"] } }];
  const before = structuredClone(items);
  const graph = visualVectorGraph(items, { projectIds: new Set(["launch"]), categoryIds: new Set(["shared"]) });
  assert.equal(graph.representative, true);
  assert.equal(graph.edges.length, 1_200, "a large shared assignment must not expand into 719,400 pairs");
  assert.equal(new Set(graph.edges.map(edge => JSON.stringify([edge.from, edge.to]))).size, graph.edges.length);
  assert.ok(graph.edges.every(edge => edge.from !== edge.to));
  assert.deepEqual(new Set(graph.edges.flatMap(edge => [edge.from, edge.to])), new Set(shared.map(item => item.id)));
  assert.deepEqual(visualVectorGraph(items).edges, graph.edges, "repeated assignments cannot duplicate links");
  assert.deepEqual(items, before, "visual sparsification never changes stored memories");
});

test("focus includes every real neighbor at depths one and two despite representative links", () => {
  const shared = Array.from({ length: 1_200 }, (_, index) => ({
    id: `claim-${index}`, classification: { project_ids: ["launch"], category_ids: index === 777 ? ["bridge"] : [] },
  }));
  const secondHop = Array.from({ length: 30 }, (_, index) => ({
    id: `related-${index}`, classification: { category_ids: ["bridge"], project_ids: index === 17 ? ["next"] : [] },
  }));
  const thirdHop = { id: "third-hop", classification: { project_ids: ["next"] } };
  const items = [...shared, ...secondHop, thirdHop, { id: "unrelated" }];
  const catalog = { projectIds: new Set(["launch", "next"]), categoryIds: new Set(["bridge"]) };
  const graph = visualVectorGraph(items, catalog);
  assert.ok(graph.edges.length <= 1_233);
  assert.equal(graph.edges.filter(edge => edge.from === "claim-0" || edge.to === "claim-0").length, 2);
  assert.deepEqual(memoryNeighborhood(items, "claim-0", 0, catalog), [shared[0]]);
  assert.deepEqual(memoryNeighborhood(items, "claim-0", 1, catalog), shared);
  assert.deepEqual(memoryNeighborhood(items, "claim-0", 2, catalog), [...shared, ...secondHop]);
  assert.deepEqual(memoryNeighborhood(items, "claim-0", 3, catalog), [...shared, ...secondHop, thirdHop]);
  assert.deepEqual(memoryNeighborhood(items.filter(item => item.id !== "claim-777"), "claim-0", 3, catalog), shared.filter(item => item.id !== "claim-777"));
});

test("membership traversal matches exact graph neighborhoods for overlapping active assignments", () => {
  const items = Array.from({ length: 72 }, (_, index) => ({
    id: `node-${index}`,
    classification: {
      category_ids: [String(index % 7), String((index * 3) % 11), "removed", " "],
      project_ids: [String(index % 4), String((index * 5) % 13), "removed"],
    },
  }));
  const catalog = { categoryIds: new Set(["0", "2", "5"]), projectIds: new Set(["1", "2", "3"]) };
  const edges = vectorEdges(items, catalog);
  for (const item of items) {
    for (const depth of [0, 1, 2, 3]) {
      assert.deepEqual(memoryNeighborhood(items, item.id, depth, catalog), memoryNeighborhood(items, item.id, depth, edges));
    }
  }
  const exact = new Set(edges.map(edge => JSON.stringify([edge.from, edge.to])));
  assert.ok(visualVectorGraph(items, catalog).edges.every(edge => exact.has(JSON.stringify([edge.from, edge.to]))), "every representative edge must be an authorized real relationship");
});

test("sample graph is deterministic with shared assignments and isolated memories", () => {
  const items = makeDemoVectors();
  assert.deepEqual(items, makeDemoVectors());
  assert.equal(new Set(items.map((item) => item.id)).size, items.length);
  assert.ok(
    items.every((item) => Number.isFinite(item.x) && Number.isFinite(item.y)),
  );
  const byId = new Map(items.map((item) => [item.id, item]));
  assert.ok(
    vectorEdges(items).some(
      (edge) => byId.get(edge.from).category !== byId.get(edge.to).category,
    ),
  );
  const linked = new Set(vectorEdges(items).flatMap(edge => [edge.from, edge.to]));
  assert.ok(items.some(item => !linked.has(item.id)), "Sample includes unconnected memories");
});

test("each sample source revision can be opened independently of the current document", () => {
  const history = makeDemoHistories();
  for (const item of makeDemoVectors()) {
    const current = demoDocuments.find((doc) => doc.id === item.sourceId);
    const source =
      current.revision === item.sourceRevision
        ? current
        : history[item.sourceId]?.find(
            (doc) => doc.revision === item.sourceRevision,
          );
    assert.ok(
      source,
      `Missing sample source ${item.sourceId} v${item.sourceRevision}`,
    );
    assert.equal(item.stale, current.revision !== item.sourceRevision);
  }
});
