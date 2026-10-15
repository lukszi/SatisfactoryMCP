/* Laying a plan graph out: ELK in a Web Worker, a column layout routed by our own router when no
 * worker can run, and the player's placed nodes laid over either with their edges re-routed. */

import ElkApi from "elkjs/lib/elk-api.js";
import elkWorkerUrl from "elkjs/lib/elk-worker.min.js?url";

import { footprint, ordered, PAD, readElk, usableEdges } from "./layout-input";
import { placeOnLine } from "./labels";
import { routeOrthogonal } from "./router";

import type { ELK, ElkNode } from "elkjs/lib/elk-api";
import type { Layout, LayoutEdgeIn, LayoutIn, LayoutNodeIn } from "./layout-input";
import type { Pt, Rect } from "./router";

const WORKER_PATIENCE_MS = 15000;
const COLUMN_GAP = 140;
const ROW_GAP = 36;

let engine: ELK | null = null;
let broken = false;

function startEngine(): ELK | null {
  if (broken || typeof Worker === "undefined") return null;
  if (!engine) {
    engine = new ElkApi({
      workerFactory: function () {
        const worker = new Worker(elkWorkerUrl);
        worker.addEventListener("error", function () {
          broken = true;
        });
        return worker;
      },
    });
  }
  return engine;
}

/** ELK's answer, or a rejection once the worker has taken too long; a dead worker never answers. */
function inWorker(elk: ELK, graph: ElkNode): Promise<ElkNode> {
  return new Promise(function (resolve, reject) {
    const timer = setTimeout(function () {
      broken = true;
      reject(new Error("layout worker did not answer"));
    }, WORKER_PATIENCE_MS);
    elk.layout(graph).then(
      function (out) {
        clearTimeout(timer);
        resolve(out);
      },
      function (error: unknown) {
        clearTimeout(timer);
        reject(error);
      }
    );
  });
}

function byId(input: LayoutIn): Record<string, LayoutNodeIn> {
  const out: Record<string, LayoutNodeIn> = {};
  input.nodes.forEach(function (n) {
    out[n.id] = n;
  });
  return out;
}

/** Each node's box and the label under it, separately, so a port beside a wide label is free. */
export function obstacles(input: LayoutIn, boxes: Record<string, Rect>): Rect[] {
  const out: Rect[] = [];
  input.nodes.forEach(function (n) {
    const box = boxes[n.id];
    if (!box) return;
    out.push(box);
    if (n.below) {
      const all = footprint(box, n);
      out.push({ x: all.x, y: box.y + box.h, w: all.w, h: all.h - box.h });
    }
  });
  return out;
}

export function ports(edge: LayoutEdgeIn, nodes: Record<string, LayoutNodeIn>, boxes: Record<string, Rect>): [Pt, Pt] | null {
  const a = boxes[edge.source];
  const b = boxes[edge.target];
  const na = nodes[edge.source];
  const nb = nodes[edge.target];
  if (!a || !b || !na || !nb) return null;
  return [
    { x: a.x + a.w, y: a.y + na.portY },
    { x: b.x, y: b.y + nb.portY },
  ];
}

/** Routes and labels again for every edge in `which`, around every node where it now stands. */
export function reroute(input: LayoutIn, layout: Layout, which: Set<string>): void {
  const nodes = byId(input);
  const walls = obstacles(input, layout.boxes);
  const taken = walls.slice();
  const edges = usableEdges(input);
  edges.forEach(function (e) {
    const keep = layout.routes[e.id];
    if (!which.has(e.id) && keep && keep.label) taken.push(keep.label);
  });
  edges.forEach(function (e) {
    if (!which.has(e.id)) return;
    const ends = ports(e, nodes, layout.boxes);
    if (!ends) return;
    const points = routeOrthogonal(ends[0], ends[1], walls);
    const label = placeOnLine(points, e.labelW, e.labelH, taken);
    if (label) taken.push(label);
    layout.routes[e.id] = { points: points, label: label };
  });
}

export function fitBounds(input: LayoutIn, layout: Layout): void {
  let right = 0;
  let bottom = 0;
  const nodes = byId(input);
  Object.keys(layout.boxes).forEach(function (id) {
    const f = footprint(layout.boxes[id]!, nodes[id]);
    right = Math.max(right, f.x + f.w);
    bottom = Math.max(bottom, f.y + f.h);
  });
  Object.keys(layout.routes).forEach(function (id) {
    const r = layout.routes[id]!;
    r.points.forEach(function (p) {
      right = Math.max(right, p.x);
      bottom = Math.max(bottom, p.y);
    });
    if (r.label) {
      right = Math.max(right, r.label.x + r.label.w);
      bottom = Math.max(bottom, r.label.y + r.label.h);
    }
  });
  layout.width = Math.ceil(right + PAD);
  layout.height = Math.ceil(bottom + PAD);
}

