/* The conveyor network, drawn as the routes it actually takes.
 *
 * ONE colour family for the whole network, and tier as a VALUE step inside it. Tier cannot be a
 * WIDTH step because width is physical here and every belt is the same two metres across, Mk1
 * to Mk5 -- drawing a Mk5 wider would contradict the same map's machines, drawn at their
 * measured footprint.
 *
 * A LIFT gets a ring instead of a line, because a line is not available: a lift is exactly
 * vertical, so its top-down polyline is one point and draws nothing at all. A class the docs
 * dump has no entry for gets a `lift` of NULL rather than false, and earns the same ring on
 * different evidence -- "this route covers no ground" -- which the popup says out loud,
 * because a reader cannot tell a measured ring from an inferred one by looking.
 *
 * A SPLITTER or MERGER gets a square, in this layer and in no other: it is in no other payload,
 * and without it a belt-only view has a four-metre hole at every junction.
 */

import { code, popup, popupTitleRow } from "../../kit/dom";
import { L } from "../leaflet";
import { BAND, clearedLayer } from "../layers";
import { footprintCorners, latLngOf, pixelsPerMetre } from "../map";
import { tone } from "../map-tone";
import { declareColours } from "../palette";
import { registerFetch } from "../../app/registry";
import { routePolyline } from "./route-geometry";
import { liftRadius, ROUTE_WIDTH_M, routeWeight, sinkRoutes } from "./route-passes";

import type { AttachmentRow, BeltRow, BeltsResponse } from "../../api/shapes";
import type { Point3M } from "../geometry";

/* Mid steel with one value step either side for the tiers (docs/frontend_palette.md); the
 * middle stays the layer's swatch. */
var BELTS = declareColours("routes", {
  belts: "#93a5b4",
  "belt slow": "#7f8f9d",
  "belt fast": "#a7b9c7",
  // The hole inside a lift's ring, and the belts' casing: near-black.
  "lift fill": "#0e1116",
});
var BELT_COLOUR = BELTS.belts;
var LIFT_FILL = BELTS["lift fill"];
var BELT_SLOW = BELTS["belt slow"];
var BELT_FAST = BELTS["belt fast"];

/* Tier as value. `items_per_min` is the dump's own figure for the class -- 60, 120, 270, 480,
 * 780 -- so this is a banding of a measurement rather than a parse of "Mk3" out of a display
 * name. An unknown tier draws at the middle tone: the darkest would read as Mk1. */
function beltColour(items_per_min: number | null | undefined): string {
  if (!items_per_min) return BELT_COLOUR;
  if (items_per_min >= 480) return BELT_FAST;
  if (items_per_min >= 270) return BELT_COLOUR;
  return BELT_SLOW;
}

/* How much wider a belt's casing is than the belt, in SCREEN pixels. */
var BELT_CASING_PX = 2;

/* A splitter or merger with no measured footprint: the docs dump carries clearance for none of
 * these four classes, so the server sends null rather than a number invented there, which
 * would arrive indistinguishable from a measurement. Four metres is the square the pieces snap
 * to, which is also what makes a run read as continuous through one. */
var ATTACHMENT_FALLBACK_M = 4;

function beltWeight(ppm: number): number {
  return routeWeight(ROUTE_WIDTH_M.belts, ppm);
}

/* Does this route go anywhere seen from above? The only evidence left when the dump has no
 * entry for a class.
 *
 * NOT a point count: a lift arrives as TWO points, one at each end of its rise, so counting
 * them says "this is an ordinary run" about the one shape that cannot be drawn as one.
 *
 * A tenth of a metre because that is what the payload is rounded to. */
var FLAT_ROUTE_M = 0.1;

function coversGround(points: Point3M[]): boolean {
  const first = points[0];
  if (!first) return false;
  for (let i = 1; i < points.length; i++) {
    const point = points[i]!;
    if (Math.abs(point[0] - first[0]) >= FLAT_ROUTE_M) return true;
    if (Math.abs(point[1] - first[1]) >= FLAT_ROUTE_M) return true;
  }
  return false;
}

/* The popup's word on the ring, three answers because `lift` has three. `true` and `false` are
 * measurements, and `false` needs no line: a belt drawn as a line is what a reader assumes.
 * `null` is the server declining to guess for a class the dump does not know, so the page says
 * which way the geometry decided it. */
function liftNote(belt: BeltRow, drawAsRing: boolean): string | null {
  if (belt.lift === true) return "conveyor lift: vertical, so drawn as a ring";
  if (belt.lift === false || belt.lift === undefined) return null;
  return drawAsRing
    ? "lift or not is unknown for this class: drawn as a ring, since it covers no ground"
    : "lift or not is unknown for this class: drawn as a line, since it covers ground";
}

