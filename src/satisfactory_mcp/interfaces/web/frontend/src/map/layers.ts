/* Named layer groups: the registry the drawing modules add to and the control lists.
 *
 * `clearedLayer()` is the page's one way of getting a group, and the identity it preserves is
 * the point -- a refetch replaces a group's CONTENTS so the checkbox does not forget whether it
 * was ticked. Kept apart from the control that displays them because everything that draws
 * needs this and nothing that draws needs the folds.
 *
 * THIS FILE IMPORTS NOTHING THAT IMPORTS IT: every drawing module reaches this one, so
 * anything it reached back would be evaluated before all of them. test_frontend_layout.py
 * derives the rule from the source as "no module that imports ./layers".
 */

import { esc } from "../kit/dom";
import { L } from "./leaflet";
import { batch, control } from "./layercontrol/control";
import { map } from "./map";
import { state } from "../app/state";

/* The ROW RANK's bands: the sections of the legend as a reader sees it, top to bottom. Not the
 * fetch rank and not draw order; frontend/README.md, "Two ranks", says why.
 *
 * The control doubles as the map's legend and its only filter, so its order has to survive a
 * reload -- `sortLayers` pins it to the rank each group is given when it is created, rather
 * than to whatever order parallel fetches resolved in. The base map has no row at all: it is
 * the MODE radios at the top of the control, and tiles.ts puts its layer on the map directly.
 */
export var BAND = {
  /** The frame the world is read against: the biomes, their names, where you last stood,
   *  and the labels naming the places on it. Not things the player built. */
  chrome: 0,
  /** What the player built -- the networks and the placements, in the order a base is read
   *  in: the concrete, then what runs over it, then what stands on it. */
  built: 1,
  /** `node: `, one row per resource. Data-driven and alphabetical within the band. */
  node: 2,
  /** `pickup: `, one row per category. Data-driven and alphabetical within the band. */
  pickup: 3,
};

/* [band, slot, name]. The slot orders one band's fixed members; the name breaks the tie
 * inside a slot, which is the whole of the ordering for the two data-driven bands, where
 * every member shares slot 0.
 *
 * Slots are spaced by TEN, the same convention as the fetch ranks they are declared beside,
 * so a layer can be inserted between two others without renumbering the band.
 */
export type Rank = [number, number, string];

/** How a layer's control row looks and starts, fixed when the group is first created. */
export interface LayerOptions {
  /** Ticked on creation, unless the reader's remembered tick says otherwise. */
  on?: boolean;
  /** A swatch in the control row, which is what makes the control readable as a legend. */
  colour?: string;
  /** Where the row sits. The name is repeated inside it so the sort key reads as one whole
   *  value at the call site; dev mode checks the two agree. */
  rank: Rank;
  /** The row's text, when it is not the layer's name. */
  title?: string;
}

var TICKS_KEY = "layer-ticks";

var UNREMEMBERED = ["pickup: ", "regions"];

function remembered(name: string): boolean {
  return !UNREMEMBERED.some(function (prefix) {
    return name.indexOf(prefix) === 0;
  });
}

function readTicks(): Record<string, boolean> {
  try {
    const saved = JSON.parse(localStorage.getItem(TICKS_KEY) || "{}");
    return saved && typeof saved === "object" ? saved : {};
  } catch (ignored) {
    return {};
  }
}

export function rememberTick(event: L.LeafletEvent): void {
  const name = state.layerName[L.Util.stamp((event as L.LayersControlEvent).layer)];
  if (!name || !remembered(name)) return;
  const ticks = readTicks();
  ticks[name] = event.type === "overlayadd";
  try {
    localStorage.setItem(TICKS_KEY, JSON.stringify(ticks));
  } catch (ignored) {
    return;
  }
}

/** The named group, emptied for a redraw: created with its control row the first time, the
 *  same group with its tick kept ever after. */
export function clearedLayer(name: string, options: LayerOptions): L.LayerGroup {
  if (!state.layers[name]) {
    if (import.meta.env.DEV && options.rank[2] !== name) {
      console.error('layer "' + name + '" ranks itself as "' + options.rank[2] + '"');
    }
    const created = L.layerGroup();
    created._rank = options.rank;
    state.layers[name] = created;
    // Keyed by the same stamp Leaflet writes onto the row's checkbox, so decorateControl
    // can read a row's name back without parsing the swatch markup out of its text.
    state.layerName[L.Util.stamp(created)] = name;
    const shown = esc(options.title || name);
    control.addOverlay(
      created,
      options.colour ? '<i class="swatch" style="background:' + options.colour + '"></i>' + shown : shown
    );
    const ticked = remembered(name) ? readTicks()[name] : undefined;
    if (typeof ticked === "boolean" ? ticked : options.on) created.addTo(map);
  }
  const group = state.layers[name]!;
  // The floor filter's undo goes with the contents it is an undo OF: a refetch is exactly the
  // moment `_floorAll` stops being about anything. floors/filter.ts takes a fresh one.
  delete group._floorAll;
  return group.clearLayers();
}

/* What a factory is MADE OF, turned on by the gestures that fly to one: without belts the
 * machines are a scatter, without pipes a refinery block is half missing. Not storage, a toggle
 * asked for on purpose; not power, which starts ticked, so re-ticking it would overrule the
 * reader who unticked it. */
export var BUILT_AREA_LAYERS = ["machines", "belts", "pipes"];

/** Turn these layers on in one control render, and return the names that were off. */
export function turnOnLayers(names: string[]): string[] {
  const turned: string[] = [];
  batch(function () {
    names.forEach(function (name) {
      const group = state.layers[name];
      if (!group || map.hasLayer(group)) return;
      group.addTo(map);
      turned.push(name);
    });
  });
  return turned;
}

/* Layers whose names are data-driven (one per resource, one per pickup category) can go
 * stale on a world switch: a category the new world does not return would otherwise keep
 * the previous world's markers under a still-ticked checkbox. */
export function clearPrefixed(prefixes: string[]): void {
  Object.keys(state.layers).forEach(function (name) {
    prefixes.forEach(function (prefix) {
      if (name.indexOf(prefix) === 0) state.layers[name]!.clearLayers();
    });
  });
}
