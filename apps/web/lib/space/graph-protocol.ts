import type { GraphForces, GraphGroup, Point } from "./graph-layout";

export type GraphMode = { paused: boolean; reduced: boolean; hidden: boolean };
export type GraphCommand =
  | {
      type: "init";
      ids: string[];
      edges: { from: string; to: string }[];
      groups?: GraphGroup[];
      previous: [string, Point][];
      forces: GraphForces;
      mode: GraphMode;
    }
  | { type: "forces"; forces: GraphForces }
  | { type: "mode"; mode: GraphMode }
  | { type: "pin"; id: string; point: Point; moving: boolean }
  | { type: "release"; id: string; moved: boolean };

export type GraphFrame = {
  positions: Float32Array;
  motion: "paused" | "settling" | "settled";
};
