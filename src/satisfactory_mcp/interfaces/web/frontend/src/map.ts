/* The map object, the coordinate convention, and the three panes everything is stacked in.
 *
 * The page's one coordinate rule lives here and nowhere else: Satisfactory is +X east and +Y
 * SOUTH while Leaflet is +lat north, so a point is plotted at [-y, x]. `xy` and
 * `footprintCorners` apply it and `writeHash` inverts it, which is why the fragment is
 * written here rather than beside the world picker whose selection it also carries.
 *
 * Creating the map is a SIDE EFFECT of importing this module, and the modules that decorate
 * it get their ordering from importing this one.
 */

import { L } from "./leaflet";
import { BOOT, pinnedFilename, state } from "./state";

var BOUND = 5000; // metres; the playable world is ~7 km across, so this frames it loosely.

// The whole-world framing every load starts from.
export var HOME_VIEW: { centre: L.LatLngTuple; zoom: number } = { centre: [0, 0], zoom: -3 };

/** A row with game coordinates, plotted the page's one way round. See the note above. */
export function xy(row: { x_m: number; y_m: number }): L.LatLngTuple {
  return [-row.y_m, row.x_m];
}

/* The in-game map square, in metres, and the sheet the generator cuts from it. These two
 * numbers set the map's pixel unit below, and they are the same square the server pins by
 * default -- a render pinned anywhere else is drawn as one overlay instead (see
 * loadMapImage in tiles.ts), because the tile grid below is anchored on THIS square. */
export var MAP_SQUARE_M = { x_min: -3247, x_max: 4253, y_min: -3750, y_max: 3750 };
export var MAP_SHEET_PX = 8192;
var MAP_M_PER_SHEET = MAP_SQUARE_M.x_max - MAP_SQUARE_M.x_min; // 7500 m, and square.
var MAP_PX_PER_M = MAP_SHEET_PX / MAP_M_PER_SHEET; // 1.0923 sheet pixels to the metre.

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
var CRS_SHEET_PX = L.extend({}, L.CRS.Simple, {
  transformation: new L.Transformation(
    MAP_PX_PER_M,
    -MAP_SQUARE_M.x_min * MAP_PX_PER_M,
    -MAP_PX_PER_M,
    -MAP_SQUARE_M.y_min * MAP_PX_PER_M
  ),
});

export var map = L.map("map", {
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

(function () {
  // The viewport the page opens on: the URL's, if the fragment carries one.
  // `+undefined` is NaN, which is precisely the "no z in the fragment" branch, so the
  // assertions below are about the type and not about the value.
  var zoom = isFinite(+BOOT.z!) ? +BOOT.z! : HOME_VIEW.zoom;
  var centre = HOME_VIEW.centre;
  if (BOOT.c) {
    var raw = BOOT.c.split(",");
    if (raw.length === 2 && isFinite(+raw[0]!) && isFinite(+raw[1]!)) centre = [-+raw[1]!, +raw[0]!];
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
  var z = zoom === undefined ? map.getZoom() : zoom;
  return map.project([0, 1], z).x - map.project([0, 0], z).x;
}

// The prefix is the one piece of always-visible chrome, so it carries the one gesture the
// map has that nothing on screen hints at.
map.attributionControl
  .setPrefix("right-click: inspect a point")
  .addAttribution("map data from your save &middot; Leaflet");

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
  var parts: string[] = [];
  if (state.world) parts.push("world=" + encodeURIComponent(state.world));
  var pinned = pinnedFilename();
  if (pinned) parts.push("save=" + encodeURIComponent(pinned));
  if (state.floor) parts.push("floor=" + state.floor.platform + "/" + state.floor.band);
  if (state.mode) parts.push("mode=" + state.mode);
  // The one part of the layer control the address carries, because a dozen pickup rows are off
  // by default and a link about a drop pod is a link about a layer. Category names are the
  // API's own snake_case, so joining them needs no escaping.
  if (state.pickups.length) parts.push("pickups=" + state.pickups.join(","));
  if (dash) parts.push("dash=" + encodeURIComponent(dash).replace(/%2F/g, "/"));
  var c = map.getCenter();
  parts.push("z=" + map.getZoom());
  parts.push("c=" + Math.round(c.lng * 10) / 10 + "," + Math.round(-c.lat * 10) / 10);
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
var wrote = "";

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
  var a = ((yaw || 0) * Math.PI) / 180;
  var cos = Math.cos(a);
  var sin = Math.sin(a);
  var offsets: [number, number][] = [
    [-w, -l],
    [w, -l],
    [w, l],
    [-w, l],
  ];
  return offsets.map(function (d): L.LatLngTuple {
    return [-(y + d[0] * sin + d[1] * cos), x + d[0] * cos - d[1] * sin];
  });
}
