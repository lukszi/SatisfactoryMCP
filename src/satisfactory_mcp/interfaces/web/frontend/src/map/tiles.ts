/* The base map: which picture of this world everything else stands on.
 *
 * The MODES are the map types of the registry (`/api/maps`, held by map-types.ts) that are
 * ticked for the switcher, plus plain, which is no imagery at all and is the shipped state
 * rather than an error. Every pyramid is cut on the same frame, at the same tile size, into
 * the same grid, so a mode is at most one L.TileLayer against
 * `/api/maptiles/{id}/{z}/{x}/{y}` and a switch changes one path segment and nothing else.
 * Each pyramid does declare its own DEPTH, so `maxNativeZoom` comes from that layer's own
 * probe headers. `onModePick` is the seam to layercontrol/mode-picker.ts, which draws the radios and
 * knows nothing about tiles; the arrow points this way because an import back would be a ring.
 */

import { tilePath } from "../api/client";
import { onModePick, showModes } from "./layercontrol/mode-picker";
import { L } from "./leaflet";
import { makeLitLayer, parseLight, webglReady } from "./litlayer";
import { fetchMapRegistry, mapTypeAxes, mapRegistry, onMapRegistry, staleReasons, staleLabel } from "../app/map-types";
import { boundsOfBbox, MAP_SHEET_PX, MAP_SQUARE_M, map, writeHash } from "./map";
import { applyRegionDefaultForMode, updateRegionBlend } from "./regions";
import { BOOT, state } from "../app/state";
import { showSunControl } from "./suncontrol";
import { fail, offer } from "../kit/toast";
import { setMapTone } from "./map-tone";

import type { MapTypeBody } from "../api/shapes";
import type { BboxM } from "./geometry";
import type { ModeChoice } from "./layercontrol/mode-picker";
import type { MapTone } from "./map-tone";
import type { BaseMode } from "../app/state";

/** One base-map mode: a radio in the control, and at most one layer on the map. */
interface ModeSpec {
  key: BaseMode;
  /** The registry id `/api/maptiles/{id}/` answers on; null for plain, the one mode that is
   *  not a pyramid. */
  typeId: string | null;
  label: string;
  /** The row's tooltip when the mode can be picked: what this picture actually is. */
  about: string;
  /* ...and what it says when it cannot: which tool writes that tree. Repeated here rather
   * than read off the wire, because the page probes with HEAD and HEAD answers 204 with no
   * body -- an absent optional render is the ordinary state. */
  generator: string;
  /** "older build" or "older data" for a stale type, with why as its tooltip. */
  flag: string;
  flagTitle: string;
}

/* A mode that IS a pyramid: the same row with the null half of `typeId` ruled out, so "plain
 * has no tiles" is a thing the compiler knows rather than a thing the call order arranges. */
type PyramidSpec = ModeSpec & { typeId: string };

function isPyramid(spec: ModeSpec): spec is PyramidSpec {
  return spec.typeId !== null;
}

/** Which tool writes each painter's pyramids, for the tooltip of one that is not there. */
const GENERATORS: Record<string, string> = {
  map: "tools/gen_map_image.py, which cuts it out of the installed game",
  terrain: "tools/gen_map_renders.py, from the 1 m heightfield in data/local/heightmap/",
  satellite: "tools/gen_map_renders.py, from the heightfield and the game's own biome raster",
};

/** The id the artwork has always had in the registry, and the page's old name for it. */
const ARTWORK = "map";
const ALIASES: Record<string, string> = { artwork: ARTWORK };

const PLAIN: ModeSpec = {
  key: "plain",
  typeId: null,
  label: "plain",
  about: "no base imagery: the biome regions on the page's own sea",
  generator: "",
  flag: "",
  flagTitle: "",
};

function legacy(key: string, label: string, about: string): ModeSpec {
  return { key: key, typeId: key, label: label, about: about, generator: GENERATORS[key] || "", flag: "", flagTitle: "" };
}

