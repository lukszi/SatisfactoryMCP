/* A production graph as a left-to-right SVG: inputs, recipe groups by longest path, terminals
 * last. Generic over its node shape so a plan can be drawn with it too. See
 * docs/frontend_vision.md §9.8. */

import { button } from "../kit/dashkit";
import { make } from "../kit/dom";
import { perMin } from "../kit/format";
import { stateTone } from "./machine-states";

import type { StateTone } from "./machine-states";

export interface GraphNodeShape {
  id: string;
  kind: string;
  label: string;
  detail: string;
  machines: number;
  running: number;
  blocked: number;
  stopped: number;
  states?: Record<string, number>;
  rank?: number;
  badges?: string[];
}

export interface DrawOptions<N> {
  pickable?: (node: N) => boolean;
  picked?: string;
  flash?: string[];
}

export interface GraphEdgeShape {
  source: string;
  target: string;
  item: string;
  per_min: number | null;
  text?: string;
}

export interface GraphShape<N extends GraphNodeShape> {
  nodes: N[];
  edges: GraphEdgeShape[];
}

interface Box {
  x: number;
  y: number;
  w: number;
  h: number;
}

interface Mix {
  word: string;
  count: number;
  tone: StateTone;
}

interface GraphLayout {
  boxes: Record<string, Box>;
  width: number;
  height: number;
  nodeHeight: number;
}

interface EdgeCurve {
  x1: number;
  y1: number;
  c1x: number;
  c1y: number;
  c2x: number;
  c2y: number;
  x2: number;
  y2: number;
}

const SVG = "http://www.w3.org/2000/svg";
const NODE_MIN = 96;
const NODE_MAX = 250;
const NODE_PAD = 10;
const GAP_MIN = 48;
const GAP_MAX = 160;
const GAP_Y = 30;
const LINE = 14;
const PAD = 12;
const TERMINAL = ["storage", "export", "sink", "nowhere"];
const SEVERITY: StateTone[] = ["bad", "blocked", "mid", "ok"];
const MAX_SCALE = 14 / 12;
const ZOOM = [0.25, 3];
const SMALL_FONT = "11px";
const TITLE_FONT = "600 12px";
const LABEL_NUDGES_Y = [0, -LINE / 2, LINE / 2, -LINE, LINE];
const LABEL_STOPS_T = [0.5, 0.4, 0.6, 0.3, 0.7, 0.2, 0.8];

export const GRAPH_HINT = "ctrl+scroll zooms, drag pans, double-click fits";

let ruler: CanvasRenderingContext2D | null = null;
let family = "sans-serif";

function measure(text: string, font: string): number {
  if (!ruler) ruler = document.createElement("canvas").getContext("2d");
  if (!ruler) return text.length * 6.5;
  ruler.font = font + " " + family;
  return ruler.measureText(text).width;
}

function clipToWidth(text: string, font: string, max: number): string {
  if (measure(text, font) <= max) return text;
  let cut = text.length;
  while (cut > 1 && measure(text.slice(0, cut) + "…", font) > max) cut--;
  return text.slice(0, cut) + "…";
}

function svg<K extends keyof SVGElementTagNameMap>(tag: K, attrs: Record<string, string | number>): SVGElementTagNameMap[K] {
  const node = document.createElementNS(SVG, tag) as SVGElementTagNameMap[K];
  Object.keys(attrs).forEach(function (name) {
    node.setAttribute(name, String(attrs[name]));
  });
  return node;
}

function edgeText(edge: GraphEdgeShape): string {
  if (edge.text) return edge.text;
  return edge.per_min === null ? edge.item : edge.item + " " + perMin(edge.per_min);
}

function edgeLines(edge: GraphEdgeShape, room: number): string[] {
  const oneLine = edgeText(edge);
  if (measure(oneLine, SMALL_FONT) <= room || edge.text || edge.per_min === null) return [clipToWidth(oneLine, SMALL_FONT, room)];
  return [clipToWidth(edge.item, SMALL_FONT, room), perMin(edge.per_min)];
}

function edgeNeed(edge: GraphEdgeShape): number {
  const oneLine = measure(edgeText(edge), SMALL_FONT);
  if (edge.text || edge.per_min === null) return oneLine;
  return Math.max(measure(edge.item, SMALL_FONT), measure(perMin(edge.per_min), SMALL_FONT));
}

