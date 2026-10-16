/* The production graph's SVG: round process nodes with a utilisation ring, item boxes,
 * orthogonal edges in their item group's colour with an arrow and a "Item rate" label. Positions
 * come from layout.ts; this module only measures and draws. See docs/planner_vision.md §4.2. */

import { count, pct } from "../../kit/format";
import { hoppedPaths } from "./hops";
import { iconIdFor, iconUrl } from "./icons";
import { edgeLabel, ITEM_GROUPS, rateText } from "./model";
import { measuredRing, ringFor, ringPath } from "./ring";
import { LINE, measure, SMALL_FONT, svg, svgText, TITLE_FONT } from "./text";

import type { EdgeRoute, Layout, LayoutIn, LayoutNodeIn } from "./layout-input";
import type { ProdEdge, ProdGraph, ProdNode } from "./model";
import type { Pt, Rect } from "./router";

/** Per node: the tags it carries, whether it flashes, and the outline tone of its worst state. */
export interface NodeMarks {
  badges: Record<string, string[]>;
  flash: string[];
  tones?: Record<string, string>;
}

/** "disc": a round process node with its label below (plans); "card": a card with the building
 *  in a disc and the recipe beside it (built factories), drawn at 1:1 rather than fitted. */
export type NodeStyle = "disc" | "card";

export interface GraphHandle {
  root: SVGSVGElement;
  nodes: Record<string, SVGGElement>;
  edges: Record<string, SVGPathElement>;
  labels: Record<string, SVGGElement>;
  labelLayer: SVGGElement;
}

const DISC = 52;
const RING_R = 23;
const ITEM_H = 38;
const ICON = 20;
const LABEL_PAD = 4;
const GLYPH = "M-9,8 L-9,-1 L-4,2 L-4,-2 L1,1 L1,-8 L6,-8 L6,8 Z";
const BOLT = "M2,-9 L-6,2 L-1,2 L-3,9 L6,-3 L1,-3 Z";
const CARD_H = 44;
const CARD_DISC = 15;
/* Line width steps by rate, per minute; the class names the step (w1 thinnest). */
const RATE_STEPS = [60, 240, 780];

export function rateStep(perMin: number): string {
  let step = 1;
  RATE_STEPS.forEach(function (limit) {
    if (perMin > limit) step++;
  });
  return "w" + step;
}

function cardLines(node: ProdNode): [string, string] {
  const name = node.recipeName || node.label;
  const clock = node.clock === null ? "" : " · " + pct(node.clock, 1);
  return [name, "×" + count(node.built) + " " + (node.building || "machine") + clock];
}

export function processTitle(node: ProdNode): string {
  return "×" + count(node.built) + " " + (node.building || "machine");
}

function processSub(node: ProdNode): string {
  const name = node.recipeName || node.label;
  return node.clock === null ? name : name + " · " + pct(node.clock, 1);
}

/** An item node's rate: the server's, or the sum of what flows through it. */
export function nodeRate(node: ProdNode, view: ProdGraph): number | null {
  if (node.perMin !== null) return node.perMin;
  let sum = 0;
  let any = false;
  view.edges.forEach(function (e) {
    const through = node.kind === "input" ? e.source === node.id : e.target === node.id;
    if (through && e.group !== "power") {
      sum += e.perMin;
      any = true;
    }
  });
  return any ? sum : null;
}

export function itemLines(node: ProdNode, view: ProdGraph): [string, string] {
  const name = node.itemName || node.label;
  const rate = nodeRate(node, view);
  const amount = rate === null ? node.detail : rateText(rate, node.unit);
  if (node.note) return [name, amount + " · " + node.note];
  if (node.kind === "leftover") return [name, "surplus " + amount];
  if (node.kind === "sink") return [name, "sunk " + amount + (node.built ? " · ×" + count(node.built) + " sinks" : "")];
  if (node.kind === "input") return [name, amount + " in"];
  return [name, amount];
}

export function layoutInput(view: ProdGraph, order: Record<string, number>, style?: NodeStyle): LayoutIn {
  const nodes: LayoutNodeIn[] = view.nodes.map(function (n) {
    if (n.kind === "process" && style === "card") {
      const [a, b] = cardLines(n);
      const w = 2 * CARD_DISC + 26 + Math.max(measure(a, TITLE_FONT), measure(b, SMALL_FONT));
      return { id: n.id, rank: n.rank, w: Math.ceil(w), h: CARD_H, portY: CARD_H / 2, below: null };
    }
    if (n.kind === "process") {
      const w = Math.max(measure(processTitle(n), TITLE_FONT), measure(processSub(n), SMALL_FONT)) + 2 * LABEL_PAD;
      return { id: n.id, rank: n.rank, w: DISC, h: DISC, portY: DISC / 2, below: { w: w, h: 2 * LINE } };
    }
    const [a, b] = itemLines(n, view);
    const w = ICON + 18 + Math.max(measure(a, TITLE_FONT), measure(b, SMALL_FONT));
    return { id: n.id, rank: n.rank, w: Math.ceil(w), h: ITEM_H, portY: ITEM_H / 2, below: null };
  });
  const edges = view.edges.map(function (e) {
    return { id: e.id, source: e.source, target: e.target, labelW: measure(edgeLabel(e), SMALL_FONT) + 2 * LABEL_PAD, labelH: LINE + 2 };
  });
  return { nodes: nodes, edges: edges, order: order };
}

