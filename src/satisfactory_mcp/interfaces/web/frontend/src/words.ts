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
} as const;

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

export function version(n: number): string {
  return "version v" + n;
}

export function counted(n: number, one: string, many?: string): string {
  return count(n) + " " + (n === 1 ? one : many === undefined ? one + "s" : many);
}
