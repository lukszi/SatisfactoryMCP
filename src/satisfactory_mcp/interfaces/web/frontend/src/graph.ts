/* A production graph as a left-to-right SVG: inputs, recipe groups by longest path, terminals
 * last. Generic over its node shape so a plan can be drawn with it too. See
 * docs/frontend_vision.md §9.8. */

export interface GraphNodeShape {
  id: string;
  kind: string;
  label: string;
  detail: string;
  machines: number;
  running: number;
  blocked: number;
  stopped: number;
}

export interface GraphEdgeShape {
  source: string;
  target: string;
  item: string;
  per_min: number | null;
}

export interface GraphShape<N extends GraphNodeShape> {
  nodes: N[];
  edges: GraphEdgeShape[];
}

var SVG = "http://www.w3.org/2000/svg";
var NODE_W = 200;
var NODE_H = 58;
var GAP_X = 150;
var GAP_Y = 26;
var PAD = 30;
var TERMINAL = ["storage", "export", "sink", "nowhere"];
var FIRST_W = 1500;
var ASPECT = 560 / 1240;

function svg<K extends keyof SVGElementTagNameMap>(tag: K, attrs: Record<string, string | number>): SVGElementTagNameMap[K] {
  var node = document.createElementNS(SVG, tag) as SVGElementTagNameMap[K];
  Object.keys(attrs).forEach(function (k) {
    node.setAttribute(k, String(attrs[k]));
  });
  return node;
}

function rate(value: number | null): string {
  if (value === null) return "";
  var rounded = value >= 100 ? Math.round(value) : Math.round(value * 10) / 10;
  return " " + rounded.toLocaleString("en") + "/min";
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

function status(n: GraphNodeShape): string {
  if (n.stopped) return " stopped";
  if (n.blocked) return " blocked";
  return "";
}

function statusText(n: GraphNodeShape): string {
  if (!n.machines) return n.detail;
  var parts = [n.running + " running"];
  if (n.blocked) parts.push(n.blocked + " blocked");
  if (n.stopped) parts.push(n.stopped + " stopped");
  return parts.join(" · ");
}

function clip(text: string, max: number): string {
  return text.length > max ? text.slice(0, max - 1) + "…" : text;
}

export function drawGraph<N extends GraphNodeShape>(
  graph: GraphShape<N>,
  tip: (node: N) => string,
  pick: (node: N) => void
): SVGSVGElement {
  var rank = ranks(graph);
  var layers = order(graph, rank);
  var tallest = Math.max.apply(
    null,
    layers.map(function (l) {
      return l.length;
    })
  );
  var height = tallest * (NODE_H + GAP_Y) - GAP_Y + 2 * PAD;
  var width = layers.length * (NODE_W + GAP_X) - GAP_X + 2 * PAD;
  var at: Record<string, { x: number; y: number }> = {};
  layers.forEach(function (layer, li) {
    var top = PAD + ((tallest - layer.length) * (NODE_H + GAP_Y)) / 2;
    layer.forEach(function (id, i) {
      at[id] = { x: PAD + li * (NODE_W + GAP_X), y: top + i * (NODE_H + GAP_Y) };
    });
  });

  var root = svg("svg", { class: "graph", viewBox: "0 0 " + width + " " + height });
  var world = svg("g", {});
  root.appendChild(world);
  var defs = svg("defs", {});
  var arrow = svg("marker", { id: "graph-arrow", viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: "auto-start-reverse" });
  arrow.appendChild(svg("path", { d: "M0,0 L10,5 L0,10 z", class: "graph-arrowhead" }));
  defs.appendChild(arrow);
  root.appendChild(defs);

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
    var x1 = a.x + NODE_W;
    var y1 = a.y + NODE_H / 2;
    var x2 = b.x;
    var y2 = b.y + NODE_H / 2;
    var d: string;
    var back = x2 <= a.x;
    if (back) {
      var dip = Math.max(a.y, b.y) + NODE_H + GAP_Y / 2 + 10;
      d = "M" + x1 + "," + y1 + " C" + (x1 + 60) + "," + dip + " " + (x2 - 60) + "," + dip + " " + x2 + "," + y2;
    } else {
      var mid = (x1 + x2) / 2;
      d = "M" + x1 + "," + y1 + " C" + mid + "," + y1 + " " + mid + "," + y2 + " " + x2 + "," + y2;
    }
    var sunk = group[0]!.target === "sink";
    var path = svg("path", { d: d, class: "graph-edge" + (sunk ? " sunk" : ""), "marker-end": "url(#graph-arrow)" });
    var words = group
      .map(function (e) {
        return e.item + rate(e.per_min);
      })
      .join(" · ");
    var title = svg("title", {});
    title.textContent = words;
    path.appendChild(title);
    world.appendChild(path);
    var label = svg("text", { x: (x1 + x2) / 2, y: back ? Math.max(a.y, b.y) + NODE_H + 18 : (y1 + y2) / 2 - 4, class: "graph-edge-label" });
    label.textContent = clip(words, 44);
    world.appendChild(label);
  });

  graph.nodes.forEach(function (n) {
    var p = at[n.id];
    if (!p) return;
    var box = svg("g", { class: "graph-node kind-" + n.kind + status(n), transform: "translate(" + p.x + "," + p.y + ")" });
    box.appendChild(svg("rect", { width: NODE_W, height: NODE_H, rx: 4 }));
    var one = svg("text", { x: 10, y: 18, class: "graph-node-k" });
    one.textContent = clip(n.label, 30);
    var two = svg("text", { x: 10, y: 34, class: "graph-node-d" });
    two.textContent = clip(n.kind === "sink" ? "sunk: not a product" : n.detail, 34);
    var three = svg("text", { x: 10, y: 50, class: "graph-node-s" });
    three.textContent = n.kind === "group" ? statusText(n) : "";
    box.appendChild(one);
    box.appendChild(two);
    box.appendChild(three);
    var title = svg("title", {});
    title.textContent = tip(n);
    box.appendChild(title);
    if (n.kind === "group") {
      box.addEventListener("click", function () {
        pick(n);
      });
    }
    world.appendChild(box);
  });

  panZoom(root, width, height);
  return root;
}

