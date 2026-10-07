/* The map object, the coordinate convention, and the three panes everything is stacked in.
 *
 * The page's one coordinate rule lives here and nowhere else: the API speaks metres (nothing on
 * the page divides by 100), and Satisfactory is +X east and +Y SOUTH while Leaflet is +lat
 * north, so a point is plotted at [-y, x]. `latLngOf` and `boundsOfBbox` apply it and `gameXY`
 * inverts it; everything else goes through those three.
 *
 * Creating the map is a SIDE EFFECT of importing this module, and the modules that decorate
 * it get their ordering from importing this one.
 */

import { L } from "./leaflet";
import { BOOT, pinnedFilename, state } from "../app/state";

import type { BboxM, Point3M, PointM } from "./geometry";

const BOUND = 5000; // metres; the playable world is ~7 km across, so this frames it loosely.

// The whole-world framing every load starts from.
export const HOME_VIEW: { centre: L.LatLngTuple; zoom: number } = { centre: [0, 0], zoom: -3 };

/** A point in game metres, plotted the page's one way round. See the note above. */
export function latLngOf(point: { x_m: number; y_m: number } | PointM | Point3M): L.LatLngTuple {
  return Array.isArray(point) ? [-point[1], point[0]] : [-point.y_m, point.x_m];
}

/** A `[x_min, y_min, x_max, y_max]` box as Leaflet bounds, grown by `padM` metres a side. */
export function boundsOfBbox(bbox: BboxM, padM?: number): L.LatLngBounds {
  const pad = padM || 0;
  return L.latLngBounds([latLngOf([bbox[0] - pad, bbox[1] - pad]), latLngOf([bbox[2] + pad, bbox[3] + pad])]);
}

/** The inverse of latLngOf: a map position as game metres. */
export function gameXY(latlng: L.LatLng | L.LatLngLiteral): PointM {
  return [latlng.lng, -latlng.lat];
}

export function flyToBox(box: BboxM, options?: { maxZoom?: number; padLeft?: number }): void {
  const bounds = boundsOfBbox(box);
  const pad = overlayPad();
  if (options?.padLeft !== undefined) pad.topLeft.x = options.padLeft;
  map.flyToBounds(bounds, {
    maxZoom: options ? options.maxZoom : undefined,
    paddingTopLeft: pad.topLeft,
    paddingBottomRight: pad.bottomRight,
  });
}

export const NARROW = window.matchMedia("(max-width: 699px)");

const GAP_PX = 12;

const SHEET_SHARE = 0.9;

function shown(id: string): DOMRect | null {
  const node = document.getElementById(id);
  if (!node || node.hidden || !node.getClientRects().length) return null;
  return node.getBoundingClientRect();
}

export function overlayPad(): { topLeft: L.Point; bottomRight: L.Point } {
  const frame = map.getContainer().getBoundingClientRect();
  const topLeft = L.point(GAP_PX, GAP_PX);
  const bottomRight = L.point(GAP_PX, GAP_PX);
  const cards: string[] = [];
  document.querySelectorAll<HTMLElement>(".mapcard[id]").forEach(function (card) {
    cards.push(card.id);
  });
  ["panel"].concat(cards).forEach(function (id) {
    const r = shown(id);
    if (!r) return;
    if (id !== "panel" || r.width >= frame.width * SHEET_SHARE) {
      bottomRight.y = Math.max(bottomRight.y, frame.bottom - r.top + GAP_PX);
    } else topLeft.x = Math.max(topLeft.x, r.right - frame.left + GAP_PX);
  });
  const layers = map.getContainer().querySelector<HTMLElement>(".leaflet-control-layers");
  if (layers && layers.offsetHeight > 60) {
    bottomRight.x = Math.max(bottomRight.x, frame.right - layers.getBoundingClientRect().left + GAP_PX);
  }
  return { topLeft: topLeft, bottomRight: bottomRight };
}

