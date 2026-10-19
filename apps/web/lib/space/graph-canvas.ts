import {
  nodeScreenPoint,
  nodeScreenRadius,
  type Camera,
  type GraphNode,
  type GraphLink,
  type createGraphGeometry,
} from "./graph-layout";

export type GraphGeometry = ReturnType<typeof createGraphGeometry>;
export type CanvasGraphStyle = {
  colors: Map<string, string>;
  titles: Map<string, string>;
  labelIds: Set<string>;
  neighbors: Map<string, Set<string>>;
  labels: boolean;
  active?: string;
  selected?: string;
};

// Cache each strand's bend by identity so dragging never makes it flip sides.
// These are drawing choices, not inferred relationship types or strengths.
const curves = new WeakMap<GraphLink, number>();
function strand(ctx: CanvasRenderingContext2D, link: GraphLink) {
  let bend = curves.get(link);
  if (bend === undefined) {
    let hash = 2166136261;
    for (let i = 0; i < link.key.length; i++)
      hash = Math.imul(hash ^ link.key.charCodeAt(i), 16777619);
    bend = (hash & 1 ? 1 : -1) * (0.1 + ((hash >>> 1) % 8) / 100);
    curves.set(link, bend);
  }
  const { source, target } = link,
    dx = target.x - source.x,
    dy = target.y - source.y;
  // Limit long connections to a shallow arc, so cross-category links stay legible.
  const curve = bend * Math.min(1, 220 / Math.max(1, Math.hypot(dx, dy)));
  ctx.moveTo(source.x, source.y);
  ctx.quadraticCurveTo(
    (source.x + target.x) / 2 - dy * curve,
    (source.y + target.y) / 2 + dx * curve,
    target.x,
    target.y,
  );
}
function circle(
  ctx: CanvasRenderingContext2D,
  node: GraphNode,
  radius: number,
) {
  ctx.moveTo(node.x + radius, node.y);
  ctx.arc(node.x, node.y, radius, 0, Math.PI * 2);
}

type BackgroundLayer = {
  canvas: HTMLCanvasElement;
  context: CanvasRenderingContext2D;
  geometry?: GraphGeometry;
  revision?: number;
  camera?: string;
  colors?: CanvasGraphStyle["colors"];
  titles?: CanvasGraphStyle["titles"];
  labelIds?: CanvasGraphStyle["labelIds"];
  labels?: boolean;
};
const backgrounds = new WeakMap<CanvasRenderingContext2D, BackgroundLayer>();

// A settled overview is painted once. Hover only composites that layer and
// draws the active neighborhood; canvas/geometry lifetimes bound the cache.
export function paintGraph(
  ctx: CanvasRenderingContext2D,
  geometry: GraphGeometry,
  camera: Camera,
  width: number,
  height: number,
  dpr: number,
  style: CanvasGraphStyle,
) {
  let layer = backgrounds.get(ctx);
  if (!layer) {
    const canvas = document.createElement("canvas");
    const context = canvas.getContext("2d", { alpha: false });
    if (!context) {
      drawGraph(ctx, geometry, camera, width, height, dpr, style);
      drawGraphLabels(ctx, geometry, camera, width, height, dpr, style);
      return;
    }
    layer = { canvas, context };
    backgrounds.set(ctx, layer);
  }
  const cameraKey = `${camera.x},${camera.y},${camera.k},${width},${height},${dpr}`;
  if (
    layer.geometry !== geometry ||
    layer.revision !== geometry.revision ||
    layer.camera !== cameraKey ||
    layer.colors !== style.colors ||
    layer.titles !== style.titles ||
    layer.labelIds !== style.labelIds ||
    layer.labels !== style.labels
  ) {
    const pixelWidth = Math.round(width * dpr),
      pixelHeight = Math.round(height * dpr);
    if (
      layer.canvas.width !== pixelWidth ||
      layer.canvas.height !== pixelHeight
    ) {
      layer.canvas.width = pixelWidth;
      layer.canvas.height = pixelHeight;
    }
    drawGraph(layer.context, geometry, camera, width, height, dpr, {
      ...style,
      active: undefined,
      selected: undefined,
    });
    Object.assign(layer, {
      geometry,
      revision: geometry.revision,
      camera: cameraKey,
      colors: style.colors,
      titles: style.titles,
      labelIds: style.labelIds,
      labels: style.labels,
    });
  }
  const active = style.active ?? style.selected;
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.globalAlpha = 1;
  ctx.fillStyle = "white";
  ctx.fillRect(0, 0, ctx.canvas.width, ctx.canvas.height);
  ctx.globalAlpha = active ? 0.25 : 1;
  ctx.drawImage(layer.canvas, 0, 0);
  if (active) {
    const ids = new Set([active, ...(style.neighbors.get(active) ?? [])]);
    const local = {
      ...geometry,
      nodes: geometry.nodes.filter((node) => ids.has(node.id)),
      groups: [],
      links: geometry.links.filter(
        (link) => link.source.id === active || link.target.id === active,
      ),
    };
    drawGraph(
      ctx,
      local,
      camera,
      width,
      height,
      dpr,
      {
        ...style,
        active,
        labelIds: new Set([...style.labelIds].filter((id) => ids.has(id))),
      },
      false,
    );
  }
  drawGraphLabels(ctx, geometry, camera, width, height, dpr, {
    ...style,
    active,
  });
  ctx.globalAlpha = 1;
}

