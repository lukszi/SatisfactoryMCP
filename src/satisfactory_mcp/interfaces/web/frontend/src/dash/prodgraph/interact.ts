/* What the production graph does under the pointer: a click picks a node and lights its chain, a click
 * on open ground clears, hovering shows a short tip, and a dragged node stays where it is
 * dropped while its edges re-route around the other nodes as it moves. */

import { formatNumber, mw, pct } from "../../kit/format";
import { make } from "../../kit/dom";
import { chainOf } from "./chain";
import { drawHops, itemLines, placeNode, processTitle, routePoints, setRoute } from "./draw";
import { hopTargets } from "./hops";
import { reroute } from "./layout";
import { rateText } from "./model";

import type { GraphHandle } from "./draw";
import type { Layout, LayoutIn } from "./layout-input";
import type { ProdEdge, ProdGraph, ProdNode } from "./model";

export interface LiveGraph {
  handle: GraphHandle;
  view: ProdGraph;
  input: LayoutIn;
  layout: Layout;
}

export interface GraphCallbacks {
  pick(node: ProdNode): void;
  moved(id: string, at: [number, number]): void;
}

interface Drag {
  id: string;
  dx: number;
  dy: number;
  x0: number;
  y0: number;
  moved: boolean;
  edges: Set<string>;
  frame: number;
}

const SLOP = 4;

function ratesInto(view: ProdGraph, id: string, into: boolean): string {
  const sums: Record<string, ProdEdge & { total: number }> = {};
  const names: string[] = [];
  view.edges.forEach(function (e) {
    if ((into ? e.target : e.source) !== id) return;
    const key = e.itemName;
    if (!sums[key]) {
      sums[key] = { ...e, total: 0 };
      names.push(key);
    }
    sums[key]!.total += e.perMin;
  });
  if (!names.length) return "–";
  return names
    .map(function (name) {
      const s = sums[name]!;
      return s.group === "power" && s.text ? s.text : name + " " + rateText(s.total, s.unit);
    })
    .join(", ");
}

export function tipLines(view: ProdGraph, node: ProdNode): string[] {
  if (node.kind !== "process") return itemLines(node, view);
  const head = processTitle(node) + " · " + (node.recipeName || node.label);
  const power = node.mw ? " · " + mw(node.mw, { signed: true }) : "";
  const lines = [head, "clock " + pct(node.clock, 1) + " · " + formatNumber(node.machines, 2) + " machines of work" + power, "in: " + ratesInto(view, node.id, true), "out: " + ratesInto(view, node.id, false)];
  const built = builtLine(node);
  if (built) lines.push(built);
  return lines;
}

/** A built group's measured share and its machines by state; empty for a plan. */
export function builtLine(node: ProdNode): string {
  const parts: string[] = [];
  if (node.productivity !== null) {
    const some = node.monitored < node.built ? " of " + node.monitored + " monitored" : "";
    parts.push("producing " + pct(node.productivity, 0) + " of the time" + some);
  }
  const states = Object.keys(node.states).map(function (state) {
    return node.states[state] + " " + state;
  });
  if (states.length) parts.push(states.join(", "));
  return parts.join(" · ");
}

/** Lights `id`'s chain and dims the rest; an empty id clears. */
export function showChain(live: LiveGraph, id: string): void {
  const root = live.handle.root;
  const on = !!id && !!live.handle.nodes[id];
  root.classList.toggle("pg-chained", on);
  const chain = on ? chainOf(id, live.view.edges) : null;
  Object.keys(live.handle.nodes).forEach(function (n) {
    live.handle.nodes[n]!.classList.toggle("in-chain", !!chain && chain.nodes.has(n));
    live.handle.nodes[n]!.classList.toggle("picked", n === id);
  });
  Object.keys(live.handle.edges).forEach(function (e) {
    const lit = !!chain && chain.edges.has(e);
    live.handle.edges[e]!.classList.toggle("in-chain", lit);
    const label = live.handle.labels[e];
    if (label) label.classList.toggle("in-chain", lit);
  });
}

function svgPoint(root: SVGSVGElement, event: PointerEvent): { x: number; y: number } {
  const m = root.getScreenCTM();
  if (!m) return { x: event.clientX, y: event.clientY };
  const p = new DOMPoint(event.clientX, event.clientY).matrixTransform(m.inverse());
  return { x: p.x, y: p.y };
}

function rerouteDrag(live: LiveGraph, drag: Drag): void {
  const before = routePoints(live.layout);
  reroute(live.input, live.layout, drag.edges);
  live.view.edges.forEach(function (e) {
    if (drag.edges.has(e.id) && live.layout.routes[e.id]) setRoute(live.handle, e, live.layout.routes[e.id]!);
  });
  drawHops(live.handle, live.layout, hopTargets(routePoints(live.layout), drag.edges, before));
}

