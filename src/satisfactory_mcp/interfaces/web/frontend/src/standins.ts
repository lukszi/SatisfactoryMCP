/* Temporary stand-ins for words.ts, nav.dashParts() and dashkit.empty(), built in parallel on other
 * branches. Delete this file and point its importers at the real modules when they merge. */

import { make } from "./dom";
import { state } from "./state";

export var W = {
  generation: "generation",
  measuredDraw: "measured draw",
  nameplateDraw: "nameplate draw",
  headroomNow: "headroom now",
  headroomFull: "headroom at full rate",
  polesAndTowers: "poles and towers",
  noSaves: "no readable saves",
} as const;

export interface DashParts {
  tab: string;
  subject: string;
  rest: string[];
}

export function dashParts(dash?: string): DashParts {
  var raw = dash === undefined ? state.dash : dash;
  var cut = raw.indexOf("/");
  var subject = cut < 0 ? "" : raw.slice(cut + 1);
  return { tab: cut < 0 ? raw : raw.slice(0, cut), subject: subject, rest: subject ? subject.split("/") : [] };
}

export function empty(parent: HTMLElement, what: string, how?: string | HTMLElement): void {
  var box = make("div", "dk-state dk-empty");
  box.setAttribute("role", "status");
  box.appendChild(make("p", "dash-note", what));
  if (how instanceof HTMLElement) box.appendChild(how);
  else if (how) box.appendChild(make("p", "dash-note", how));
  parent.appendChild(box);
}
