/* The power network: the wires, and the poles they are strung between.
 *
 * A wire is drawn as a STRAIGHT CHORD and in game it sags -- nothing in the save records the
 * sag, and seen from directly above a catenary and its chord are the same line. There are NO
 * DIRECTION MARKS because power has no direction to draw: a circuit is one connected pool of
 * supply and demand, and a wire carries whatever the pool needs whichever way it needs it.
 *
 * `/api/floors` groups belt chains and pipe rows and has never carried a wire, so floors.ts
 * places this layer geometrically and every piece drawn here has to carry a `_floor` mark.
 * See `anchors` in drawPower for the rule it places them by.
 */

import { popup } from "../../kit/dom";
import { L } from "../leaflet";
import { BAND, clearedLayer } from "../layers";
import { latLngOf, pixelsPerMetre } from "../map";
import { declareColours } from "../palette";
import { registerFetch } from "../../app/registry";
import { ROUTE_FLOOR_PX, ROUTE_WIDTH_M, routeWeight, sinkRoutes } from "./routes";
import { toned } from "../map-tone";

import type { Row } from "../../kit/dom";

import type { PoleRow, PowerResponse, WireRow } from "../../api/shapes";
import type { Point3M } from "../geometry";

/* CASED LINES: a dark, wider casing under a lighter core.
 *
 * dE IS ABOUT A SWATCH AND A WIRE IS NOT ONE, which is the trap a colour picked for this layer
 * falls into. A 1.5 px stroke at 0.85 opacity puts almost no fully-covered pixel on the screen
 * -- the canvas antialiases it across two or three -- so what a reader sees is the colour
 * composited over the ground at something like 0.6, where a dE 26.7 swatch is worth about dE
 * 15 of real contrast. A single colour also cannot be far from both grounds this is drawn
 * over: the game's artwork is a pale warm tan with cyan water (binned to a 16-level cube, its
 * six commonest tones are #486878, #888878, #a8a898, #988878, #4898a8 and #989888) while
 * `plain` mode is near-black. A dark casing and a light core can be, because whichever way the
 * ground goes one half of the pair separates from it and the other half is the reason the line
 * still has a colour.
 *
 * All three below are measured in CIE Lab against the CURRENT full table, cross-owner nearest
 * neighbours; palette.ts checks them at every dev boot and would say so if any of them moved.
 */

/* The casing: a deep indigo, and the furthest any colour on this page sits from all the
 * others. Nearest cross-owner neighbour is the fluid-storage blue-violet at dE 27.8, then the
 * oil node at 34.6, the concrete at 35.4 and the lift fill at 39.7. Violet
 * rather than black because a near-black casing lands in the middle of the map's tightest grey
 * cluster.
 *
 * Against the artwork it is dE 48.4 from the nearest ground tone on the desert and the coast
 * and 54.3 on the forest, and 61.6 to 71.2 on the mean weighted by how much of the map each
 * tone covers -- which is where its whole job is done: the ground under a wire is pale, and
 * this is not. */
var CASINGS = declareColours("power", { casing: "#1c1550", "casing dark": "#08060f" });

/* On a dark base the indigo is only dE 18 from the ground; near-black keeps the rim. */
function casingColour(): string {
  return toned(CASINGS.casing, CASINGS["casing dark"]);
}

/* The core. Violet is this layer's free hue -- blue is the machines, ultramarine the
 * extractors, red the stopped outline, mint the generators, blue-violet the storage, steel the
 * belts, rust the pipes.
 *
 * Nearest cross-owner neighbour is the raw-quartz dot at dE 22.8, then the storage blue-violet
 * at 23.9, the water dot at 28.2 and the machine blue at 28.3. The belts are the comparison
 * that has to hold, because a wire and a belt genuinely do run side by side inside a factory,
 * and they are dE 33.2, 33.5 and 36.5 away across their three tones. Against the artwork it is dE 39.1 to 40.2 from the
 * nearest ground tone. */
var WIRE_COLOUR = declareColours("power", { wires: "#b8b0f8" }).wires;

