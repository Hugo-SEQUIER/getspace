import type { MemoryResponse, MemoryVector, Project, Resource } from "./types";

export function filterVectors(
  items: MemoryVector[],
  query: string,
  creator: string,
  category: string,
) {
  const term = query.trim().toLocaleLowerCase();
  return items.filter(
    (item) =>
      (!creator || item.creator === creator) &&
      (!category || item.category === category) &&
      (!term ||
        `${item.title} ${item.value} ${item.sourceTitle ?? ""}`
          .toLocaleLowerCase()
          .includes(term)),
  );
}

// Creator snapshots are independent of revision authors. Older claims remain unknown.
export function normalizeMemories(
  items: MemoryResponse[],
  sources: Resource[],
): MemoryVector[] {
  return items.flatMap((memory) =>
    memory.claims.map((claim) => {
      const source = memory.recall?.sources?.[0];
      return {
        id: `${memory.id}:${claim.id}`,
        memoryId: memory.id,
        revision: memory.revision,
        title: claim.index,
        value: claim.value,
        category: claim.memory_type,
        classification: claim.classification,
        creator: claim.creator?.kind === "human" ? "You" : claim.creator?.agent?.name ?? null,
        creatorBasis: claim.creator?.basis,
        revisionAgent: memory.author_agent,
        importOrigin: memory.import_origin,
        sourceId: source?.resource_id,
        sourceRevision: source?.revision,
        sourceTitle: sources.find((item) => item.id === source?.resource_id)
          ?.title,
        stale: memory.stale,
        edited: claim.edited,
        createdAt: claim.created_at,
      };
    }),
  );
}

export type ProjectVisibility = {
  activeProjects: ReadonlySet<string>;
  visibleProjects: ReadonlySet<string>;
  global: boolean;
  unassigned: boolean;
  unavailable: boolean;
};

export function filterByProjectVisibility(items: MemoryVector[], visibility: ProjectVisibility) {
  return items.filter(item => {
    const classification = item.classification;
    if (classification?.scope === "global") return visibility.global;
    if (classification?.scope === "project" || classification?.project_ids?.length) {
      const active = (classification.project_ids ?? []).filter(id => visibility.activeProjects.has(id));
      return active.length
        ? active.some(id => visibility.visibleProjects.has(id))
        : visibility.unavailable;
    }
    return visibility.unassigned;
  });
}

export function groupVectorsByProject(items: MemoryVector[], projects: Project[]) {
  const active = projects.filter(project => !project.deleted);
  const groups = active.map(project => ({ id: project.id, name: project.name, items: [] as MemoryVector[] }));
  const byId = new Map(groups.map(group => [group.id, group]));
  const noProject: MemoryVector[] = [];
  for (const item of items) {
    const assigned = new Set((item.classification?.project_ids ?? []).filter(id => byId.has(id)));
    if (!assigned.size) noProject.push(item);
    for (const id of assigned) byId.get(id)!.items.push(item);
  }
  return [
    ...groups.filter(group => group.items.length).sort((a, b) => a.name.localeCompare(b.name)),
    ...(noProject.length ? [{ id: "", name: "No active project", items: noProject }] : []),
  ];
}

export type MemoryEdge = { from: string; to: string };

export function projectVectorGraph(items: MemoryVector[], edges: MemoryEdge[], projects: Project[]) {
  const groups = groupVectorsByProject(items, projects);
  const originals = new Map<string, string>();
  const graphGroups = groups.map(group => {
    const ids = group.items.map(item => {
      const id = JSON.stringify([group.id, item.id]);
      originals.set(id, item.id);
      return id;
    });
    return { id: group.id, name: group.name, ids, count: ids.length };
  });
  const nodes = groups.flatMap((group, index) => group.items.map((item, nodeIndex) => ({
    ...item, id: graphGroups[index].ids[nodeIndex],
  })));
  const graphEdges = groups.flatMap((group, index) => {
    const ids = new Map(group.items.map((item, nodeIndex) => [item.id, graphGroups[index].ids[nodeIndex]]));
    return edges.flatMap(edge => {
      const from = ids.get(edge.from), to = ids.get(edge.to);
      return from && to ? [{ from, to }] : [];
    });
  });
  return { nodes, edges: graphEdges, groups: graphGroups, originals };
}

