/* What the Factories list and a factory page show, worked out from the reads: sorting, the
 * state mix, machine groups, floors and boosts. Pure. docs/frontend_kit.md, "Factories". */

import type { BarFill } from "../../kit/ui/marks";
import type { StateTone } from "../machine-states";
import type { FactoryHealthRow, FloorBand, FloorsResponse, PlacementRow, ShardsResponse, SloopsResponse } from "../../api/shapes";

export type FactorySort = "need" | "name" | "uptime" | "draw";

export const SORTS: [FactorySort, string][] = [
  ["need", "need action"],
  ["name", "name"],
  ["uptime", "uptime"],
  ["draw", "power consumption"],
];

export function sortFactories(rows: FactoryHealthRow[], by: FactorySort): FactoryHealthRow[] {
  const out = rows.slice();
  if (by === "name") out.sort((a, b) => a.name.localeCompare(b.name));
  else if (by === "uptime") out.sort((a, b) => (a.uptime ?? 2) - (b.uptime ?? 2));
  else if (by === "draw") out.sort((a, b) => b.measured_mw - a.measured_mw);
  else out.sort((a, b) => b.actionable - a.actionable);
  return out;
}

const FILL_ORDER: BarFill[] = ["ok", "mid", "blocked", "bad"];

/** The state mix as bar segments, fine first, worst last. */
export function mixParts(states: { state: string; count: number }[], tone: (state: string) => StateTone): [BarFill, number][] {
  const by: Record<string, number> = {};
  states.forEach((s) => {
    const fill = tone(s.state);
    by[fill] = (by[fill] || 0) + s.count;
  });
  return FILL_ORDER.filter((f) => by[f]).map((f): [BarFill, number] => [f, by[f]!]);
}

/** The descriptor class a building's picture is filed under, by the game's naming rule. */
export function buildingIcon(cls: string): string | null {
  return cls.indexOf("Build_") === 0 ? "Desc_" + cls.slice(6) : null;
}

/** A machine of this factory as the plan and the lists use it. */
export interface PlanMachine {
  row: PlacementRow;
  id: string;
  group: number;
  /** The floor's ordinal, or null for a machine on no deck of this factory. */
  floor: number | null;
  site: number | null;
}

/** One line of the machines list: a building running one recipe. */
export interface MachineGroup {
  index: number;
  building: string;
  cls: string;
  recipe: string | null;
  ids: string[];
  states: Record<string, number>;
  /** Mean clock as a fraction. */
  clock: number;
}

function groupKey(row: PlacementRow): string {
  return row.name + "|" + (row.recipe_name || "");
}

/** Groups by building and recipe, in building order, then recipe. */
export function groupMachines(rows: PlacementRow[]): { groups: MachineGroup[]; of: Record<string, number> } {
  const by: Record<string, PlacementRow[]> = {};
  rows.forEach((row) => {
    (by[groupKey(row)] = by[groupKey(row)] || []).push(row);
  });
  const keys = Object.keys(by).sort((a, b) => a.localeCompare(b));
  const of: Record<string, number> = {};
  const groups = keys.map(function (key, index): MachineGroup {
    const members = by[key]!;
    const states: Record<string, number> = {};
    let clock = 0;
    members.forEach((m) => {
      states[m.state] = (states[m.state] || 0) + 1;
      clock += m.clock ?? 1;
      of[m.instance_leaf] = index;
    });
    const first = members[0]!;
    return { index, building: first.name, cls: first.cls, recipe: first.recipe_name, ids: members.map((m) => m.instance_leaf), states, clock: clock / members.length };
  });
  return { groups, of };
}

/** The worst state present, by the server's order (worst first); "" for none. */
export function worstState(states: Record<string, number>, order: string[]): string {
  let best = "";
  let rank = Infinity;
  Object.keys(states).forEach((s) => {
    const at = order.indexOf(s);
    const r = at < 0 ? order.length : at;
    if (states[s] && r < rank) {
      rank = r;
      best = s;
    }
  });
  return best;
}

/** One floor of this factory, merged over the platforms it stands on by ordinal. */
export interface FloorInfo {
  ordinal: number;
  /** Deck height over the lowest floor, metres; null when no deck height is known. */
  deck: number | null;
  area: number;
  minor: boolean;
  ids: string[];
  bands: { platform: number; band: FloorBand }[];
}

