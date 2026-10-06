/* The planner's addresses: `planner/<key>` and what may follow it. */

import { dashParts } from "../../app/nav";

export interface PlannerAddress {
  key: string;
  viewedRev: number;
  alternatesItem: string;
  track: boolean;
  site: boolean;
  stage: number;
}

/** `<key>[/v<n> | /alt/<item> | /track[/<stage>] | /site]`, the part after `planner/`. */
export function parsePlannerAddress(at: string): PlannerAddress {
  var rest = dashParts("planner/" + at).rest;
  var key = rest[0] || "";
  var version = /^v(\d+)$/.exec(rest[1] || "");
  var item = rest[1] === "alt" && rest[2] ? rest.slice(2).join("/") : "";
  var track = rest[1] === "track";
  var stage = track && /^\d+$/.test(rest[2] || "") ? Number(rest[2]) : 0;
  return { key: key, viewedRev: version ? Number(version[1]) : 0, alternatesItem: item, track: track, site: rest[1] === "site", stage: stage };
}

/** The plan the address bar has open: its key, "" on the plans list, null off the planner. */
export function openPlanKey(): string | null {
  var at = dashParts();
  if (at.tab !== "planner") return null;
  return parsePlannerAddress(at.subject).key;
}

export function altDash(key: string, item: string): string {
  return "planner/" + key + "/alt/" + item;
}

export function trackDash(key: string, stage: number): string {
  return "planner/" + key + "/track" + (stage ? "/" + stage : "");
}