function stateMix(node: GraphNodeShape): Mix[] {
  const rows: Mix[] = [];
  const add = function (word: string, count: number, tone: StateTone) {
    if (!count) return;
    const same = rows.filter(function (row) {
      return row.word === word;
    })[0];
    if (same) same.count += count;
    else rows.push({ word: word, count: count, tone: tone });
  };
  if (node.states) {
    const states = node.states;
    Object.keys(states).forEach(function (state) {
      const tone = stateTone(state);
      add(tone === "ok" ? "running" : state, states[state]!, tone);
    });
  } else {
    add("running", node.running, "ok");
    add("blocked", node.blocked, "blocked");
    add("not running", node.stopped, "bad");
  }
  return rows.sort(function (a, b) {
    return SEVERITY.indexOf(a.tone) - SEVERITY.indexOf(b.tone) || b.count - a.count;
  });
}

export function stateLine(node: GraphNodeShape): string {
  return stateMix(node)
    .map(function (row) {
      return row.count + " " + row.word;
    })
    .join(" · ");
}

function worstToneClass(node: GraphNodeShape): string {
  const worst = stateMix(node)[0];
  return worst && (worst.tone === "bad" || worst.tone === "blocked") ? " tone-" + worst.tone : "";
}

function presetRanks<N extends GraphNodeShape>(graph: GraphShape<N>): Record<string, number> | null {
  const inner = graph.nodes.filter(function (node) {
    return TERMINAL.indexOf(node.kind) < 0;
  });
  if (
    !inner.length ||
    inner.some(function (node) {
      return typeof node.rank !== "number";
    })
  )
    return null;
  const rank: Record<string, number> = {};
  let last = 0;
  inner.forEach(function (node) {
    rank[node.id] = node.rank!;
    last = Math.max(last, node.rank!);
  });
  graph.nodes.forEach(function (node) {
    if (rank[node.id] === undefined) rank[node.id] = typeof node.rank === "number" ? node.rank : last + 1;
  });
  return rank;
}

function longestPathRanks<N extends GraphNodeShape>(graph: GraphShape<N>): Record<string, number> {
  const fixed = presetRanks(graph);
  if (fixed) return fixed;
  const preds: Record<string, string[]> = {};
  graph.nodes.forEach(function (node) {
    preds[node.id] = [];
  });
  graph.edges.forEach(function (edge) {
    if (preds[edge.target] && edge.source !== edge.target) preds[edge.target]!.push(edge.source);
  });
  const kind: Record<string, string> = {};
  graph.nodes.forEach(function (node) {
    kind[node.id] = node.kind;
  });
  const rank: Record<string, number> = {};
  const visiting: Record<string, boolean> = {};
  const place = function (id: string): number {
    if (rank[id] !== undefined) return rank[id]!;
    if (kind[id] === "input") return (rank[id] = 0);
    if (visiting[id]) return 0;
    visiting[id] = true;
    let best = 0;
    preds[id]!.forEach(function (pred) {
      if (TERMINAL.indexOf(kind[pred] || "") < 0) best = Math.max(best, place(pred) + 1);
    });
    visiting[id] = false;
    return (rank[id] = Math.max(best, 1));
  };
  let last = 1;
  graph.nodes.forEach(function (node) {
    if (TERMINAL.indexOf(node.kind) < 0) last = Math.max(last, place(node.id));
  });
  graph.nodes.forEach(function (node) {
    if (TERMINAL.indexOf(node.kind) >= 0) rank[node.id] = last + 1;
  });
  return rank;
}

/* Three barycentre sweeps: each node moves to the mean position of its neighbours. */
function orderLayers<N extends GraphNodeShape>(graph: GraphShape<N>, rank: Record<string, number>): string[][] {
  let layers: string[][] = [];
  graph.nodes.forEach(function (node) {
    const layer = rank[node.id]!;
    (layers[layer] = layers[layer] || []).push(node.id);
  });
  layers = layers.filter(function (layer) {
    return !!layer;
  });
  const pos: Record<string, number> = {};
  const index = function () {
    layers.forEach(function (layer) {
      layer.forEach(function (id, i) {
        pos[id] = i;
      });
    });
  };
  index();
  for (let sweep = 0; sweep < 3; sweep++) {
    layers.forEach(function (layer) {
      const centre: Record<string, number> = {};
      layer.forEach(function (id) {
        const seen: number[] = [];
        graph.edges.forEach(function (edge) {
          if (edge.target === id && pos[edge.source] !== undefined) seen.push(pos[edge.source]!);
          if (edge.source === id && pos[edge.target] !== undefined) seen.push(pos[edge.target]!);
        });
        centre[id] = seen.length
          ? seen.reduce(function (a, b) {
              return a + b;
            }, 0) / seen.length
          : pos[id]!;
      });
      layer.sort(function (a, b) {
        return centre[a]! - centre[b]!;
      });
    });
    index();
  }
  return layers;
}

