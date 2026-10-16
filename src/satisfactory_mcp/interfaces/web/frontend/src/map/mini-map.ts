/* A second, small Leaflet map on the main one's terms: the same CRS and frame, a base picture
 * (a plain copy, see `copyMode` in tiles.ts: the main map's, else its default), and the biome
 * ground only when there is no picture at all. No layer control, no inspector, no address: what it draws is the
 * caller's. The World page holds one and moves its element between renders. */

import { L } from "./leaflet";
import { BOUND, CRS_SHEET_PX, HOME_VIEW, map as mainMap } from "./map";
import { knownRegions, regionCells } from "./regions";
import { baseCopy, copyMode, onBaseChange } from "./tiles";

export interface MiniMap {
  el: HTMLElement;
  map: L.Map;
  /** Follows `copyMode`; cheap when nothing changed, and also run on every base change. */
  sync(): void;
}

export function miniMap(className: string): MiniMap {
  const el = document.createElement("div");
  el.className = className;
  const m = L.map(el, {
    crs: CRS_SHEET_PX,
    minZoom: mainMap.getMinZoom(),
    maxZoom: mainMap.getMaxZoom(),
    zoomSnap: 0.25,
    attributionControl: false,
    maxBounds: [
      [-BOUND, -BOUND],
      [BOUND, BOUND],
    ],
    maxBoundsViscosity: 0.6,
  });
  m.createPane("basemap").style.zIndex = "340";
  m.createPane("regions").style.zIndex = "350";
  m.setView(HOME_VIEW.centre, HOME_VIEW.zoom);
  const ground = L.layerGroup().addTo(m);
  const groundRenderer = L.canvas({ pane: "regions" });
  let shownMode: string | null = null;
  let shownRegions: unknown = null;
  let base: L.Layer | null = null;

  function sync(): void {
    const mode = copyMode();
    if (mode !== shownMode) {
      shownMode = mode;
      if (base) m.removeLayer(base);
      base = baseCopy("basemap");
      if (base) base.addTo(m);
      /* the cells are opaque: left over from before the picture, they hide it */
      ground.clearLayers();
      shownRegions = null;
    }
    const regions = base ? null : knownRegions();
    if (regions === shownRegions) return;
    shownRegions = regions;
    ground.clearLayers();
    if (regions) regionCells(regions, "regions", groundRenderer).forEach((cell) => cell.addTo(ground));
  }

  onBaseChange(sync);
  return { el, map: m, sync };
}
