/* A production graph as a left-to-right SVG: inputs, recipe groups by longest path, terminals
 * last. Generic over its node shape so a plan can be drawn with it too. See
 * docs/frontend_vision.md §9.8. */

import { button } from "./dashkit";
import { make } from "./dom";
import { perMin } from "./format";
import { tone } from "./states";

import type { Tone } from "./states";

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
  tone: Tone;
}

var SVG = "http://www.w3.org/2000/svg";
var NODE_MIN = 96;
var NODE_MAX = 250;
var NODE_PAD = 10;
var GAP_MIN = 48;
var GAP_MAX = 160;
var GAP_Y = 30;
var LINE = 14;
var PAD = 12;
var TERMINAL = ["storage", "export", "sink", "nowhere"];
var SEVERITY: Tone[] = ["bad", "blocked", "mid", "ok"];
var MAX_SCALE = 14 / 12;
var ZOOM = [0.25, 3];

export var GRAPH_HINT = "ctrl+scroll zooms, drag pans, double-click fits";

var ruler: CanvasRenderingContext2D | null = null;
var family = "sans-serif";

function measure(text: string, font: string): number {
  if (!ruler) ruler = document.createElement("canvas").getContext("2d");
  if (!ruler) return text.length * 6.5;
  ruler.font = font + " " + family;
  return ruler.measureText(text).width;
}

function clip(text: string, font: string, max: number): string {
  if (measure(text, font) <= max) return text;
  var cut = text.length;
  while (cut > 1 && measure(text.slice(0, cut) + "…", font) > max) cut--;
  return text.slice(0, cut) + "…";
}

function svg<K extends keyof SVGElementTagNameMap>(tag: K, attrs: Record<string, string | number>): SVGElementTagNameMap[K] {
  var node = document.createElementNS(SVG, tag) as SVGElementTagNameMap[K];
  Object.keys(attrs).forEach(function (k) {
    node.setAttribute(k, String(attrs[k]));
  });
  return node;
}

function edgeText(e: GraphEdgeShape): string {
  if (e.text) return e.text;
  return e.per_min === null ? e.item : e.item + " " + perMin(e.per_min);
}

function edgeLines(e: GraphEdgeShape, room: number): string[] {
  var one = edgeText(e);
  if (measure(one, "11px") <= room || e.text || e.per_min === null) return [clip(one, "11px", room)];
  return [clip(e.item, "11px", room), perMin(e.per_min)];
}

function edgeNeed(e: GraphEdgeShape): number {
  var one = measure(edgeText(e), "11px");
  if (e.text || e.per_min === null) return one;
  return Math.max(measure(e.item, "11px"), measure(perMin(e.per_min), "11px"));
}

export function stateMix(n: GraphNodeShape): Mix[] {
  var rows: Mix[] = [];
  var add = function (word: string, count: number, t: Tone) {
    if (!count) return;
    var same = rows.filter(function (r) {
      return r.word === word;
    })[0];
    if (same) same.count += count;
    else rows.push({ word: word, count: count, tone: t });
  };
  if (n.states) {
    var states = n.states;
    Object.keys(states).forEach(function (s) {
      var t = tone(s);
      add(t === "ok" ? "running" : s, states[s]!, t);
    });
  } else {
    add("running", n.running, "ok");
    add("blocked", n.blocked, "blocked");
    add("not running", n.stopped, "bad");
  }
  return rows.sort(function (a, b) {
    return SEVERITY.indexOf(a.tone) - SEVERITY.indexOf(b.tone) || b.count - a.count;
  });
}

export function stateLine(n: GraphNodeShape): string {
  return stateMix(n)
    .map(function (r) {
      return r.count + " " + r.word;
    })
    .join(" · ");
}

function outline(n: GraphNodeShape): string {
  var worst = stateMix(n)[0];
  return worst && (worst.tone === "bad" || worst.tone === "blocked") ? " tone-" + worst.tone : "";
}

