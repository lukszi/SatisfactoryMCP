/* A second, small Leaflet map on the main one's terms: the same CRS and frame, the base picture
 * the main map shows (as a plain copy, see `baseCopy` in tiles.ts), and the biome ground when
 * that picture is plain. No layer control, no inspector, no address: what it draws is the
 * caller's. The World page holds one and moves its element between renders. */

import { L } from "./leaflet";
import { BOUND, CRS_SHEET_PX, HOME_VIEW, map as mainMap } from "./map";
import { knownRegions, regionCells } from "./regions";
import { baseCopy } from "./tiles";
import { state } from "../app/state";

export interface MiniMap {
  el: HTMLElement;
  map: L.Map;
  /** Follows the main map's base picture; cheap when nothing changed. */
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
    if (state.mode !== shownMode) {
      shownMode = state.mode;
      if (base) m.removeLayer(base);
      base = baseCopy("basemap");
      if (base) base.addTo(m);
      shownRegions = null;
    }
    const regions = base ? null : knownRegions();
    if (regions === shownRegions) return;
    shownRegions = regions;
    ground.clearLayers();
    if (regions) regionCells(regions, "regions", groundRenderer).forEach((cell) => cell.addTo(ground));
  }

  return { el, map: m, sync };
}
