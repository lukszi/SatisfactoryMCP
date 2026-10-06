/* The fluid network, drawn the same way as the belts and told apart by colour.
 *
 * The same posture as the belts -- one colour family, tier as a VALUE step inside it, width
 * physical -- because a page that encodes two networks by two different grammars makes the
 * reader learn twice.
 *
 * NO LIFT GLYPH: no pipe piece is vertical, so every pipe is drawn as a line. The one-point
 * guard below stays anyway, because "none so far" is not "none ever".
 *
 * ARROWS WHERE THE DIRECTION IS INFERRED, and nothing at all where it is not. The save does not
 * record a pipe's flow direction, but it does record the plumbing graph and TYPE a machine's
 * ports, so `/api/pipes` infers a direction where the network admits only one and says
 * `unknown` where it admits two. The popup names which of the two a reader is looking at.
 */

import { popup, popupTitleRow } from "../../kit/dom";
import { L } from "../leaflet";
import { BAND, clearedLayer } from "../layers";
import { latLngOf, pixelsPerMetre } from "../map";
import { declareColours } from "../palette";
import { registerFetch } from "../../app/registry";
import { chevronOpacity, routeChevrons, routePolyline } from "./route-geometry";
import { ROUTE_WIDTH_M, routeWeight, sinkRoutes } from "./route-passes";

import type { PipeFlowBasis, PipeRow, PipesResponse } from "../../api/shapes";
import type { Point3M } from "../geometry";

// Oxide: warm where the belts are cool (docs/frontend_palette.md). The middle tone is the swatch.
var PIPE_COLOUR = declareColours("routes", { pipes: "#7d221a" }).pipes;

// The two tiers, one value step either side of PIPE_COLOUR.
var PIPE_TIER = declareColours("routes", { "pipe mk1": "#690e06", "pipe mk2": "#91362e" });
var PIPE_MK1 = PIPE_TIER["pipe mk1"];
var PIPE_MK2 = PIPE_TIER["pipe mk2"];

/* Tier as value, the belts' banding: `flow_m3_min` is the dump's own figure for the class --
 * 300 on Mk1, 600 on Mk2 -- so this bands a measurement rather than parsing "MK2" out of a
 * display name. A Pipeline Mk.2 is the same 1.3 m bore with a better pump rating, so drawing
 * it wider would contradict the map's own scale bar. Two tiers, so two tones and the middle for
 * the unknown: the darker would read as Mk1. */
function pipeColour(flow_m3_min: number | null | undefined): string {
  if (!flow_m3_min) return PIPE_COLOUR;
  return flow_m3_min >= 600 ? PIPE_MK2 : PIPE_MK1;
}

function pipeWeight(ppm: number): number {
  return routeWeight(ROUTE_WIDTH_M.pipes, ppm);
}

/* What the popup says about a direction, keyed by what the server based it on. The `from` and
 * `to` rows carry the direction itself, so this row only has to carry the WARRANT.
 *
 * EXHAUSTIVE, which is what `Record<PipeFlowBasis, …>` buys over `Record<string, …>`: a fifth
 * basis in `domain/world/flow.py` is a missing key here and a compile error, rather than a
 * basis nobody thought about falling through a default and being printed as a claim about the
 * network. `unresolved` is a real key for the pipes the network does not settle -- one in a
 * loop, or a trunk with producers and consumers on both sides. */
var PIPE_FLOW_UNKNOWN = "not recorded, and the network does not imply it";

var PIPE_FLOW_BASIS: Record<PipeFlowBasis, string> = {
  "machine port": "→ a typed machine port at one end",
  pump: "→ pump orientation",
  propagated: "→ inferred from the network",
  unresolved: PIPE_FLOW_UNKNOWN,
};

