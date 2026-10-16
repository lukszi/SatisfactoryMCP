/* Where the player stood at the save: the World page's strip and the point its distances are
 * measured from. In the live wave, so it is ready before the page opens; a new save drops
 * the finder reads made against the old one. */

import { registerFetch } from "../../app/registry";
import { forget } from "../factories/data";
import { requestRender } from "../actions";
import { state } from "../../app/state";

import type { HereResponse } from "../../api/shapes";
import type { PointM } from "./model";

export const here = { data: null as HereResponse | null, failed: false };

let token = "";
const made: string[] = [];

/** Notes a finder read, so a new save reads it again. */
export function madeRead(path: string): string {
  if (made.indexOf(path) < 0) made.push(path);
  return path;
}

/** The player's place, when the save has a pawn. */
export function playerAt(): PointM | null {
  const p = here.data?.player;
  return p ? [p.x_m, p.y_m] : null;
}

/** False only when the save is known to hold no pawn: "near me" then means nothing. */
export function playerKnown(): boolean {
  return !here.data || !!here.data.player;
}

/* The World page and the Overview's map draw the player. */
function shown(): boolean {
  return state.dash === "world" || state.dash.indexOf("world?") === 0 || state.dash === "overview";
}

registerFetch<HereResponse>({
  wave: "live",
  rank: 80,
  path: "/api/world/here",
  query: () => "radius_m=500",
  label: "where the player is",
  clears: [],
  refilters: false,
  draw: function (data) {
    if (token && token !== data.save_token) made.splice(0).forEach(forget);
    token = data.save_token;
    here.data = data;
    here.failed = false;
    if (shown()) requestRender();
  },
  failed: function () {
    here.data = null;
    here.failed = true;
    if (shown()) requestRender();
  },
});
