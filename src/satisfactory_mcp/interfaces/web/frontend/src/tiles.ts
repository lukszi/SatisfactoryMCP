/* The base map: which picture of this world everything else stands on.
 *
 * Four MODES with radio semantics -- the game's own artwork, a hypsometric terrain render, a
 * biome-coloured satellite render, and plain, which is no imagery at all and is the shipped
 * state rather than an error. Every pyramid is cut on the same frame, at the same tile size,
 * into the same grid, so a mode is at most one L.TileLayer against
 * `/api/maptiles/{layer}/{z}/{x}/{y}` and a switch changes one path segment and nothing else.
 * Each pyramid does declare its own DEPTH, so `maxNativeZoom` comes from that layer's own
 * probe headers. `onModePick` is the seam to layercontrol.ts, which draws the radios and
 * knows nothing about tiles; the arrow points this way because an import back would be a ring.
 */

import { tilePath } from "./api";
import { onModePick, showModes } from "./layercontrol";
import { L } from "./leaflet";
import { MAP_SHEET_PX, MAP_SQUARE_M, map, writeHash } from "./map";
import { regionsUnderMode, updateRegionBlend } from "./regions";
import { BOOT, state } from "./state";
import { fail } from "./toast";

import type { MapTileLayer } from "./api";
import type { ModeChoice } from "./layercontrol";
import type { BaseMode } from "./state";

/** One base-map mode: a radio in the control, and at most one layer on the map. */
interface ModeSpec {
  key: BaseMode;
  /* The path segment `/api/maptiles/{layer}/` answers on. "" is Plain, the one mode that is
   * not a pyramid, which is why this is a union with the empty string rather than an optional
   * field. Everything else has to be a layer this server serves; see MapTileLayer in api.ts. */
  layer: MapTileLayer | "";
  label: string;
  /** The row's tooltip when the mode can be picked: what this picture actually is. */
  about: string;
  /* ...and what it says when it cannot: which tool writes that tree. Repeated here rather
   * than read off the wire, because the page probes with HEAD and HEAD answers 204 with no
   * body -- an absent optional render is the ordinary state, and asking for the sentence
   * would mean asking for the 404 the server goes out of its way not to raise. */
  generator: string;
}

/* A mode that IS a pyramid: the same row with the "no imagery" half of `layer` ruled out, so
 * "plain has no tiles" is a thing the compiler knows rather than a thing the call order
 * arranges. */
type PyramidSpec = ModeSpec & { layer: MapTileLayer };

function isPyramid(spec: ModeSpec): spec is PyramidSpec {
  return !!spec.layer;
}

var MODES: ModeSpec[] = [
  {
    key: "artwork",
    layer: "map",
    label: "artwork",
    about: "the game's own map artwork",
    generator: "tools/gen_map_image.py, which cuts it out of the installed game",
  },
  {
    key: "terrain",
    layer: "terrain",
    label: "terrain",
    about: "a hypsometric relief map of this world, drawn from its own heightfield",
    generator:
      "tools/gen_map_renders.py, which draws a hypsometric relief map of this world from " +
      "the 1 m heightfield in data/local/heightmap/",
  },
  {
    key: "satellite",
    layer: "satellite",
    label: "satellite",
    about: "the same relief, coloured from the game's own biome raster",
    generator:
      "tools/gen_map_renders.py, which draws the same relief coloured from the game's own " +
      "biome raster, from the 1 m heightfield in data/local/heightmap/",
  },
  {
    key: "plain",
    layer: "",
    label: "plain",
    about: "no base imagery: the biome regions on the page's own sea",
    generator: "",
  },
];

/* How to build each mode's layer, decided once by the probes and never again.
 *
 * A function rather than the layer itself, so a mode nobody looks at costs nothing: a
 * TileLayer constructed and never added still holds its options and its event handlers.
 *
 * A key that is not here is a mode whose pyramid is not on disk -- or one whose tiles turned
 * out not to draw, which `modeFailed` treats as the same thing. */
var makers: Partial<Record<BaseMode, () => L.Layer>> = {};

/** Why a mode cannot be picked, when the reason is not simply "never generated". */
var refusals: Partial<Record<BaseMode, string>> = {};

/** The one layer the active mode has on the map, so a switch can take it off again. */
var drawn: L.Layer | null = null;

function specFor(key: string): ModeSpec | null {
  var found: ModeSpec | null = null;
  MODES.forEach(function (spec) {
    if (spec.key === key) found = spec;
  });
  return found;
}