function overlaps(a: Box, b: Box): boolean {
  return a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
}

function bezier(p0: number, p1: number, p2: number, p3: number, t: number): number {
  const u = 1 - t;
  return u * u * u * p0 + 3 * u * u * t * p1 + 3 * u * t * t * p2 + t * t * t * p3;
}

function badgeRoom(node: GraphNodeShape): number {
  let room = 0;
  (node.badges || []).forEach(function (word) {
    room += measure(word, SMALL_FONT) + 11;
  });
  return room;
}

export function setPicked(frame: HTMLElement, id: string): void {
  Array.prototype.forEach.call(frame.querySelectorAll(".graph-node"), function (node: Element) {
    node.classList.toggle("picked", node.getAttribute("data-node") === id);
  });
}

function wrapDetail(detail: string): string[] {
  const room = NODE_MAX - 2 * NODE_PAD;
  if (measure(detail, SMALL_FONT) <= room) return [detail];
  const out: string[] = [];
  detail.split(" · ").forEach(function (part) {
    const last = out.length - 1;
    if (last >= 0 && measure(out[last] + " · " + part, SMALL_FONT) <= room) out[last] += " · " + part;
    else out.push(part);
  });
  return out;
}

function nodeTextLines(node: GraphNodeShape): string[] {
  const detail = node.kind === "sink" ? "sunk: not a product" : node.detail;
  const out = detail ? [node.label].concat(wrapDetail(detail)) : [node.label];
  if (node.kind === "group" && node.machines) out.push(stateLine(node));
  return out;
}

function columnWidths<N extends GraphNodeShape>(layers: string[][], byId: Record<string, N>): number[] {
  return layers.map(function (layer) {
    let widest = NODE_MIN;
    layer.forEach(function (id) {
      const node = byId[id]!;
      nodeTextLines(node).forEach(function (text, i) {
        widest = Math.max(widest, measure(text, i ? SMALL_FONT : TITLE_FONT) + 2 * NODE_PAD + (i ? 0 : badgeRoom(node)));
      });
    });
    return Math.min(NODE_MAX, Math.ceil(widest));
  });
}

/* A gap is as wide as the widest label on an edge that spans just that gap. */
function gapWidths<N extends GraphNodeShape>(graph: GraphShape<N>, layers: string[][]): number[] {
  const layerOf: Record<string, number> = {};
  layers.forEach(function (layer, li) {
    layer.forEach(function (id) {
      layerOf[id] = li;
    });
  });
  const gaps = layers.map(function () {
    return GAP_MIN;
  });
  graph.edges.forEach(function (edge) {
    const li = layerOf[edge.source];
    if (li === undefined || layerOf[edge.target] !== li + 1) return;
    gaps[li] = Math.min(GAP_MAX, Math.max(gaps[li]!, Math.ceil(edgeNeed(edge)) + 16));
  });
  return gaps;
}

function layoutGraph<N extends GraphNodeShape>(graph: GraphShape<N>, byId: Record<string, N>): GraphLayout {
  const layers = orderLayers(graph, longestPathRanks(graph));
  const rows = Math.max.apply(
    null,
    graph.nodes.map(function (node) {
      return nodeTextLines(node).length;
    })
  );
  const nodeHeight = NODE_PAD + rows * LINE + 6;
  const colW = columnWidths(layers, byId);
  const gapW = gapWidths(graph, layers);
  const colX: number[] = [];
  let x = PAD;
  layers.forEach(function (_layer, li) {
    colX.push(x);
    x += colW[li]! + gapW[li]!;
  });
  const width = x - gapW[layers.length - 1]! + PAD;
  const tallest = Math.max.apply(
    null,
    layers.map(function (layer) {
      return layer.length;
    })
  );
  const height = tallest * (nodeHeight + GAP_Y) - GAP_Y + 2 * PAD + 2 * LINE;
  const boxes: Record<string, Box> = {};
  layers.forEach(function (layer, li) {
    const top = PAD + ((tallest - layer.length) * (nodeHeight + GAP_Y)) / 2;
    layer.forEach(function (id, i) {
      boxes[id] = { x: colX[li]!, y: top + i * (nodeHeight + GAP_Y), w: colW[li]!, h: nodeHeight };
    });
  });
  return { boxes: boxes, width: width, height: height, nodeHeight: nodeHeight };
}

