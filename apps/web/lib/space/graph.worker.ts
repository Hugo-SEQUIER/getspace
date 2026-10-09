import { createGraphLayout, type GraphForces } from "./graph-layout";
import type { GraphCommand, GraphFrame, GraphMode } from "./graph-protocol";

// This module is bundled as a dedicated browser worker. No memory text is sent
// here, and no graph data or interaction is sent to a server.
const scope = self as unknown as {
  onmessage: ((event: MessageEvent<GraphCommand>) => void) | null;
  postMessage: (message: GraphFrame, transfer: Transferable[]) => void;
};
let model: ReturnType<typeof createGraphLayout> | undefined;
let mode: GraphMode = { paused: false, reduced: false, hidden: false };
let timer: ReturnType<typeof setTimeout> | undefined;
let forces: GraphForces | undefined;

function stop() {
  clearTimeout(timer);
  timer = undefined;
}
function publish(motion: GraphFrame["motion"]) {
  if (!model) return;
  const positions = new Float32Array(model.nodes.length * 2);
  model.nodes.forEach((node, i) => {
    positions[i * 2] = node.x;
    positions[i * 2 + 1] = node.y;
  });
  scope.postMessage({ positions, motion }, [positions.buffer]);
}
function step() {
  timer = undefined;
  if (!model || mode.paused || mode.hidden || mode.reduced) return;
  model.simulation.tick();
  const settled = model.simulation.alpha() < model.simulation.alphaMin();
  publish(settled ? "settled" : "settling");
  if (!settled) timer = setTimeout(step, 16);
}
function run(alpha = 0.3) {
  if (!model) return;
  if (mode.paused || mode.hidden) {
    stop();
    publish("paused");
    return;
  }
  model.simulation.alpha(Math.max(model.simulation.alpha(), alpha));
  if (mode.reduced) {
    stop();
    model.simulation.tick(180);
    publish("settled");
    return;
  }
  if (timer === undefined) step();
}
scope.onmessage = ({ data }) => {
  if (data.type === "init") {
    stop();
    model?.simulation.stop();
    mode = data.mode;
    forces = data.forces;
    model = createGraphLayout(
      data.ids.map((id) => ({ id })),
      data.edges,
      new Map(data.previous),
      data.forces,
      data.groups,
    );
    // Warm-up belongs off the UI thread; the first visible frame is already arranged.
    model.simulation.tick(100);
    run();
    return;
  }
  if (!model) return;
  if (data.type === "mode") {
    if (
      mode.paused === data.mode.paused &&
      mode.reduced === data.mode.reduced &&
      mode.hidden === data.mode.hidden
    )
      return;
    mode = data.mode;
    run(0.15);
  } else if (data.type === "forces") {
    if (
      forces?.repulsion === data.forces.repulsion &&
      forces?.distance === data.forces.distance
    )
      return;
    forces = data.forces;
    model.configure(data.forces);
    run(0.45);
  } else if (data.type === "pin") {
    const node = model.byId.get(data.id);
    if (!node) return;
    node.x = node.fx = data.point.x;
    node.y = node.fy = data.point.y;
    if (data.moving && !mode.paused && !mode.reduced && !mode.hidden) {
      model.simulation.alphaTarget(0.18);
      run(0.25);
    } else
      publish(
        mode.paused || mode.hidden
          ? "paused"
          : timer === undefined
            ? "settled"
            : "settling",
      );
  } else if (data.type === "release") {
    const node = model.byId.get(data.id);
    if (node) {
      node.fx = null;
      node.fy = null;
    }
    model.simulation.alphaTarget(0);
    if (data.moved) run(0.15);
    else
      publish(
        mode.paused || mode.hidden
          ? "paused"
          : timer === undefined
            ? "settled"
            : "settling",
      );
  }
};