/** The floors that hold any of `mine`, top down. */
export function floorsOf(body: FloorsResponse | null, mine: Record<string, unknown>): FloorInfo[] {
  if (!body) return [];
  const by: Record<number, FloorInfo> = {};
  let base = Infinity;
  body.platforms.forEach(function (p) {
    p.bands.forEach(function (band) {
      const ids = band.machines.filter((id) => id in mine);
      if (!ids.length) return;
      const f = (by[band.ordinal] = by[band.ordinal] || { ordinal: band.ordinal, deck: null, area: 0, minor: true, ids: [], bands: [] });
      f.ids = f.ids.concat(ids);
      f.area += band.area_m2;
      f.minor = f.minor && band.minor;
      f.bands.push({ platform: p.index, band });
      if (band.top_m !== null) {
        f.deck = f.deck === null ? band.top_m : Math.min(f.deck, band.top_m);
        base = Math.min(base, band.top_m);
      }
    });
  });
  const out = Object.keys(by).map((k) => by[+k]!);
  out.forEach((f) => {
    if (f.deck !== null) f.deck = Math.round((f.deck - base) * 10) / 10;
  });
  return out.sort((a, b) => b.ordinal - a.ordinal);
}

/** Which floor shows: "all" (-1) for fewer than two floors or when asked, an asked ordinal
 *  that exists, else the floor with most machines needing action (ties: the largest deck). */
export function pickFloor(floors: FloorInfo[], asked: string, needs: (id: string) => boolean): number {
  if (floors.length < 2 || asked === "all") return -1;
  if (asked !== "" && floors.some((f) => String(f.ordinal) === asked)) return +asked;
  let best = floors[0]!;
  let most = -1;
  floors.forEach(function (f) {
    const n = f.ids.filter(needs).length;
    if (n > most || (n === most && f.area > best.area)) {
      best = f;
      most = n;
    }
  });
  return best.ordinal;
}

/** The floor shown below `ordinal` as a ghost: the next lower one, if any. */
export function floorBelow(floors: FloorInfo[], ordinal: number): number | null {
  let below: number | null = null;
  floors.forEach((f) => {
    if (f.ordinal < ordinal && (below === null || f.ordinal > below)) below = f.ordinal;
  });
  return below;
}

/** Shards and sloops held by one group's machines, from the progress reads. */
export interface Boost {
  shards: number;
  idle: number;
  sloops: number;
  disagrees: boolean;
}

function leafOf(instance: string): string {
  const cut = instance.lastIndexOf(".");
  return cut < 0 ? instance : instance.slice(cut + 1);
}

export function boostsOf(groups: MachineGroup[], shards: ShardsResponse | null, sloops: SloopsResponse | null): Record<number, Boost> {
  const groupOf: Record<string, number> = {};
  groups.forEach((g) => g.ids.forEach((id) => (groupOf[id] = g.index)));
  const out: Record<number, Boost> = {};
  const at = function (instance: string): Boost | null {
    const g = groupOf[leafOf(instance)];
    if (g === undefined) return null;
    return (out[g] = out[g] || { shards: 0, idle: 0, sloops: 0, disagrees: false });
  };
  (shards ? shards.holders : []).forEach((h) => {
    const b = at(h.instance);
    if (!b || !h.slotted) return;
    b.shards += h.slotted;
    b.idle += h.idle;
  });
  (sloops ? sloops.holders : []).forEach((h) => {
    const b = at(h.instance);
    if (!b || !h.sloops) return;
    b.sloops += h.sloops;
    if (h.boost !== null && h.boost_in_save !== null && Math.abs(h.boost - h.boost_in_save) > 1e-3) b.disagrees = true;
  });
  return out;
}

/** The index of the site whose centre is nearest, among those with a centre. */
export function nearestSite(x: number | null, y: number | null, sites: { x_m: number | null; y_m: number | null }[]): number | null {
  if (x === null || y === null) return null;
  let best: number | null = null;
  let dist = Infinity;
  sites.forEach((s, i) => {
    if (s.x_m === null || s.y_m === null) return;
    const d = (s.x_m - x) ** 2 + (s.y_m - y) ** 2;
    if (d < dist) {
      dist = d;
      best = i;
    }
  });
  return best;
}