/* What the switcher offers before the registry answers, or when it cannot: the three names the
 * page had before there was a registry, which the server still serves unregistered. */
const LEGACY: ModeSpec[] = [
  legacy(ARTWORK, "artwork", "the game's own map artwork"),
  legacy("terrain", "terrain", "a hypsometric relief map of this world, drawn from its own heightfield"),
  legacy("satellite", "satellite", "the same relief, coloured from the game's own biome raster"),
];

let MODES: ModeSpec[] = LEGACY.concat([PLAIN]);

function specOf(row: MapTypeBody): ModeSpec {
  return {
    key: row.id,
    typeId: row.id,
    label: row.title,
    about: mapTypeAxes(row) + (row.freshness.stale.length ? "\n" + staleLabel(row) + ": " + staleReasons(row) : ""),
    generator: GENERATORS[row.layer] || "the Maps tab in Settings",
    flag: staleLabel(row),
    flagTitle: staleReasons(row),
  };
}

/** A fragment's `mode=` as an id: the old `artwork` is the registry's `map`. */
export function aliasMode(raw: string): string {
  return ALIASES[raw] || raw;
}

/* The modes the switcher lists: every ticked type that can be served, the default first, and
 * also whatever is on screen or was asked for by the address, ticked or not. */
function wantedModes(): ModeSpec[] {
  const body = mapRegistry.body;
  if (!body) return LEGACY.concat([PLAIN]);
  const chosen = body.default;
  const keep: Record<string, boolean> = {};
  keep[aliasMode(BOOT.mode || "")] = true;
  if (state.mode) keep[state.mode] = true;
  if (chosen) keep[chosen] = true;
  const rows = body.types.filter(function (row) {
    return (row.status === "ready" || row.status === "missing") && (row.in_switcher || !!keep[row.id]);
  });
  rows.sort(function (a, b) {
    return (b.id === chosen ? 1 : 0) - (a.id === chosen ? 1 : 0);
  });
  return rows.map(specOf).concat([PLAIN]);
}

/** Whether `raw` names a mode this page can be asked for: plain, or a type the server serves. */
export function servableMode(raw: string | undefined): BaseMode | null {
  if (!raw) return null;
  const id = aliasMode(raw);
  if (id === "plain") return id;
  const body = mapRegistry.body;
  if (!body) {
    return LEGACY.some(function (spec) {
      return spec.key === id;
    })
      ? id
      : null;
  }
  return body.types.some(function (row) {
    return row.id === id && (row.status === "ready" || row.status === "missing");
  })
    ? id
    : null;
}

/* How to build each mode's layer, decided once by the probes and never again.
 *
 * A function rather than the layer itself, so a mode nobody looks at costs nothing: a
 * TileLayer constructed and never added still holds its options and its event handlers.
 *
 * A key that is not here is a mode whose pyramid is not on disk -- or one whose tiles turned
 * out not to draw, which `modeFailed` treats as the same thing. */
let layerFactories: Partial<Record<BaseMode, () => BaseLayer>> = {};

/** A built base layer, and whether it is lit live (which is what shows the sun control). */
interface BaseLayer {
  layer: L.Layer;
  lit: boolean;
}

/** Why a mode cannot be picked, when the reason is not simply "never generated". */
let refusals: Partial<Record<BaseMode, string>> = {};

/** The one layer the active mode has on the map, so a switch can take it off again. */
let baseLayer: L.Layer | null = null;

/** The modes whose live light failed, drawn with their baked light instead, and why. */
const litOff: Partial<Record<BaseMode, string>> = {};

function specFor(key: string): ModeSpec | null {
  let found: ModeSpec | null = null;
  MODES.forEach(function (spec) {
    if (spec.key === key) found = spec;
  });
  return found;
}

