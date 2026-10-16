/* One production graph on the page: laid out once per distinct shape, redrawn when its data or
 * its placed nodes change, with a frame whose zoom and scroll survive the page's redraws. The
 * page around it (planner or factory view) says where places are stored and what a pick does.
 * See docs/planner_vision.md §4.2. */

import { make } from "../../kit/dom";
import { zoomableFrame } from "../graph-frame";
import { drawGraphSvg, layoutInput } from "./draw";
import { showChain, wireGround, wireNodes } from "./interact";
import { computeLayout, orderOf, placeNodes } from "./layout";
import { elkGraph } from "./layout-input";

import type { FrameView } from "../frame-fit";
import type { NodeMarks, NodeStyle } from "./draw";
import type { LiveGraph } from "./interact";
import type { Layout, LayoutIn } from "./layout-input";
import type { ProdGraph, ProdNode } from "./model";

export type Positions = Record<string, [number, number]>;

export interface CanvasHost {
  /** Which graph this is; the order down each column is remembered per key. */
  key: string;
  /** The places to draw: stored ones with the page's newer ones laid over them. */
  positions(): Positions;
  marks(): NodeMarks;
  pick(node: ProdNode): void;
  clear(): void;
  moved(id: string, at: [number, number]): void;
  /** Asks the page to render again. */
  changed(): void;
}

export interface Canvas {
  view: ProdGraph | null;
  serial: number;
  sig: string;
  base: Layout | null;
  pending: string;
  drawn: string;
  live: LiveGraph | null;
  wrap: HTMLElement | null;
  x: number;
  y: number;
  zoom: FrameView;
  host: CanvasHost | null;
  orders: Record<string, Record<string, number>>;
  style: NodeStyle;
}

export function newCanvas(style?: NodeStyle): Canvas {
  return { view: null, serial: 0, sig: "", base: null, pending: "", drawn: "", live: null, wrap: null, x: 0, y: 0, zoom: { zoom: 1 }, host: null, orders: {}, style: style || "disc" };
}

/** Everything the layout depends on but the previous order. */
export function shapeKey(input: LayoutIn): string {
  return JSON.stringify([
    input.nodes.map(function (n) {
      return [n.id, n.rank, n.w, n.h, n.portY, n.below ? n.below.w : 0];
    }),
    input.edges.map(function (e) {
      return [e.id, e.source, e.target, e.labelW];
    }),
  ]);
}

/** Takes `view` as the graph to draw; a different object is a new drawing. */
export function useView(c: Canvas, view: ProdGraph): void {
  if (c.view !== view) {
    c.view = view;
    c.serial++;
  }
}

/** Draws again on the next frame even though nothing the draw key covers changed. */
export function redrawAll(c: Canvas): void {
  c.drawn = "";
  if (c.host) c.host.changed();
}

function drawKey(c: Canvas, marks: NodeMarks): string {
  return c.serial + "|" + JSON.stringify([c.host!.positions(), marks]);
}

function wrapper(c: Canvas): HTMLElement {
  if (c.wrap) return c.wrap;
  const wrap = make("div", "pg-wrap");
  wireGround(wrap, function () {
    if (c.host) c.host.clear();
  });
  wrap.addEventListener(
    "scroll",
    function (event) {
      const frame = event.target as HTMLElement;
      if (!frame.classList || !frame.classList.contains("graph-frame")) return;
      c.x = frame.scrollLeft;
      c.y = frame.scrollTop;
    },
    true
  );
  c.wrap = wrap;
  return wrap;
}

function draw(c: Canvas, view: ProdGraph, input: LayoutIn, key: string, marks: NodeMarks): void {
  const host = c.host!;
  const layout = placeNodes(input, c.base!, host.positions());
  const handle = drawGraphSvg(
    view,
    layout,
    marks,
    function () {
      redrawAll(c);
    },
    c.style
  );
  const live: LiveGraph = { handle: handle, view: view, input: input, layout: layout };
  const frame = zoomableFrame(handle.root, layout.width, layout.height, { fitAll: c.style !== "card", view: c.zoom });
  wrapper(c).replaceChildren(frame);
  wireNodes(live, c.wrap!, {
    pick: function (node) {
      c.host!.pick(node);
    },
    moved: function (id, at) {
      c.host!.moved(id, at);
      c.drawn = drawKey(c, marks);
      c.host!.changed();
    },
  });
  c.live = live;
  c.drawn = key;
}

function layOut(c: Canvas, input: LayoutIn, sig: string): void {
  if (c.pending === sig) return;
  c.pending = sig;
  const key = c.host!.key;
  const t0 = performance.now();
  computeLayout(input, function () {
    return elkGraph(input);
  }).then(function (base) {
    if (c.pending !== sig) return;
    c.base = base;
    c.sig = sig;
    c.zoom = { zoom: 1 };
    c.x = 0;
    c.y = 0;
    c.orders[key] = orderOf(base);
    if (import.meta.env.DEV) console.debug("graph laid out in " + Math.round(performance.now() - t0) + " ms, " + input.nodes.length + " nodes");
    if (c.host) c.host.changed();
  });
}

/** The graph's wrap, drawn or re-used; it keeps the last drawing while a new shape lays out.
 *  Marks that flash ask for one more render once `flashMs` has passed. */
export function canvasFrame(c: Canvas, host: CanvasHost, flashMs?: number): HTMLElement {
  c.host = host;
  const view = c.view!;
  const input = layoutInput(view, c.orders[host.key] || {}, c.style);
  const sig = shapeKey(input);
  const marks = host.marks();
  const key = drawKey(c, marks);
  const wrap = wrapper(c);
  if (c.base && c.sig === sig) {
    if (c.drawn !== key) {
      draw(c, view, input, key, marks);
      if (marks.flash.length && flashMs) setTimeout(host.changed, flashMs);
    }
  } else {
    layOut(c, input, sig);
    wrap.classList.add("pg-waiting");
    if (!c.live) wrap.replaceChildren(make("p", "dash-note", "laying the graph out…"));
    return wrap;
  }
  wrap.classList.remove("pg-waiting");
  return wrap;
}

/** Puts the frame's scroll back after the page re-attached it, and lights `picked`'s chain. */
export function settleFrame(c: Canvas, wrap: HTMLElement, picked: string): void {
  const frame = wrap.querySelector<HTMLElement>(".graph-frame");
  if (frame) {
    frame.scrollLeft = c.x;
    frame.scrollTop = c.y;
  }
  if (c.live) showChain(c.live, picked);
}