/* The corners a base-map probe answered with, as [x_min, y_min, x_max, y_max] metres. */
function mapImageBounds(response: Response): number[] {
  var raw = (response.headers.get("X-Map-Bounds-M") || "").split(",").map(Number);
  if (raw.length === 4 && raw.every(isFinite)) return raw;
  return [MAP_SQUARE_M.x_min, MAP_SQUARE_M.y_min, MAP_SQUARE_M.x_max, MAP_SQUARE_M.y_max];
}

/* Those corners as Leaflet bounds -- the [-y, x] flip, so the y ends swap. */
function mapImageLatLngBounds(b: number[]): L.LatLngBounds {
  return L.latLngBounds([
    [-b[3]!, b[0]!],
    [-b[1]!, b[2]!],
  ]);
}

/* A picture that turns out not to draw stops being a mode.
 *
 * A truncated download, an error page saved as .png, a pyramid half-deleted under a running
 * server: the file EXISTS, so the probe said yes, and the first tile says otherwise. The mode
 * greys out as an absent one does, with the reason in its tooltip instead of the generator's
 * name -- plus a toast, because unlike an absent render this one IS a fault and the player
 * asked for it by name.
 *
 * Falling back to plain rather than to another render: silently substituting a different
 * picture of the same world is the one answer that could be mistaken for success. */
function modeFailed(spec: ModeSpec, why: string, message: string): void {
  delete makers[spec.key];
  refusals[spec.key] = why;
  if (state.mode === spec.key) setMode("plain", false);
  else showModes(modeChoices(), state.mode || "plain");
  fail(message);
}

/* The two extras every pyramid layer below is built with: how deep its @2x tree goes, in the
 * pyramid's own z, and the query fragment that asks for it -- separator included, "" when
 * this display or this layer has no use for one. Options rather than a second URL template
 * because the choice is per TILE: one layer spans levels the dense tree has and levels only
 * the 1x tree reaches. */
interface PyramidOptions extends L.TileLayerOptions {
  denseMaxZ?: number;
  denseQuery?: string;
}

/* A TileLayer that knows how many tiles its pyramid actually has.
 *
 * `bounds` alone does not, and the difference is a 404 on every page load. Leaflet culls
 * tiles by intersecting their bounds with the layer's, in floating-point coordinates -- and
 * the sheet's east edge, unprojected as 8192 px over 1.0922666... px per metre, comes back as
 * 4252.999999999999, so the column starting exactly AT the edge "overlaps" the map by a
 * rounding error and gets fetched. The grid is 2^z tiles a side exactly, in integers, with
 * nothing to round.
 *
 * It also decides which DENSITY each level gets: `?px=` rides on the levels the dense tree
 * reaches and is dropped past its top, so a hi-DPI display keeps zooming into the deep 1x
 * levels instead of stopping where the dense tree does. */
var PyramidLayer = L.TileLayer.extend({
  getTileUrl: function (this: L.TileLayer, coords: L.Coords) {
    // Asserted rather than defaulted: this layer is only ever constructed below, with a
    // zoomOffset, and `|| 0` here would be a silently different grid rather than a fix.
    var span = 1 << (coords.z + this.options.zoomOffset!);
    if (coords.x < 0 || coords.y < 0 || coords.x >= span || coords.y >= span) {
      return L.Util.emptyImageUrl;
    }
    var url = L.TileLayer.prototype.getTileUrl.call(this, coords);
    var options = this.options as PyramidOptions;
    // The same arithmetic as `span`: coords.z + zoomOffset is the pyramid's own z, already
    // clamped to maxNativeZoom by Leaflet, so past the dense tree's top this asks for the
    // 1x tile of the SAME level -- the identical square of the world, standard density.
    if (options.denseQuery && coords.z + options.zoomOffset! <= options.denseMaxZ!) {
      url += options.denseQuery;
    }
    return url;
  },
}) as new (url: string, options: PyramidOptions) => L.TileLayer;

/* Whether this display can show more pixels than a 256 px tile carries.
 *
 * Read once, at probe time, and not watched: a window dragged to another monitor changes
 * devicePixelRatio, and rebuilding every tile layer mid-drag would refetch the whole view. A
 * reload picks up the new one, which is the same bargain the CRS and the bounds make.
 *
 * `>= 1.5` rather than `> 1`: a 125% Windows scale factor reports 1.25, at which the @2x tile
 * is 60% more pixels than the screen can show. 150% and up is where the denser tile is nearer
 * the truth than the sparser one. */
function wantsDenseTiles(): boolean {
  return (window.devicePixelRatio || 1) >= 1.5;
}

