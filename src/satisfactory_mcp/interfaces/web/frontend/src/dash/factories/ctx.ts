/* One factory page's reads joined: its machines from /api/machines with their groups, floors
 * and sites, the floor shown, and the pick. Built once per render. */

import { vitals } from "../../app/vitals";
import { hashFor } from "../../map/map";
import { factoryDash } from "./address";
import { pending, q, read } from "./data";
import { buildingIcon, floorsOf, groupMachines, nearestSite, pickFloor, type FloorInfo, type MachineGroup, type PlanMachine } from "./model";
import { needsAction } from "../machine-states";

import type { Safe } from "../../kit/html";
import type { BuildingShape, FactoryAspectsResponse, FactoryGraphResponse, FactoryHealthRow, FloorsResponse, MachinesResponse, SitesResponse } from "../../api/shapes";
import type { Slot } from "./data";

export interface FCtx {
  row: FactoryHealthRow;
  name: string;
  aspect: string;
  /** The floor value links carry: "" (the default), "all" or an ordinal. */
  floorQ: string;
  /** The machines, or the line that stands in while they load. */
  machines: PlanMachine[] | null;
  machinesWait: Safe | null;
  shapes: Record<string, BuildingShape>;
  groups: MachineGroup[];
  floors: FloorInfo[];
  floorsBody: FloorsResponse | null;
  /** The floor shown: an ordinal, or -1 for all. */
  floor: number;
  sites: SitesResponse | null;
  aspects: Slot<FactoryAspectsResponse>;
  aspectsPath: string;
  graph: FactoryGraphResponse | null;
  /** The states, worst first, as the health read orders them. */
  order: string[];
  picked: string;
}

let pick = { factory: "", key: "" };

export function pickedKey(name: string): string {
  return pick.factory === name ? pick.key : "";
}

export function setPick(name: string, key: string): void {
  pick = { factory: name, key };
}

export function slug(text: string): string {
  return text.toLowerCase().replace(/[^a-z0-9]+/g, "-");
}

export function aspectHref(c: Pick<FCtx, "name" | "floorQ">, aspect: string, floorQ?: string): string {
  return hashFor(factoryDash(c.name, aspect, floorQ === undefined ? c.floorQ : floorQ));
}

/** The building icons the health read or the graph names, else the naming rule. */
export function iconFor(c: FCtx, cls: string, building: string): string | null {
  const issue = c.row.worst.concat(c.row.worst_actionable).filter((i) => i.building === cls && i.building_icon)[0];
  if (issue) return issue.building_icon;
  if (c.graph) {
    const node = c.graph.nodes.filter((n) => n.building === building && n.building_icon)[0];
    if (node) return node.building_icon;
  }
  return buildingIcon(cls);
}

/** The keys a plan mark answers to; rows carry one of them in data-hl. */
export function machineKeys(m: PlanMachine): string {
  return ["g" + m.group, "m" + m.id, m.floor === null ? "" : "fl" + m.floor, m.site === null ? "" : "s" + m.site, "st-" + slug(m.row.state)].filter(Boolean).join(" ");
}

export function onFloor(c: FCtx, m: PlanMachine): boolean {
  return c.floor < 0 || m.floor === c.floor;
}

export function buildCtx(row: FactoryHealthRow, aspect: string, asked: string): FCtx {
  const name = row.name;
  const machinesRead = read<MachinesResponse>("/api/machines");
  const floorsRead = read<FloorsResponse>(q("/api/floors", { factory: "label:" + name }));
  const sitesRead = read<SitesResponse>(q("/api/factories/sites", { factory: name }));
  const aspectsPath = q("/api/factories/aspects", { factory: name });
  const aspectsRead = read<FactoryAspectsResponse>(aspectsPath);
  const graphRead = read<FactoryGraphResponse>(q("/api/factories/graph", { factory: name }));
  const health = vitals().health;
  const c: FCtx = {
    row,
    name,
    aspect,
    floorQ: asked,
    machines: null,
    machinesWait: pending(machinesRead, "the machines", "/api/machines"),
    shapes: {},
    groups: [],
    floors: [],
    floorsBody: floorsRead.data,
    floor: -1,
    sites: sitesRead.data,
    aspects: aspectsRead,
    aspectsPath,
    graph: graphRead.data,
    order: health ? health.states : [],
    picked: pickedKey(name),
  };
  const body = machinesRead.data;
  if (!body) return c;
  const aspectIds: Record<string, boolean> = {};
  (aspectsRead.data ? aspectsRead.data.machines : []).forEach((m) => (aspectIds[m.instance] = true));
  const rows = body.machines.concat(body.extractors, body.generators).filter((r) => r.factory === name || aspectIds[r.instance_leaf]);
  const { groups, of } = groupMachines(rows);
  const mine: Record<string, boolean> = {};
  rows.forEach((r) => (mine[r.instance_leaf] = true));
  c.floors = floorsOf(c.floorsBody, mine);
  const floorOf: Record<string, number> = {};
  c.floors.forEach((f) => f.ids.forEach((id) => (floorOf[id] = f.ordinal)));
  const sites = c.sites ? c.sites.sites.filter((s) => s.mine > 0) : [];
  c.groups = groups;
  c.shapes = body.shapes;
  c.machines = rows.map((r) => ({
    row: r,
    id: r.instance_leaf,
    group: of[r.instance_leaf]!,
    floor: floorOf[r.instance_leaf] ?? null,
    site: sites.length ? nearestSite(r.x_m, r.y_m, sites) : null,
  }));
  const states: Record<string, string> = {};
  rows.forEach((r) => (states[r.instance_leaf] = r.state));
  c.floor = pickFloor(c.floors, asked, (id) => needsAction(states[id] || ""));
  return c;
}
