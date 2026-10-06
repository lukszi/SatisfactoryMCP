/* The power network: the wires, and the poles they are strung between.
 *
 * A wire is drawn as a STRAIGHT CHORD: it sags in game, but the save records no sag, and from
 * directly above a catenary and its chord are the same line. There are no direction marks,
 * because power has none. `/api/floors` never groups a wire, so floors/filter.ts places every
 * piece drawn here by its `_floor` mark. Colours and the cased-line technique:
 * docs/frontend_palette.md.
 */

import { popup, popupTitleRow } from "../../kit/dom";
import { L } from "../leaflet";
import { BAND, clearedLayer } from "../layers";
import { latLngOf, pixelsPerMetre } from "../map";
import { declareColours } from "../palette";
import { registerFetch } from "../../app/registry";
import { ROUTE_FLOOR_PX, ROUTE_WIDTH_M, routeWeight, sinkRoutes } from "./route-passes";
import { byMapTone } from "../map-tone";

import type { PoleRow, PowerResponse, WireRow } from "../../api/shapes";
import type { Point3M } from "../geometry";

// The casing under every wire and pole: deep indigo on a light base, near-black on a dark one.
var CASINGS = declareColours("power", { casing: "#1c1550", "casing dark": "#08060f" });

function casingColour(): string {
  return byMapTone(CASINGS.casing, CASINGS["casing dark"]);
}

// The wire's core: violet, the hue this layer has to itself.
var WIRE_COLOUR = declareColours("power", { wires: "#b8b0f8" }).wires;

// One value step above the wire: the line, and the thing the line ends at.
var POLE_COLOUR = declareColours("power", { poles: "#d8c8f8" }).poles;

/* A pole's disc in PIXELS, not metres: it answers "is there one here", like a node dot, and a
 * true-size pole is a fifth of a pixel at the world view. The steps follow how many wires each
 * kind carries (4, 7, 10), a pixel apart so they can be told apart, with wall sockets smallest.
 * An unknown class gets the Mk1 size: it is still a place where wires end. */
var POLE_RADIUS_PX: Record<string, number> = {
  Build_PowerPoleWall_C: 2.5,
  Build_PowerPoleWall_Mk2_C: 2.5,
  Build_PowerPoleWallDouble_Mk2_C: 2.5,
  Build_PowerPoleMk1_C: 3.2,
  Build_PowerPoleMk2_C: 4.2,
  Build_PowerPoleMk3_C: 5.2,
};
var POLE_FALLBACK_PX = 3.2;

/* A Power Tower is a RING, not a bigger disc, because a size already means "more connections":
 * a ring says "structure, seen from above", as a conveyor lift's does. The biggest mark on the
 * layer, because towers carry the long spans the whole-world view is read by. */
var TOWER_CLASS = "Build_PowerTowerPlatform_C";
var TOWER_RADIUS_PX = 7.5;
var TOWER_WEIGHT_PX = 2;

/* The casing's extra width in SCREEN pixels, so the rim is 1 px a side at every zoom; the zoom
 * pass re-adds it through `_widen`. The same rim widens a tower's ring. */
var WIRE_CASING_PX = 2;

/* A disc's rim, thinner than a wire's casing: an outline all the way round needs less to read
 * as edged, and 2 px on a 2.5 px wall socket would be a dark dot. */
var POLE_RIM_PX = 1;

/* One opacity for the whole layer, casing and core alike, matching the belts and pipes. */
var WIRE_OPACITY = 0.85;

function poleRadius(cls: string | null): number {
  if (cls === TOWER_CLASS) return TOWER_RADIUS_PX;
  return (cls && POLE_RADIUS_PX[cls]) || POLE_FALLBACK_PX;
}

function polePopup(p: PoleRow): string {
  return popup([
    popupTitleRow(p.cls === TOWER_CLASS ? "power tower" : "power pole", p.name, p.cls),
    // A count off the wiring graph: 0 is an answer, a pole built and never strung.
    [
      "connections",
      p.connections === 0 ? "none: nothing is wired to this" : String(p.connections),
    ],
    ["facing", p.yaw === null ? null : Math.round(p.yaw) + "°"],
    ["at", p.x_m + ", " + p.y_m + " m"],
    // The base's elevation, not the connector's: the save carries where the actor stands.
    ["elevation", p.z_m + " m"],
  ]);
}

