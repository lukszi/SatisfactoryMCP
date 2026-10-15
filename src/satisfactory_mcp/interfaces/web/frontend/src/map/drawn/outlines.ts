/* Buildings drawn as their own top-down outline, with a notch at each belt and pipe port.
 *
 * The outline comes with the payload, once per class (`shapes`); a class without one falls back
 * to the measured rectangle. Notches are hidden below NOTCH_MIN_PPM, where they would be specks.
 * docs/building-shapes.md. */

import { L } from "../leaflet";
import { footprintCorners, latLngOf, map, pixelsPerMetre } from "../map";
import { declareColours } from "../palette";
import { state } from "../../app/state";
import { notchLocal, placeLocal, placeRings } from "./outline-geometry";

import type { BuildingShape } from "../../api/shapes";
import type { FloorMark } from "../leaflet-private";
import type { XY } from "./outline-geometry";

/* The routes' own tones, so a notch says which network plugs in there (docs/frontend_palette.md). */
const NOTCH = declareColours("routes", {
  "notch belt": "#a7b9c7",
  "notch pipe": "#91362e",
  "notch rim": "#0e1116",
});

/* Pixels per metre at which a 1.6 m notch is about five pixels across. */
export const NOTCH_MIN_PPM = 3;

/* The layers that carry notches; restyled together on zoom. */
const NOTCHED_LAYERS = ["machines", "extractors", "generators", "storage", "belts"];

function latLngs(points: XY[]): L.LatLngTuple[] {
  return points.map(function (p) {
    return latLngOf(p);
  });
}

/** The latlngs of a building's outline: its shape's rings, else a `w` x `l` rectangle (half
 *  extents). Several rings are a multipolygon, never one ring with holes. */
export function outlineLatLngs(
  x: number,
  y: number,
  yaw: number | null | undefined,
  shape: BuildingShape | undefined,
  w: number,
  l: number
): L.LatLngTuple[] | L.LatLngTuple[][][] {
  if (!shape || !shape.rings.length) return footprintCorners(x, y, w, l, yaw);
  const rings = placeRings(x, y, yaw, shape.rings).map(latLngs);
  if (rings.length === 1) return rings[0]!;
  return rings.map(function (ring) {
    return [ring];
  });
}

function notchStyle(shown: boolean): L.PathOptions {
  return { opacity: shown ? 1 : 0, fillOpacity: shown ? 1 : 0 };
}

/** One notch per port, in `group`, joined to the floor filter like its building but never
 *  ghosted (no height on the mark). */
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
    mark._floor = { id: floor.id, x_m: floor.x_m, y_m: floor.y_m, z_m: floor.z_m };
    mark.addTo(group);
  });
}

/** Show or hide every notch for the current zoom, skipping a piece the floor filter has ghosted. */
export function restyleNotchesForZoom(): void {
  const style = notchStyle(pixelsPerMetre() >= NOTCH_MIN_PPM);
  NOTCHED_LAYERS.forEach(function (name) {
    const group = state.layers[name];
    if (!group || !map.hasLayer(group)) return;
    group.eachLayer(function (layer) {
      const piece = layer as L.Path;
      if (piece._port && !piece._floorStyle) piece.setStyle(style);
    });
  });
}
