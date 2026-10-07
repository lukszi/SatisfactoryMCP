/* The passes every route layer -- belts, pipes and power -- goes through by NAME: the width
 * table, the pixel restyle on zoom, and the sink that keeps a run from stealing a machine's
 * click. The drawing modules import this one; it imports none of them. See
 * frontend/README.md, "Route passes". */

import { L } from "../leaflet";
import { map, pixelsPerMetre } from "../map";
import { state } from "../../app/state";
import { raiseNodeDots } from "./markers";
import { chevronOpacity, retessellate } from "./route-geometry";

export const ROUTE_LAYERS = ["belts", "pipes", "power"] as const;

type RouteLayer = (typeof ROUTE_LAYERS)[number];

/* Each route layer's width in metres: the save carries a centre line and no width. */
export const ROUTE_WIDTH_M: Record<RouteLayer, number> = {
  belts: 2,
  pipes: 1.3,
  power: 0.2,
};

/* The thinnest a stroked line is drawn, one hairline for every network. */
const ROUTE_MIN_PX = 1.5;

/* A layer whose floor is not ROUTE_MIN_PX: the wires are marks sized to be seen. */
export const ROUTE_FLOOR_PX: Partial<Record<RouteLayer, number>> = {
  power: 2.5,
};

/* A lift's ring is a belt seen end-on, floored a little above a line. */
const LIFT_MIN_RADIUS_PX = 2;

/** A route's stroke width in pixels, from its width in the world. Shared by every first draw
 *  and the zoom pass, so a layer never changes thickness the first time the map moves. Without
 *  a `floor_px` the floor is ROUTE_MIN_PX. */
export function routeWeight(width_m: number, ppm: number, floor_px?: number): number {
  return Math.max(floor_px === undefined ? ROUTE_MIN_PX : floor_px, width_m * ppm);
}

export function liftRadius(ppm: number): number {
  return Math.max(LIFT_MIN_RADIUS_PX, (ROUTE_WIDTH_M.belts / 2) * ppm);
}

/* The sizes given in screen pixels, re-derived whenever the scale changes, on the layers that
 * are on the map. */
export function restyleRoutesForZoom() {
  const ppm = pixelsPerMetre();
  const radius = liftRadius(ppm);
  const alpha = chevronOpacity(ppm);
  ROUTE_LAYERS.forEach(function (name) {
    const group = state.layers[name];
    if (!group || !map.hasLayer(group)) return;
    const weight = routeWeight(ROUTE_WIDTH_M[name], ppm, ROUTE_FLOOR_PX[name]);
    group.eachLayer(function (layer) {
      if (!(layer instanceof L.Path)) return;
      const piece = layer as L.Path & { setRadius?: (r: number) => void };
      if (piece._chevron) piece.setStyle({ opacity: alpha });
      else if (piece.setRadius) {
        if (!piece._fixed) piece.setRadius(radius);
      } else if (!(piece instanceof L.Polygon)) {
        piece.setStyle({ weight: weight + (piece._widen || 0) });
        retessellate(piece as L.Polyline, ppm);
      }
    });
  });
}

/* Every route piece sent below the machines and node dots; sunk first is left highest, so the
 * chevrons go first, then the glyphs, then the runs. */
export function sinkRoutes() {
  ROUTE_LAYERS.forEach(function (name) {
    const group = state.layers[name];
    if (!group) return;
    const chevrons: L.Path[] = [];
    const glyphs: L.Path[] = [];
    const runs: L.Path[] = [];
    group.eachLayer(function (layer) {
      if (!(layer instanceof L.Path)) return;
      const piece = layer as L.Path;
      if (piece._chevron) chevrons.push(piece);
      else if (piece instanceof L.Polygon || piece._fixed) glyphs.push(piece);
      else runs.push(piece);
    });
    chevrons
      .concat(glyphs)
      .concat(runs)
      .forEach(function (piece) {
        if (piece.bringToBack) piece.bringToBack();
      });
  });
  raiseNodeDots();
}