function wirePopup(w: WireRow): string {
  var unnamed = "not a building this projection names";
  return popup([
    /* A DASH and not an arrow, because a wire has no from and no to. The ORDER still lines up
     * with the two coordinates in `ends` below: the server matches each endpoint to its actor,
     * because the save's own order often disagrees with the wiring. */
    ["power line", (w.from || unnamed) + " → " + (w.to || unnamed)],
    ["span", w.span_m + " m, straight line; a wire sags and the save records no sag"],
    ["ends", w.a_m[0] + ", " + w.a_m[1] + " m and " + w.b_m[0] + ", " + w.b_m[1] + " m"],
    // Unsigned: a climb is only a climb once there is an end to measure it from.
    ["height difference", Math.abs(Math.round((w.b_m[2] - w.a_m[2]) * 10) / 10) + " m"],
  ]);
}

export function drawPower(data: PowerResponse): void {
  /* ON at the whole-world zoom, the one network layer that is: wires are mostly long lines
   * between distant bases and read as a world's spine, where belts and pipes smear. The poles
   * are drawn too; they are the layer at factory zoom. Last of the three networks. */
  var group = clearedLayer("power", { on: true, colour: WIRE_COLOUR, rank: [BAND.built, 30, "power"] });
  // The same expression the zoom pass restyles these with, off the same two tables.
  var weight = routeWeight(ROUTE_WIDTH_M.power, pixelsPerMetre(), ROUTE_FLOOR_PX.power);

  /* THE CORES FIRST AND THE CASINGS AFTER THEM, WHICH IS WHAT PUTS THE CASINGS UNDERNEATH:
   * `sinkRoutes` at the end calls `bringToBack` down the list, so the piece sunk last ends up at
   * the bottom. All cores then all casings, not a pair at a time, or each casing would cover the
   * NEXT wire's core where they cross. */
  function chord(w: WireRow): L.LatLngTuple[] {
    return [latLngOf(w.a_m), latLngOf(w.b_m)];
  }

  /* Both endpoints, in the payload's own [x, y, z] order: the one piece in this layer that can
   * be on two storeys at once. */
  function ends(w: WireRow): [Point3M, Point3M] {
    return [w.a_m, w.b_m];
  }

  /* Where each end counts as STANDING for the floor filter: the base of the pole it ends at
   * (`a_pole`/`b_pole` index this payload's poles), the very point that pole is filtered by, so
   * a wire and its pole land on one storey. An endpoint is a connector up to 24 m above that
   * base. An end with no pole keeps its endpoint. */
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
    // Its core's ends and anchors, so the filter reaches the same verdict about both; `casing`
    // keeps it from earning a second arrow.
    cased._floor = { power: "casing", ends: ends(w), anchors: anchors(w) };
    cased.addTo(group);
  });

  /* A tower's casing is a second RING, added after every disc and core ring for the same
   * reversal reason as the wires. */
  var towerCasings: L.CircleMarker[] = [];

  data.poles.forEach(function (p) {
    var tower = p.cls === TOWER_CLASS;
    var radius = poleRadius(p.cls);
    var piece = L.circleMarker(latLngOf(p), {
      radius: radius,
      // A disc is cased by its own outline; a ring's stroke IS the mark, hence the second ring.
      color: tower ? POLE_COLOUR : casingColour(),
      weight: tower ? TOWER_WEIGHT_PX : POLE_RIM_PX,
      fillColor: POLE_COLOUR,
      fillOpacity: tower ? 0 : 0.9,
    });
    piece._fixed = true;
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

/* Static for the reason the belts are: a wire changes when the player builds, not when the game
 * autosaves. `refilters: true` although `/api/floors` says nothing about a wire, because the
 * floor filter places this layer itself; unfiltered, a redraw in floor mode would draw every
 * storey's wires over one deck. */
registerFetch<PowerResponse>({
  wave: "static",
  rank: 50,
  path: "/api/power",
  label: "power",
  clears: ["power"],
  refilters: true,
  draw: drawPower,
});