function pipePopup(pipe: PipeRow, first: Point3M, last: Point3M): string {
  const known = pipe.direction === "forward" || pipe.direction === "reverse";
  const head = pipe.direction === "reverse" ? last : first;
  const tail = pipe.direction === "reverse" ? first : last;
  return popup([
    popupTitleRow("pipe", pipe.name, pipe.cls),
    // Off the game's own FGPipeNetwork rather than from what the pipe is plugged into, which is
    // why it can be stated flatly.
    ["fluid", pipe.fluid_name],
    ["capacity", pipe.flow_m3_min ? pipe.flow_m3_min + " m³/min at 100%" : null],
    ["flow", known ? PIPE_FLOW_BASIS[pipe.basis] : PIPE_FLOW_UNKNOWN],
    // `from`/`to` where the direction is known, which is the belt popup's own wording and means
    // the same thing there; `ends` where it is not, so the two are never confused.
    ["from", known ? head[0] + ", " + head[1] + " m" : null],
    ["to", known ? tail[0] + ", " + tail[1] + " m" : null],
    ["ends", known ? null : first[0] + ", " + first[1] + " m and " + last[0] + ", " + last[1] + " m"],
    // Signed only where there is a direction to sign it against.
    [
      "rise",
      known
        ? Math.round((tail[2] - head[2]) * 10) / 10 + " m"
        : Math.abs(Math.round((last[2] - first[2]) * 10) / 10) + " m",
    ],
    ["network", pipe.network === null ? null : "#" + pipe.network],
  ]);
}

/* A chevron's stroke: a fixed pixel width, in a pale cream far above every pipe tone, so the
 * mark reads on the line it sits on. Not interactive, or it would steal its pipe's popup. */
var CHEVRON_WEIGHT_PX = 1.5;
var CHEVRON_COLOUR = declareColours("routes", { chevrons: "#e8cbb4" }).chevrons;

/** One pipe's direction marks, joined to the floor filter by the pipe's key and NO ends, so a
 *  mark is kept exactly when its pipe is and never earns a connector arrow. */
function drawChevrons(pipe: PipeRow, group: L.LayerGroup, opacity: number): void {
  routeChevrons(pipe.points_m, pipe.direction === "reverse").forEach(function (mark) {
    const piece = L.polyline(
      mark.map(function (apex): L.LatLngTuple {
        return latLngOf(apex);
      }),
      {
        color: CHEVRON_COLOUR,
        weight: CHEVRON_WEIGHT_PX,
        opacity: opacity,
        interactive: false,
      }
    );
    piece._chevron = true;
    piece._floor = { run: { kind: "pipe", key: pipe.row } };
    piece.addTo(group);
  });
}

export function drawPipes(data: PipesResponse): void {
  // Off by default, like `belts` and `machines`. Directly under the belts, which is the pair
  // they are.
  const group = clearedLayer("pipes", { on: false, colour: PIPE_COLOUR, rank: [BAND.built, 20, "pipes"] });
  const ppm = pixelsPerMetre();
  const alpha = chevronOpacity(ppm);
  data.pipes.forEach(function (pipe) {
    if (pipe.points_m.length < 2) return; // a route with one point is not a route
    const first = pipe.points_m[0]!;
    const last = pipe.points_m[pipe.points_m.length - 1]!;
    const run = routePolyline(pipe.points_m, pipe.curve_m, ppm, {
      color: pipeColour(pipe.flow_m3_min),
      weight: pipeWeight(ppm),
      opacity: 0.85,
    }).bindPopup(pipePopup(pipe, first, last));
    // `row`, not this list's index: it is the pipe's position in the RAW table, which is
    // what `/api/floors` keys a pipe run by and what stays right when a row is torn.
    run._floor = { run: { kind: "pipe", key: pipe.row }, ends: [first, last] };
    run.addTo(group);
    if (pipe.direction === "forward" || pipe.direction === "reverse") drawChevrons(pipe, group, alpha);
  });
  sinkRoutes();
}

/** The belts' twin; see the note on that registration for why both are static. */
registerFetch<PipesResponse>({
  wave: "static",
  rank: 40,
  path: "/api/pipes",
  label: "pipes",
  clears: ["pipes"],
  refilters: true,
  draw: drawPipes,
});