/* One pyramid, wired to the pixel space map.ts' CRS_SHEET_PX sets up: Leaflet's tile level Z
 * + 5 is the pyramid's z, because 256 * 2^(Z+5) is 8192 * 2^Z and 8192 sheet pixels are one
 * screen pixel each at map zoom 0. So the level Leaflet asks for is the level whose pixels
 * match the view.
 *
 * A layer may hold that grid TWICE -- `tiles/` at 256 px a tile and `tiles@2x/` at 512 -- and
 * this is where the second one is chosen. Nothing about the grid changes; the tile simply
 * arrives with twice the pixels in it. The @2x tree is SHALLOWER than the 1x tree, by one
 * level by arithmetic (512 * 2^z runs out of sheet before 256 * 2^z does) and by more where
 * the deep 1x levels were enhanced past the sheet, so density is chosen per LEVEL rather than
 * per layer: `maxNativeZoom` stays the 1x tree's depth, `?px=` rides on the levels the @2x
 * tree reaches, and past its top the request falls back to the 1x tile of the same z.
 *
 * Returns null -- this mode cannot be drawn as a pyramid -- when the server describes one
 * this grid cannot draw: corners that are not the square the CRS is anchored on, or a tile
 * size that is not a power-of-two fraction of the sheet. For the artwork that means the
 * single-image fallback; for a render it means the mode is not offered, because a tile grid
 * quietly offset from its own picture is worse than no picture. */
function pyramidMaker(spec: PyramidSpec, response: Response): (() => L.Layer) | null {
  var b = mapImageBounds(response);
  var anchored = [
    MAP_SQUARE_M.x_min,
    MAP_SQUARE_M.y_min,
    MAP_SQUARE_M.x_max,
    MAP_SQUARE_M.y_max,
  ];
  var moved = b.some(function (v, i) {
    return Math.abs(v - anchored[i]!) > 1;
  });
  if (moved) return null;

  var tilePx = +response.headers.get("X-Map-Tile-Px")! || 256;
  // Each layer's OWN depth, stated in its sidecar. Past it Leaflet upscales the deepest level
  // it has instead of asking for one that is not there.
  var maxZ = +response.headers.get("X-Map-Tile-Max-Z")!;
  if (!isFinite(maxZ) || maxZ < 0) maxZ = 5;
  var top = Math.log2(MAP_SHEET_PX / tilePx); // the pyramid z that IS the sheet: 5.
  if (!isFinite(top) || top !== Math.round(top)) return null;

  // ...and, when this layer has a denser tree and this display can use it, that tree's size
  // and depth as well. `tileSize` stays `tilePx`, which is the CSS size of a tile: the grid
  // must not move, only how many pixels arrive inside it.
  var densePx = +response.headers.get("X-Map-Tile-2x-Px")!;
  var denseMaxZ = +response.headers.get("X-Map-Tile-2x-Max-Z")!;
  var dense = wantsDenseTiles() && isFinite(densePx) && densePx > 0 && isFinite(denseMaxZ);
  if (dense) maxZ = Math.max(maxZ, denseMaxZ);

  // The build tag makes every URL change when the pyramid is recut, which is what lets the
  // server mark a tile immutable. It is per layer, so recutting the satellite cannot
  // invalidate the terrain a browser is holding.
  //
  // `px=` is NOT in this query: it is per tile, because one layer spans levels the dense tree
  // has and levels only the 1x tree reaches. PyramidLayer appends `denseQuery` -- separator
  // and all, which is why it is cut here where the rest of the query is known.
  var tag = response.headers.get("X-Map-Build");
  var query = [];
  if (tag) query.push("v=" + encodeURIComponent(tag));
  var url =
    tilePath(spec.layer, "{z}", "{x}", "{y}") + (query.length ? "?" + query.join("&") : "");
  var denseQuery = dense ? (query.length ? "&" : "?") + "px=" + densePx : "";
  var bounds = mapImageLatLngBounds(b);

  return function () {
    var tiles = new PyramidLayer(url, {
      pane: "basemap",
      tileSize: tilePx,
      noWrap: true,
      // Clamped to the world the tiles cover, so a pan out into the sea beyond it asks for
      // nothing. This is the coarse half of it -- see PyramidLayer for the exact half.
      bounds: bounds,
      minZoom: map.getMinZoom(),
      maxZoom: map.getMaxZoom(),
      // Below z0 there is nothing smaller to fetch and above the top nothing sharper: both
      // ends reuse the level they have, scaled, instead of asking for a level that is not
      // there.
      minNativeZoom: -top,
      maxNativeZoom: maxZ - top,
      zoomOffset: top,
      updateWhenZooming: false,
      denseMaxZ: denseMaxZ,
      denseQuery: denseQuery,
    });
    var broke = false;
    tiles.on("tileerror", function () {
      if (broke) return;
      broke = true;
      modeFailed(
        spec,
        "the pyramid is on disk but a tile would not load",
        spec.label + " tiles: the pyramid is there but a tile would not load; showing plain instead"
      );
    });
    return tiles;
  };
}

