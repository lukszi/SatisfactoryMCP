/* A production graph's columns: each node's rank by longest path from the inputs, terminals
 * last, and the order down each column. See docs/frontend_vision.md §9.8. */

import type { GraphNodeShape, GraphShape } from "./graph";

const TERMINAL = ["storage", "export", "sink", "nowhere"];

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

export function longestPathRanks<N extends GraphNodeShape>(graph: GraphShape<N>): Record<string, number> {
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
export function orderLayers<N extends GraphNodeShape>(graph: GraphShape<N>, rank: Record<string, number>): string[][] {
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