/* A backward edge dips under both nodes instead of crossing them. */
function edgeCurve(from: Box, to: Box, nodeHeight: number): EdgeCurve {
  const x1 = from.x + from.w;
  const y1 = from.y + from.h / 2;
  const x2 = to.x;
  const y2 = to.y + to.h / 2;
  if (x2 <= from.x) {
    const dip = Math.max(from.y, to.y) + nodeHeight + GAP_Y / 2;
    return { x1: x1, y1: y1, c1x: x1 + 60, c1y: dip, c2x: x2 - 60, c2y: dip, x2: x2, y2: y2 };
  }
  const middle = (x1 + x2) / 2;
  return { x1: x1, y1: y1, c1x: middle, c1y: y1, c2x: middle, c2y: y2, x2: x2, y2: y2 };
}

/* The first spot along the curve, nearest its middle, that clears every box drawn so far. */
function placeEdgeLabel(curve: EdgeCurve, lines: string[], occupied: Box[], width: number, height: number): Box | null {
  const w = Math.max.apply(
    null,
    lines.map(function (text) {
      return measure(text, SMALL_FONT);
    })
  );
  const h = lines.length * LINE;
  let spot: Box | null = null;
  LABEL_NUDGES_Y.some(function (dy) {
    return LABEL_STOPS_T.some(function (t) {
      const cx = bezier(curve.x1, curve.c1x, curve.c2x, curve.x2, t);
      const cy = bezier(curve.y1, curve.c1y, curve.c2y, curve.y2, t);
      const box = { x: cx - w / 2 - 2, y: cy - h / 2 + dy - 2, w: w + 4, h: h + 4 };
      if (box.x < 0 || box.y < 0 || box.x + box.w > width || box.y + box.h > height) return false;
      if (
        occupied.some(function (other) {
          return overlaps(box, other);
        })
      )
        return false;
      spot = box;
      return true;
    });
  });
  return spot;
}

function drawEdgeLabel(labelLayer: SVGGElement, place: Box, lines: string[]): void {
  labelLayer.appendChild(svg("rect", { class: "graph-edge-back", x: place.x, y: place.y, width: place.w, height: place.h, rx: 2 }));
  const label = svg("text", { class: "graph-edge-label", x: place.x + place.w / 2, y: place.y + 2 });
  lines.forEach(function (text, i) {
    const line = svg("tspan", { x: place.x + place.w / 2, y: place.y + 2 + (i + 1) * LINE - 3 });
    line.textContent = text;
    label.appendChild(line);
  });
  labelLayer.appendChild(label);
}