function wireTip(live: LiveGraph, wrap: HTMLElement, g: SVGGElement, node: ProdNode): void {
  const tip = function () {
    let box = wrap.querySelector<HTMLElement>(".pg-tip");
    if (!box) {
      box = make("div", "pg-tip");
      box.setAttribute("role", "tooltip");
      wrap.appendChild(box);
    }
    box.replaceChildren();
    tipLines(live.view, node).forEach(function (line, i) {
      box!.appendChild(make("div", i ? "" : "pg-tip-k", line));
    });
    const r = g.getBoundingClientRect();
    const w = wrap.getBoundingClientRect();
    box.style.left = Math.max(0, Math.round(r.left - w.left)) + "px";
    box.style.top = Math.round(r.bottom - w.top + 6) + "px";
    box.hidden = false;
  };
  const hide = function () {
    const box = wrap.querySelector<HTMLElement>(".pg-tip");
    if (box) box.hidden = true;
  };
  g.addEventListener("pointerenter", tip);
  g.addEventListener("focus", tip);
  g.addEventListener("pointerleave", hide);
  g.addEventListener("blur", hide);
}

function wireDrag(live: LiveGraph, g: SVGGElement, id: string, callbacks: GraphCallbacks): void {
  let drag: Drag | null = null;
  g.addEventListener("pointerdown", function (event) {
    if (event.button !== 0 || event.pointerType === "touch") return;
    event.stopPropagation();
    const p = svgPoint(live.handle.root, event);
    const box = live.layout.boxes[id]!;
    const edges = new Set<string>();
    live.view.edges.forEach(function (e) {
      if (e.source === id || e.target === id) edges.add(e.id);
    });
    drag = { id: id, dx: p.x - box.x, dy: p.y - box.y, x0: event.clientX, y0: event.clientY, moved: false, edges: edges, frame: 0 };
    g.setPointerCapture(event.pointerId);
  });
  g.addEventListener("pointermove", function (event) {
    if (!drag) return;
    if (!drag.moved && Math.abs(event.clientX - drag.x0) + Math.abs(event.clientY - drag.y0) < SLOP) return;
    drag.moved = true;
    g.classList.add("dragging");
    const p = svgPoint(live.handle.root, event);
    const box = live.layout.boxes[id]!;
    box.x = Math.round(Math.max(0, Math.min(live.layout.width - box.w, p.x - drag.dx)));
    box.y = Math.round(Math.max(0, Math.min(live.layout.height - box.h, p.y - drag.dy)));
    placeNode(live.handle, id, box);
    const d = drag;
    if (!d.frame) {
      d.frame = requestAnimationFrame(function () {
        d.frame = 0;
        rerouteDrag(live, d);
      });
    }
  });
  const end = function () {
    const d = drag;
    drag = null;
    if (!d || !d.moved) return;
    if (d.frame) cancelAnimationFrame(d.frame);
    g.classList.remove("dragging");
    rerouteDrag(live, d);
    const box = live.layout.boxes[id]!;
    swallow();
    callbacks.moved(id, [box.x, box.y]);
  };
  g.addEventListener("pointerup", end);
  g.addEventListener("pointercancel", end);
}

let swallowed = false;

function swallow(): void {
  swallowed = true;
  setTimeout(function () {
    swallowed = false;
  }, 0);
}

/** A click on open ground clears the pick; a pan that ends there does not. Wired once per wrap. */
export function wireGround(wrap: HTMLElement, clear: () => void): void {
  let downAt: { x: number; y: number } | null = null;
  wrap.addEventListener(
    "pointerdown",
    function (event) {
      downAt = { x: event.clientX, y: event.clientY };
    },
    true
  );
  wrap.addEventListener("click", function (event) {
    if (swallowed) return;
    const target = event.target as Element;
    if (target.closest(".pg-node, .pg-tip")) return;
    if (downAt && Math.abs(event.clientX - downAt.x) + Math.abs(event.clientY - downAt.y) >= SLOP) return;
    clear();
  });
}

export function wireNodes(live: LiveGraph, wrap: HTMLElement, callbacks: GraphCallbacks): void {
  live.view.nodes.forEach(function (node) {
    const g = live.handle.nodes[node.id];
    if (!g) return;
    g.addEventListener("click", function (event) {
      event.stopPropagation();
      if (!swallowed) callbacks.pick(node);
    });
    g.addEventListener("keydown", function (event) {
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
      callbacks.pick(node);
    });
    wireTip(live, wrap, g, node);
    wireDrag(live, g, node.id, callbacks);
  });
}
