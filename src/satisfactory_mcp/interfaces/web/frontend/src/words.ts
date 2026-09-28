/* The page's canonical terms, one spelling per concept.
 * The reasoning behind each choice is in docs/frontend_vision.md. */

import { count } from "./dom";

export var W = {
  needAction: "need action",
  notRunning: "not running",
  powerProblems: "power problems",
  noWire: "no wire",
  noGenerator: "no generator",
  starvedGenerator: "starved generator",
  noFuel: "no fuel loaded",
  blocked: "blocked",
  paused: "paused",
  factory: "factory",
  factories: "factories",
  unnamedCluster: "unnamed cluster",
  unnamedClusters: "unnamed clusters",
  generation: "generation",
  measuredDraw: "measured draw",
  nameplateDraw: "nameplate draw",
  headroomNow: "headroom now",
  headroomFull: "headroom at full rate",
  biomassNotCounted: "biomass not counted",
  polesAndTowers: "poles and towers",
  plan: "plan",
  actorYou: "you",
  actorChat: "chat",
  noSaves: "no readable saves",
  free: "free",
  tapped: "tapped",
  locked: "locked",
  field: "field",
  hiddenBySpoilers: "hidden while spoilers are off",
  node: "node",
  run: "run",
  network: "network",
  remaining: "remaining",
  collected: "collected",
  neverStreamed: "never streamed",
  mapDataBehind: "map data older than this save",
  letSolverChoose: "let the solver choose",
  recipes: "recipes",
  pin: "pin",
  lockedHidden: function (n: number): string {
    return counted(n, "locked recipe") + " hidden";
  },
  track: "track",
  askChat: "ask chat",
  countAsBuilt: "count as built",
  wholeWorld: "whole world",
  startupHeadroom: "startup headroom",
  stageUnit: "stage",
  stages: "stages",
  stage: function (n: number, of: number): string {
    return W.stageUnit + " " + count(n) + " of " + count(of);
  },
} as const;

export var NODE_KIND: Record<string, string> = { node: "node", well_sat: "well satellite", geyser: "geyser" };

export var VERB: Record<string, string> = {
  ok: "–",
  unpause: "unpause",
  setrecipe: "set recipe",
  build: "build",
};

export var ASK_STATE: Record<string, string> = {
  open: "waiting for chat",
  seen: "seen by chat",
  answered: "answered",
};

export var ASK_KIND: Record<string, string> = {
  plan: "plan",
  process: "process",
  stage: "stage",
  item: "item",
  pin: "pin",
};

export var PIN_KIND: Record<string, string> = {
  plan: "plan",
  process: "process",
  machine: "machine",
  factory: "factory",
  field: "field",
  node: "node",
  point: "point",
};

export var OBJECTIVES: Record<string, string> = {
  max_mw: "max MW",
  max_item: "max item",
  min_raw: "min raw",
  min_machines: "min machines",
  min_power: "min power",
};

export function objectiveText(text: string): string {
  return text.replace(/\((\w+)\)$/, function (all, objective: string) {
    return OBJECTIVES[objective] ? "(" + OBJECTIVES[objective] + ")" : all;
  });
}

export var RECIPE_KIND: Record<string, string> = { part: "machine", building: "building", manual: "crafted" };

export function gapText(gap: string): string {
  return gap.replace(/buildVersion/g, "build").replace(/saveVersion/g, "save format").replace("->", "→");
}

export function version(n: number): string {
  return "version v" + n;
}

export function counted(n: number, one: string, many?: string): string {
  return count(n) + " " + (n === 1 ? one : many === undefined ? one + "s" : many);
}