/** Columns by rank in the model order, every edge routed by our router: the no-worker layout. */
export function columnLayout(input: LayoutIn): Layout {
  const columns: LayoutNodeIn[][] = [];
  ordered(input).forEach(function (n) {
    const at = Math.max(0, Math.round(n.rank));
    (columns[at] = columns[at] || []).push(n);
  });
  const boxes: Record<string, Rect> = {};
  let x = PAD;
  columns.forEach(function (column) {
    let y = PAD;
    let widest = 0;
    column.forEach(function (n) {
      const f = footprint({ x: 0, y: 0, w: n.w, h: n.h }, n);
      widest = Math.max(widest, f.w);
      boxes[n.id] = { x: 0, y: y, w: n.w, h: n.h };
      y += f.h + ROW_GAP;
    });
    column.forEach(function (n) {
      boxes[n.id]!.x = Math.round(x + (widest - n.w) / 2);
    });
    x += widest + COLUMN_GAP;
  });
  const layout: Layout = { boxes: boxes, routes: {}, width: 0, height: 0 };
  reroute(
    input,
    layout,
    new Set(
      usableEdges(input).map(function (e) {
        return e.id;
      })
    )
  );
  fitBounds(input, layout);
  return layout;
}

/** The player's placements laid over a layout, with every edge they touch routed again. */
export function placeNodes(input: LayoutIn, base: Layout, positions: Record<string, [number, number]>): Layout {
  const layout: Layout = { boxes: {}, routes: {}, width: base.width, height: base.height };
  const moved = new Set<string>();
  Object.keys(base.boxes).forEach(function (id) {
    const box = base.boxes[id]!;
    const at = positions[id];
    layout.boxes[id] = at ? { x: Math.max(0, Math.round(at[0])), y: Math.max(0, Math.round(at[1])), w: box.w, h: box.h } : { ...box };
    if (at && (at[0] !== box.x || at[1] !== box.y)) moved.add(id);
  });
  Object.keys(base.routes).forEach(function (id) {
    layout.routes[id] = base.routes[id]!;
  });
  if (!moved.size) return layout;
  const touched = new Set<string>();
  usableEdges(input).forEach(function (e) {
    if (moved.has(e.source) || moved.has(e.target)) touched.add(e.id);
  });
  reroute(input, layout, touched);
  fitBounds(input, layout);
  return layout;
}

/** An ELK layout with every edge it left unrouted or unlabelled routed by ours. */
export function completed(input: LayoutIn, layout: Layout): Layout {
  const missing = new Set<string>();
  const unlabelled: LayoutEdgeIn[] = [];
  usableEdges(input).forEach(function (e) {
    const r = layout.routes[e.id];
    if (!r) missing.add(e.id);
    else if (!r.label) unlabelled.push(e);
  });
  if (missing.size) reroute(input, layout, missing);
  if (unlabelled.length) {
    const taken = obstacles(input, layout.boxes);
    Object.keys(layout.routes).forEach(function (id) {
      const label = layout.routes[id]!.label;
      if (label) taken.push(label);
    });
    unlabelled.forEach(function (e) {
      const r = layout.routes[e.id]!;
      r.label = placeOnLine(r.points, e.labelW, e.labelH, taken);
      if (r.label) taken.push(r.label);
    });
  }
  if (missing.size || unlabelled.length) fitBounds(input, layout);
  return layout;
}

/** ELK's layout of `input`, or the column layout when no worker can run or ELK fails. */
export function computeLayout(input: LayoutIn, graph: () => ElkNode): Promise<Layout> {
  const elk = startEngine();
  if (!elk) return Promise.resolve(columnLayout(input));
  return inWorker(elk, graph())
    .then(function (out) {
      return completed(input, readElk(out));
    })
    .catch(function () {
      return columnLayout(input);
    });
}

/** Each node's height down the page, the order a re-solve starts from. */
export function orderOf(layout: Layout): Record<string, number> {
  const out: Record<string, number> = {};
  Object.keys(layout.boxes).forEach(function (id) {
    out[id] = layout.boxes[id]!.y;
  });
  return out;
}
