/* One map type's tile pyramid as a Leaflet layer: the probe's headers read into a grid on the
 * main map's frame (map.ts' CRS_SHEET_PX), and the layer that fetches it, at the density this
 * display can use. tiles.ts decides which mode shows and what happens when one breaks. */

import { tilePath } from "../api/client";
import { SHARE } from "../share/mode";
import { siteTileUrl } from "../share/site";
import { L } from "./leaflet";
import { parseLight } from "./litlayer";
import { boundsOfBbox, MAP_SHEET_PX, MAP_SQUARE_M, map } from "./map";

import type { BboxM } from "./geometry";

/* The corners a base-map probe answered with, as [x_min, y_min, x_max, y_max] metres. */
export function mapImageBounds(response: Response): BboxM {
  const raw = (response.headers.get("X-Map-Bounds-M") || "").split(",").map(Number);
  if (raw.length === 4 && raw.every(isFinite)) return [raw[0]!, raw[1]!, raw[2]!, raw[3]!];
  return [MAP_SQUARE_M.x_min, MAP_SQUARE_M.y_min, MAP_SQUARE_M.x_max, MAP_SQUARE_M.y_max];
}

/* The two extras every pyramid layer below is built with: how deep its @2x tree goes, in the
 * pyramid's own z, and the query fragment that asks for it -- separator included, "" when
 * this display or this layer has no use for one. Options rather than a second URL template
 * because the choice is per TILE: one layer spans levels the dense tree has and levels only
 * the 1x tree reaches. */
interface PyramidOptions extends L.TileLayerOptions {
  denseMaxZ?: number;
  denseQuery?: string;
  /** The layer id, for the shared map's file paths. */
  siteLayer?: string;
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
    if (SHARE) {
      const z = coords.z + options.zoomOffset!;
      return siteTileUrl(options.siteLayer!, z, coords.x, coords.y, "", !!options.denseQuery && z <= options.denseMaxZ!);
    }
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

/** A pyramid's layer, in any pane, and the live light its sidecar declares. */
export interface PyramidTiles {
  tilesIn(pane: string): L.TileLayer;
  light: ReturnType<typeof parseLight>;
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
export function pyramidTiles(typeId: string, response: Response): PyramidTiles | null {
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
  // server mark a tile immutable. It is per layer, so recutting the painted layer cannot
  // invalidate the terrain a browser is holding.
  //
  // `px=` is NOT in this query: it is per tile, because one layer spans levels the dense tree
  // has and levels only the 1x tree reaches. PyramidLayer appends `denseQuery` -- separator
  // and all, which is why it is cut here where the rest of the query is known.
  const tag = response.headers.get("X-Map-Build");
  const query = [];
  if (tag) query.push("v=" + encodeURIComponent(tag));
  const url =
    tilePath(typeId, "{z}", "{x}", "{y}") + (query.length ? "?" + query.join("&") : "");
  const separator = query.length ? "&" : "?";
  const denseQuery = dense ? separator + "px=" + densePx : "";
  const bounds = boundsOfBbox(b);
  const light = parseLight(response.headers.get("X-Map-Light"));

  function tilesIn(pane: string): L.TileLayer {
    const tiles = new PyramidLayer(url, {
      pane: pane,
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
    if (SHARE) (tiles.options as PyramidOptions).siteLayer = typeId;
    return tiles;
  }

  return { tilesIn: tilesIn, light: light };
}
