import {
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
  forceX,
  forceY,
  type SimulationNodeDatum,
  type SimulationLinkDatum,
} from "d3-force";
import type { MemoryVector } from "./types";

export type Point = { x: number; y: number };
export type Camera = Point & { k: number };
export type GraphForces = { repulsion: number; distance: number };
export const defaultForces: GraphForces = { repulsion: 20, distance: 42 };
export type GraphNode = SimulationNodeDatum & {
  id: string;
  radius: number;
  degree: number;
  group?: string;
  x: number;
  y: number;
};
export type GraphGroup = { id: string; name: string; ids: string[]; count: number; x: number; y: number; radius: number };

export function placeGraphGroups(groups: { id: string; name: string; ids: string[]; count: number }[]): GraphGroup[] {
  let x = 0, y = 0, rowHeight = 0;
  const placed = groups.map(group => {
    const radius = Math.max(100, Math.sqrt(group.count) * 19);
    if (x > 0 && x + radius * 2 > 1500) {
      x = 0;
      y += rowHeight + 80;
      rowHeight = 0;
    }
    const result = { ...group, x: x + radius, y: y + radius, radius };
    x += radius * 2 + 80;
    rowHeight = Math.max(rowHeight, radius * 2);
    return result;
  });
  const width = Math.max(0, ...placed.map(group => group.x + group.radius));
  const height = y + rowHeight;
  return placed.map(group => ({ ...group, x: group.x - width / 2, y: group.y - height / 2 }));
}

export type GraphLink = SimulationLinkDatum<GraphNode> & {
  key: string;
  source: GraphNode;
  target: GraphNode;
};

// Layout coordinates belong to the renderer. Never mutate the saved memories or
// imply that these force positions are an embedding projection.
export function createGraphGeometry(
  items: Pick<MemoryVector, "id">[],
  edges: { from: string; to: string }[],
  previous: Map<string, Point> = new Map(),
  groups: GraphGroup[] = [],
) {
  const membership = new Map(groups.flatMap(group => group.ids.map(id => [id, group] as const)));
  const groupIndexes = new Map<string, number>();
  const degree = new Map(items.map((item) => [item.id, 0]));
  for (const edge of edges) {
    degree.set(edge.from, (degree.get(edge.from) ?? 0) + 1);
    degree.set(edge.to, (degree.get(edge.to) ?? 0) + 1);
  }
  const nodes: GraphNode[] = items.map((item, index) => {
    const position = previous.get(item.id);
    const group = membership.get(item.id);
    const groupIndex = groupIndexes.get(group?.id ?? "") ?? 0;
    if (group) groupIndexes.set(group.id, groupIndex + 1);
    const angle = (group ? groupIndex : index) * Math.PI * (3 - Math.sqrt(5));
    const radius = Math.sqrt((group ? groupIndex : index) + 0.5) * 14;
    const connections = degree.get(item.id) ?? 0;
    return {
      id: item.id,
      degree: connections,
      group: group?.id,
      radius: Math.min(8, 2.3 + Math.sqrt(connections) * 0.85),
      x: position?.x ?? (group?.x ?? 0) + Math.cos(angle) * radius,
      y: position?.y ?? (group?.y ?? 0) + Math.sin(angle) * radius,
    };
  });
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const links: GraphLink[] = edges.flatMap((edge) => {
    const source = byId.get(edge.from),
      target = byId.get(edge.to);
    return source && target
      ? [{ key: JSON.stringify([edge.from, edge.to]), source, target }]
      : [];
  });
  return { nodes, links, byId, groups, revision: 0 };
}