function beltPopup(belt: BeltRow, kind: string | null, first: Point3M, last: Point3M): string {
  return popup([
    popupTitleRow("belt", belt.name, belt.cls),
    ["kind", kind],
    ["rate", belt.items_per_min ? belt.items_per_min + " items/min at 100%" : null],
    // Travel order, input to output: the projection reverses the save's own output-first
    // storage, so these two rows mean what they say.
    ["from", first[0] + ", " + first[1] + " m"],
    ["to", last[0] + ", " + last[1] + " m"],
    ["rise", Math.round((last[2] - first[2]) * 10) / 10 + " m"],
    ["chain", "#" + belt.chain],
  ]);
}

/* On a light base mid steel sinks into bare ground, so runs are cased (docs/frontend_palette.md);
 * added after every core so sinkRoutes puts the casing underneath. */
function caseBeltRuns(runs: L.Polyline[], group: L.LayerGroup, ppm: number): void {
  runs.forEach(function (run) {
    const cased = L.polyline(run.getLatLngs() as L.LatLng[], {
      color: LIFT_FILL,
      weight: beltWeight(ppm) + BELT_CASING_PX,
      opacity: 0.85,
      interactive: false,
    });
    cased._widen = BELT_CASING_PX;
    cased._floor = run._floor;
    const route = run._route;
    if (route) cased._route = { points_m: route.points_m, curve_m: route.curve_m, steps: route.steps.slice() };
    cased.addTo(group);
  });
}

/* A splitter or merger's square. It rides in the belts layer but is joined the machines' way --
 * by instance id, in the band it stands on -- which is the split `/api/floors` makes between
 * runs and placements. */
function junctionSquare(attachment: AttachmentRow, x_m: number, y_m: number): L.Polygon {
  const halfWidth = (attachment.w_m || ATTACHMENT_FALLBACK_M) / 2;
  const halfLength = (attachment.l_m || ATTACHMENT_FALLBACK_M) / 2;
  const junction = L.polygon(
    footprintCorners(x_m, y_m, halfWidth, halfLength, attachment.yaw),
    {
      color: BELT_COLOUR,
      weight: 1,
      fillColor: BELT_COLOUR,
      fillOpacity: 0.85,
    }
  ).bindPopup(
    popup([
      popupTitleRow("belt part", attachment.name, attachment.cls),
      ["facing", attachment.yaw === null || attachment.yaw === undefined ? null : Math.round(attachment.yaw) + "°"],
      ["at", x_m + ", " + y_m + " m"],
      ["id", code(attachment.instance_leaf)],
    ])
  );
  junction._floor = { id: attachment.instance_leaf, z_m: attachment.z_m === null ? undefined : attachment.z_m };
  return junction;
}

export function drawBelts(data: BeltsResponse): void {
  // Off by default, like `machines`: a world's routes are a smear at the whole-world view. Just
  // over the concrete they run on, in the order a reader names the networks: belts, pipes, power.
  const group = clearedLayer("belts", { on: false, colour: BELT_COLOUR, rank: [BAND.built, 10, "belts"] });
  const ppm = pixelsPerMetre();
  const runs: L.Polyline[] = [];
  data.belts.forEach(function (belt) {
    const first = belt.points_m[0]!;
    const last = belt.points_m[belt.points_m.length - 1]!;
    let piece: L.Path;
    // An unknown class draws on its own geometry: no horizontal extent means a ring, because a
    // polyline through coincident points draws nothing at all.
    const drawAsRing = belt.lift === true || (belt.lift === null && !coversGround(belt.points_m));
    if (drawAsRing) {
      piece = L.circleMarker(latLngOf(first), {
        radius: liftRadius(ppm),
        color: beltColour(belt.items_per_min),
        weight: 1.5,
        fillColor: LIFT_FILL,
        fillOpacity: 0.9,
      });
    } else if (belt.points_m.length < 2) {
      return; // a route with one point is not a route, and this is not a lift
    } else {
      piece = routePolyline(belt.points_m, belt.curve_m, ppm, {
        color: beltColour(belt.items_per_min),
        weight: beltWeight(ppm),
        opacity: 0.85,
      });
    }
    // The join the floor filter uses: a belt is keyed by its CHAIN, which is the unit
    // `/api/floors` reasons about. The two ends ride along so a connector's glyph can be put on
    // the end that is actually on the floor being looked at.
    piece._floor = { run: { kind: "belt", key: belt.chain }, ends: [first, last] };
    piece.bindPopup(beltPopup(belt, liftNote(belt, drawAsRing), first, last)).addTo(group);
    if (!drawAsRing) runs.push(piece as L.Polyline);
  });
  if (tone() === "light") caseBeltRuns(runs, group, ppm);
  (data.attachments || []).forEach(function (attachment) {
    if (attachment.x_m === null || attachment.y_m === null) return;
    junctionSquare(attachment, attachment.x_m, attachment.y_m).addTo(group);
  });
  sinkRoutes();
}

/* Static, and adjacent to the pipes in the wave: a belt changes when the player builds one,
 * not when the game autosaves, and the two share the overlay canvas and the sink pass that
 * decides what a click lands on. */
registerFetch<BeltsResponse>({
  wave: "static",
  rank: 30,
  path: "/api/belts",
  label: "belts",
  clears: ["belts"],
  refilters: true,
  draw: drawBelts,
});
