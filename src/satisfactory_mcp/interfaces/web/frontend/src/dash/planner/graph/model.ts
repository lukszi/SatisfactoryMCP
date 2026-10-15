/* The plan graph read from a solve response in either of its two shapes: the full one (the
 * shared production graph shape, dash/prodgraph/model.ts) or the older process/input/export
 * shape, which is filled in from the build rows. A build row's clock is a fraction, unlike a
 * full node's percent. See docs/planner_vision.md §4.2. */

import { countBuildings, kindOf, lastShare, num, readGraph, readNode, str } from "../../prodgraph/model";

import type { SolveResponse, SolveRow } from "../../../api/shapes";
import type { Loose, ProdEdge, ProdGraph, ProdNode } from "../../prodgraph/model";

export { countBuildings, edgeLabel, ITEM_GROUPS, lastShare, nodeForPick, rateText, readPositions } from "../../prodgraph/model";
export type { BuildingCount, ItemGroup, NodeKind, Unit } from "../../prodgraph/model";

export type PlanNode = ProdNode;
export type PlanEdge = ProdEdge;
export type PlanGraphView = ProdGraph;

const POWER = "MW";

function rowWork(row: SolveRow): number {
  if (row.last_clock !== null && row.last_clock !== undefined) return row.machines - 1 + row.last_clock;
  return row.machines * row.clock;
}

function rowsByRecipe(rows: SolveRow[]): Record<string, SolveRow> {
  const out: Record<string, SolveRow> = {};
  rows.forEach(function (row) {
    if (row.recipe_id) out[row.recipe_id] = row;
  });
  return out;
}

function fullNode(raw: Loose, rows: Record<string, SolveRow>): PlanNode {
  const node = readNode(raw);
  if (!node.row && node.recipeId && rows[node.recipeId]) {
    node.row = rows[node.recipeId]!.id;
    node.rows = [node.row];
  }
  return node;
}

function legacyNode(raw: Loose, rows: Record<string, SolveRow>): PlanNode {
  const row = str(raw.row) ? rows[String(raw.row)] : undefined;
  const kind = kindOf(raw.kind);
  const item = str(raw.item);
  return {
    id: String(raw.id),
    kind: kind,
    label: String(raw.label ?? raw.id),
    detail: String(raw.detail ?? ""),
    itemId: item,
    itemName: kind === "process" ? (row ? row.item : null) : String(raw.label ?? item ?? ""),
    recipeId: row ? row.recipe_id : null,
    recipeName: row ? row.recipe : null,
    building: row ? row.building : null,
    buildingIcon: null,
    built: row ? row.machines : 0,
    machines: row ? rowWork(row) : 0,
    clock: row ? row.clock : null,
    mw: row ? row.mw : 0,
    utilisation: row ? lastShare(rowWork(row)) : 1,
    rank: num(raw.rank, 0),
    perMin: null,
    unit: "items",
    note: null,
    productivity: null,
    monitored: 0,
    states: {},
    bbox: null,
    row: row ? row.id : null,
    rows: row ? [row.id] : [],
  };
}

function legacyEdge(raw: Loose, index: number): PlanEdge {
  const item = String(raw.item ?? "");
  const power = item === POWER;
  return {
    id: String(raw.source) + ">" + String(raw.target) + ":" + item + (index ? "#" + index : ""),
    source: String(raw.source),
    target: String(raw.target),
    itemId: null,
    itemName: power ? "power" : item,
    group: power ? "power" : "other",
    perMin: num(raw.per_min, 0),
    unit: power ? "MW" : "items",
    text: str(raw.text) || "",
  };
}

/** The full shape is recognised by its edges' ids and item groups, or by a node's utilisation. */
function isFull(graph: Loose): boolean {
  const edges = (graph.edges as Loose[] | undefined) || [];
  const nodes = (graph.nodes as Loose[] | undefined) || [];
  if ("positions" in graph || "buildings" in graph) return true;
  if (edges.length) return "item_group" in edges[0]! || "item_id" in edges[0]!;
  return nodes.length > 0 && "utilisation" in nodes[0]!;
}

export function readPlanGraph(data: Pick<SolveResponse, "graph" | "rows">): PlanGraphView {
  const graph = (data.graph || { nodes: [], edges: [] }) as unknown as Loose;
  const rawNodes = (graph.nodes as Loose[] | undefined) || [];
  const rawEdges = (graph.edges as Loose[] | undefined) || [];
  if (isFull(graph)) {
    const rows = rowsByRecipe(data.rows || []);
    const read = readGraph({ ...graph, nodes: [] });
    read.nodes = rawNodes.map(function (n) {
      return fullNode(n, rows);
    });
    if (!Array.isArray(graph.buildings)) read.buildings = countBuildings(read.nodes);
    return read;
  }
  const byRowId: Record<string, SolveRow> = {};
  (data.rows || []).forEach(function (row) {
    byRowId[row.id] = row;
  });
  const nodes = rawNodes.map(function (n) {
    return legacyNode(n, byRowId);
  });
  const seen: Record<string, number> = {};
  const edges = rawEdges.map(function (e) {
    const key = String(e.source) + ">" + String(e.target) + ":" + String(e.item);
    seen[key] = (seen[key] || 0) + 1;
    return legacyEdge(e, seen[key]! - 1);
  });
  return { nodes: nodes, edges: edges, positions: {}, buildings: countBuildings(nodes), full: false };
}

const read = new WeakMap<object, PlanGraphView>();

/** `readPlanGraph`, once per response object. */
export function planGraphOf(data: Pick<SolveResponse, "graph" | "rows">): PlanGraphView {
  let view = read.get(data);
  if (!view) {
    view = readPlanGraph(data);
    read.set(data, view);
  }
  return view;
}
