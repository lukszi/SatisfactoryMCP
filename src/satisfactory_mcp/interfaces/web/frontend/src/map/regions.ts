/* The biome raster: the ground everything else stands on, and how it shares the screen
 * with a real map render when there is one.
 *
 * The cells are painted OPAQUE and the transparency is applied once at the pane; the fill and
 * the blend are one decision, so they live together. See REGION_BLEND.
 */

import { esc } from "../kit/dom";
import { L } from "./leaflet";
import { BAND, layer } from "./layers";
import { map } from "./map";
import { declareColours } from "./palette";
import { state } from "../app/state";

import type { RegionsResponse } from "../api/shapes";

var REGION_FILL = 1; // opaque cells, or the shared borders become a grid: see REGION_BLEND.

/* How much of the base map shows through the region fill when BOTH are drawn.
 *
 * THE ALPHA GOES ON THE PANE, NOT ON THE CELLS. A per-cell fillOpacity blends 768 rectangles
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
var REGION_BLEND = 0.45;

/* Applied on every layer change, because every path into "both are drawn" is one: a mode
 * switch, a mode's tiles failing back to plain, and the region box being ticked by hand.
 * Read off `state.imagery` rather than asked of tiles.ts, which already imports this module.
 *
 * Guarded against its own no-ops rather than debounced. Drawing a world adds thousands of
 * layers to the map, each of which fires this, and the guard turns all but the two that
 * change anything into two property reads. */
var regionBlend = "";

export function updateRegionBlend() {
  var pane = map.getPane("regions");
  if (!pane) return;
  var want = state.imagery ? String(REGION_BLEND) : "";
  if (want === regionBlend) return;
  regionBlend = want;
  pane.style.opacity = want;
}

/* Whether the region tint is on: the mode's business until the player says otherwise.
 *
 * A rule about STATES and not a reaction to a transition, because "a render arrived" happens
 * on every mode switch and would untick the box each time the player looked at the terrain and
 * back. OFF under any imagery mode, ON under plain, where there is nothing to hide and nothing
 * to blend against.
 *
 * The rule stops applying the moment the player disagrees with it: one tick of that box is a
 * decision about this session, and every mode switch after it leaves the box alone. */
var chosen = false;

/* Programmatic ticks are not decisions, and there is no way to tell them apart afterwards:
 * Leaflet fires `overlayadd` from the LAYER's own add event, so `map.addLayer(group)` is
 * indistinguishable from a click by the time the event arrives. The flag is the same trick
 * `setSection` uses in layercontrol/control.ts for the same reason. */
var applying = false;

/* ...and nothing at all counts before the modes exist. `drawRegions` adds the group to the map
 * as it creates it, which fires `overlayadd` on a page where nobody has clicked anything. */
var armed = false;

export function regionsUnderMode(imagery: boolean): void {
  armed = true;
  var group = state.layers["regions"];
  if (!group || chosen) return;
  var want = !imagery;
  if (map.hasLayer(group) === want) return;
  applying = true;
  try {
    if (want) group.addTo(map);
    else map.removeLayer(group);
  } finally {
    applying = false;
  }
}

/** Registered in main.ts with the rest of the map listeners, so the order it runs in is
 *  written down in one place rather than decided by the import graph. */
export function noteRegionChoice(event: L.LeafletEvent): void {
  if (!armed || applying) return;
  if ((event as L.LayersControlEvent).layer === state.layers["regions"]) chosen = true;
}

/* One muted colour per biome letter, keyed by the legend letter `data/region_names.json`
 * assigns -- which is alphabetical by region name, so adding a region moves the letters.
 * Hand-picked to read as ground at a glance, and 19 hex strings rather than a single pixel of
 * anyone's artwork.
 *
 * These are the BLENDED values, baked in, because every cell is painted at full opacity; see
 * REGION_BLEND above for why the transparency is not done here. The letter is also the name
 * each colour is declared under, because it is the API's own key.
 */