function ranks<N extends GraphNodeShape>(graph: GraphShape<N>): Record<string, number> {
  var preds: Record<string, string[]> = {};
  graph.nodes.forEach(function (n) {
    preds[n.id] = [];
  });
  graph.edges.forEach(function (e) {
    if (preds[e.target] && e.source !== e.target) preds[e.target]!.push(e.source);
  });
  var kind: Record<string, string> = {};
  graph.nodes.forEach(function (n) {
    kind[n.id] = n.kind;
  });
  var rank: Record<string, number> = {};
  var visiting: Record<string, boolean> = {};
  var place = function (id: string): number {
    if (rank[id] !== undefined) return rank[id]!;
    if (kind[id] === "input") return (rank[id] = 0);
    if (visiting[id]) return 0;
    visiting[id] = true;
    var best = 0;
    preds[id]!.forEach(function (p) {
      if (TERMINAL.indexOf(kind[p] || "") < 0) best = Math.max(best, place(p) + 1);
    });
    visiting[id] = false;
    return (rank[id] = Math.max(best, 1));
  };
  var last = 1;
  graph.nodes.forEach(function (n) {
    if (TERMINAL.indexOf(n.kind) < 0) last = Math.max(last, place(n.id));
  });
  graph.nodes.forEach(function (n) {
    if (TERMINAL.indexOf(n.kind) >= 0) rank[n.id] = last + 1;
  });
  return rank;
}