// Curves and cells are batched by paint style. Halos are translucent disks,
// not per-node blur/shadow effects, and nothing animates on a settled canvas.
function drawGraph(
  ctx: CanvasRenderingContext2D,
  geometry: GraphGeometry,
  camera: Camera,
  width: number,
  height: number,
  dpr: number,
  style: CanvasGraphStyle,
  clear = true,
) {
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.globalAlpha = 1;
  ctx.fillStyle = "#fff";
  if (clear) ctx.fillRect(0, 0, width, height);
  const { k } = camera;
  const origin = { x: width / 2 + camera.x, y: height / 2 + camera.y };
  ctx.translate(origin.x, origin.y);
  ctx.scale(k, k);
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  const active = style.active,
    connected = active ? style.neighbors.get(active) : undefined;
  const activeColor = (active && style.colors.get(active)) || "#5b6fb5";

  for (const group of geometry.groups) {
    ctx.beginPath();
    ctx.arc(group.x, group.y, group.radius, 0, Math.PI * 2);
    ctx.fillStyle = "#8a978d";
    ctx.globalAlpha = 0.07;
    ctx.fill();
    ctx.strokeStyle = "#8a978d";
    ctx.globalAlpha = 0.22;
    ctx.lineWidth = 1 / k;
    ctx.stroke();
  }
  ctx.globalAlpha = 1;

  const edgeGroups = new Map<
    string,
    { color: string; trunk: boolean; links: GraphLink[] }
  >();
  const highlighted: GraphLink[] = [];
  for (const link of geometry.links) {
    if (active && (link.source.id === active || link.target.id === active)) {
      highlighted.push(link);
      continue;
    }
    const sourceColor = style.colors.get(link.source.id),
      targetColor = style.colors.get(link.target.id);
    const color =
      sourceColor && sourceColor === targetColor ? sourceColor : "#a7b0a9";
    const trunk = link.source.degree > 3 && link.target.degree > 3;
    const key = color + trunk;
    let group = edgeGroups.get(key);
    if (!group) {
      group = { color, trunk, links: [] };
      edgeGroups.set(key, group);
    }
    group.links.push(link);
  }
  for (const group of edgeGroups.values()) {
    ctx.beginPath();
    for (const link of group.links) strand(ctx, link);
    ctx.strokeStyle = group.color;
    ctx.globalAlpha = (group.trunk ? 0.25 : 0.18) * (active ? 0.25 : 1);
    ctx.lineWidth = (group.trunk ? 1 : 0.65) / k;
    ctx.stroke();
  }
  if (highlighted.length) {
    ctx.beginPath();
    for (const link of highlighted) strand(ctx, link);
    ctx.strokeStyle = activeColor;
    ctx.globalAlpha = 0.07;
    ctx.lineWidth = 4 / k;
    ctx.stroke();
    ctx.globalAlpha = 0.62;
    ctx.lineWidth = 1.15 / k;
    ctx.stroke();
  }

  const groups = new Map<
    string,
    { color: string; alpha: number; cells: boolean; nodes: GraphNode[] }
  >();
  for (const node of geometry.nodes) {
    const x = origin.x + node.x * k,
      y = origin.y + node.y * k;
    if (x < -30 || x > width + 30 || y < -30 || y > height + 30) continue;
    const color = style.colors.get(node.id) ?? "#939a95";
    const alpha =
      active && node.id !== active && !connected?.has(node.id) ? 0.25 : 1;
    const cells = node.degree > 3;
    const key = color + alpha + cells;
    let group = groups.get(key);
    if (!group) {
      group = { color, alpha, cells, nodes: [] };
      groups.set(key, group);
    }
    group.nodes.push(node);
  }
  for (const group of groups.values()) {
    if (group.cells) {
      // Outer membrane; its space grows with connectivity, never agent ownership.
      ctx.beginPath();
      for (const node of group.nodes)
        circle(ctx, node, (nodeScreenRadius(node, k) + 3) / k);
      ctx.globalAlpha = group.alpha * 0.075;
      ctx.fillStyle = group.color;
      ctx.fill();
    }
    ctx.beginPath();
    for (const node of group.nodes)
      circle(ctx, node, nodeScreenRadius(node, k) / k);
    // A white cell body separates connections before the category-colored nucleus.
    ctx.globalAlpha = group.alpha;
    ctx.fillStyle = group.cells ? "white" : group.color;
    ctx.fill();
    ctx.strokeStyle = group.cells ? group.color : "white";
    ctx.lineWidth = (group.cells ? 1.1 : 0.8) / k;
    ctx.stroke();
    if (group.cells) {
      ctx.globalAlpha = group.alpha * 0.1;
      ctx.fillStyle = group.color;
      ctx.fill();
      ctx.beginPath();
      for (const node of group.nodes)
        circle(ctx, node, Math.max(1.5, nodeScreenRadius(node, k) * 0.4) / k);
      ctx.globalAlpha = group.alpha;
      ctx.fillStyle = group.color;
      ctx.fill();
    }
  }
  const selected = style.selected && geometry.byId.get(style.selected);
  const activeNode = active && geometry.byId.get(active);
  if (activeNode) {
    const radius = nodeScreenRadius(activeNode, k);
    ctx.beginPath();
    circle(ctx, activeNode, (radius + 8) / k);
    ctx.globalAlpha = 0.09;
    ctx.fillStyle = activeColor;
    ctx.fill();
    ctx.beginPath();
    circle(ctx, activeNode, (radius + 4) / k);
    ctx.globalAlpha = 0.5;
    ctx.strokeStyle = activeColor;
    ctx.lineWidth = 0.8 / k;
    ctx.stroke();
    ctx.beginPath();
    circle(ctx, activeNode, radius / k);
    ctx.globalAlpha = 1;
    ctx.fillStyle = "white";
    ctx.fill();
    ctx.strokeStyle = activeColor;
    ctx.lineWidth = 1.5 / k;
    ctx.stroke();
    ctx.beginPath();
    circle(ctx, activeNode, Math.max(1.8, radius * 0.45) / k);
    ctx.fillStyle = activeColor;
    ctx.fill();
  }
  if (selected && selected !== activeNode) {
    ctx.beginPath();
    circle(ctx, selected, (nodeScreenRadius(selected, k) + 4) / k);
    ctx.globalAlpha = 0.7;
    ctx.strokeStyle = style.colors.get(selected.id) ?? "#5b6fb5";
    ctx.lineWidth = 1.2 / k;
    ctx.stroke();
  }

  ctx.globalAlpha = 1;
}