/* Parallel edges between one pair of nodes share a curve and one stacked label. */
function drawEdgeBundles<N extends GraphNodeShape>(
  edgeLayer: SVGGElement,
  labelLayer: SVGGElement,
  graph: GraphShape<N>,
  byId: Record<string, N>,
  layout: GraphLayout
): void {
  const boxes = layout.boxes;
  const occupied: Box[] = Object.keys(boxes).map(function (id) {
    return boxes[id]!;
  });
  const bundles: Record<string, GraphEdgeShape[]> = {};
  graph.edges.forEach(function (edge) {
    if (!boxes[edge.source] || !boxes[edge.target]) return;
    const key = edge.source + "\u0000" + edge.target;
    (bundles[key] = bundles[key] || []).push(edge);
  });
  Object.keys(bundles).forEach(function (key) {
    const bundle = bundles[key]!;
    const curve = edgeCurve(boxes[bundle[0]!.source]!, boxes[bundle[0]!.target]!, layout.nodeHeight);
    const d = "M" + curve.x1 + "," + curve.y1 + " C" + curve.c1x + "," + curve.c1y + " " + curve.c2x + "," + curve.c2y + " " + curve.x2 + "," + curve.y2;
    const sunk = byId[bundle[0]!.target]!.kind === "sink";
    const path = svg("path", { d: d, class: "graph-edge" + (sunk ? " sunk" : ""), "marker-end": "url(#graph-arrow)" });
    const title = svg("title", {});
    title.textContent = bundle.map(edgeText).join("\n");
    path.appendChild(title);
    edgeLayer.appendChild(path);

    const room = Math.max(GAP_MIN, curve.x2 > curve.x1 ? curve.x2 - curve.x1 - 8 : GAP_MAX);
    let lines: string[] = [];
    bundle.forEach(function (edge) {
      lines = lines.concat(edgeLines(edge, room));
    });
    const place = placeEdgeLabel(curve, lines, occupied, layout.width, layout.height);
    if (!place) return;
    occupied.push(place);
    drawEdgeLabel(labelLayer, place, lines);
  });
}

function drawBadges(group: SVGGElement, node: GraphNodeShape, box: Box): void {
  let right = box.w - 4;
  (node.badges || []).forEach(function (word) {
    const w = measure(word, SMALL_FONT) + 8;
    right -= w;
    const tag = svg("g", { class: "graph-badge", transform: "translate(" + right + ",4)" });
    tag.appendChild(svg("rect", { width: w, height: LINE, rx: 3 }));
    const text = svg("text", { x: w / 2, y: LINE - 3 });
    text.textContent = word;
    tag.appendChild(text);
    group.appendChild(tag);
    right -= 3;
  });
}

function makePickable<N extends GraphNodeShape>(group: SVGGElement, node: N, lines: string[], pick: (node: N) => void): void {
  const choose = function () {
    pick(node);
  };
  group.setAttribute("tabindex", "0");
  group.setAttribute("role", "button");
  group.setAttribute("aria-label", lines.concat(node.badges || []).join(", "));
  group.addEventListener("click", choose);
  group.addEventListener("keydown", function (event) {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    choose();
  });
}

function drawNode<N extends GraphNodeShape>(
  node: N,
  box: Box,
  options: DrawOptions<N>,
  pickable: (node: N) => boolean,
  tip: (node: N) => string,
  pick?: (node: N) => void
): SVGGElement {
  const live = !!pick && pickable(node);
  const marks = (live ? " pickable" : "") + (options.picked === node.id ? " picked" : "") + (options.flash && options.flash.indexOf(node.id) >= 0 ? " flash" : "");
  const group = svg("g", { class: "graph-node kind-" + node.kind + worstToneClass(node) + marks, transform: "translate(" + box.x + "," + box.y + ")", "data-node": node.id });
  group.appendChild(svg("rect", { width: box.w, height: box.h, rx: 3 }));
  const lines = nodeTextLines(node);
  const inner = box.w - 2 * NODE_PAD;
  const room = badgeRoom(node);
  lines.forEach(function (words, i) {
    const text = svg("text", { x: NODE_PAD, y: NODE_PAD + (i + 1) * LINE - 2, class: i ? "graph-node-d" : "graph-node-k" });
    text.textContent = clipToWidth(words, i ? SMALL_FONT : TITLE_FONT, inner - (i ? 0 : room));
    group.appendChild(text);
  });
  drawBadges(group, node, box);
  const title = svg("title", {});
  title.textContent = tip(node);
  group.appendChild(title);
  if (live) makePickable(group, node, lines, pick!);
  return group;
}

function svgScaffold(width: number, height: number): { root: SVGSVGElement; edges: SVGGElement; nodes: SVGGElement; labels: SVGGElement } {
  const root = svg("svg", { class: "graph", viewBox: "0 0 " + width + " " + height, role: "group", "aria-label": "production graph" });
  const defs = svg("defs", {});
  const arrow = svg("marker", { id: "graph-arrow", viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: "auto-start-reverse" });
  arrow.appendChild(svg("path", { d: "M0,0 L10,5 L0,10 z", class: "graph-arrowhead" }));
  defs.appendChild(arrow);
  root.appendChild(defs);
  const edges = svg("g", {});
  const labels = svg("g", {});
  const nodes = svg("g", {});
  root.appendChild(edges);
  root.appendChild(nodes);
  root.appendChild(labels);
  return { root: root, edges: edges, nodes: nodes, labels: labels };
}