export function pathData(points: Pt[]): string {
  return points
    .map(function (p, i) {
      return (i ? "L" : "M") + p.x + "," + p.y;
    })
    .join(" ");
}

function arrowMarkers(defs: SVGDefsElement): void {
  ITEM_GROUPS.forEach(function (group) {
    const marker = svg("marker", { id: "pg-arrow-" + group, viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 6, markerHeight: 6, orient: "auto-start-reverse", markerUnits: "strokeWidth" });
    marker.appendChild(svg("path", { d: "M0,0 L10,5 L0,10 z", class: "pg-arrowhead grp-" + group }));
    defs.appendChild(marker);
  });
}

function glyphOrIcon(group: SVGGElement, node: ProdNode, redraw: () => void, cx: number, cy: number, size: number): void {
  const url = iconUrl(iconIdFor(node), redraw);
  const power = node.itemId === "power" || node.unit === "MW";
  const glyph = svg("path", { d: power ? BOLT : GLYPH, class: "pg-glyph", transform: "translate(" + cx + "," + cy + ") scale(" + size / 20 + ")" });
  group.appendChild(glyph);
  if (!url) return;
  const image = svg("image", { href: url, x: cx - size / 2, y: cy - size / 2, width: size, height: size });
  glyph.setAttribute("visibility", "hidden");
  image.addEventListener("error", function () {
    image.remove();
    glyph.removeAttribute("visibility");
  });
  group.appendChild(image);
}

function drawProcess(group: SVGGElement, node: ProdNode, box: Rect, redraw: () => void): void {
  const c = box.w / 2;
  group.appendChild(svg("circle", { class: "pg-disc", cx: c, cy: c, r: RING_R }));
  group.appendChild(svg("circle", { class: "pg-ring-track", cx: c, cy: c, r: RING_R }));
  const ring = node.productivity === null ? ringFor(node.machines, node.utilisation) : measuredRing(node.productivity);
  const d = ringPath(c, c, RING_R, ring.fraction);
  if (d) group.appendChild(svg("path", { class: "pg-ring tone-" + ring.tone, d: d }));
  glyphOrIcon(group, node, redraw, c, c, 26);
  const top = box.h + LABEL_PAD;
  group.appendChild(svgText("pg-title", c, top + LINE - 3, processTitle(node)));
  group.appendChild(svgText("pg-sub", c, top + 2 * LINE - 3, processSub(node)));
}

function drawCard(group: SVGGElement, node: ProdNode, box: Rect, redraw: () => void): void {
  const c = CARD_H / 2;
  group.appendChild(svg("rect", { class: "pg-card", width: box.w, height: box.h, rx: 4 }));
  group.appendChild(svg("circle", { class: "pg-disc", cx: c, cy: c, r: CARD_DISC }));
  if (node.productivity !== null) {
    const d = ringPath(c, c, CARD_DISC, measuredRing(node.productivity).fraction);
    if (d) group.appendChild(svg("path", { class: "pg-ring tone-" + measuredRing(node.productivity).tone, d: d }));
  }
  glyphOrIcon(group, node, redraw, c, c, 20);
  const [a, b] = cardLines(node);
  const x = 2 * CARD_DISC + 12;
  const title = svgText("pg-title", x, c - 3, a);
  title.setAttribute("text-anchor", "start");
  const sub = svgText("pg-sub", x, c + LINE - 3, b);
  sub.setAttribute("text-anchor", "start");
  group.appendChild(title);
  group.appendChild(sub);
}

function drawItem(group: SVGGElement, node: ProdNode, box: Rect, view: ProdGraph, redraw: () => void): void {
  group.appendChild(svg("rect", { class: "pg-box", width: box.w, height: box.h, rx: 6 }));
  glyphOrIcon(group, node, redraw, 6 + ICON / 2, box.h / 2, ICON);
  const [a, b] = itemLines(node, view);
  const x = ICON + 12;
  const title = svgText("pg-title", x, LINE, a);
  title.setAttribute("text-anchor", "start");
  const sub = svgText("pg-sub", x, 2 * LINE + 1, b);
  sub.setAttribute("text-anchor", "start");
  group.appendChild(title);
  group.appendChild(sub);
}

function drawBadges(group: SVGGElement, words: string[], centre: number): void {
  let width = 0;
  const sizes = words.map(function (word) {
    const w = measure(word, SMALL_FONT) + 8;
    width += w + 3;
    return w;
  });
  let x = centre - (width - 3) / 2;
  words.forEach(function (word, i) {
    const w = sizes[i]!;
    const tag = svg("g", { class: "graph-badge", transform: "translate(" + x + "," + -(LINE + 2) + ")" });
    tag.appendChild(svg("rect", { width: w, height: LINE, rx: 3 }));
    tag.appendChild(svgText("", w / 2, LINE - 3, word));
    group.appendChild(tag);
    x += w + 3;
  });
}