/* The poles, one value step up the same hue -- the grammar the belts, the pipes and the
 * storage boxes all use for a distinction inside one family. Here the distinction is a KIND
 * rather than a tier: the line and the thing the line ends at.
 *
 * The step is the house step, dE 16.0, against the belts' 15.6 between their slowest and
 * fastest, the pipes' 15.1 between Mk1 and Mk2 and the storage pair's 15.8. Lighter rather
 * than darker buys the world view, where a pole is the mark that says a base is here.
 *
 * Nearest cross-owner neighbour is the fast belt at dE 23.6, then the raw-quartz dot at 25.2
 * and the limestone dot at 26.1; the nearest ground tone is dE 28.4 on the forest and 31.7 on
 * the desert and the coast. */
var POLE_COLOUR = declareColours("power", { poles: "#d8c8f8" }).poles;

/* How big a pole's disc is, in PIXELS, by what the pole is.
 *
 * Pixels and not metres, which is the opposite of every other placement on this map. A machine
 * is drawn at its measured footprint because the question there is "does this fit"; a pole is
 * drawn at a fixed size because the question is "is there one here", which is the node dots'
 * grammar (PURITY_RADIUS in markers.ts) and the reason `_fixed` exists in routes.ts. A
 * true-size pole would be a fifth of a pixel at the world view.
 *
 * The steps are a MEASUREMENT: a pole's mark is how many wires it can carry, and the game's
 * three limits are 4 for a Mk1, 7 for a Mk2 and 10 for a Mk3. So the ramp is 3.2 / 4.2 / 5.2
 * px, with the wall outlets under Mk1 at 2.5 because a socket on a wall is the smallest thing
 * here. A 1.0 px step, because three sizes 0.7 px apart cannot be told apart at the zoom the
 * difference is meant to be read at.
 *
 * A class this table has never heard of gets the Mk1 size rather than nothing: an unrecognised
 * pole is still a place where wires end. */
var POLE_RADIUS_PX: Record<string, number> = {
  Build_PowerPoleWall_C: 2.5,
  Build_PowerPoleWall_Mk2_C: 2.5,
  Build_PowerPoleWallDouble_Mk2_C: 2.5,
  Build_PowerPoleMk1_C: 3.2,
  Build_PowerPoleMk2_C: 4.2,
  Build_PowerPoleMk3_C: 5.2,
};
var POLE_FALLBACK_PX = 3.2;

/* A Power Tower is drawn as a RING and every other pole as a filled disc, because the shape
 * has to do work a size cannot. A tower is a 12 m platform on a pylon, tall enough that its
 * connectors sit 24 m above its own base, and it carries the only spans on a world longer than
 * 300 m; a bigger dot would say "a pole with more connections", which is what the ramp above
 * already means. A ring says "structure, seen from above" -- the same thing the conveyor lift's
 * ring says in the belts layer.
 *
 * The biggest mark on the layer, because a tower's job is the whole-world view, where "the long
 * spans start here" is the only thing about this layer worth reading. */
var TOWER_CLASS = "Build_PowerTowerPlatform_C";
var TOWER_RADIUS_PX = 7.5;
var TOWER_WEIGHT_PX = 2;

/* How much wider the casing is than the core it sits under, in SCREEN pixels, so the rim is
 * the same thickness at every zoom -- which is what a casing is. Two, so 1 px shows either
 * side: enough to be a rim on a 2.5 px line and not so much that the pair reads as a road.
 *
 * A pixel constant and not a width in metres, unlike ROUTE_WIDTH_M next door: a casing is not
 * part of what a wire IS, it is a drawing technique for making a thin thing survive a busy
 * background, so it is stated in the units the background is measured in. See `_widen` in
 * styleRoutes, which re-adds it on every zoom. The same number widens the ring under a power
 * tower, because it is the same rim. */
var WIRE_CASING_PX = 2;

/* And how thick the rim around a pole's DISC is. Smaller than the wires' casing: a disc has an
 * outline all the way round rather than two edges, so it needs less of one to read as edged,
 * and 2 px of rim on a 2.5 px wall socket would be a dark dot with a highlight. */
var POLE_RIM_PX = 1;

/* One opacity for the whole layer, casing and core alike, matching the belts and pipes. */
var WIRE_OPACITY = 0.85;

function poleRadius(cls: string | null): number {
  if (cls === TOWER_CLASS) return TOWER_RADIUS_PX;
  return (cls && POLE_RADIUS_PX[cls]) || POLE_FALLBACK_PX;
}