const POPUP_TOP_LEFT = L.point(GAP_PX, GAP_PX);
const POPUP_BOTTOM_RIGHT = L.point(GAP_PX, GAP_PX);

L.Popup.mergeOptions({ autoPanPaddingTopLeft: POPUP_TOP_LEFT, autoPanPaddingBottomRight: POPUP_BOTTOM_RIGHT });

export function padPopups(): void {
  const pad = overlayPad();
  POPUP_TOP_LEFT.x = pad.topLeft.x;
  POPUP_TOP_LEFT.y = pad.topLeft.y;
  POPUP_BOTTOM_RIGHT.x = pad.bottomRight.x;
  POPUP_BOTTOM_RIGHT.y = pad.bottomRight.y;
}

export function flyPadded(bounds: L.LatLngBounds, maxZoom: number, snap?: number): void {
  const pad = overlayPad();
  const was = map.options.zoomSnap;
  if (snap) map.options.zoomSnap = snap;
  map.flyToBounds(bounds, { maxZoom: maxZoom, paddingTopLeft: pad.topLeft, paddingBottomRight: pad.bottomRight });
  map.options.zoomSnap = was;
}

export function flyToPoint(at: L.LatLngTuple, zoom: number): void {
  flyPadded(L.latLngBounds([at, at]), zoom);
}

export const FIT_SNAP = 0.25;

export function fitWorld(): void {
  const pad = overlayPad();
  const snap = map.options.zoomSnap;
  map.options.zoomSnap = FIT_SNAP;
  map.fitBounds(
    boundsOfBbox([MAP_SQUARE_M.x_min, MAP_SQUARE_M.y_min, MAP_SQUARE_M.x_max, MAP_SQUARE_M.y_max]),
    { paddingTopLeft: pad.topLeft, paddingBottomRight: pad.bottomRight }
  );
  map.options.zoomSnap = snap;
}

/* The in-game map square, in metres, and the sheet the generator cuts from it. These two
 * numbers set the map's pixel unit below, and they are the same square the server pins by
 * default -- a render pinned anywhere else is drawn as one overlay instead (see
 * loadMapImage in tiles.ts), because the tile grid below is anchored on THIS square. */
export const MAP_SQUARE_M = { x_min: -3247, x_max: 4253, y_min: -3750, y_max: 3750 };
export const MAP_SHEET_PX = 8192;
const MAP_M_PER_SHEET = MAP_SQUARE_M.x_max - MAP_SQUARE_M.x_min; // 7500 m, and square.
const MAP_PX_PER_M = MAP_SHEET_PX / MAP_M_PER_SHEET; // 1.0923 sheet pixels to the metre.

/* CRS.Simple with one change: a pixel is a pixel OF THE MAP SHEET, not a metre.
 *
 * This is what lets the base map be a tile pyramid at all. Leaflet lays a tile grid out in the
 * CRS's pixel space, from its origin, in whole tiles, and the pyramid's grid is the sheet cut
 * into 2^z squares -- so the sheet's north-west corner has to BE the pixel origin and the
 * sheet has to be a power-of-two count of tiles across. Under plain CRS.Simple the sheet
 * starts at x = -3247 m and spans 7500, and no tile size lands both on a tile boundary: the
 * grid would be offset from the picture at every level. Anchoring the pixel space on the sheet
 * makes zoom 0 exactly one screen pixel per sheet pixel, which is also what makes Leaflet's
 * own choice of tile level the right one -- at map zoom Z it draws level Z + 5, whose
 * 256 * 2^(Z+5) pixels are precisely the 8192 * 2^Z the view has room for. */
const CRS_SHEET_PX = L.extend({}, L.CRS.Simple, {
  transformation: new L.Transformation(
    MAP_PX_PER_M,
    -MAP_SQUARE_M.x_min * MAP_PX_PER_M,
    -MAP_PX_PER_M,
    -MAP_SQUARE_M.y_min * MAP_PX_PER_M
  ),
});