function drawNode(node: ProdNode, box: Rect, view: ProdGraph, marks: NodeMarks, redraw: () => void, style?: NodeStyle): SVGGElement {
  const flash = marks.flash.indexOf(node.id) >= 0 ? " flash" : "";
  const tone = marks.tones && marks.tones[node.id] ? " state-" + marks.tones[node.id] : "";
  const group = svg("g", { class: "pg-node kind-" + node.kind + flash + tone, transform: "translate(" + box.x + "," + box.y + ")", "data-node": node.id, tabindex: 0, role: "button" });
  if (node.itemName) group.setAttribute("data-item", node.itemName);
  if (node.kind === "process") {
    if (style === "card") drawCard(group, node, box, redraw);
    else drawProcess(group, node, box, redraw);
    const badges = marks.badges[node.id];
    if (badges && badges.length) drawBadges(group, badges, box.w / 2);
    group.setAttribute("aria-label", processTitle(node) + ", " + processSub(node));
  } else {
    drawItem(group, node, box, view, redraw);
    group.setAttribute("aria-label", node.kind + ": " + itemLines(node, view).join(", "));
  }
  return group;
}

export function drawLabel(layer: SVGGElement, edge: ProdEdge, place: Rect): SVGGElement {
  const g = svg("g", { class: "pg-label grp-" + edge.group, "data-edge": edge.id, "data-item": edge.itemName });
  g.appendChild(svg("rect", { x: place.x, y: place.y, width: place.w, height: place.h, rx: 3 }));
  g.appendChild(svgText("pg-label-text", place.x + place.w / 2, place.y + place.h - 4, edgeLabel(edge)));
  layer.appendChild(g);
  return g;
}

export function setRoute(handle: GraphHandle, edge: ProdEdge, route: EdgeRoute): void {
  const path = handle.edges[edge.id];
  if (path) path.setAttribute("d", pathData(route.points));
  const old = handle.labels[edge.id];
  if (old) old.remove();
  delete handle.labels[edge.id];
  if (route.label) handle.labels[edge.id] = drawLabel(handle.labelLayer, edge, route.label);
}

export function routePoints(layout: Layout): Record<string, Pt[]> {
  const out: Record<string, Pt[]> = {};
  Object.keys(layout.routes).forEach(function (id) {
    out[id] = layout.routes[id]!.points;
  });
  return out;
}

/** Redraws the edges in `which` with their line jumps over every other edge. */
export function drawHops(handle: GraphHandle, layout: Layout, which: Iterable<string>): void {
  const paths = hoppedPaths(routePoints(layout), which);
  Object.keys(paths).forEach(function (id) {
    const path = handle.edges[id];
    if (path) path.setAttribute("d", paths[id]!);
  });
}

export function placeNode(handle: GraphHandle, id: string, box: Rect): void {
  const g = handle.nodes[id];
  if (g) g.setAttribute("transform", "translate(" + box.x + "," + box.y + ")");
}

export function drawGraphSvg(view: ProdGraph, layout: Layout, marks: NodeMarks, redraw: () => void, style?: NodeStyle): GraphHandle {
  const root = svg("svg", { class: "graph pg", viewBox: "0 0 " + layout.width + " " + layout.height, role: "group", "aria-label": "production graph" });
  const defs = svg("defs", {});
  arrowMarkers(defs);
  root.appendChild(defs);
  root.appendChild(svg("rect", { class: "pg-ground", width: layout.width, height: layout.height }));
  const edgeLayer = svg("g", {});
  const nodeLayer = svg("g", {});
  const labelLayer = svg("g", {});
  root.appendChild(edgeLayer);
  root.appendChild(nodeLayer);
  root.appendChild(labelLayer);
  const handle: GraphHandle = { root: root, nodes: {}, edges: {}, labels: {}, labelLayer: labelLayer };
  const hopped = hoppedPaths(routePoints(layout), Object.keys(layout.routes));
  view.edges.forEach(function (e) {
    const route = layout.routes[e.id];
    if (!route) return;
    const path = svg("path", { class: "pg-edge grp-" + e.group + " " + rateStep(e.perMin), d: hopped[e.id] || pathData(route.points), "marker-end": "url(#pg-arrow-" + e.group + ")", "data-edge": e.id, "data-item": e.itemName });
    const title = svg("title", {});
    title.textContent = edgeLabel(e);
    path.appendChild(title);
    edgeLayer.appendChild(path);
    handle.edges[e.id] = path;
    if (route.label) handle.labels[e.id] = drawLabel(labelLayer, e, route.label);
  });
  view.nodes.forEach(function (n) {
    const box = layout.boxes[n.id];
    if (!box) return;
    const g = drawNode(n, box, view, marks, redraw, style);
    nodeLayer.appendChild(g);
    handle.nodes[n.id] = g;
  });
  return handle;
}