export type MemoryGraphCatalog = {
  categoryIds: ReadonlySet<string>;
  projectIds: ReadonlySet<string>;
};

// Only shared project/category assignments create links. Creator, memory type,
// source, and legacy parent/connections metadata must never connect memories.
function vectorGroups(items: MemoryVector[], catalog?: MemoryGraphCatalog) {
  const groups = new Map<string, Set<string>>();
  for (const item of items) {
    for (const [kind, assignments, allowed] of [
      ["category", item.classification?.category_ids, catalog?.categoryIds],
      ["project", item.classification?.project_ids, catalog?.projectIds],
    ] as const) {
      for (const id of assignments ?? []) {
        if (!id.trim() || (allowed && !allowed.has(id))) continue;
        const key = `${kind}:${id}`;
        if (!groups.has(key)) groups.set(key, new Set());
        groups.get(key)!.add(item.id);
      }
    }
  }
  return groups;
}

function connectVectorGroups(groups: Map<string, Set<string>>, cliqueLimit: number) {
  const seen = new Set<string>();
  const edges: MemoryEdge[] = [];
  let representative = false;
  function connect(from: string, to: string) {
    if (from === to) return;
    const pair = from < to ? [from, to] : [to, from];
    const key = JSON.stringify(pair);
    if (!seen.has(key)) {
      seen.add(key);
      edges.push({ from: pair[0], to: pair[1] });
    }
  }
  for (const group of groups.values()) {
    const members = [...group];
    if (members.length > cliqueLimit) {
      representative = true;
      // A ring keeps every member connected without materializing a quadratic
      // clique. Every drawn link remains a real shared assignment. Focus depth
      // must use memberships below, never this representative visual adjacency.
      for (let i = 0; i < members.length; i++) connect(members[i], members[(i + 1) % members.length]);
    } else {
      for (let i = 0; i < members.length; i++) {
        for (let j = i + 1; j < members.length; j++) connect(members[i], members[j]);
      }
    }
  }
  return { edges, representative };
}

// Exact edges remain available for callers that need the complete relationship.
export function vectorEdges(items: MemoryVector[], catalog?: MemoryGraphCatalog): MemoryEdge[] {
  return connectVectorGroups(vectorGroups(items, catalog), Infinity).edges;
}

// At most 5.5 edges per membership: small groups retain all links; larger ones
// show representative links. This bound also holds for overlapping assignments.
export function visualVectorGraph(items: MemoryVector[], catalog?: MemoryGraphCatalog) {
  return connectVectorGroups(vectorGroups(items, catalog), 12);
}

export function memoryNeighborhood(
  items: MemoryVector[],
  root: string,
  depth: number,
  relationships?: MemoryEdge[] | MemoryGraphCatalog,
) {
  if (!items.some((item) => item.id === root)) return [];
  const visible = new Set([root]);
  let frontier = new Set([root]);
  // Expand each shared assignment at most once. A whole group is one hop in
  // the exact graph, even when only representative links are drawn on screen.
  const groups = Array.isArray(relationships) ? undefined : vectorGroups(items, relationships);
  const memberships = new Map<string, string[]>();
  const expanded = new Set<string>();
  for (const [group, members] of groups ?? []) {
    for (const id of members) {
      if (!memberships.has(id)) memberships.set(id, []);
      memberships.get(id)!.push(group);
    }
  }
  for (let step = 0; step < Math.max(0, Math.min(3, depth)); step++) {
    const next = new Set<string>();
    if (Array.isArray(relationships)) {
      for (const edge of relationships) {
        if (frontier.has(edge.from) && !visible.has(edge.to)) next.add(edge.to);
        if (frontier.has(edge.to) && !visible.has(edge.from)) next.add(edge.from);
      }
    } else {
      for (const id of frontier) {
        for (const group of memberships.get(id) ?? []) {
          if (expanded.has(group)) continue;
          expanded.add(group);
          for (const member of groups!.get(group)!) {
            if (!visible.has(member)) next.add(member);
          }
        }
      }
    }
    next.forEach((id) => visible.add(id));
    frontier = next;
    if (!frontier.size) break;
  }
  return items.filter((item) => visible.has(item.id));
}
