/* The plan graph as the page draws it, read from a solve response in either of its two shapes:
 * the full one (stable ids, rates and groups on every node and edge) or the older
 * process/input/export shape, which is filled in from the build rows. The full shape sends a
 * node's clock in percent, unlike a build row's fraction. See docs/planner_vision.md §4.2. */

import type { SolveResponse, SolveRow } from "../../../api/shapes";

export type NodeKind = "process" | "input" | "export" | "leftover" | "sink";
export type Unit = "items" | "m3" | "MW";

export const ITEM_GROUPS = ["oil", "water", "gas", "ore", "metal", "component", "nuclear", "power", "other"] as const;
export type ItemGroup = (typeof ITEM_GROUPS)[number];

export interface PlanNode {
  id: string;
  kind: NodeKind;
  label: string;
  /** The older shape's one-line summary; empty in the full shape. */
  detail: string;
  itemId: string | null;
  itemName: string | null;
  recipeId: string | null;
  recipeName: string | null;
  building: string | null;
  /** The building's descriptor class, which its picture is served under. */
  buildingIcon: string | null;
  /** Machines actually built, whole. */
  built: number;
  /** Work in machines at 100% (built × clock), so 15.31 for 16 at 95.7%. */
  machines: number;
  /** Mean clock as a fraction (1 = 100%); null where nothing runs. */
  clock: number | null;
  /** Signed: negative draws. */
  mw: number;
  /** The last machine's share of the work, or 1 when the work is whole machines. */
  utilisation: number;
  rank: number;
  perMin: number | null;
  unit: Unit;
  /** The first build row this node stands for; `rows` has them all. */
  row: string | null;
  rows: string[];
}

export interface PlanEdge {
  id: string;
  source: string;
  target: string;
  itemId: string | null;
  itemName: string;
  group: ItemGroup;
  perMin: number;
  unit: Unit;
  text: string;
}

export interface BuildingCount {
  building: string;
  count: number;
  groups: number;
  mw: number;
}

export interface PlanGraphView {
  nodes: PlanNode[];
  edges: PlanEdge[];
  positions: Record<string, [number, number]>;
  buildings: BuildingCount[];
  /** True when the server sent the full shape, which is also the one that stores positions. */
  full: boolean;
}

const KINDS: NodeKind[] = ["process", "input", "export", "leftover", "sink"];
const POWER = "MW";

type Loose = Record<string, unknown>;

function str(value: unknown): string | null {
  return typeof value === "string" && value ? value : null;
}

function num(value: unknown, fallback: number): number {
  return typeof value === "number" && isFinite(value) ? value : fallback;
}

function unitOf(value: unknown): Unit {
  return value === "m3" || value === "MW" ? value : "items";
}

function groupOf(value: unknown): ItemGroup {
  return (ITEM_GROUPS as readonly string[]).indexOf(String(value)) >= 0 ? (value as ItemGroup) : "other";
}

function kindOf(value: unknown): NodeKind {
  return KINDS.indexOf(value as NodeKind) >= 0 ? (value as NodeKind) : "process";
}

export function rateText(perMin: number, unit: Unit): string {
  const n = Math.round(perMin * 10) / 10;
  if (unit === "MW") return n.toLocaleString("en-GB") + " MW";
  return n.toLocaleString("en-GB") + (unit === "m3" ? " m³/min" : "/min");
}

export function edgeLabel(edge: Pick<PlanEdge, "itemName" | "perMin" | "unit" | "text">): string {
  return edge.text || edge.itemName + " " + rateText(edge.perMin, edge.unit);
}

const WHOLE = 1e-3;