function drawGraphLabels(
  ctx: CanvasRenderingContext2D,
  geometry: GraphGeometry,
  camera: Camera,
  width: number,
  height: number,
  dpr: number,
  style: CanvasGraphStyle,
) {
  const { k } = camera;
  const active = style.active;
  const connected = active ? style.neighbors.get(active) : undefined;
  const activeColor = (active && style.colors.get(active)) || "#5b6fb5";
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  for (const group of k >= 0.4 ? geometry.groups : []) {
    const point = nodeScreenPoint(group, camera, width, height);
    const x = point.x - group.radius * k + 14;
    const y = point.y - group.radius * k + 25;
    if (x > width || y > height || x < -250 || y < 0) continue;
    ctx.font = '600 14px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
    ctx.fillStyle = "#515b56";
    ctx.fillText(`${group.name} · ${group.count}`, x, y, Math.max(90, group.radius * k * 2 - 28));
  }
  const boxes: { x: number; y: number; width: number; height: number }[] = [];
  for (const id of new Set([
    ...(active ? [active] : []),
    ...(style.labels && k >= 0.32 ? style.labelIds : []),
  ])) {
    const node = geometry.byId.get(id),
      title = style.titles.get(id);
    if (!node || !title) continue;
    const point = nodeScreenPoint(node, camera, width, height);
    const focused = id === active;
    const y = point.y + 3;
    ctx.font = focused
      ? '550 14px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
      : '13px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
    const labelWidth = ctx.measureText(title).width + (focused ? 21 : 0),
      labelHeight = focused ? 30 : 19;
    // Face the active label toward the canvas center, away from the inspector.
    const x =
      focused && point.x > width / 2
        ? point.x - nodeScreenRadius(node, k) - labelWidth - 12
        : point.x + nodeScreenRadius(node, k) + (focused ? 12 : 7);
    if (x < -180 || x > width || y < 0 || y > height) continue;
    if (
      !focused &&
      boxes.some(
        (box) =>
          x < box.x + box.width + 8 &&
          x + labelWidth + 8 > box.x &&
          Math.abs(y - box.y) < (labelHeight + box.height) / 2,
      )
    )
      continue;
    boxes.push({ x, y, width: labelWidth, height: labelHeight });
    if (focused) {
      ctx.beginPath();
      ctx.roundRect(x - 7, y - 19, labelWidth + 14, 31, 7);
      ctx.globalAlpha = 1;
      ctx.fillStyle = "white";
      ctx.fill();
      ctx.globalAlpha = 0.24;
      ctx.strokeStyle = activeColor;
      ctx.lineWidth = 1;
      ctx.stroke();
      ctx.globalAlpha = 1;
      ctx.fillStyle = activeColor;
      ctx.beginPath();
      ctx.arc(x + 2, y - 2.5, 2.5, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = "#202522";
      ctx.fillText(title, x + 12, y + 1);
    } else {
      ctx.globalAlpha = active && !connected?.has(id) ? 0.25 : 1;
      ctx.lineWidth = 4;
      ctx.strokeStyle = "white";
      ctx.strokeText(title, x, y);
      ctx.fillStyle = "#68716b";
      ctx.fillText(title, x, y);
    }
  }
  ctx.globalAlpha = 1;
}
