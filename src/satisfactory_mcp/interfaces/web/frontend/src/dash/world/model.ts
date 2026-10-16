/* The World finder's rows: grouping, sorting, distances and the marks the map draws. Pure. */

import type { CollectibleRow, FoundField, FoundNode, NetworkRow, RankedSite, RunRow } from "../../api/shapes";

export type PointM = [number, number];

/** What the map draws for one result; `k` is the row's key, shared with its entry. */
export type Mark =
  | { t: "node"; k: string; x: number; y: number; resource: string; purity: string; free: boolean; tip: string }
  | { t: "hull"; k: string; pts: PointM[]; tip: string }
  | { t: "site"; k: string; x: number; y: number; r_m: number; rank: number; tip: string }
  | { t: "line"; k: string; lines: PointM[][]; tip: string }
  | { t: "spot"; k: string; x: number; y: number; tip: string }
  | { t: "pick"; k: string; x: number; y: number; category: string; collected: boolean; tip: string }
  | { t: "label"; x: number; y: number; text: string }
  | { t: "factory"; x: number; y: number; text: string };

/** Rows shown before "show more". */
export const PAGE = 30;

const FLUIDS = ["Desc_LiquidOil_C", "Desc_NitrogenGas_C", "Desc_Water_C"];

export function isFluid(resource: string): boolean {
  return FLUIDS.indexOf(resource) >= 0;
}

export function rateUnit(resource: string): string {
  return isFluid(resource) ? "m³/min" : "/min";
}

export function distanceM(a: { x_m: number; y_m: number }, b: PointM | null): number | null {
  if (!b) return null;
  return Math.hypot(a.x_m - b[0], a.y_m - b[1]);
}

function farLast(a: number | null, b: number | null): number {
  return (a === null ? Infinity : a) - (b === null ? Infinity : b);
}

export interface Grouped<R> {
  label: string;
  rows: R[];
}

export function sortNodes(rows: FoundNode[], sort: string): FoundNode[] {
  const out = rows.slice();
  if (sort === "rate") out.sort((a, b) => b.rate - a.rate || farLast(a.distance_m, b.distance_m));
  else out.sort((a, b) => farLast(a.distance_m, b.distance_m) || b.rate - a.rate);
  return out;
}

const STATUS_ORDER = ["free", "tapped", "locked"];

/** None groups by status (free, tapped, locked); region by region, the fullest first. */
export function groupNodes(rows: FoundNode[], group: string): Grouped<FoundNode>[] {
  const by: Record<string, FoundNode[]> = {};
  const key = (n: FoundNode) => (group === "region" ? (n.region ? n.region.name : "off the map") : n.status);
  rows.forEach((n) => (by[key(n)] = by[key(n)] || []).push(n));
  const labels = Object.keys(by);
  if (group === "region") labels.sort((a, b) => by[b]!.length - by[a]!.length || a.localeCompare(b));
  else labels.sort((a, b) => STATUS_ORDER.indexOf(a) - STATUS_ORDER.indexOf(b));
  return labels.map((label) => ({ label, rows: by[label]! }));
}

export function sortFields(rows: FoundField[], sort: string): FoundField[] {
  const out = rows.slice();
  if (sort === "total") out.sort((a, b) => b.total - a.total || farLast(a.distance_m, b.distance_m));
  else out.sort((a, b) => farLast(a.distance_m, b.distance_m) || b.total - a.total);
  return out;
}

export function sortRuns(rows: RunRow[], sort: string): RunRow[] {
  const out = rows.slice();
  if (sort === "distance") out.sort((a, b) => a.distance_m - b.distance_m || b.length_m - a.length_m);
  else out.sort((a, b) => b.length_m - a.length_m || a.distance_m - b.distance_m);
  return out;
}

export function sortNetworks(rows: NetworkRow[], sort: string): NetworkRow[] {
  const out = rows.slice();
  if (sort === "length") out.sort((a, b) => b.length_m - a.length_m || a.distance_m - b.distance_m);
  else out.sort((a, b) => a.distance_m - b.distance_m || b.length_m - a.length_m);
  return out;
}

/** Pickups with a distance measured from `from` where the server sent none. */
export interface Pickup {
  row: CollectibleRow;
  distance_m: number | null;
  /** True when the page measured the distance. */
  measured: boolean;
}

export function pickups(rows: CollectibleRow[], from: PointM | null, sort: string, labelOf: (category: string) => string): Pickup[] {
  const out = rows.map(function (row): Pickup {
    if (row.distance_m !== null) return { row, distance_m: row.distance_m, measured: false };
    const d = distanceM(row, from);
    return { row, distance_m: d, measured: d !== null };
  });
  if (sort === "kind") out.sort((a, b) => labelOf(a.row.category).localeCompare(labelOf(b.row.category)) || farLast(a.distance_m, b.distance_m));
  else out.sort((a, b) => farLast(a.distance_m, b.distance_m));
  return out;
}

/** Ranked sites carry no distance; the page measures it from the place searched near. */
export function siteDistance(site: RankedSite, from: PointM | null): number | null {
  return distanceM(site, from);
}

/** The convex hull, counter-clockwise; fewer than three points come back as they are. */
export function hull(points: PointM[]): PointM[] {
  const p = points.slice().sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  if (p.length < 3) return p;
  const cross = (o: PointM, a: PointM, b: PointM) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
  const lo: PointM[] = [];
  const hi: PointM[] = [];
  p.forEach(function (q) {
    while (lo.length > 1 && cross(lo[lo.length - 2]!, lo[lo.length - 1]!, q) <= 0) lo.pop();
    lo.push(q);
  });
  p.slice()
    .reverse()
    .forEach(function (q) {
      while (hi.length > 1 && cross(hi[hi.length - 2]!, hi[hi.length - 1]!, q) <= 0) hi.pop();
      hi.push(q);
    });
  return lo.slice(0, -1).concat(hi.slice(0, -1));
}

/** A field's outline: the hull of its member nodes where their places are known, else its box. */
export function fieldOutline(field: FoundField, placeOf: (name: string) => PointM | null): PointM[] {
  const pts = field.members.map(placeOf).filter((p): p is PointM => !!p);
  if (pts.length) return hull(pts);
  const b = field.bbox_m;
  return [
    [b[0], b[1]],
    [b[2], b[1]],
    [b[2], b[3]],
    [b[0], b[3]],
  ];
}

/** A pickup category's game icon, where it has one. */
export const PICKUP_ICON: Record<string, string> = {
  somersloop: "Desc_WAT1_C",
  mercer_sphere: "Desc_WAT2_C",
  power_slug_blue: "Desc_Crystal_C",
  power_slug_yellow: "Desc_Crystal_mk2_C",
  power_slug_purple: "Desc_Crystal_mk3_C",
  hard_drive: "Desc_HardDrive_C",
};

/** The keys rows and marks share. */
export const keyOf = {
  node: (n: FoundNode) => "n-" + n.id,
  field: (f: FoundField) => "f-" + f.key,
  site: (s: RankedSite) => "s-" + s.rank,
  run: (r: RunRow) => "r-" + r.id,
  net: (n: NetworkRow) => "w-" + (n.network === null ? "none" : n.network),
  pick: (p: CollectibleRow) => "p-" + p.name,
};
