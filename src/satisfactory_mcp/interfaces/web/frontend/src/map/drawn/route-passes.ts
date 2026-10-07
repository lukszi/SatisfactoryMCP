/* The passes every route layer -- belts, pipes and power -- goes through by NAME: the width
 * table, the pixel restyle on zoom, and the sink that keeps a run from stealing a machine's
 * click. The drawing modules import this one; it imports none of them. */

import { L } from "../leaflet";
import { map, pixelsPerMetre } from "../map";
import { state } from "../../app/state";
import { raiseNodeDots } from "./markers";
import { chevronOpacity, retessellate } from "./route-geometry";

/* The route layers share the overlay canvas with the machines and the node dots, and
 * hit-testing there is draw order with the LAST match winning. A belt run crosses every machine
 * it feeds, and a polyline's hit area is its width plus Leaflet's click tolerance, so a route
 * layer added after the machines would quietly take the click on every machine it passes over.
 * Pushed to the back instead: under the machines, under the node dots, over the foundations
 * (a separate pane, so a separate canvas).
 *
 * THEIR OWN PANE WOULD NOT WORK: the DOM delivers a click to the topmost element under the
 * pointer and the overlay pane's canvas covers the entire viewport, so a clickable layer below
 * it is not clickable at all. `bringToBack` is a no-op on a path whose group is not on the map,
 * so this is safe to call whenever.
 *
 * ORDER INSIDE A LAYER is decided by the order of the calls: each `bringToBack` puts its caller
 * below everything already sunk, so the piece sunk LAST ends up at the very bottom. The
 * junction squares go first and the runs after them, which leaves a splitter sitting ON the
 * lines it joins -- a square hidden beneath a line is not CLICKABLE, and the popup naming the
 * piece is the whole reason it has one.
 *
 * A POWER POLE IS THE SAME CASE AS A SPLITTER, which is what `_fixed` buys here beyond the
 * restyle: a pole sunk with the runs would be a mark under every line it terminates and a
 * popup nobody can open. Partitioned on the mark rather than on draw order, because draw order
 * is power-wires.ts's business and this rule is not.
 */
export const ROUTE_LAYERS = ["belts", "pipes", "power"] as const;

type RouteLayer = (typeof ROUTE_LAYERS)[number];

/* What each route layer is worth in metres, and the one place the three differ. Each is a
 * CONSTANT rather than a field, because the save carries a centre line and no width: a belt is
 * the game's 2 m whatever its tier, a pipe its 1.3 m bore, a wire 0.2 m. */
export const ROUTE_WIDTH_M: Record<RouteLayer, number> = {
  belts: 2,
  pipes: 1.3,
  power: 0.2,
};

/* The width below which a stroked line stops being drawn at all. One hairline for every
 * network: a statement about lines, not belts, or the networks would fade out at different
 * zooms and the page would invent a difference the world does not have. */
const ROUTE_MIN_PX = 1.5;

/* Where a layer's width stops falling, for the layers whose answer is not ROUTE_MIN_PX. The
 * wires are on at the whole-world view, where 1.5 px over the artwork barely shows; 2.5 px is
 * above 0.2 m at every zoom this map has, so a wire is a MARK sized to be seen, like the pole
 * at its end. */
export const ROUTE_FLOOR_PX: Partial<Record<RouteLayer, number>> = {
  power: 2.5,
};

/* A lift is a belt seen end-on, so its ring is that circle: radius half the belt width,
 * floored a little higher than a line, because a ring has to enclose something to read as one. */
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

/* The pixels half of the route layers, re-derived whenever the scale changes.
 *
 * A polyline's weight and a circleMarker's radius are the two sizes on this page given in
 * SCREEN pixels, so they are the two that do not follow the map on their own. Everything else
 * here is a polygon in map units and needs none of this.
 *
 * Restyling on zoomend rather than drawing routes as thin polygons in map units, because a
 * polygon cannot have a floor -- zoomed out, a true two-metre belt is a fraction of a pixel,
 * and the floor is a pixel statement that needs a pixel size to make it in -- and because a
 * layer of two-metre ribbons would be unclickable at exactly the zooms where the popup is worth
 * opening. The pass runs once per zoom step and costs well under a frame.
 *
 * A LAYER NOBODY IS LOOKING AT IS SKIPPED, which is most zoomends, since belts and pipes start
 * unticked. That means ticking a layer on at a zoom it was not drawn at HAS to restyle it, or a
 * belt turned on at the factory view arrives at the world view's hairline -- main.ts's
 * `overlayadd` handler is where that happens. */
export function restyleRoutesForZoom() {
  const ppm = pixelsPerMetre();
  const radius = liftRadius(ppm);
  const alpha = chevronOpacity(ppm);
  ROUTE_LAYERS.forEach(function (name) {
    const group = state.layers[name];
    if (!group || !map.hasLayer(group)) return;
    const weight = routeWeight(ROUTE_WIDTH_M[name], ppm, ROUTE_FLOOR_PX[name]);
    group.eachLayer(function (layer) {
      // Floor mode puts a connector's up/down glyph in its run's layer: a marker, with no stroke.
      if (!(layer instanceof L.Path)) return;
      const piece = layer as L.Path & { setRadius?: (r: number) => void };
      // Four kinds of piece share these layers and only two are sized in pixels: a lift's ring
      // by its radius, a run by its weight. A splitter is a polygon in map units and is already
      // right at every zoom. A chevron is map units too, so what changes for it is whether it
      // is drawn at all -- the one piece here with a zoom below which it is noise.
      if (piece._chevron) piece.setStyle({ opacity: alpha });
      // A lift's ring is sized from the scale; a power pole's disc is a MARK at a fixed pixel
      // radius, and says so with `_fixed`.
      else if (piece.setRadius) {
        if (!piece._fixed) piece.setRadius(radius);
      } else if (!(piece instanceof L.Polygon)) {
        // A casing's `_widen` rim is in SCREEN pixels around a line that follows the scale, so
        // the two are recombined at every zoom or the rim grows with the map.
        piece.setStyle({ weight: weight + (piece._widen || 0) });
        // And the geometry: the zoom that changes the width changes how many pieces a bend is.
        retessellate(piece as L.Polyline, ppm);
      }
    });
  });
}

export function sinkRoutes() {
  ROUTE_LAYERS.forEach(function (name) {
    const group = state.layers[name];
    if (!group) return;
    const chevrons: L.Path[] = [];
    const glyphs: L.Path[] = [];
    const runs: L.Path[] = [];
    group.eachLayer(function (layer) {
      // Paths only: a floor connector's glyph is a marker in the marker pane, above this
      // canvas and no part of the stacking question this pass answers.
      if (!(layer instanceof L.Path)) return;
      const piece = layer as L.Path;
      if (piece._chevron) chevrons.push(piece);
      else if (piece instanceof L.Polygon || piece._fixed) glyphs.push(piece);
      else runs.push(piece);
    });
    // Sunk FIRST is left highest, per the note above, so the chevrons go before the glyphs
    // and the runs: a direction mark under the line it marks would not be a mark.
    chevrons
      .concat(glyphs)
      .concat(runs)
      .forEach(function (piece) {
        if (piece.bringToBack) piece.bringToBack();
      });
  });
  raiseNodeDots();
}