/* The row that names the thing a reader just clicked. Both `name` and `cls` are nullable
 * server-side, and a popup whose title row was dropped would lose the word "pole" while
 * keeping every coordinate under it. */
function titleRow(key: string, name: string | null, cls: string | null): Row {
  return [key, name || cls || "class not recorded in this projection"];
}

function polePopup(p: PoleRow): string {
  return popup([
    titleRow(p.cls === TOWER_CLASS ? "power tower" : "power pole", p.name, p.cls),
    // A count off the wiring graph, and 0 is one of its answers rather than a missing one:
    // two of this world's tower platforms were built and never strung to anything.
    [
      "connections",
      p.connections === 0 ? "none: nothing is wired to this" : String(p.connections),
    ],
    ["facing", p.yaw === null ? null : Math.round(p.yaw) + "°"],
    ["at", p.x_m + ", " + p.y_m + " m"],
    // The base's elevation, not the pole's height: the projection carries where the actor
    // stands, and the seven metres up to a Mk1's connector are the model's, not the save's.
    ["elevation", p.z_m + " m"],
  ]);
}

function wirePopup(w: WireRow): string {
  var unnamed = "not a building this projection names";
  return popup([
    /* A DASH and not an arrow, because a wire has no from and no to. The ORDER is still
     * meaningful: the server measures which published endpoint belongs to which actor, since
     * the save's own order agrees with the wiring only about half the time, so this pair lines
     * up with the two coordinates in `ends` below. */
    ["power line", (w.from || unnamed) + " → " + (w.to || unnamed)],
    ["span", w.span_m + " m, straight line; a wire sags and the save records no sag"],
    ["ends", w.a_m[0] + ", " + w.a_m[1] + " m and " + w.b_m[0] + ", " + w.b_m[1] + " m"],
    // Unsigned: a climb is only a climb once there is an end to measure it from.
    ["height difference", Math.abs(Math.round((w.b_m[2] - w.a_m[2]) * 10) / 10) + " m"],
  ]);
}