/* The whole sheet as one imageOverlay: the artwork mode's fallback, and what any render that
 * is not this generator's -- other corners, no pyramid -- is drawn as. */
function overlayMaker(spec: ModeSpec, response: Response): () => L.Layer {
  var bounds = mapImageLatLngBounds(mapImageBounds(response));
  return function () {
    var image = L.imageOverlay("/api/mapimage", bounds, {
      pane: "basemap",
      interactive: false,
    });
    image.on("error", function () {
      modeFailed(
        spec,
        "data/local/map.png exists but could not be decoded",
        "map image: data/local/map.png exists but could not be decoded; showing plain instead"
      );
    });
    return image;
  };
}

/** One HEAD against one pyramid's z0 tile. Never rejects: a probe that fails is a mode
 *  that is not there, which is the ordinary state for all three of them. */
function probePyramid(spec: PyramidSpec): Promise<void> {
  return fetch(tilePath(spec.layer, 0, 0, 0), { method: "HEAD" })
    .then(function (r) {
      if (r.status !== 200) return; // 204: never generated, and that is not an error
      var make = pyramidMaker(spec, r);
      if (make) makers[spec.key] = make;
    })
    .catch(function () {
      /* the probe failing means no picture, which is the default state anyway */
    });
}

/** ...and the artwork's fallback, probed only when its pyramid did not answer. */
function probeMapImage(spec: ModeSpec): Promise<void> {
  return fetch("/api/mapimage", { method: "HEAD" })
    .then(function (r) {
      if (r.status !== 200) return; // 204: no local render, which is the default state
      makers[spec.key] = overlayMaker(spec, r);
    })
    .catch(function () {
      /* same as above: no picture is the shipped answer */
    });
}

/** The rows layercontrol.ts draws, rebuilt from the probes every time anything changes. */
function modeChoices(): ModeChoice[] {
  return MODES.map(function (spec): ModeChoice {
    var ready = spec.key === "plain" || !!makers[spec.key];
    return {
      key: spec.key,
      label: spec.label,
      ready: ready,
      note: ready
        ? spec.about
        : refusals[spec.key] || "not generated yet; written by " + spec.generator,
    };
  });
}

/* Swap the one layer, and nothing else: the panes were created once by map.ts, the overlays
 * are the player's, and the CRS and tile grid are the same for every layer the server cuts.
 *
 * A mode that cannot be drawn resolves to plain rather than refusing, because the two callers
 * that can ask for one are a pasted link and a tile that just broke. `pinned` tells a click
 * from a boot: a click is a decision and belongs in the fragment, while the boot resolution
 * would otherwise write a mode into the URL of a page nobody chose anything on. */
export function setMode(key: BaseMode, pinned: boolean): void {
  var mode: BaseMode = key === "plain" || makers[key] ? key : "plain";
  if (drawn) {
    map.removeLayer(drawn);
    drawn = null;
  }
  var make = makers[mode];
  if (make) {
    drawn = make();
    drawn.addTo(map);
  }
  state.mode = mode;
  state.imagery = !!drawn;
  regionsUnderMode(state.imagery);
  updateRegionBlend();
  showModes(modeChoices(), mode);
  if (pinned) writeHash();
}

/* Which mode a fresh page opens in: the fragment's, if that mode can actually be drawn here,
 * otherwise the artwork if it is there and plain if it is not.
 *
 * Terrain and satellite are never chosen FOR you, even when they are the only pictures on
 * disk. They are interpretations of this world rather than the map of it, so the page opens
 * on the one picture that is not an opinion and the radios say what else there is. */
function bootMode(): BaseMode {
  var asked = specFor(BOOT.mode || "");
  if (asked && (asked.key === "plain" || makers[asked.key])) return asked.key;
  return makers.artwork ? "artwork" : "plain";
}

/* Probe every pyramid once, then open on a mode. Three HEADs in parallel -- they are
 * independent questions about three independent directories, and the artwork's own fallback
 * is the only thing that has to wait for an answer. */
export function loadBaseMap(): Promise<void> {
  onModePick(function (key) {
    setMode(key as BaseMode, true);
  });
  return Promise.all(MODES.filter(isPyramid).map(probePyramid))
    .then(function () {
      var artwork = specFor("artwork");
      if (!artwork || makers.artwork) return;
      return probeMapImage(artwork);
    })
    .then(function () {
      setMode(bootMode(), false);
    });
}
