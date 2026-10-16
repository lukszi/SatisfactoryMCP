/* Buildings drawn as their own top-down outline, with lighter height bands inside it, a notch at
 * each belt and pipe port, and a dashed x-ray line above everything else.
 *
 * The outline comes with the payload, once per class (`shapes`); a class without one falls back
 * to the measured rectangle. Bands and notches are hidden below their pixel thresholds, where
 * they would be specks. The x-ray line is what keeps a building under an upper deck's one
 * findable. docs/building-shapes.md. */

import { L } from "../leaflet";
import { footprintCorners, latLngOf, map, pixelsPerMetre } from "../map";
import { declareColours } from "../palette";
import { state } from "../../app/state";
import { bandVeil, notchLocal, placeLocal, placeRings } from "./outline-geometry";

import type { BuildingShape } from "../../api/shapes";
import type { FloorMark } from "../leaflet-private";
import type { XY } from "./outline-geometry";

/* The routes' own tones, so a notch says which network plugs in there (docs/frontend_palette.md). */
const NOTCH = declareColours("routes", {
  "notch belt": "#a7b9c7",
  "notch pipe": "#91362e",
  "notch rim": "#0e1116",
});

/* A neutral veil, never drawn on its own: laid over a building's fill in even steps (bandVeil), so
 * a higher band reads lighter in whatever hue the layer gives it (docs/frontend_palette.md). */
const BAND_LIGHT = declareColours("outlines", { "band veil": "#ffffff" })["band veil"];
const BAND_EDGE_OPACITY = 0.45;

/* Pixels per metre at which a 1.6 m notch is about five pixels across. */
export const NOTCH_MIN_PPM = 3;
/* Pixels per metre at which a 2 m band is about three pixels across. */
export const BAND_MIN_PPM = 1.5;

/* The pane x-ray lines are drawn in: over every building, belt and pipe, under the markers. */
export const XRAY_PANE = "xray";
map.createPane(XRAY_PANE);
map.getPane(XRAY_PANE)!.style.zIndex = "450";
map.getPane(XRAY_PANE)!.style.pointerEvents = "none";

/* The layers that carry notches or bands; restyled together on zoom. */
const DETAILED_LAYERS = ["machines", "extractors", "generators", "facilities", "storage", "belts", "pipes"];

function latLngs(points: XY[]): L.LatLngTuple[] {
  return points.map(function (p) {
    return latLngOf(p);
  });
}

/** Rings placed in the world as one polygon's latlngs: a single ring flat, several as a
 *  multipolygon, never one ring with holes. */
function ringsLatLngs(
  x: number,
  y: number,
  yaw: number | null | undefined,
  rings: number[][][]
): L.LatLngTuple[] | L.LatLngTuple[][][] {
  const placed = placeRings(x, y, yaw, rings).map(latLngs);
  if (placed.length === 1) return placed[0]!;
  return placed.map(function (ring) {
    return [ring];
  });
}

/** The latlngs of a building's outline: its shape's rings, else a `w` x `l` rectangle (half
 *  extents). */
export function outlineLatLngs(
  x: number,
  y: number,
  yaw: number | null | undefined,
  shape: BuildingShape | undefined,
  w: number,
  l: number
): L.LatLngTuple[] | L.LatLngTuple[][][] {
  if (!shape || !shape.rings.length) return footprintCorners(x, y, w, l, yaw);
  return ringsLatLngs(x, y, yaw, shape.rings);
}

/* A detail piece carries its building's floor mark without a height, so the floor filter keeps
 * or drops it with the building and never ghosts it. */
function detailMark(floor: FloorMark): FloorMark {
  return { id: floor.id, x_m: floor.x_m, y_m: floor.y_m, z_m: floor.z_m };
}

function notchStyle(shown: boolean): L.PathOptions {
  return { opacity: shown ? 1 : 0, fillOpacity: shown ? 1 : 0 };
}

function bandStyle(shown: boolean, veil: number): L.PathOptions {
  return { opacity: shown ? BAND_EDGE_OPACITY : 0, fillOpacity: shown ? veil : 0 };
}

/** One notch per port, in `group`, joined to the floor filter like its building. */
export function addNotches(
  group: L.LayerGroup,
  x: number,
  y: number,
  yaw: number | null | undefined,
  shape: BuildingShape | undefined,
  floor: FloorMark
): void {
  if (!shape || !shape.ports.length) return;
  const shown = pixelsPerMetre() >= NOTCH_MIN_PPM;
  shape.ports.forEach(function (port) {
    const mark = L.polygon(latLngs(placeLocal(x, y, yaw, notchLocal(port))), {
      color: NOTCH["notch rim"],
      weight: 1,
      fillColor: port.kind === "pipe" ? NOTCH["notch pipe"] : NOTCH["notch belt"],
      interactive: false,
      ...notchStyle(shown),
    });
    mark._port = true;
    mark._floor = detailMark(floor);
    mark.addTo(group);
  });
}

/** The height bands of a building, lowest first, each a lighter veil with a dark edge, in
 *  `group` after the building itself. `faded` is an idle building's thin fill. */
export function addBands(
  group: L.LayerGroup,
  x: number,
  y: number,
  yaw: number | null | undefined,
  shape: BuildingShape | undefined,
  floor: FloorMark,
  faded: boolean
): void {
  if (!shape || !shape.bands.length) return;
  const shown = pixelsPerMetre() >= BAND_MIN_PPM;
  let under = 0;
  shape.bands.forEach(function (band) {
    const veil = bandVeil(band.tier, under, faded);
    under = band.tier;
    const piece = L.polygon(ringsLatLngs(x, y, yaw, band.rings), {
      color: NOTCH["notch rim"],
      weight: 1,
      fillColor: BAND_LIGHT,
      interactive: false,
      ...bandStyle(shown, veil),
    });
    piece._band = veil;
    piece._floor = detailMark(floor);
    piece.addTo(group);
  });
}

/** A dashed copy of a building's outline in the x-ray pane, in its outline colour: invisible on
 *  the building itself, and the one line left of it where an upper deck's building covers it. */
export function addXray(group: L.LayerGroup, outline: L.Polygon, floor: FloorMark): void {
  const xray = L.polygon(outline.getLatLngs() as L.LatLng[], {
    pane: XRAY_PANE,
    color: outline.options.color,
    weight: 1,
    opacity: 0.9,
    dashArray: "4,3",
    fill: false,
    interactive: false,
  });
  xray._floor = detailMark(floor);
  xray.addTo(group);
}

/** Show or hide every notch and band for the current zoom, skipping a piece the floor filter has
 *  ghosted. */
export function restyleNotchesForZoom(): void {
  const ppm = pixelsPerMetre();
  const notches = notchStyle(ppm >= NOTCH_MIN_PPM);
  const bandsShown = ppm >= BAND_MIN_PPM;
  DETAILED_LAYERS.forEach(function (name) {
    const group = state.layers[name];
    if (!group || !map.hasLayer(group)) return;
    group.eachLayer(function (layer) {
      const piece = layer as L.Path;
      if (piece._floorStyle) return;
      if (piece._port) piece.setStyle(notches);
      else if (piece._band !== undefined) piece.setStyle(bandStyle(bandsShown, piece._band));
    });
  });
}
