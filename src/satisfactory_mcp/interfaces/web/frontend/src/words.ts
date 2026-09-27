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
  polesAndTowers: "poles and towers",
  plan: "plan",
  actorYou: "you",
  actorChat: "chat",
  noSaves: "no readable saves",
} as const;

export function version(n: number): string {
  return "version v" + n;
}

export function counted(n: number, one: string, many?: string): string {
  return count(n) + " " + (n === 1 ? one : many === undefined ? one + "s" : many);
}
