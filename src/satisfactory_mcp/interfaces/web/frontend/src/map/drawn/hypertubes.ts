/* The hypertubes: the player's own travel network, drawn from the splines the save keeps for
 * them exactly as the pipes are (route-geometry.ts), with an outline at each entrance.
 *
 * FICSIT orange, a hue no ore and no other network spends (docs/frontend_palette.md). No
 * chevrons: a tube carries the player either way. On by default: sixty pieces are no clutter,
 * and a tube network is where a reader goes to get across the map.
 */

import { code, popup, popupTitleRow } from "../../kit/dom";
import { L } from "../leaflet";
import { BAND, clearedLayer } from "../layers";
import { pixelsPerMetre } from "../map";
import { declareColours } from "../palette";
import { registerFetch } from "../../app/registry";
import { outlineLatLngs } from "./outlines";
import { routePolyline } from "./route-geometry";
import { ROUTE_WIDTH_M, routeWeight, sinkRoutes } from "./route-passes";

import type { AttachmentRow, BuildingShape, HypertubeRow, HypertubesResponse } from "../../api/shapes";

const TUBE = declareColours("hypertubes", { hypertubes: "#ff9f1c", "hypertube entrance": "#c86b00" });
const TUBE_COLOUR = TUBE.hypertubes;

function tubePopup(tube: HypertubeRow): string {
  const first = tube.points_m[0]!;
  const last = tube.points_m[tube.points_m.length - 1]!;
  return popup([
    popupTitleRow("hypertube", tube.name, tube.cls),
    ["ends", first[0] + ", " + first[1] + " m and " + last[0] + ", " + last[1] + " m"],
    ["rise", Math.abs(Math.round((last[2] - first[2]) * 10) / 10) + " m"],
  ]);
}

/* The square an entrance gets if the shapes table has no outline for it. */
const ENTRANCE_FALLBACK_M = 3;

function entranceShape(entrance: AttachmentRow, x_m: number, y_m: number, shape?: BuildingShape): L.Polygon {
  const halfWidth = (entrance.w_m || ENTRANCE_FALLBACK_M) / 2;
  const halfLength = (entrance.l_m || ENTRANCE_FALLBACK_M) / 2;
  return L.polygon(outlineLatLngs(x_m, y_m, entrance.yaw, shape, halfWidth, halfLength), {
    color: TUBE["hypertube entrance"],
    weight: 1.5,
    fillColor: TUBE_COLOUR,
    fillOpacity: 0.8,
  }).bindPopup(
    popup([
      popupTitleRow("hypertube entrance", entrance.name, entrance.cls),
      ["facing", entrance.yaw === null || entrance.yaw === undefined ? null : Math.round(entrance.yaw) + "°"],
      ["at", x_m + ", " + y_m + " m"],
      ["id", code(entrance.instance_leaf)],
    ])
  );
}

export function drawHypertubes(data: HypertubesResponse): void {
  // Between the pipes and the power, in the built band: a network, not a placement.
  const group = clearedLayer("hypertubes", {
    on: true,
    colour: TUBE_COLOUR,
    rank: [BAND.built, 25, "hypertubes"],
  });
  const ppm = pixelsPerMetre();
  // Not in the floor filter's list: a tube runs between factories, not on one floor of one.
  data.hypertubes.forEach(function (tube) {
    if (tube.points_m.length < 2) return;
    routePolyline(tube.points_m, tube.curve_m, ppm, {
      color: TUBE_COLOUR,
      weight: routeWeight(ROUTE_WIDTH_M.hypertubes, ppm),
      opacity: 0.85,
    })
      .bindPopup(tubePopup(tube))
      .addTo(group);
  });
  data.entrances.forEach(function (entrance) {
    if (entrance.x_m === null || entrance.y_m === null) return;
    entranceShape(entrance, entrance.x_m, entrance.y_m, data.shapes[entrance.cls]).addTo(group);
  });
  sinkRoutes();
}

/** Static, like the pipes: tubes move when the player builds, and a switch refetches them. */
registerFetch<HypertubesResponse>({
  wave: "static",
  rank: 45,
  path: "/api/hypertubes",
  label: "hypertubes",
  clears: ["hypertubes"],
  refilters: true,
  draw: drawHypertubes,
});