export const map = L.map("map", {
  crs: CRS_SHEET_PX,
  preferCanvas: true, // thousands of markers: canvas, not one SVG node each.
  minZoom: -6,
  maxZoom: 3,
  attributionControl: true,
  maxBounds: [
    [-BOUND, -BOUND],
    [BOUND, BOUND],
  ],
  maxBoundsViscosity: 0.6,
});
state.map = map;

// Leaflet's `_rebound` without the centring: where the bounds fit, move only an edge that is out.
function rebound(lo: number, hi: number): number {
  if (lo + hi > 0) return lo < 0 ? lo : hi < 0 ? -hi : 0;
  return Math.max(0, Math.ceil(lo)) - Math.max(0, Math.floor(hi));
}

// Leaflet's maxBounds check, measured against the part of the map no card or panel covers.
map._getBoundsOffset = function (px: L.Bounds, bounds: L.LatLngBounds, zoom?: number): L.Point {
  const pad = overlayPad();
  let min = px.min!.add(pad.topLeft);
  let max = px.max!.subtract(pad.bottomRight);
  if (min.x >= max.x || min.y >= max.y) {
    min = px.min!;
    max = px.max!;
  }
  const northEastPx = map.project(bounds.getNorthEast(), zoom);
  const southWestPx = map.project(bounds.getSouthWest(), zoom);
  const lo = L.point(Math.min(northEastPx.x, southWestPx.x) - min.x, Math.min(northEastPx.y, southWestPx.y) - min.y);
  const hi = L.point(max.x - Math.max(northEastPx.x, southWestPx.x), max.y - Math.max(northEastPx.y, southWestPx.y));
  return L.point(rebound(lo.x, hi.x), rebound(lo.y, hi.y));
};

(function () {
  // The viewport the page opens on: the URL's, if the fragment carries one.
  // `+undefined` is NaN, which is precisely the "no z in the fragment" branch, so the
  // assertions below are about the type and not about the value.
  const zoom = isFinite(+BOOT.z!) ? +BOOT.z! : HOME_VIEW.zoom;
  let centre = HOME_VIEW.centre;
  if (BOOT.c) {
    const raw = BOOT.c.split(",");
    if (raw.length === 2 && isFinite(+raw[0]!) && isFinite(+raw[1]!)) centre = latLngOf([+raw[0]!, +raw[1]!]);
  }
  map.setView(centre, zoom);
})();

/* How many screen pixels one metre of ground is worth at a given zoom.
 *
 * Asked of the map rather than restated as MAP_PX_PER_M * 2^zoom, so it cannot drift away from
 * the CRS above: project() runs the very transformation Leaflet draws with. Call it once per
 * zoom change, never once per layer.
 *
 * This is what lets anything sized in PIXELS -- a polyline's weight, a circle's radius -- be
 * given a size in metres instead. Polygons never need it: they are already in map units and
 * scale for free. */
export function pixelsPerMetre(zoom?: number): number {
  const z = zoom === undefined ? map.getZoom() : zoom;
  return map.project([0, 1], z).x - map.project([0, 0], z).x;
}

// The prefix is the one piece of always-visible chrome, so it carries the one gesture the
// map has that nothing on screen hints at.
map.attributionControl
  .setPrefix(window.matchMedia("(pointer: coarse)").matches ? "" : "right-click: inspect a point")
  .addAttribution("map data from the save &middot; Leaflet");

/* The optional map render -- tile pyramid or single overlay -- gets the bottom pane of the
 * three, so the region fill is drawn OVER it rather than instead of it. Sharing a pane with
 * the regions would make the stacking a question of which loader finished first. */
map.createPane("basemap");
map.getPane("basemap")!.style.zIndex = "340";

/* The biome regions everything else stands on get their own pane, below overlayPane (400),
 * so the region fill can never end up in front of a node the player is trying to click. The
 * pane is also the unit of transparency: see REGION_BLEND in regions.ts. */
map.createPane("regions");
map.getPane("regions")!.style.zIndex = "350";

