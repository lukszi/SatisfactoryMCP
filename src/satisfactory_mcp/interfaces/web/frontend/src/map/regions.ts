/* The biome raster: the ground everything else stands on, and how it shares the screen
 * with a real map render when there is one.
 *
 * The cells are painted OPAQUE and the transparency is applied once at the pane; the fill and
 * the blend are one decision, so they live together. See REGION_BLEND.
 */

import { esc } from "../kit/dom";
import { L } from "./leaflet";
import { BAND, clearedLayer } from "./layers";
import { boundsOfBbox, latLngOf, map } from "./map";
import { declareColours } from "./palette";
import { state } from "../app/state";

import type { RegionsResponse } from "../api/shapes";

const REGION_FILL = 1; // opaque cells, or the shared borders become a grid: see REGION_BLEND.

/* How much of the base map shows through the region fill when BOTH are drawn.
 *
 * THE ALPHA GOES ON THE PANE, NOT ON THE CELLS. A per-cell fillOpacity blends every rectangle
 * against the picture ONE AT A TIME, and every shared border -- where a cell's antialiased
 * edge and its neighbour's overlap -- is blended twice, which draws a visible 256 m grid. That
 * is why the cells are opaque and their strokes are their own fill colour: painted into the
 * pane's own canvas they merge into one shape, and the browser composites that finished shape
 * over the tiles once, at this alpha.
 *
 * 0.45 by eye over both extremes of the render -- the pale sand of the Dune Desert, where a
 * heavier fill turns the dunes to mud, and the near-black canopy of the Northern Forest, where
 * a lighter one leaves the region tint invisible. Region NAMES are unaffected: they are
 * tooltips, and tooltips live in Leaflet's tooltipPane. */
const REGION_BLEND = 0.45;

/* Applied on every layer change, because every path into "both are drawn" is one: a mode
 * switch, a mode's tiles failing back to plain, and the region box being ticked by hand.
 * Read off `state.imagery` rather than asked of tiles.ts, which already imports this module.
 *
 * Guarded against its own no-ops rather than debounced. Drawing a world adds thousands of
 * layers to the map, each of which fires this, and the guard turns all but the two that
 * change anything into two property reads. */
let appliedBlend = "";

export function updateRegionBlend() {
  const pane = map.getPane("regions");
  if (!pane) return;
  const want = state.imagery ? String(REGION_BLEND) : "";
  if (want === appliedBlend) return;
  appliedBlend = want;
  pane.style.opacity = want;
}

/** Whether the player has ticked or unticked the region box; after that, modes leave it be. */
let playerChoseRegions = false;

/** True while this module ticks the box itself: Leaflet reports that exactly like a click. */
let applyingModeDefault = false;

/** False until a mode first applies its default: `drawRegions` ticks the box as it creates it. */
let modeDefaultsArmed = false;

/* Whether the region tint is on: the mode's business until the player says otherwise.
 *
 * A rule about STATES and not a reaction to a transition, because "a render arrived" happens
 * on every mode switch and would untick the box each time the player looked at the terrain and
 * back. OFF under any imagery mode, ON under plain, where there is nothing to hide and nothing
 * to blend against. One tick of that box by the player is a decision about this session, and
 * every mode switch after it leaves the box alone. */
export function applyRegionDefaultForMode(imagery: boolean): void {
  modeDefaultsArmed = true;
  const group = state.layers["regions"];
  if (!group || playerChoseRegions) return;
  const want = !imagery;
  if (map.hasLayer(group) === want) return;
  applyingModeDefault = true;
  try {
    if (want) group.addTo(map);
    else map.removeLayer(group);
  } finally {
    applyingModeDefault = false;
  }
}

/** Registered in main.ts with the rest of the map listeners, so the order it runs in is
 *  written down in one place rather than decided by the import graph. */
export function noteRegionChoice(event: L.LeafletEvent): void {
  if (!modeDefaultsArmed || applyingModeDefault) return;
  if ((event as L.LayersControlEvent).layer === state.layers["regions"]) playerChoseRegions = true;
}