function panZoom(root: SVGSVGElement, width: number, height: number): void {
  var fit = function () {
    var w = Math.min(width, FIRST_W);
    var h = w * ASPECT;
    if (height > h) {
      h = height;
      w = h / ASPECT;
    }
    return { x: 0, y: (height - h) / 2, w: w, h: h };
  };
  var view = fit();
  var apply = function () {
    root.setAttribute("viewBox", view.x + " " + view.y + " " + view.w + " " + view.h);
  };
  root.addEventListener(
    "wheel",
    function (event) {
      event.preventDefault();
      var rect = root.getBoundingClientRect();
      var fx = (event.clientX - rect.left) / rect.width;
      var fy = (event.clientY - rect.top) / rect.height;
      var scale = event.deltaY > 0 ? 1.15 : 1 / 1.15;
      var w = Math.min(width * 4, Math.max(width / 8, view.w * scale));
      var h = (w / view.w) * view.h;
      view.x += (view.w - w) * fx;
      view.y += (view.h - h) * fy;
      view.w = w;
      view.h = h;
      apply();
    },
    { passive: false }
  );
  var drag: { x: number; y: number } | null = null;
  root.addEventListener("pointerdown", function (event) {
    if ((event.target as Element).closest(".graph-node.kind-group")) return;
    drag = { x: event.clientX, y: event.clientY };
    root.setPointerCapture(event.pointerId);
  });
  root.addEventListener("pointermove", function (event) {
    if (!drag) return;
    var rect = root.getBoundingClientRect();
    view.x -= ((event.clientX - drag.x) / rect.width) * view.w;
    view.y -= ((event.clientY - drag.y) / rect.height) * view.h;
    drag = { x: event.clientX, y: event.clientY };
    apply();
  });
  root.addEventListener("pointerup", function () {
    drag = null;
  });
  root.addEventListener("dblclick", function () {
    view = fit();
    apply();
  });
  apply();
}