/* The player's own concrete, in its own pane between the biome raster (350) and the
 * overlay pane (400): a floor plan has to cover the ground it was poured on and sit
 * under every machine, node and label that stands on it. Leaflet builds one canvas per
 * pane, so this is also what keeps 8,000 rectangles off the region cells' canvas. */
map.createPane("foundations");
map.getPane("foundations")!.style.zIndex = "360";

/* The fragment is the page's whole address: what is being looked at (world, save), how much of
 * it (floor), what it is drawn on (mode), and where the eye is (z, c). `replaceState` and not
 * assignment, because panning must not grow the browser history by one entry per drag.
 *
 * `floor` is `<platform>/<band>`, the platform index `/api/floors` hands out and either a
 * band's ordinal or `ground`; absent when the page is showing the whole world.
 *
 * `mode` is omitted while `state.mode` is "", which is the window between the page loading and
 * tiles.ts' probes answering. A pan in that window must not pin a mode the page has not chosen
 * yet: the fragment would say `plain` on a machine whose artwork was about to load, and the
 * next reload would honour it. */
export function hashFor(dash: string): string {
  const parts: string[] = [];
  if (state.world) parts.push("world=" + encodeURIComponent(state.world));
  const pinned = pinnedFilename();
  if (pinned) parts.push("save=" + encodeURIComponent(pinned));
  if (state.floor) parts.push("floor=" + state.floor.platform + "/" + state.floor.band);
  if (state.mode) parts.push("mode=" + state.mode);
  // The one part of the layer control the address carries, because a dozen pickup rows are off
  // by default and a link about a drop pod is a link about a layer. Category names are the
  // API's own snake_case, so joining them needs no escaping.
  if (state.pickups.length) parts.push("pickups=" + state.pickups.join(","));
  if (dash) parts.push("dash=" + encodeURIComponent(dash).replace(/%2F/g, "/"));
  const centre = gameXY(map.getCenter());
  parts.push("z=" + map.getZoom());
  parts.push("c=" + Math.round(centre[0] * 10) / 10 + "," + Math.round(centre[1] * 10) / 10);
  return "#" + parts.join("&");
}

export function writeHash(): void {
  wrote = hashFor(state.dash);
  history.replaceState(null, "", wrote);
}

/* The exact string the page last put in the address bar, so that a reader of the fragment
 * (fragment.ts) can tell the page's own handwriting from a human's -- which is also what a
 * Back button onto a fragment already applied looks like. The STRING is compared rather than
 * four re-parsed fields, because writeHash is the only thing that produces this spelling. */
let wrote = "";

export function writtenHash(): string {
  return wrote;
}

/* The four corners of a footprint placed at (x, y) and turned by `yaw`, as latlngs.
 *
 * `w` and `l` are HALF-extents along the building's own X and Y, so a rotated Manufacturer
 * stays 18 x 20 m instead of growing into the bounding box of its turned self.
 *
 * The API sends yaw in degrees about world Z, positive turning +X towards +Y, so a local
 * offset (dx, dy) lands at (x + dx*cos - dy*sin, y + dx*sin + dy*cos).
 *
 * A null yaw is not a zero yaw: null means the projection predates schema 12 and this
 * placement's facing was never recorded, zero means it was recorded and points east. Both draw
 * the same axis-aligned box, so the difference between the two claims stays in the popup where
 * it can be read.
 */
export function footprintCorners(
  x: number,
  y: number,
  w: number,
  l: number,
  yaw: number | null | undefined
): L.LatLngTuple[] {
  const a = ((yaw || 0) * Math.PI) / 180;
  const cos = Math.cos(a);
  const sin = Math.sin(a);
  const offsets: [number, number][] = [
    [-w, -l],
    [w, -l],
    [w, l],
    [-w, l],
  ];
  return offsets.map(function (d): L.LatLngTuple {
    return latLngOf([x + d[0] * cos - d[1] * sin, y + d[0] * sin + d[1] * cos]);
  });
}