export function drawPower(data: PowerResponse): void {
  /* ON at the whole-world zoom, and the one network layer that is. `machines`, `belts` and
   * `pipes` are a smear at that scale; the wires are not, because their MEDIAN span is 21 m
   * against a belt piece's few metres and 1% of them run over 291 m, so the layer is mostly
   * long lines between distant places and reads as the spine joining a world's bases.
   *
   * The poles do NOT survive that zoom and are drawn anyway: 701 fixed discs cluster into the
   * shape the wires already make, and they are the layer at factory zoom.
   *
   * Last of the three networks in the legend, under the belts and the pipes. */
  var group = clearedLayer("power", { on: true, colour: WIRE_COLOUR, rank: [BAND.built, 30, "power"] });
  // The same expression the zoom pass restyles these with, off the same two tables -- see
  // routeWeight in routes.ts, which is exported for exactly this line.
  var weight = routeWeight(ROUTE_WIDTH_M.power!, pixelsPerMetre(), ROUTE_FLOOR_PX.power);

  /* THE CORES FIRST AND THE CASINGS AFTER THEM, WHICH IS WHAT PUTS THE CASINGS UNDERNEATH.
   * `sinkRoutes` runs at the end of this function and REVERSES the runs -- it calls
   * `bringToBack` down the list, so the piece sunk last ends up at the very bottom.
   *
   * All the cores and then all the casings, rather than a pair at a time: interleaved, that
   * same reversal would leave each wire's casing above the NEXT wire's core, and a crossing
   * would show one line breaking the other.
   */
  function chord(w: WireRow): L.LatLngTuple[] {
    return [latLngOf(w.a_m), latLngOf(w.b_m)];
  }

  /* Both endpoints, in the payload's own [x, y, z] order, which is the shape the floor
   * filter reads a two-ended piece by. A wire is the only thing in this layer that can be on
   * two storeys at once. */
  function ends(w: WireRow): [Point3M, Point3M] {
    return [w.a_m, w.b_m];
  }

  /* Where each end counts as STANDING, for the floor filter -- distinct from where it is
   * drawn. An endpoint is a CONNECTOR position, 7 m over a Mk1 pole's base and 24 m over a
   * tower's, so a filter reading heights off `ends` files a cable one storey up wherever the
   * storeys are shorter than that offset. An end whose pole the payload names (`a_pole` /
   * `b_pole`, an index into this same payload's poles) is therefore anchored at the pole's own
   * base -- the exact point the pole mark itself is filtered by, which is what puts a wire and
   * the pole it plugs into on the same storey by construction. An end with no pole keeps its
   * endpoint. */
  function anchor(at: Point3M, pole: number | null, poles: PoleRow[]): Point3M {
    var p = pole === null ? undefined : poles[pole];
    return p ? [p.x_m, p.y_m, p.z_m] : at;
  }

  function anchors(w: WireRow): [Point3M, Point3M] {
    return [anchor(w.a_m, w.a_pole, data.poles), anchor(w.b_m, w.b_pole, data.poles)];
  }

  data.wires.forEach(function (w) {
    var core = L.polyline(chord(w), {
      color: WIRE_COLOUR,
      weight: weight,
      opacity: WIRE_OPACITY,
    });
    core._floor = { power: "wire", ends: ends(w), anchors: anchors(w) };
    core.bindPopup(wirePopup(w)).addTo(group);
  });

  data.wires.forEach(function (w) {
    var cased = L.polyline(chord(w), {
      color: casingColour(),
      weight: weight + WIRE_CASING_PX,
      opacity: WIRE_OPACITY,
      interactive: false,
    });
    cased._widen = WIRE_CASING_PX;
    // The same ends and anchors as the core it sits under, so the filter reaches the same
    // verdict about both. `casing` rather than `wire` keeps it from earning a second connector
    // glyph on top of its core's.
    cased._floor = { power: "casing", ends: ends(w), anchors: anchors(w) };
    cased.addTo(group);
  });

  /* A tower's casing is a second RING, for the reason a wire's is a second line, and it is
   * collected here rather than added in place: the same reversal applies inside the glyphs,
   * so the casings have to go in after every disc and every core ring. */
  var towerCasings: L.CircleMarker[] = [];

  data.poles.forEach(function (p) {
    var tower = p.cls === TOWER_CLASS;
    var radius = poleRadius(p.cls);
    var piece = L.circleMarker(latLngOf(p), {
      radius: radius,
      // A DISC IS CASED BY ITS OWN OUTLINE, in the casing colour, which turns a flat dot into
      // an edged mark for no extra path. A ring cannot do that -- its stroke IS the mark -- so
      // a tower gets the second ring below instead.
      color: tower ? POLE_COLOUR : casingColour(),
      weight: tower ? TOWER_WEIGHT_PX : POLE_RIM_PX,
      fillColor: POLE_COLOUR,
      fillOpacity: tower ? 0 : 0.9,
    });
    piece._fixed = true;
    // Where it stands, which is how the floor filter places a thing no band lists. See `power`
    // in FloorMark.
    piece._floor = { power: "pole", x_m: p.x_m, y_m: p.y_m, z_m: p.z_m };
    piece.bindPopup(polePopup(p)).addTo(group);
    if (!tower) return;
    var cased = L.circleMarker(latLngOf(p), {
      radius: radius,
      color: casingColour(),
      weight: TOWER_WEIGHT_PX + WIRE_CASING_PX,
      fillOpacity: 0,
      interactive: false,
    });
    cased._fixed = true;
    cased._floor = { power: "casing", x_m: p.x_m, y_m: p.y_m, z_m: p.z_m };
    towerCasings.push(cased);
  });

  towerCasings.forEach(function (piece) {
    piece.addTo(group);
  });

  sinkRoutes();
}

/* Static for the reason the belts are: a wire changes when the player builds, not when the
 * game autosaves. The header's power figures come from a different endpoint on the other wave,
 * which is why a save write updates the number without redrawing the network.
 *
 * `refilters: true` even though `/api/floors` says nothing about a wire, because floors.ts
 * places this layer geometrically instead: unfiltered, a redraw during floor mode draws every
 * wire of every storey over one deck's plan. See classifyPowerPiece in floors/filter.ts. */
registerFetch<PowerResponse>({
  wave: "static",
  rank: 50,
  path: "/api/power",
  label: "power",
  clears: ["power"],
  refilters: true,
  draw: drawPower,
});
