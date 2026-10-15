/* A production graph as the page draws it, for a plan or a built factory: one wire shape with
 * stable ids, rates and item groups on every node and edge. A node's clock comes in percent and
 * is kept as a fraction. See docs/planner_vision.md §4.2 and docs/frontend_vision.md §9.8. */

export type NodeKind = "process" | "input" | "export" | "leftover" | "sink";
export type Unit = "items" | "m3" | "MW";

export const ITEM_GROUPS = ["oil", "water", "gas", "ore", "metal", "component", "nuclear", "power", "other"] as const;
export type ItemGroup = (typeof ITEM_GROUPS)[number];

export interface ProdNode {
  id: string;
  kind: NodeKind;
  label: string;
  /** A plan's older one-line summary; empty in the full shape. */
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
  /** What an item node's rate means here, e.g. "to storage"; null for a plan. */
  note: string | null;
  /** Measured share of time producing over the monitored machines; null when not measured. */
  productivity: number | null;
  monitored: number;
  /** Machines per health state; empty for a plan. */
  states: Record<string, number>;
  /** Where a built process's machines stand, in metres. */
  bbox: [number, number, number, number] | null;
  /** A plan's first build row this node stands for; `rows` has them all. */
  row: string | null;
  rows: string[];
}

export interface ProdEdge {
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

export interface ProdGraph {
  nodes: ProdNode[];
  edges: ProdEdge[];
  positions: Record<string, [number, number]>;
  buildings: BuildingCount[];
  /** True when the server sent the full shape, which is also the one that stores positions. */
  full: boolean;
}

export type Loose = Record<string, unknown>;

const KINDS: NodeKind[] = ["process", "input", "export", "leftover", "sink"];
const WHOLE = 1e-3;

export function str(value: unknown): string | null {
  return typeof value === "string" && value ? value : null;
}

export function num(value: unknown, fallback: number): number {
  return typeof value === "number" && isFinite(value) ? value : fallback;
}

export function unitOf(value: unknown): Unit {
  return value === "m3" || value === "MW" ? value : "items";
}

export function groupOf(value: unknown): ItemGroup {
  return (ITEM_GROUPS as readonly string[]).indexOf(String(value)) >= 0 ? (value as ItemGroup) : "other";
}

export function kindOf(value: unknown): NodeKind {
  return KINDS.indexOf(value as NodeKind) >= 0 ? (value as NodeKind) : "process";
}

export function rateText(perMin: number, unit: Unit): string {
  const n = Math.round(perMin * 10) / 10;
  if (unit === "MW") return n.toLocaleString("en-GB") + " MW";
  return n.toLocaleString("en-GB") + (unit === "m3" ? " m³/min" : "/min");
}

export function edgeLabel(edge: Pick<ProdEdge, "itemName" | "perMin" | "unit" | "text">): string {
  return edge.text || edge.itemName + " " + rateText(edge.perMin, edge.unit);
}

/** The last machine's share of `work`, or 1 when the work is whole machines. */
export function lastShare(work: number): number {
  const part = work - Math.floor(work);
  return part < WHOLE || 1 - part < WHOLE ? 1 : part;
}

function readStates(raw: unknown): Record<string, number> {
  const out: Record<string, number> = {};
  if (!raw || typeof raw !== "object") return out;
  Object.keys(raw as Loose).forEach(function (state) {
    const n = (raw as Loose)[state];
    if (typeof n === "number" && n > 0) out[state] = n;
  });
  return out;
}

function readBox(raw: unknown): [number, number, number, number] | null {
  if (!Array.isArray(raw) || raw.length !== 4) return null;
  const [a, b, c, d] = raw as unknown[];
  return typeof a === "number" && typeof b === "number" && typeof c === "number" && typeof d === "number" ? [a, b, c, d] : null;
}

/** One node of the full shape; `row` and `rows` are the plan's, where it sends them. */
export function readNode(raw: Loose): ProdNode {
  const listed = Array.isArray(raw.rows) ? raw.rows.map(String) : [];
  const row = str(raw.row) || listed[0] || null;
  const machines = num(raw.machines, 0);
  return {
    id: String(raw.id),
    kind: kindOf(raw.kind),
    label: String(raw.label ?? raw.id),
    detail: String(raw.detail ?? ""),
    itemId: str(raw.item_id),
    itemName: str(raw.item_name),
    recipeId: str(raw.recipe_id),
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
    note: str(raw.note),
    productivity: typeof raw.productivity === "number" ? Math.max(0, Math.min(1, raw.productivity)) : null,
    monitored: num(raw.monitored, 0),
    states: readStates(raw.states),
    bbox: readBox(raw.bbox_m),
    row: row,
    rows: listed.length ? listed : row ? [row] : [],
  };
}

export function readEdge(raw: Loose): ProdEdge {
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

/** Totals per building over the process nodes, for a response that sends none. */
export function countBuildings(nodes: ProdNode[]): BuildingCount[] {
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

export function readBuildings(raw: unknown): BuildingCount[] | null {
  if (!Array.isArray(raw)) return null;
  return raw.map(function (b: Loose) {
    return { building: String(b.building), count: num(b.count, 0), groups: num(b.groups, 0), mw: num(b.mw, 0) };
  });
}

/** A graph in the full shape: `{nodes, edges, positions?, buildings?}`. */
export function readGraph(graph: Loose): ProdGraph {
  const nodes = ((graph.nodes as Loose[] | undefined) || []).map(readNode);
  return {
    nodes: nodes,
    edges: ((graph.edges as Loose[] | undefined) || []).map(readEdge),
    positions: readPositions(graph.positions),
    buildings: readBuildings(graph.buildings) || countBuildings(nodes),
    full: true,
  };
}

/** The graph node a list pick (a plan row id) or a graph pick (a node id) stands for. */
export function nodeForPick(view: ProdGraph, picked: string): ProdNode | undefined {
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