export function drawGraph<N extends GraphNodeShape>(
  graph: GraphShape<N>,
  tip: (node: N) => string,
  pick?: (node: N) => void,
  drawOptions?: DrawOptions<N>
): HTMLElement {
  const options = drawOptions || {};
  const pickable =
    options.pickable ||
    function (node: N) {
      return node.kind === "group";
    };
  family = getComputedStyle(document.body).fontFamily || family;
  const byId: Record<string, N> = {};
  graph.nodes.forEach(function (node) {
    byId[node.id] = node;
  });
  const layout = layoutGraph(graph, byId);
  const scaffold = svgScaffold(layout.width, layout.height);
  drawEdgeBundles(scaffold.edges, scaffold.labels, graph, byId, layout);
  graph.nodes.forEach(function (node) {
    const box = layout.boxes[node.id];
    if (box) scaffold.nodes.appendChild(drawNode(node, box, options, pickable, tip, pick));
  });
  return zoomableFrame(scaffold.root, layout.width, layout.height);
}

function zoomableFrame(root: SVGSVGElement, width: number, height: number): HTMLElement {
  const box = make("div", "graph-frame");
  box.appendChild(root);
  let base = 1;
  let zoom = 1;
  const edge = function () {
    box.classList.toggle("more", box.scrollLeft + box.clientWidth < box.scrollWidth - 2);
  };
  const size = function () {
    const scale = base * zoom;
    root.style.width = Math.round(width * scale) + "px";
    root.style.height = Math.round(height * scale) + "px";
    edge();
  };
  const fit = function () {
    const room = box.clientWidth;
    if (!room) return;
    base = Math.min(MAX_SCALE, Math.max(1, room / width));
    size();
  };
  size();
  box.addEventListener("scroll", edge);
  if (typeof ResizeObserver !== "undefined") {
    new ResizeObserver(function () {
      if (zoom === 1) fit();
    }).observe(box);
  }
  box.addEventListener(
    "wheel",
    function (event) {
      if (!event.ctrlKey) return;
      event.preventDefault();
      const rect = box.getBoundingClientRect();
      const px = event.clientX - rect.left;
      const py = event.clientY - rect.top;
      const old = base * zoom;
      const next = Math.min(ZOOM[1]!, Math.max(ZOOM[0]!, old * (event.deltaY > 0 ? 1 / 1.15 : 1.15)));
      zoom = next / base;
      size();
      box.scrollLeft = ((box.scrollLeft + px) * next) / old - px;
      box.scrollTop = ((box.scrollTop + py) * next) / old - py;
    },
    { passive: false }
  );
  let drag: { x: number; y: number } | null = null;
  box.addEventListener("pointerdown", function (event) {
    if (event.pointerType !== "mouse" || event.button !== 0) return;
    if ((event.target as Element).closest(".graph-node.pickable")) return;
    drag = { x: event.clientX, y: event.clientY };
    box.setPointerCapture(event.pointerId);
    box.classList.add("dragging");
  });
  box.addEventListener("pointermove", function (event) {
    if (!drag) return;
    box.scrollLeft -= event.clientX - drag.x;
    box.scrollTop -= event.clientY - drag.y;
    drag = { x: event.clientX, y: event.clientY };
  });
  const drop = function () {
    drag = null;
    box.classList.remove("dragging");
  };
  box.addEventListener("pointerup", drop);
  box.addEventListener("pointercancel", drop);
  box.addEventListener("dblclick", function (event) {
    if ((event.target as Element).closest(".graph-node.pickable")) return;
    zoom = 1;
    fit();
    box.scrollLeft = 0;
    box.scrollTop = 0;
  });
  return box;
}

export function graphCardFrame(heading: string, shown: boolean, toggle?: () => void): HTMLElement {
  const card = make("section", "dash-card dash-graph");
  const bar = make("div", "dash-title");
  bar.appendChild(make("h2", "dash-h", heading));
  if (toggle) bar.appendChild(button(shown ? "hide graph" : "graph", toggle, { title: shown ? "hide the production graph" : "draw the production graph" }));
  card.appendChild(bar);
  return card;
}