/** The last machine's share of `work`, or 1 when the work is whole machines. */
export function lastShare(work: number): number {
  const part = work - Math.floor(work);
  return part < WHOLE || 1 - part < WHOLE ? 1 : part;
}

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
  const recipeId = str(raw.recipe_id);
  const listed = Array.isArray(raw.rows) ? raw.rows.map(String) : [];
  const row = str(raw.row) || listed[0] || (recipeId && rows[recipeId] ? rows[recipeId]!.id : null);
  const machines = num(raw.machines, 0);
  return {
    id: String(raw.id),
    kind: kindOf(raw.kind),
    label: String(raw.label ?? raw.id),
    detail: String(raw.detail ?? ""),
    itemId: str(raw.item_id),
    itemName: str(raw.item_name),
    recipeId: recipeId,
    recipeName: str(raw.recipe_name),
    building: str(raw.building),
    buildingIcon: str(raw.building_icon),
    built: num(raw.built, Math.ceil(machines - WHOLE)),
    machines: machines,
    clock: typeof raw.clock === "number" ? raw.clock / 100 : null,
    mw: num(raw.mw, 0),
    utilisation: Math.max(0, Math.min(1, num(raw.utilisation, 1))),
    rank: num(raw.rank, 0),
    perMin: typeof raw.per_min === "number" ? raw.per_min : null,
    unit: unitOf(raw.unit),
    row: row,
    rows: listed.length ? listed : row ? [row] : [],
  };
}

function fullEdge(raw: Loose): PlanEdge {
  const source = String(raw.source);
  const target = String(raw.target);
  const itemId = str(raw.item_id);
  return {
    id: str(raw.id) || source + ">" + target + ":" + (itemId || ""),
    source: source,
    target: target,
    itemId: itemId,
    itemName: String(raw.item_name ?? itemId ?? ""),
    group: groupOf(raw.item_group),
    perMin: num(raw.per_min, 0),
    unit: unitOf(raw.unit),
    text: str(raw.text) || "",
  };
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

/** Totals per building over the process nodes, for a response that sends none. */
export function countBuildings(nodes: PlanNode[]): BuildingCount[] {
  const by: Record<string, BuildingCount> = {};
  nodes.forEach(function (node) {
    if (node.kind !== "process" || !node.building) return;
    const b = (by[node.building] = by[node.building] || { building: node.building, count: 0, groups: 0, mw: 0 });
    b.count += node.built;
    b.groups += 1;
    b.mw += node.mw;
  });
  return Object.keys(by)
    .sort()
    .map(function (k) {
      return by[k]!;
    });
}

/** A stored set as the page uses it: pairs of numbers, anything else dropped. */
export function readPositions(raw: unknown): Record<string, [number, number]> {
  const out: Record<string, [number, number]> = {};
  if (!raw || typeof raw !== "object") return out;
  Object.keys(raw as Loose).forEach(function (id) {
    const p = (raw as Loose)[id];
    if (Array.isArray(p) && typeof p[0] === "number" && typeof p[1] === "number") out[id] = [p[0], p[1]];
  });
  return out;
}

function readBuildings(raw: unknown): BuildingCount[] | null {
  if (!Array.isArray(raw)) return null;
  return raw.map(function (b: Loose) {
    return { building: String(b.building), count: num(b.count, 0), groups: num(b.groups, 0), mw: num(b.mw, 0) };
  });
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
  const rows = rowsByRecipe(data.rows || []);
  const rawNodes = (graph.nodes as Loose[] | undefined) || [];
  const rawEdges = (graph.edges as Loose[] | undefined) || [];
  if (isFull(graph)) {
    const nodes = rawNodes.map(function (n) {
      return fullNode(n, rows);
    });
    return {
      nodes: nodes,
      edges: rawEdges.map(fullEdge),
      positions: readPositions(graph.positions),
      buildings: readBuildings(graph.buildings) || countBuildings(nodes),
      full: true,
    };
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

/** The graph node a build-list pick (a row id) or a graph pick (a node id) stands for. */
export function nodeForPick(view: PlanGraphView, picked: string): PlanNode | undefined {
  if (!picked) return undefined;
  return (
    view.nodes.filter(function (n) {
      return n.id === picked;
    })[0] ||
    view.nodes.filter(function (n) {
      return n.rows.indexOf(picked) >= 0;
    })[0]
  );
}