var REGION_COLOUR: Record<string, string> = declareColours("regions", {
  A: "#3e3e3c", // Abyss Cliffs
  B: "#284e5a", // Blue Crater
  C: "#2e5348", // Crater Lakes
  D: "#654e37", // Desert Canyons
  E: "#726443", // Dune Desert
  F: "#3b5a3b", // Grass Fields
  G: "#294834", // Jungle Spires
  H: "#32544d", // Lake Forest
  I: "#594a37", // Maze Canyons
  /* No Man's Land: the game's own name for the outer coast and the ocean, and 287 of the
   * 768 painted cells -- so it is the largest thing on this layer and the one that must NOT
   * read as a biome. Bare, pale and desaturated, one step brighter than any ground here.
   *
   * Measured like the pipe rust and the storage blue-violet. In CIE Lab it is dE 17.1 from its
   * nearest neighbour (Rocky Desert, which it borders for most of the west coast), 18.4 from
   * Dune Desert and 20.6 from Western Dune Forest -- above the ~15.6 step the belts use and
   * comfortably above the pipes' 15.1. All three are same-owner comparisons and palette.ts
   * makes none of them: this is the ground's own ramp, and the audit draws its line at owners
   * so that a deliberate step like this one never has to be excused. The alternatives measured
   * beside it were all worse against that same Rocky Desert border: the render's own
   * no-man's-land tone (#7c7a6c) lands at dE 12.6, a warm sand (#807a68) at 13.3, and anything
   * darker collapses onto it (#5a5750 is dE 3.9). Cool greys were rejected for the other end:
   * #46484a is dE 5.1 from Abyss Cliffs. */
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

/* The base map: one flat rectangle per 256 m raster cell, plus a name per region.
 *
 * Orientation is the trap: grid row 0 is the NORTH edge because y0_m is the smallest y and
 * game +Y is south, so cell (i, j) spanning y in [y, y+cell] is latitude [-(y+cell), -y] and
 * the y bounds swap here and only here. Void cells are left unpainted: the sea colour showing
 * through them is the coastline.
 *
 * Names print at `label_m`, not the centroid: a concave region's centroid can sit on a
 * neighbour's ground, and a name printed there contradicts the same page's right-click
 * inspector.
 */
var tips: L.Tooltip[] = [];

var raster: RegionsResponse | null = null;

export function regionLabels(): HTMLElement[] {
  var out: HTMLElement[] = [];
  tips.forEach(function (tip) {
    var node = tip.getElement();
    if (node) out.push(node);
  });
  return out;
}

export function regionAt(x_m: number, y_m: number): string | null {
  if (!raster) return null;
  var row = raster.grid[Math.floor((y_m - raster.y0_m) / raster.cell_m)];
  var letter = row ? row.charAt(Math.floor((x_m - raster.x0_m) / raster.cell_m)) : "";
  return (letter && raster.legend[letter]) || null;
}

export function drawRegions(data: RegionsResponse): void {
  raster = data;
  tips = [];
  // Adjacent slots at the top of the legend, because they are a pair: the biome fill is the
  // ground every other layer is drawn over, and its names are the same thing said in words.
  var regions = layer("regions", true, undefined, [BAND.chrome, 0, "regions"]);
  var names = layer("region names", true, undefined, [BAND.chrome, 10, "region names"]);
  var cell = data.cell_m;
  data.grid.forEach(function (row, j) {
    for (var i = 0; i < row.length; i++) {
      var letter = row.charAt(i);
      if (letter === ".") continue;
      var colour = REGION_COLOUR[letter] || "#3f4640";
      var x = data.x0_m + i * cell;
      var y = data.y0_m + j * cell;
      L.rectangle(
        [
          [-(y + cell), x],
          [-y, x + cell],
        ],
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
    var here = data.regions[name]!;
    var at = here.label_m || here.centroid_m;
    tips.push(
      L.tooltip({ permanent: true, direction: "center", className: "region-label" })
        .setLatLng([-at[1], at[0]])
        .setContent(esc(name))
        .addTo(names)
    );
  });
}