export function createGraphLayout(
  items: Pick<MemoryVector, "id">[],
  edges: { from: string; to: string }[],
  previous: Map<string, Point> = new Map(),
  forces = defaultForces,
  groups: GraphGroup[] = [],
) {
  const { nodes, links, byId } = createGraphGeometry(items, edges, previous, groups);
  const centers = new Map(groups.map(group => [group.id, group]));
  const simulation = forceSimulation<GraphNode>(nodes)
    .stop()
    .alphaDecay(0.027)
    .velocityDecay(0.36);
  function configure(next: GraphForces) {
    simulation.force(
      "links",
      forceLink<GraphNode, GraphLink>(links)
        .id((node) => node.id)
        .distance(
          (link) =>
            next.distance *
            (link.source.degree > 5 && link.target.degree > 5 ? 1.45 : 1),
        )
        .strength(0.32),
    );
    simulation.force(
      "repel",
      forceManyBody<GraphNode>()
        .strength((node) => -next.repulsion * (node.degree > 5 ? 1.3 : 1))
        .distanceMin(12)
        .distanceMax(750),
    );
    simulation.force(
      "collide",
      forceCollide<GraphNode>()
        .radius((node) => node.radius + 5)
        .strength(0.7),
    );
    const centerStrength = (node: GraphNode) =>
      node.degree === 0 ? 0.25 : node.degree === 1 ? 0.06 : 0.018;
    simulation.force("x", forceX<GraphNode>(node => centers.get(node.group ?? "")?.x ?? 0)
      .strength(node => node.group !== undefined ? 0.12 : centerStrength(node)));
    simulation.force("y", forceY<GraphNode>(node => centers.get(node.group ?? "")?.y ?? 0)
      .strength(node => node.group !== undefined ? 0.12 : centerStrength(node)));
  }
  configure(forces);
  return { nodes, links, byId, simulation, configure };
}

export function worldPoint(
  point: Point,
  camera: Camera,
  width: number,
  height: number,
): Point {
  return {
    x: (point.x - width / 2 - camera.x) / camera.k,
    y: (point.y - height / 2 - camera.y) / camera.k,
  };
}

export function zoomAt(
  camera: Camera,
  point: Point,
  scale: number,
  width: number,
  height: number,
  minimumScale = 0.12,
): Camera {
  const world = worldPoint(point, camera, width, height);
  const k = Math.max(minimumScale, Math.min(5, scale));
  return {
    k,
    x: point.x - width / 2 - world.x * k,
    y: point.y - height / 2 - world.y * k,
  };
}

export function fitCamera(
  nodes: Point[],
  width: number,
  height: number,
): Camera {
  if (!nodes.length) return { x: 0, y: 0, k: 1 };
  const xs = nodes.map((node) => node.x),
    ys = nodes.map((node) => node.y);
  const left = Math.min(...xs),
    right = Math.max(...xs),
    top = Math.min(...ys),
    bottom = Math.max(...ys);
  const horizontalPadding = width < 600 ? 48 : 140;
  const k = Math.max(
    0.12,
    Math.min(
      1.5,
      Math.max(100, width - horizontalPadding) / Math.max(100, right - left),
      Math.max(100, height - 190) / Math.max(100, bottom - top),
    ),
  );
  return { k, x: (-(left + right) / 2) * k, y: (-(top + bottom) / 2) * k + 10 };
}

export function minimumGraphScale(nodes: Point[], width: number, height: number) {
  return Math.max(0.12, fitCamera(nodes, width, height).k * 0.7);
}

export function limitCameraPan(
  camera: Camera,
  nodes: Point[],
  width: number,
  height: number,
): Camera {
  if (!nodes.length) return camera;
  const xs = nodes.map((node) => node.x),
    ys = nodes.map((node) => node.y);
  return {
    ...camera,
    x: Math.max(
      -width / 4 - Math.max(...xs) * camera.k,
      Math.min(width / 4 - Math.min(...xs) * camera.k, camera.x),
    ),
    y: Math.max(
      -height / 4 - Math.max(...ys) * camera.k,
      Math.min(height / 4 - Math.min(...ys) * camera.k, camera.y),
    ),
  };
}

export function nodeScreenPoint(
  node: Point,
  camera: Camera,
  width: number,
  height: number,
) {
  return {
    x: width / 2 + camera.x + node.x * camera.k,
    y: height / 2 + camera.y + node.y * camera.k,
  };
}
// Keep memories legible at overview scale without letting them balloon on zoom.
export function nodeScreenRadius(
  node: Pick<GraphNode, "radius">,
  scale: number,
) {
  return Math.max(2, Math.min(12, node.radius * Math.sqrt(scale)));
}
export function hitGraphNode(
  nodes: GraphNode[],
  screen: Point,
  camera: Camera,
  width: number,
  height: number,
) {
  const point = worldPoint(screen, camera, width, height);
  let found: GraphNode | undefined,
    closest = Infinity;
  for (const node of nodes) {
    const distance = (point.x - node.x) ** 2 + (point.y - node.y) ** 2;
    const radius = Math.max(nodeScreenRadius(node, camera.k) + 5, 8) / camera.k;
    if (distance < radius ** 2 && distance < closest) {
      closest = distance;
      found = node;
    }
  }
  return found;
}
