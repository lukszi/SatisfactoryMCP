/* What the layout is asked and what it answers: node boxes with one west and one east port at
 * a fixed height, labelled edges, and the ELK graph and options built from them. Pure, so the
 * worker round trip and the fallback share it and the tests can drive it in node. */

import type { ElkExtendedEdge, ElkNode } from "elkjs/lib/elk-api";
import type { Pt, Rect } from "./router";

export interface LayoutNodeIn {
  id: string;
  rank: number;
  w: number;
  h: number;
  /** Height of both ports, from the box's top. */
  portY: number;
  /** A label under the box, drawn by us but reserved by the layout. */
  below: { w: number; h: number } | null;
}

export interface LayoutEdgeIn {
  id: string;
  source: string;
  target: string;
  labelW: number;
  labelH: number;
}

export interface LayoutIn {
  nodes: LayoutNodeIn[];
  edges: LayoutEdgeIn[];
  /** Each node's place down its column last time, so a re-solve keeps the order. */
  order: Record<string, number>;
}

export interface EdgeRoute {
  points: Pt[];
  label: Rect | null;
}

export interface Layout {
  boxes: Record<string, Rect>;
  routes: Record<string, EdgeRoute>;
  width: number;
  height: number;
}

export const PAD = 24;
const BELOW_GAP = 4;

const ROOT_OPTIONS: Record<string, string> = {
  "elk.algorithm": "layered",
  "elk.direction": "RIGHT",
  "elk.edgeRouting": "ORTHOGONAL",
  "elk.edgeLabels.inline": "true",
  "elk.partitioning.activate": "true",
  "elk.layered.considerModelOrder.strategy": "NODES_AND_EDGES",
  "elk.layered.spacing.nodeNodeBetweenLayers": "56",
  "elk.layered.spacing.edgeNodeBetweenLayers": "18",
  "elk.layered.spacing.edgeEdgeBetweenLayers": "10",
  "elk.spacing.nodeNode": "28",
  "elk.spacing.edgeEdge": "10",
  "elk.spacing.edgeNode": "14",
  "elk.spacing.edgeLabel": "2",
  "elk.spacing.labelNode": String(BELOW_GAP),
  "elk.padding": "[top=" + PAD + ",left=" + PAD + ",bottom=" + PAD + ",right=" + PAD + "]",
};

export function ordered(input: LayoutIn): LayoutNodeIn[] {
  const index: Record<string, number> = {};
  input.nodes.forEach(function (n, i) {
    index[n.id] = i;
  });
  const before = function (id: string): number {
    const at = input.order[id];
    return at === undefined ? Infinity : at;
  };
  return input.nodes.slice().sort(function (a, b) {
    return a.rank - b.rank || before(a.id) - before(b.id) || index[a.id]! - index[b.id]!;
  });
}

export function usableEdges(input: LayoutIn): LayoutEdgeIn[] {
  const known = new Set(
    input.nodes.map(function (n) {
      return n.id;
    })
  );
  return input.edges.filter(function (e) {
    return e.source !== e.target && known.has(e.source) && known.has(e.target);
  });
}

export function elkGraph(input: LayoutIn): ElkNode {
  const children: ElkNode[] = ordered(input).map(function (n) {
    const child: ElkNode = {
      id: n.id,
      width: n.w,
      height: n.h,
      layoutOptions: {
        "elk.portConstraints": "FIXED_POS",
        "elk.partitioning.partition": String(Math.max(0, Math.round(n.rank))),
        "elk.nodeLabels.placement": "OUTSIDE V_BOTTOM H_CENTER",
      },
      ports: [
        { id: n.id + "|in", x: 0, y: n.portY, width: 0, height: 0, layoutOptions: { "elk.port.side": "WEST" } },
        { id: n.id + "|out", x: n.w, y: n.portY, width: 0, height: 0, layoutOptions: { "elk.port.side": "EAST" } },
      ],
    };
    if (n.below) child.labels = [{ text: n.id, width: n.below.w, height: n.below.h }];
    return child;
  });
  const edges: ElkExtendedEdge[] = usableEdges(input).map(function (e) {
    return {
      id: e.id,
      sources: [e.source + "|out"],
      targets: [e.target + "|in"],
      labels: [{ text: e.id, width: e.labelW, height: e.labelH, layoutOptions: { "elk.edgeLabels.placement": "CENTER" } }],
    };
  });
  return { id: "root", layoutOptions: ROOT_OPTIONS, children: children, edges: edges };
}

function round(v: number): number {
  return Math.round(v);
}

export function readElk(out: ElkNode): Layout {
  const boxes: Record<string, Rect> = {};
  const routes: Record<string, EdgeRoute> = {};
  (out.children || []).forEach(function (c) {
    boxes[c.id] = { x: round(c.x || 0), y: round(c.y || 0), w: round(c.width || 0), h: round(c.height || 0) };
  });
  (out.edges || []).forEach(function (e) {
    const section = (e as ElkExtendedEdge).sections?.[0];
    if (!section) return;
    const points = [section.startPoint].concat(section.bendPoints || [], [section.endPoint]).map(function (p) {
      return { x: round(p.x), y: round(p.y) };
    });
    const l = e.labels?.[0];
    const label = l && l.x !== undefined && l.y !== undefined ? { x: round(l.x), y: round(l.y), w: round(l.width || 0), h: round(l.height || 0) } : null;
    routes[e.id] = { points: points, label: label };
  });
  return { boxes: boxes, routes: routes, width: round(out.width || 0), height: round(out.height || 0) };
}

/** The box plus the label reserved under it: what an edge must route around. */
export function footprint(box: Rect, node: LayoutNodeIn | undefined): Rect {
  if (!node || !node.below) return box;
  const w = Math.max(box.w, node.below.w);
  return { x: box.x + box.w / 2 - w / 2, y: box.y, w: w, h: box.h + BELOW_GAP + node.below.h };
}