/* The corners a base-map probe answered with, as [x_min, y_min, x_max, y_max] metres. */
function mapImageBounds(response: Response): BboxM {
  const raw = (response.headers.get("X-Map-Bounds-M") || "").split(",").map(Number);
  if (raw.length === 4 && raw.every(isFinite)) return [raw[0]!, raw[1]!, raw[2]!, raw[3]!];
  return [MAP_SQUARE_M.x_min, MAP_SQUARE_M.y_min, MAP_SQUARE_M.x_max, MAP_SQUARE_M.y_max];
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
  delete layerFactories[spec.key];
  refusals[spec.key] = why;
  if (state.mode === spec.key) showBaseMode("plain", false);
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
const PyramidLayer = L.TileLayer.extend({
  getTileUrl: function (this: L.TileLayer, coords: L.Coords) {
    // Asserted rather than defaulted: this layer is only ever constructed below, with a
    // zoomOffset, and `|| 0` here would be a silently different grid rather than a fix.
    const span = 1 << (coords.z + this.options.zoomOffset!);
    if (coords.x < 0 || coords.y < 0 || coords.x >= span || coords.y >= span) {
      return L.Util.emptyImageUrl;
    }
    let url = L.TileLayer.prototype.getTileUrl.call(this, coords);
    const options = this.options as PyramidOptions;
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
function pyramidMaker(spec: PyramidSpec, response: Response): (() => BaseLayer) | null {
  const b = mapImageBounds(response);
  const anchored = [
    MAP_SQUARE_M.x_min,
    MAP_SQUARE_M.y_min,
    MAP_SQUARE_M.x_max,
    MAP_SQUARE_M.y_max,
  ];
  const moved = b.some(function (v, i) {
    return Math.abs(v - anchored[i]!) > 1;
  });
  if (moved) return null;

  const tilePx = +response.headers.get("X-Map-Tile-Px")! || 256;
  // Each layer's OWN depth, stated in its sidecar. Past it Leaflet upscales the deepest level
  // it has instead of asking for one that is not there.
  let maxZ = +response.headers.get("X-Map-Tile-Max-Z")!;
  if (!isFinite(maxZ) || maxZ < 0) maxZ = 5;
  const top = Math.log2(MAP_SHEET_PX / tilePx); // the pyramid z that IS the sheet: 5.
  if (!isFinite(top) || top !== Math.round(top)) return null;

  // ...and, when this layer has a denser tree and this display can use it, that tree's size
  // and depth as well. `tileSize` stays `tilePx`, which is the CSS size of a tile: the grid
  // must not move, only how many pixels arrive inside it.
  const densePx = +response.headers.get("X-Map-Tile-2x-Px")!;
  const denseMaxZ = +response.headers.get("X-Map-Tile-2x-Max-Z")!;
  const dense = wantsDenseTiles() && isFinite(densePx) && densePx > 0 && isFinite(denseMaxZ);
  if (dense) maxZ = Math.max(maxZ, denseMaxZ);

  // The build tag makes every URL change when the pyramid is recut, which is what lets the
  // server mark a tile immutable. It is per layer, so recutting the satellite cannot
  // invalidate the terrain a browser is holding.
  //
  // `px=` is NOT in this query: it is per tile, because one layer spans levels the dense tree
  // has and levels only the 1x tree reaches. PyramidLayer appends `denseQuery` -- separator
  // and all, which is why it is cut here where the rest of the query is known.
  const tag = response.headers.get("X-Map-Build");
  const query = [];
  if (tag) query.push("v=" + encodeURIComponent(tag));
  const url =
    tilePath(spec.typeId, "{z}", "{x}", "{y}") + (query.length ? "?" + query.join("&") : "");
  const denseQuery = dense ? (query.length ? "&" : "?") + "px=" + densePx : "";
  const bounds = boundsOfBbox(b);
  const light = parseLight(response.headers.get("X-Map-Light"));

  return function (): BaseLayer {
    if (light && webglReady() && !litOff[spec.key]) {
      try {
        const lit = makeLitLayer(spec.typeId, light, function (why) {
          if (litOff[spec.key]) return;
          litOff[spec.key] = why;
          fail(spec.label + ": " + why + "; showing it with the default sun baked in");
          if (state.mode === spec.key) showBaseMode(spec.key, false);
        });
        return { layer: lit, lit: true };
      } catch (ignored) {
        litOff[spec.key] = "WebGL would not start";
      }
    }
    const tiles = new PyramidLayer(url, {
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
    let broke = false;
    tiles.on("tileerror", function () {
      if (broke) return;
      broke = true;
      modeFailed(
        spec,
        "the pyramid is on disk but a tile would not load",
        spec.label + " tiles: the pyramid is there but a tile would not load; showing plain instead"
      );
    });
    return { layer: tiles, lit: false };
  };
}

/* The whole sheet as one imageOverlay: the artwork mode's fallback, and what any render that
 * is not this generator's -- other corners, no pyramid -- is drawn as. */
function overlayMaker(spec: ModeSpec, response: Response): () => BaseLayer {
  const bounds = boundsOfBbox(mapImageBounds(response));
  return function (): BaseLayer {
    const image = L.imageOverlay("/api/mapimage", bounds, {
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
    return { layer: image, lit: false };
  };
}

/** One HEAD against one pyramid's z0 tile. Never rejects: a probe that fails is a mode
 *  that is not there, which is the ordinary state for all three of them. */
function probePyramid(spec: PyramidSpec): Promise<void> {
  return fetch(tilePath(spec.typeId, 0, 0, 0), { method: "HEAD" })
    .then(function (r) {
      if (r.status !== 200) return; // 204: never generated, and that is not an error
      const factory = pyramidMaker(spec, r);
      if (factory) layerFactories[spec.key] = factory;
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
      layerFactories[spec.key] = overlayMaker(spec, r);
    })
    .catch(function () {
      /* same as above: no picture is the shipped answer */
    });
}

/** The rows layercontrol/mode-picker.ts draws, rebuilt from the probes every time anything changes. */
function modeChoices(): ModeChoice[] {
  return MODES.map(function (spec): ModeChoice {
    const ready = spec.key === "plain" || !!layerFactories[spec.key];
    return {
      key: spec.key,
      label: spec.label,
      flag: spec.flag,
      flagTitle: spec.flagTitle,
      ready: ready,
      note: ready
        ? spec.about
        : refusals[spec.key] || "not generated yet; written by " + spec.generator,
    };
  });
}

/** The tone a mode's picture declares; plain is the page's own dark sea. */
function toneOf(mode: BaseMode): MapTone {
  const body = mapRegistry.body;
  if (mode === "plain") return body ? body.plain_tone : "dark";
  const row = body
    ? body.types.filter(function (t) {
        return t.id === mode;
      })[0]
    : undefined;
  return row ? row.tone : "light";
}

/* Swap the one layer, and nothing else: the panes were created once by map.ts, the overlays
 * are the player's, and the CRS and tile grid are the same for every layer the server cuts.
 *
 * A mode that cannot be drawn resolves to plain rather than refusing, because the two callers
 * that can ask for one are a pasted link and a tile that just broke. `recordInHash` tells a
 * click from a boot: a click is a decision and belongs in the fragment, while the boot
 * resolution would otherwise write a mode into the URL of a page nobody chose anything on. */
function showBaseMode(key: BaseMode, recordInHash: boolean): void {
  const mode: BaseMode = key === "plain" || layerFactories[key] ? key : "plain";
  if (baseLayer) {
    map.removeLayer(baseLayer);
    baseLayer = null;
  }
  const factory = layerFactories[mode];
  let lit = false;
  if (factory) {
    const built = factory();
    baseLayer = built.layer;
    lit = built.lit;
    baseLayer.addTo(map);
  }
  showSunControl(lit);
  state.mode = mode;
  state.imagery = !!baseLayer;
  setMapTone(toneOf(mode));
  applyRegionDefaultForMode(state.imagery);
  updateRegionBlend();
  showModes(modeChoices(), mode);
  if (recordInHash) writeHash();
}

/* Which mode a fresh page opens in: the fragment's, if that mode can actually be drawn here,
 * then the default every browser here shares (Settings -> general), then the artwork if it is
 * there and plain if it is not. A render is opened on only when someone made it the default:
 * the page does not choose an interpretation of the world for anyone. */
function bootMode(): BaseMode {
  const asked = aliasMode(BOOT.mode || "");
  if (asked && (asked === "plain" || layerFactories[asked])) return asked;
  const chosen = mapRegistry.body ? mapRegistry.body.default : null;
  if (chosen && (chosen === "plain" || layerFactories[chosen])) return chosen;
  return layerFactories[ARTWORK] ? ARTWORK : "plain";
}

/* Probe these pyramids, in parallel, then the artwork's single-image fallback if its
 * pyramid did not answer. A probe is per id, so a type that was recut gets its new tag. */
function probeAll(specs: ModeSpec[]): Promise<void> {
  return Promise.all(specs.filter(isPyramid).map(probePyramid)).then(function () {
    const artwork = specFor(ARTWORK);
    if (!artwork || layerFactories[ARTWORK]) return;
    return probeMapImage(artwork);
  });
}

let booted = false;
let readyBefore: Record<string, boolean> = {};

function readyIds(): Record<string, boolean> {
  const out: Record<string, boolean> = {};
  (mapRegistry.body ? mapRegistry.body.types : []).forEach(function (row) {
    if (row.status === "ready") out[row.id] = true;
  });
  return out;
}

/* The registry moved: a job finished, a type was renamed, ticked, deleted or made default.
 * The switcher is rebuilt from it and every pyramid re-probed; a type that has just become
 * ready is offered with a toast rather than switched to. */
function rebuildModes(): void {
  const before = readyBefore;
  readyBefore = readyIds();
  MODES = wantedModes();
  layerFactories = {};
  refusals = {};
  probeAll(MODES).then(function () {
    if (state.mode && state.mode !== "plain" && !layerFactories[state.mode]) showBaseMode("plain", false);
    else {
      if (state.mode && baseLayer === null && layerFactories[state.mode]) showBaseMode(state.mode, false);
      showModes(modeChoices(), state.mode || "plain");
    }
    Object.keys(readyBefore).forEach(function (id) {
      if (before[id]) return;
      const spec = specFor(id);
      offer((spec ? spec.label : id) + " is ready", "show", function () {
        requestBaseMode(id, true);
      });
    });
  });
}

/** Switch to a mode a link or the Maps tab asked for, probing it first when the switcher does
 *  not list it. */
export function requestBaseMode(key: BaseMode, recordInHash: boolean): void {
  if (key === "plain" || layerFactories[key] || !servableMode(key)) {
    showBaseMode(key, recordInHash);
    return;
  }
  const row = mapRegistry.body
    ? mapRegistry.body.types.filter(function (t) {
        return t.id === key;
      })[0]
    : undefined;
  const spec = row ? specOf(row) : specFor(key);
  if (!spec || !isPyramid(spec)) {
    showBaseMode(key, recordInHash);
    return;
  }
  const known = spec;
  if (!specFor(key)) MODES.splice(MODES.length - 1, 0, known);
  probePyramid(known).then(function () {
    showBaseMode(key, recordInHash);
  });
}

/* Ask the registry which types there are, probe the ones the switcher lists, then open on a
 * mode. Without a registry answer the three old names are probed instead. */
export function loadBaseMap(): Promise<void> {
  onModePick(function (key) {
    showBaseMode(key as BaseMode, true);
  });
  onMapRegistry(function (listed) {
    if (listed && booted) rebuildModes();
  });
  return fetchMapRegistry()
    .then(function () {
      MODES = wantedModes();
      readyBefore = readyIds();
      return probeAll(MODES);
    })
    .then(function () {
      booted = true;
      showBaseMode(bootMode(), false);
    });
}