function order<N extends GraphNodeShape>(graph: GraphShape<N>, rank: Record<string, number>): string[][] {
  var layers: string[][] = [];
  graph.nodes.forEach(function (n) {
    var r = rank[n.id]!;
    (layers[r] = layers[r] || []).push(n.id);
  });
  layers = layers.filter(function (l) {
    return !!l;
  });
  var pos: Record<string, number> = {};
  var index = function () {
    layers.forEach(function (l) {
      l.forEach(function (id, i) {
        pos[id] = i;
      });
    });
  };
  index();
  for (var sweep = 0; sweep < 3; sweep++) {
    layers.forEach(function (layer) {
      var centre: Record<string, number> = {};
      layer.forEach(function (id) {
        var seen: number[] = [];
        graph.edges.forEach(function (e) {
          if (e.target === id && pos[e.source] !== undefined) seen.push(pos[e.source]!);
          if (e.source === id && pos[e.target] !== undefined) seen.push(pos[e.target]!);
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
  var u = 1 - t;
  return u * u * u * p0 + 3 * u * u * t * p1 + 3 * u * t * t * p2 + t * t * t * p3;
}

function lines(n: GraphNodeShape): string[] {
  var detail = n.kind === "sink" ? "sunk: not a product" : n.detail;
  var out = detail ? [n.label, detail] : [n.label];
  if (n.kind === "group" && n.machines) out.push(stateLine(n));
  return out;
}

export function drawGraph<N extends GraphNodeShape>(
  graph: GraphShape<N>,
  tip: (node: N) => string,
  pick?: (node: N) => void
): HTMLElement {
  family = getComputedStyle(document.body).fontFamily || family;
  var rank = ranks(graph);
  var layers = order(graph, rank);
  var byId: Record<string, N> = {};
  graph.nodes.forEach(function (n) {
    byId[n.id] = n;
  });
  var rows = Math.max.apply(
    null,
    graph.nodes.map(function (n) {
      return lines(n).length;
    })
  );
  var nodeH = NODE_PAD + rows * LINE + 6;
  var colW = layers.map(function (layer) {
    var widest = NODE_MIN;
    layer.forEach(function (id) {
      var n = byId[id]!;
      lines(n).forEach(function (text, i) {
        widest = Math.max(widest, measure(text, i ? "11px" : "600 12px") + 2 * NODE_PAD);
      });
    });
    return Math.min(NODE_MAX, Math.ceil(widest));
  });
  var layerOf: Record<string, number> = {};
  layers.forEach(function (layer, li) {
    layer.forEach(function (id) {
      layerOf[id] = li;
    });
  });
  var gapW = layers.map(function () {
    return GAP_MIN;
  });
  graph.edges.forEach(function (e) {
    var li = layerOf[e.source];
    if (li === undefined || layerOf[e.target] !== li + 1) return;
    gapW[li] = Math.min(GAP_MAX, Math.max(gapW[li]!, Math.ceil(edgeNeed(e)) + 16));
  });
  var colX: number[] = [];
  var x = PAD;
  layers.forEach(function (_layer, li) {
    colX.push(x);
    x += colW[li]! + gapW[li]!;
  });
  var width = x - gapW[layers.length - 1]! + PAD;
  var tallest = Math.max.apply(
    null,
    layers.map(function (l) {
      return l.length;
    })
  );
  var height = tallest * (nodeH + GAP_Y) - GAP_Y + 2 * PAD + 2 * LINE;
  var at: Record<string, Box> = {};
  layers.forEach(function (layer, li) {
    var top = PAD + ((tallest - layer.length) * (nodeH + GAP_Y)) / 2;
    layer.forEach(function (id, i) {
      at[id] = { x: colX[li]!, y: top + i * (nodeH + GAP_Y), w: colW[li]!, h: nodeH };
    });
  });

  var root = svg("svg", { class: "graph", viewBox: "0 0 " + width + " " + height, role: "group", "aria-label": "production graph" });
  var defs = svg("defs", {});
  var arrow = svg("marker", { id: "graph-arrow", viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: "auto-start-reverse" });
  arrow.appendChild(svg("path", { d: "M0,0 L10,5 L0,10 z", class: "graph-arrowhead" }));
  defs.appendChild(arrow);
  root.appendChild(defs);
  var edgeLayer = svg("g", {});
  var labelLayer = svg("g", {});
  var nodeLayer = svg("g", {});
  root.appendChild(edgeLayer);
  root.appendChild(nodeLayer);
  root.appendChild(labelLayer);

  var taken: Box[] = Object.keys(at).map(function (id) {
    return at[id]!;
  });
  var paired: Record<string, GraphEdgeShape[]> = {};
  graph.edges.forEach(function (e) {
    if (!at[e.source] || !at[e.target]) return;
    var key = e.source + "\u0000" + e.target;
    (paired[key] = paired[key] || []).push(e);
  });
  Object.keys(paired).forEach(function (key) {
    var group = paired[key]!;
    var a = at[group[0]!.source]!;
    var b = at[group[0]!.target]!;
    var x1 = a.x + a.w;
    var y1 = a.y + a.h / 2;
    var x2 = b.x;
    var y2 = b.y + b.h / 2;
    var c1x: number, c1y: number, c2x: number, c2y: number;
    if (x2 <= a.x) {
      var dip = Math.max(a.y, b.y) + nodeH + GAP_Y / 2;
      c1x = x1 + 60;
      c2x = x2 - 60;
      c1y = c2y = dip;
    } else {
      c1x = c2x = (x1 + x2) / 2;
      c1y = y1;
      c2y = y2;
    }
    var d = "M" + x1 + "," + y1 + " C" + c1x + "," + c1y + " " + c2x + "," + c2y + " " + x2 + "," + y2;
    var sunk = byId[group[0]!.target]!.kind === "sink";
    var path = svg("path", { d: d, class: "graph-edge" + (sunk ? " sunk" : ""), "marker-end": "url(#graph-arrow)" });
    var texts = group.map(edgeText);
    var title = svg("title", {});
    title.textContent = texts.join("\n");
    path.appendChild(title);
    edgeLayer.appendChild(path);

    var room = Math.max(GAP_MIN, x2 > x1 ? x2 - x1 - 8 : GAP_MAX);
    var shown: string[] = [];
    group.forEach(function (e) {
      shown = shown.concat(edgeLines(e, room));
    });
    var w = Math.max.apply(
      null,
      shown.map(function (t) {
        return measure(t, "11px");
      })
    );
    var h = shown.length * LINE;
    var spot: Box | null = null;
    [0, -LINE / 2, LINE / 2, -LINE, LINE].some(function (dy) {
      return [0.5, 0.4, 0.6, 0.3, 0.7, 0.2, 0.8].some(function (t) {
        var cx = bezier(x1, c1x, c2x, x2, t);
        var cy = bezier(y1, c1y, c2y, y2, t);
        var box = { x: cx - w / 2 - 2, y: cy - h / 2 + dy - 2, w: w + 4, h: h + 4 };
        if (box.x < 0 || box.y < 0 || box.x + box.w > width || box.y + box.h > height) return false;
        if (
          taken.some(function (o) {
            return overlaps(box, o);
          })
        )
          return false;
        spot = box;
        return true;
      });
    });
    if (!spot) return;
    var place: Box = spot;
    taken.push(place);
    var label = svg("text", { class: "graph-edge-label", x: place.x + place.w / 2, y: place.y + 2 });
    shown.forEach(function (t, i) {
      var line = svg("tspan", { x: place.x + place.w / 2, y: place.y + 2 + (i + 1) * LINE - 3 });
      line.textContent = t;
      label.appendChild(line);
    });
    labelLayer.appendChild(label);
  });

  graph.nodes.forEach(function (n) {
    var p = at[n.id];
    if (!p) return;
    var live = !!pick && n.kind === "group";
    var box = svg("g", { class: "graph-node kind-" + n.kind + outline(n) + (live ? " pickable" : ""), transform: "translate(" + p.x + "," + p.y + ")" });
    box.appendChild(svg("rect", { width: p.w, height: p.h, rx: 3 }));
    var text = lines(n);
    var inner = p.w - 2 * NODE_PAD;
    text.forEach(function (words, i) {
      var t = svg("text", { x: NODE_PAD, y: NODE_PAD + (i + 1) * LINE - 2, class: i ? "graph-node-d" : "graph-node-k" });
      t.textContent = clip(words, i ? "11px" : "600 12px", inner);
      box.appendChild(t);
    });
    var title = svg("title", {});
    title.textContent = tip(n);
    box.appendChild(title);
    if (live) {
      var go = function () {
        pick!(n);
      };
      box.setAttribute("tabindex", "0");
      box.setAttribute("role", "button");
      box.setAttribute("aria-label", text.join(", "));
      box.addEventListener("click", go);
      box.addEventListener("keydown", function (event) {
        if (event.key !== "Enter" && event.key !== " ") return;
        event.preventDefault();
        go();
      });
    }
    nodeLayer.appendChild(box);
  });

  return frame(root, width, height);
}

function frame(root: SVGSVGElement, width: number, height: number): HTMLElement {
  var box = make("div", "graph-frame");
  box.appendChild(root);
  var base = 1;
  var zoom = 1;
  var edge = function () {
    box.classList.toggle("more", box.scrollLeft + box.clientWidth < box.scrollWidth - 2);
  };
  var size = function () {
    var s = base * zoom;
    root.style.width = Math.round(width * s) + "px";
    root.style.height = Math.round(height * s) + "px";
    edge();
  };
  var fit = function () {
    var room = box.clientWidth;
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
      var rect = box.getBoundingClientRect();
      var px = event.clientX - rect.left;
      var py = event.clientY - rect.top;
      var old = base * zoom;
      var next = Math.min(ZOOM[1]!, Math.max(ZOOM[0]!, old * (event.deltaY > 0 ? 1 / 1.15 : 1.15)));
      zoom = next / base;
      size();
      box.scrollLeft = ((box.scrollLeft + px) * next) / old - px;
      box.scrollTop = ((box.scrollTop + py) * next) / old - py;
    },
    { passive: false }
  );
  var drag: { x: number; y: number } | null = null;
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
  var drop = function () {
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

export function graphCard(heading: string, shown: boolean, toggle?: () => void): HTMLElement {
  var card = make("section", "dash-card dash-graph");
  var bar = make("div", "dash-title");
  bar.appendChild(make("h2", "dash-h", heading));
  if (toggle) bar.appendChild(button(shown ? "hide graph" : "graph", toggle, { title: shown ? "hide the production graph" : "draw the production graph" }));
  card.appendChild(bar);
  return card;
}
