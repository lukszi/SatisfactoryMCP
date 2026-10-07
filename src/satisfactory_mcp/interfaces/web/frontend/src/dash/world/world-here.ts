/* World > here: where the player stood at the save, and the nodes around them. The read is in
 * the live wave so the view is ready before the tab opens. */

import { appendNote, empty, heading, link, pendingNotice } from "../../kit/dashkit";
import { make } from "../../kit/dom";
import { coords, count, formatNumber, metres, roundHalfEven, withDetail } from "../../kit/format";
import { loadOne } from "../../app/load";
import { withQuery } from "../../app/nav";
import { showPoint } from "../../map/map-highlight";
import { registerFetch } from "../../app/registry";
import { counted } from "../../kit/words";
import { mapButton } from "../actions";
import { nodeTable } from "./world-nodes";
import { bumpSaveWave, copyCell, redraw, regionCell, showAllToggle, staleLine, viewDash } from "./world-kit";

import type { ApiPath } from "../../api/client";
import type { HereResponse } from "../../api/shapes";

const HERE_PATH: ApiPath = "/api/world/here";

const HERE_RADIUS_M = 500;

const herePart = {
  data: null as HereResponse | null,
  failed: false,
  token: "",
};

function hereQuery(): string {
  return withQuery("", { radius_m: String(HERE_RADIUS_M) }).slice(1);
}

function factList(data: HereResponse, player: NonNullable<HereResponse["player"]>): HTMLElement {
  const facts: [string, string | HTMLElement][] = [
    ["position", coords(player.x_m, player.y_m) + ", " + formatNumber(player.z_m, 0) + " m up"],
    ["region", regionCell(data.region, true)],
    ["grid", data.grid ? withDetail(data.grid, data.direction) : "–"],
    ["nearest building", data.nearest_building ? data.nearest_building.name + ", " + metres(data.nearest_building.distance_m) : "none"],
    ["id", copyCell(roundHalfEven(player.x_m) + "," + roundHalfEven(player.y_m), "copy")],
  ];
  if (data.pawns > 1) facts.push(["players", count(data.pawns) + " in this save; the position is the host's"]);
  const list = make("dl", "world-facts");
  facts.forEach(function (fact) {
    list.appendChild(make("dt", "", fact[0]));
    const value = make("dd");
    if (typeof fact[1] === "string") value.textContent = fact[1];
    else value.appendChild(fact[1]);
    list.appendChild(value);
  });
  return list;
}

function hereActions(player: NonNullable<HereResponse["player"]>): HTMLElement {
  const acts = make("div", "world-acts");
  acts.appendChild(
    mapButton(
      "fly the map to the player",
      function () {
        showPoint(player.x_m, player.y_m, { label: "the player" });
      },
      "show the player on the map"
    )
  );
  acts.appendChild(link(viewDash("nodes", { near: "me" }), "nodes near me"));
  acts.appendChild(link(viewDash("conduits", { near: "me" }), "conduits near me"));
  acts.appendChild(link(viewDash("pickups", { view: "nearest" }), "pickups near me"));
  return acts;
}

export function renderHere(body: HTMLElement): void {
  const card = make("section", "dash-card");
  heading(card, "where the player is");
  body.appendChild(card);
  const data = herePart.data;
  if (!data) {
    pendingNotice(card, "the player's position", null, herePart.failed, function () {
      loadOne(HERE_PATH);
    });
    return;
  }
  appendNote(card, data.written_ago ? "as of the save written " + data.written_ago : "as of the save shown in the header").title = data.age_note;
  const player = data.player;
  if (!player) {
    empty(card, "no player position in this save", "a dedicated-server save holds no pawn; nodes, fields, conduits, pickups and regions still work");
    return;
  }
  card.appendChild(factList(data, player));
  card.appendChild(hereActions(player));
  data.stale.forEach(function (age) {
    if (age.table === "nodes") staleLine(card, age);
  });
  const near = make("section", "dash-card");
  heading(near, counted(data.nodes_total, "node") + " within " + formatNumber(data.radius_m, 0), "m");
  body.appendChild(near);
  if (!data.nodes.length) {
    empty(near, "no resource node within " + formatNumber(data.radius_m, 0) + " m");
    return;
  }
  const grid = nodeTable(data.nodes, true, null);
  near.appendChild(grid);
  showAllToggle(near, grid, data.nodes.length, "here", "node");
}

registerFetch<HereResponse>({
  wave: "live",
  rank: 80,
  path: HERE_PATH,
  query: hereQuery,
  label: "where the player is",
  clears: [],
  refilters: false,
  draw: function (data) {
    if (herePart.token && herePart.token !== data.save_token) bumpSaveWave();
    herePart.token = data.save_token;
    herePart.data = data;
    herePart.failed = false;
    redraw();
  },
  failed: function () {
    herePart.data = null;
    herePart.failed = true;
    redraw();
  },
});
