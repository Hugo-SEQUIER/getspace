
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";
import type { MemoryVector } from "../../../lib/space/types";
import type { MemoryEdge } from "../../../lib/space/memory-model";
import {
  createGraphGeometry,
  hitGraphNode,
  nodeScreenPoint,
  defaultForces,
  fitCamera,
  limitCameraPan,
  minimumGraphScale,
  worldPoint,
  zoomAt,
  type Camera,
  type GraphNode,
  type Point,
} from "../../../lib/space/graph-layout";
import {
  paintGraph,
  type GraphGeometry,
} from "../../../lib/space/graph-canvas";
import type {
  GraphCommand,
  GraphFrame,
} from "../../../lib/space/graph-protocol";
import { Icon } from "./ui-icon";
import { useFeedback } from "../notifications";

type Drag = {
  start: Point;
  camera: Camera;
  node?: GraphNode;
  moved: boolean;
  panOnly: boolean;
};
type Pinch = { distance: number; camera: Camera; anchor: Point };

export function VectorGraph({
  items,
  edges,
  selected,
  onSelect,
  onFocus,
  color,
  colorBy,
  onColorBy,
  projectsOpen,
  onProjectsToggle,
}: {
  items: MemoryVector[];
  edges: MemoryEdge[];
  selected?: string;
  onSelect: (id?: string) => void;
  onFocus: (id: string) => void;
  color: (item: MemoryVector) => string;
  colorBy: string;
  onColorBy: (value: string) => void;
  projectsOpen: boolean;
  onProjectsToggle: () => void;
}) {
  const canvas = useRef<HTMLCanvasElement>(null),
    zoomOutput = useRef<HTMLOutputElement>(null);
  const keyboardNodes = useRef(new Map<string, HTMLButtonElement>());
  const geometry = useRef<GraphGeometry | null>(null),
    positions = useRef(new Map<string, Point>());
  const worker = useRef<Worker | null>(null),
    ready = useRef(false);
  const camera = useRef<Camera>({ x: 0, y: 0, k: 1 }),
    bounds = useRef({ width: 1000, height: 700, dpr: 1 });
  const pointers = useRef(new Map<number, Point>()),
    drag = useRef<Drag | null>(null),
    pinch = useRef<Pinch | null>(null);
  const reduced = useRef(false),
    interacted = useRef(false),
    frame = useRef(0),
    hoverPoint = useRef<Point | null>(null),
    hoveredRef = useRef<string | undefined>(undefined);
  const [hovered, setHovered] = useState<string | undefined>(),
    [dragging, setDragging] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false),
    [paused, setPaused] = useState(false),
    [labels, setLabels] = useState(true),
    [forces, setForces] = useState(defaultForces);
  const [motion, setMotion] = useState("settling"),
    [workerError, setWorkerError] = useState(false);
  useFeedback({ error: workerError ? "Graph motion is unavailable in this browser. You can still pan, zoom, and explore memories." : "" });
  const byId = useMemo(
    () => new Map(items.map((item) => [item.id, item])),
    [items],
  );
  const degree = useMemo(() => {
    const map = new Map<string, number>();
    for (const edge of edges) {
      map.set(edge.from, (map.get(edge.from) ?? 0) + 1);
      map.set(edge.to, (map.get(edge.to) ?? 0) + 1);
    }
    return map;
  }, [edges]);
  const neighbors = useMemo(() => {
    const map = new Map<string, Set<string>>();
    for (const { from, to } of edges) {
      if (!map.has(from)) map.set(from, new Set());
      if (!map.has(to)) map.set(to, new Set());
      map.get(from)!.add(to);
      map.get(to)!.add(from);
    }
    return map;
  }, [edges]);
  const labelIds = useMemo(() => {
    const titles = new Set<string>();
    return new Set(
      [...items]
        .sort((a, b) => (degree.get(b.id) ?? 0) - (degree.get(a.id) ?? 0))
        .filter((item) => {
          if (titles.has(item.title) || titles.size >= 16) return false;
          titles.add(item.title);
          return true;
        })
        .map((item) => item.id),
    );
  }, [items, degree]);
  const colors = useMemo(
    () => new Map(items.map((item) => [item.id, color(item)])),
    [items, color],
  );
  const titles = useMemo(
    () => new Map(items.map((item) => [item.id, item.title])),
    [items],
  );
  const options = useRef({
    paused,
    selected,
    onSelect,
    onFocus,
    forces,
    colors,
    titles,
    labelIds,
    neighbors,
    labels,
  });
  options.current = {
    paused,
    selected,
    onSelect,
    onFocus,
    forces,
    colors,
    titles,
    labelIds,
    neighbors,
    labels,
  };

  function send(command: GraphCommand) {
    worker.current?.postMessage(command);
  }
  function mode() {
    return {
      paused: options.current.paused,
      reduced: reduced.current,
      hidden: document.hidden,
    };
  }
  function hover(id?: string) {
    if (hoveredRef.current === id) return;
    hoveredRef.current = id;
    setHovered(id);
  }
  function draw() {
    frame.current = 0;
    const element = canvas.current,
      model = geometry.current;
    if (!element || !model || !ready.current || document.hidden) return;
    const ctx = element.getContext("2d", { alpha: false });
    if (!ctx) return;
    const { width, height, dpr } = bounds.current,
      current = options.current;
    if (hoverPoint.current && !drag.current && !pinch.current) {
      hover(
        hitGraphNode(
          model.nodes,
          hoverPoint.current,
          camera.current,
          width,
          height,
        )?.id,
      );
      hoverPoint.current = null;
    }
    paintGraph(ctx, model, camera.current, width, height, dpr, {
      ...current,
      active: hoveredRef.current ?? current.selected,
    });
    element.dataset.scale = String(camera.current.k);
    element.dataset.camera = `${camera.current.x},${camera.current.y}`;
    const percent = `${Math.round(camera.current.k * 100)}%`;
    if (zoomOutput.current?.textContent !== percent && zoomOutput.current)
      zoomOutput.current.textContent = percent;
    element.style.cursor =
      drag.current?.moved || drag.current?.panOnly
        ? "grabbing"
        : hoveredRef.current
          ? "grab"
          : "default";
    for (const [id, button] of keyboardNodes.current) {
      const node = model.byId.get(id);
      if (!node) continue;
      const point = nodeScreenPoint(node, camera.current, width, height);
      button.style.transform = `translate(${point.x - 7}px, ${point.y - 7}px)`;
    }
  }
  function invalidate() {
    if (!frame.current && !document.hidden)
      frame.current = requestAnimationFrame(draw);
  }
  function setCamera(next: Camera) {
    const { width, height } = bounds.current;
    const minimum = minScale();
    const bounded = next.k < minimum
      ? zoomAt(next, { x: width / 2, y: height / 2 }, minimum, width, height, minimum)
      : next;
    camera.current = limitCameraPan(
      bounded, geometry.current?.nodes ?? [], width, height,
    );
    invalidate();
  }
  function minScale() {
    const { width, height } = bounds.current;
    return minimumGraphScale(geometry.current?.nodes ?? [], width, height);
  }
  function fit() {
    if (geometry.current) {
      interacted.current = false;
      setCamera(fitCamera(geometry.current.nodes, bounds.current.width, bounds.current.height));
    }
  }
  function zoomBy(factor: number) {
    interacted.current = true;
    const { width, height } = bounds.current;
    setCamera(
      zoomAt(
        camera.current,
        { x: width / 2, y: height / 2 },
        camera.current.k * factor,
        width,
        height,
        minScale(),
      ),
    );
  }
  useEffect(() => {
    const element = canvas.current;
    if (!element) return;
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    reduced.current = media.matches;
    const motionPreference = () => {
      reduced.current = media.matches;
      send({ type: "mode", mode: mode() });
    };
    media.addEventListener("change", motionPreference);
    const resize = () => {
      const { width, height } = element.getBoundingClientRect();
      if (!width || !height) return;
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      bounds.current = { width, height, dpr };
      element.width = Math.round(width * dpr);
      element.height = Math.round(height * dpr);
      if (!interacted.current && ready.current) fit();
      else setCamera(camera.current);
    };
    const observer = new ResizeObserver(resize);
    observer.observe(element);
    window.addEventListener("resize", resize);
    resize();
    const wheel = (event: WheelEvent) => {
      event.preventDefault();
      interacted.current = true;
      const rect = element.getBoundingClientRect();
      const delta =
        event.deltaY *
        (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? rect.height : 1);
      setCamera(
        zoomAt(
          camera.current,
          { x: event.clientX - rect.left, y: event.clientY - rect.top },
          camera.current.k *
            Math.exp(-Math.max(-160, Math.min(160, delta)) * 0.0025),
          bounds.current.width,
          bounds.current.height,
          minScale(),
        ),
      );
    };
    const visibility = () => {
      send({ type: "mode", mode: mode() });
      if (!document.hidden) invalidate();
      else {
        cancelAnimationFrame(frame.current);
        frame.current = 0;
      }
    };
    element.addEventListener("wheel", wheel, { passive: false });
    document.addEventListener("visibilitychange", visibility);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", resize);
      media.removeEventListener("change", motionPreference);
      element.removeEventListener("wheel", wheel);
      document.removeEventListener("visibilitychange", visibility);
      cancelAnimationFrame(frame.current);
      frame.current = 0;
    };
  }, []);
  useEffect(() => {
    const model = createGraphGeometry(items, edges, positions.current);
    geometry.current = model;
    ready.current = false;
    setWorkerError(false);
    setMotion("settling");
    hover(undefined);
    setDragging(false);
    let instance: Worker | null = null;
    function failed() {
      instance?.terminate();
      worker.current = null;
      setWorkerError(true);
      setMotion("paused");
      ready.current = true;
      fit();
    }
    try {
      instance = new Worker(
        new URL("../../../lib/space/graph.worker.ts", import.meta.url),
        { type: "module" },
      );
      worker.current = instance;
      instance.onmessage = ({ data }: MessageEvent<GraphFrame>) => {
        for (let i = 0; i < model.nodes.length; i++) {
          const node = model.nodes[i];
          // The held point follows the pointer immediately, not a delayed worker frame.
          if (node.id === drag.current?.node?.id) continue;
          node.x = data.positions[i * 2];
          node.y = data.positions[i * 2 + 1];
        }
        model.revision++;
        if (!ready.current) {
          ready.current = true;
          if (!interacted.current) fit();
        }
        setMotion(data.motion);
        setCamera(camera.current);
      };
      instance.onerror = (event) => {
        event.preventDefault();
        failed();
      };
      send({
        type: "init",
        ids: items.map((item) => item.id),
        edges,
        previous: [...positions.current],
        forces: options.current.forces,
        mode: mode(),
      });
    } catch {
      failed();
    }
    return () => {
      if (instance) {
        instance.onmessage = null;
        instance.onerror = null;
        instance.terminate();
      }
      if (worker.current === instance) worker.current = null;
      for (const node of model.nodes)
        positions.current.set(node.id, { x: node.x, y: node.y });
      pointers.current.clear();
      drag.current = null;
      pinch.current = null;
      hoverPoint.current = null;
    };
  }, [items, edges]);
  useEffect(() => {
    send({ type: "forces", forces });
  }, [forces]);
  useEffect(() => {
    send({ type: "mode", mode: mode() });
  }, [paused]);
  useEffect(invalidate, [selected, colors, labels, labelIds]);
  function relative(event: ReactPointerEvent<HTMLCanvasElement>): Point {
    const rect = event.currentTarget.getBoundingClientRect();
    return { x: event.clientX - rect.left, y: event.clientY - rect.top };
  }
  function releaseNode() {
    if (drag.current?.node)
      send({
        type: "release",
        id: drag.current.node.id,
        moved: drag.current.moved,
      });
    drag.current = null;
    setDragging(false);
    invalidate();
  }
  function startPointer(event: ReactPointerEvent<HTMLCanvasElement>) {
    const panOnly = event.button === 1;
    if ((!panOnly && event.button !== 0) || !ready.current) return;
    if (panOnly) event.preventDefault();
    event.currentTarget.focus();
    hoverPoint.current = null;
    const point = relative(event);
    pointers.current.set(event.pointerId, point);
    event.currentTarget.setPointerCapture(event.pointerId);
    if (pointers.current.size === 2) {
      releaseNode();
      const [a, b] = [...pointers.current.values()];
      const center = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
      pinch.current = {
        distance: Math.max(1, Math.hypot(a.x - b.x, a.y - b.y)),
        camera: { ...camera.current },
        anchor: worldPoint(
          center,
          camera.current,
          bounds.current.width,
          bounds.current.height,
        ),
      };
      return;
    }
    const node =
      !panOnly && geometry.current
        ? hitGraphNode(
            geometry.current.nodes,
            point,
            camera.current,
            bounds.current.width,
            bounds.current.height,
          )
        : undefined;
    drag.current = {
      node,
      start: point,
      camera: { ...camera.current },
      moved: false,
      panOnly,
    };
    if (panOnly) {
      hover(undefined);
      setDragging(true);
      invalidate();
    } else if (node) {
      send({
        type: "pin",
        id: node.id,
        point: { x: node.x, y: node.y },
        moving: false,
      });
      hover(node.id);
      invalidate();
    }
  }
  function movePointer(event: ReactPointerEvent<HTMLCanvasElement>) {
    const point = relative(event);
    if (pointers.current.has(event.pointerId))
      pointers.current.set(event.pointerId, point);
    if (pinch.current && pointers.current.size >= 2) {
      const [a, b] = [...pointers.current.values()],
        start = pinch.current;
      const k = Math.max(
        minScale(),
        Math.min(
          5,
          (start.camera.k * Math.hypot(a.x - b.x, a.y - b.y)) / start.distance,
        ),
      );
      interacted.current = true;
      setCamera({
        k,
        x: (a.x + b.x) / 2 - bounds.current.width / 2 - start.anchor.x * k,
        y: (a.y + b.y) / 2 - bounds.current.height / 2 - start.anchor.y * k,
      });
      return;
    }
    const current = drag.current;
    if (!current) {
      hoverPoint.current = point;
      invalidate();
      return;
    }
    const dx = point.x - current.start.x,
      dy = point.y - current.start.y;
    if (!current.moved && Math.hypot(dx, dy) < 3) return;
    if (!current.moved) setDragging(true);
    current.moved = true;
    interacted.current = true;
    if (current.node) {
      const position = worldPoint(
        point,
        camera.current,
        bounds.current.width,
        bounds.current.height,
      );
      current.node.x = position.x;
      current.node.y = position.y;
      if (geometry.current) geometry.current.revision++;
      send({ type: "pin", id: current.node.id, point: position, moving: true });
      invalidate();
    } else
      setCamera({
        ...current.camera,
        x: current.camera.x + dx,
        y: current.camera.y + dy,
      });
  }
  function endPointer(
    event: ReactPointerEvent<HTMLCanvasElement>,
    cancelled = false,
  ) {
    const current = drag.current;
    pointers.current.delete(event.pointerId);
    if (pinch.current) {
      pinch.current = null;
      releaseNode();
      const remaining = [...pointers.current.values()][0];
      if (remaining)
        drag.current = {
          start: remaining,
          camera: { ...camera.current },
          moved: true,
          panOnly: true,
        };
    } else {
      if (!cancelled && current && !current.moved && !current.panOnly)
        options.current.onSelect(
          current.node
            ? current.node.id === options.current.selected
              ? undefined
              : current.node.id
            : undefined,
        );
      releaseNode();
      hover(undefined);
    }
    if (event.currentTarget.hasPointerCapture(event.pointerId))
      event.currentTarget.releasePointerCapture(event.pointerId);
  }
  function key(event: ReactKeyboardEvent<HTMLElement>) {
    if (event.key === "Escape") {
      onSelect(undefined);
      hover(undefined);
      invalidate();
      return;
    }
    if (
      ![
        "+",
        "=",
        "-",
        "0",
        "ArrowLeft",
        "ArrowRight",
        "ArrowUp",
        "ArrowDown",
      ].includes(event.key)
    )
      return;
    event.preventDefault();
    if (event.key === "+" || event.key === "=") zoomBy(1.2);
    else if (event.key === "-") zoomBy(1 / 1.2);
    else if (event.key === "0") fit();
    else {
      interacted.current = true;
      const amount = event.shiftKey ? 120 : 40;
      setCamera({
        ...camera.current,
        x:
          camera.current.x +
          (event.key === "ArrowLeft"
            ? amount
            : event.key === "ArrowRight"
              ? -amount
              : 0),
        y:
          camera.current.y +
          (event.key === "ArrowUp"
            ? amount
            : event.key === "ArrowDown"
              ? -amount
              : 0),
      });
    }
  }
  const hoverMemory =
    hovered && hovered !== selected ? byId.get(hovered) : undefined;
  const keyboardIds = new Set([...labelIds, ...(selected ? [selected] : [])]);
  return (
    <div
      className={`sp-vector-map sp-force-graph ${dragging ? "is-dragging" : ""}`}
    >
      <canvas
        ref={canvas}
        className="sp-graph-surface"
        data-layout="force"
        data-renderer="canvas"
        data-node-count={items.length}
        data-motion={motion}
        aria-label="Interactive memory network. Hold the middle mouse button and drag to pan anywhere. Drag memories or empty space, scroll or pinch to zoom. Select a memory for details; double-click to focus its connections. List view provides access to every memory."
        role="group"
        tabIndex={0}
        onPointerDown={startPointer}
        onPointerMove={movePointer}
        onPointerUp={(event) => endPointer(event)}
        onPointerCancel={(event) => endPointer(event, true)}
        onAuxClick={(event) => {
          if (event.button === 1) event.preventDefault();
        }}
        onLostPointerCapture={(event) => {
          if (pointers.current.has(event.pointerId)) endPointer(event, true);
        }}
        onPointerLeave={() => {
          if (!drag.current) {
            hoverPoint.current = null;
            hover(undefined);
            invalidate();
          }
        }}
        onDoubleClick={(event) => {
          const rect = event.currentTarget.getBoundingClientRect();
          const node =
            geometry.current &&
            hitGraphNode(
              geometry.current.nodes,
              { x: event.clientX - rect.left, y: event.clientY - rect.top },
              camera.current,
              bounds.current.width,
              bounds.current.height,
            );
          if (node) {
            onSelect(node.id);
            onFocus(node.id);
          }
        }}
        onKeyDown={key}
      >
        Use List view to browse every memory with a keyboard or screen reader.
      </canvas>
      <div className="sp-graph-keyboard-nodes" aria-label="Key memories">
        {[...keyboardIds].map((id) => {
          const item = byId.get(id);
          if (!item) return null;
          return (
            <button
              key={id}
              ref={(element) => {
                if (element) keyboardNodes.current.set(id, element);
                else keyboardNodes.current.delete(id);
              }}
              data-node-id={id}
              className="sp-graph-keyboard-node"
              aria-label={`${item.title}. Created by ${item.creator ?? "unknown"}.`}
              aria-pressed={selected === id}
              onFocus={() => {
                hover(id);
                invalidate();
              }}
              onBlur={() => {
                hover(undefined);
                invalidate();
              }}
              onClick={() => onSelect(id === selected ? undefined : id)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && event.shiftKey) {
                  event.preventDefault();
                  onFocus(id);
                } else key(event);
              }}
            />
          );
        })}
      </div>
      <div className="sp-graph-display">
        <button
          className={projectsOpen ? "active" : ""}
          aria-label="Choose visible projects"
          aria-expanded={projectsOpen}
          onClick={onProjectsToggle}
        >
          Projects
        </button>
        <button
          className={settingsOpen ? "active" : ""}
          aria-label="Graph settings"
          aria-expanded={settingsOpen}
          onClick={() => setSettingsOpen(!settingsOpen)}
        >
          <Icon name="sliders" size={16} />
          <span>Display</span>
        </button>
      </div>
      {settingsOpen && (
        <section
          className="sp-graph-settings"
          aria-label="Graph settings"
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              setSettingsOpen(false);
              event.stopPropagation();
            }
          }}
        >
          <div className="sp-graph-settings-head">
            <h2>Graph settings</h2>
            <button
              className="sp-icon-button"
              aria-label="Close graph settings"
              onClick={() => setSettingsOpen(false)}
            >
              <Icon name="close" size={15} />
            </button>
          </div>
          <label className="sp-graph-setting-inline">
            Color by
            <select
              aria-label="Color vectors by"
              value={colorBy}
              onChange={(event) => onColorBy(event.target.value)}
            >
              <option value="category">Category</option>
              <option value="creator">Creator</option>
            </select>
          </label>
          <label className="sp-graph-setting-inline">
            Show labels
            <input
              type="checkbox"
              checked={labels}
              onChange={(event) => setLabels(event.target.checked)}
            />
          </label>
          <div className="sp-graph-setting-group">
            <span>Layout</span>
            <label>
              Repulsion<output>{forces.repulsion}</output>
              <input
                type="range"
                aria-label="Node repulsion"
                min="10"
                max="70"
                step="5"
                value={forces.repulsion}
                onChange={(event) =>
                  setForces({
                    ...forces,
                    repulsion: Number(event.target.value),
                  })
                }
              />
            </label>
            <label>
              Link distance<output>{forces.distance}</output>
              <input
                type="range"
                aria-label="Link distance"
                min="20"
                max="110"
                step="2"
                value={forces.distance}
                onChange={(event) =>
                  setForces({ ...forces, distance: Number(event.target.value) })
                }
              />
            </label>
          </div>
          <button
            className="sp-graph-reset"
            onClick={() => {
              setForces(defaultForces);
              setLabels(true);
              onColorBy("category");
              setPaused(false);
            }}
          >
            Restore defaults
          </button>
        </section>
      )}
      {hoverMemory && (
        <div className="sp-graph-hover-card" aria-hidden="true">
          <span>
            <i style={{ background: color(hoverMemory) }} />
            {hoverMemory.creator ? `Created by ${hoverMemory.creator}` : "Memory"}
          </span>
          <strong>{hoverMemory.title}</strong>
          <p>{hoverMemory.value}</p>
        </div>
      )}
      <div className="sp-graph-controls">
        <button
          aria-label={paused ? "Resume graph motion" : "Pause graph motion"}
          aria-pressed={paused}
          title={paused ? "Resume layout" : "Pause layout"}
          onClick={() => setPaused(!paused)}
        >
          <Icon name={paused ? "play" : "pause"} size={14} />
        </button>
        <span className="sp-graph-control-divider" />
        <button aria-label="Zoom out" onClick={() => zoomBy(1 / 1.2)}>
          <Icon name="minus" size={14} />
        </button>
        <output ref={zoomOutput} aria-label="Graph zoom">
          100%
        </output>
        <button aria-label="Zoom in" onClick={() => zoomBy(1.2)}>
          <Icon name="plus" size={14} />
        </button>
        <button
          className="sp-graph-recenter"
          aria-label="Recenter graph"
          title="Recenter on all visible memories · 0"
          onClick={fit}
        >
          <Icon name="fit" size={16} />
          <span>Recenter</span>
        </button>
      </div>
      <div className="sp-graph-gesture-hint">
        Middle-drag to pan<span>·</span>Scroll to zoom<span>·</span>Double-click
        a memory to focus
      </div>
    </div>
  );
}