/* One muted ground per biome letter, keyed by the legend letter `data/region_names.json`
 * assigns -- alphabetical by region name, so adding a region moves the letters. These are the
 * BLENDED values, painted at full opacity; see REGION_BLEND. Why each is where it is:
 * docs/frontend_palette.md.
 */
const REGION_COLOUR: Record<string, string> = declareColours("regions", {
  A: "#3e3e3c", // Abyss Cliffs
  B: "#284e5a", // Blue Crater
  C: "#2e5348", // Crater Lakes
  D: "#654e37", // Desert Canyons
  E: "#726443", // Dune Desert
  F: "#3b5a3b", // Grass Fields
  G: "#294834", // Jungle Spires
  H: "#32544d", // Lake Forest
  I: "#594a37", // Maze Canyons
  // The outer coast and the ocean: the one ground that must not read as a biome.
  J: "#8a8478", // No Man's Land
  K: "#2e4637", // Northern Forest
  L: "#65423b", // Red Bamboo Fields
  M: "#4e3937", // Red Jungle
  N: "#5c5b4e", // Rocky Desert
  O: "#335041", // Southern Forest
  P: "#295258", // Spire Coast
  Q: "#374232", // Swamp
  R: "#294233", // Titan Forest
  S: "#585d40", // Western Dune Forest
});

/* The base map: one flat rectangle per 256 m raster cell, plus a name per region. Grid row 0
 * is the NORTH edge, because y0_m is the smallest y and game +Y is south. Void cells are left
 * unpainted: the sea colour showing through them is the coastline.
 *
 * Names print at `label_m`, not the centroid: a concave region's centroid can sit on a
 * neighbour's ground, and a name printed there contradicts the same page's right-click
 * inspector.
 */
let tips: L.Tooltip[] = [];

export function regionLabels(): HTMLElement[] {
  const labels: HTMLElement[] = [];
  tips.forEach(function (tip) {
    const node = tip.getElement();
    if (node) labels.push(node);
  });
  return labels;
}

export function drawRegions(data: RegionsResponse): void {
  tips = [];
  // Adjacent slots at the top of the legend, because they are a pair: the biome fill is the
  // ground every other layer is drawn over, and its names are the same thing said in words.
  const regions = clearedLayer("regions", { on: true, rank: [BAND.chrome, 0, "regions"] });
  const names = clearedLayer("region names", { on: true, rank: [BAND.chrome, 10, "region names"] });
  const cell = data.cell_m;
  data.grid.forEach(function (row, j) {
    for (let i = 0; i < row.length; i++) {
      const letter = row.charAt(i);
      if (letter === ".") continue;
      const colour = REGION_COLOUR[letter] || "#3f4640";
      const x = data.x0_m + i * cell;
      const y = data.y0_m + j * cell;
      // Cell (i, j) spans [x, x + cell] by [y, y + cell] in game metres; row 0 is the north edge.
      L.rectangle(
        boundsOfBbox([x, y, x + cell, y + cell]),
        {
          // Stroked in its own fill colour so neighbouring cells of one biome merge into
          // a shape instead of showing a grid; interactive:false so the region fill never
          // eats a click meant for a node sitting on top of it.
          color: colour,
          weight: 1,
          opacity: REGION_FILL,
          fillColor: colour,
          fillOpacity: REGION_FILL,
          interactive: false,
          pane: "regions",
        }
      ).addTo(regions);
    }
  });

  Object.keys(data.regions).forEach(function (name) {
    // A standalone tooltip, not a zero-opacity marker: a marker would drag Leaflet's
    // default icon (and its two image requests) into the page for a label that is meant
    // to be text and nothing else.
    const here = data.regions[name]!;
    const at = here.label_m || here.centroid_m;
    tips.push(
      L.tooltip({ permanent: true, direction: "center", className: "region-label" })
        .setLatLng(latLngOf(at))
        .setContent(esc(name))
        .addTo(names)
    );
  });
}
