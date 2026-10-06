/* Named layer groups: the registry the drawing modules add to and the control lists.
 *
 * `layer()` is the page's one way of getting a group, and the identity it preserves is the
 * point -- a refetch replaces a group's CONTENTS so the checkbox does not forget whether it
 * was ticked. Kept apart from the control that displays them because everything that draws
 * needs this and nothing that draws needs the folds.
 *
 * THIS FILE IMPORTS NOTHING THAT IMPORTS IT: every drawing module reaches this one, so
 * anything it reached back would be evaluated before all of them. test_architecture.py
 * derives the rule from the source as "no module that imports ./layers".
 */

import { esc } from "../kit/dom";
import { L } from "./leaflet";
import { batch, control } from "./layercontrol/control";
import { map } from "./map";
import { state } from "../app/state";

/* THE PAGE HAS TWO RANKS. THEY DECIDE DIFFERENT THINGS AND NEITHER DRIVES THE OTHER.
 *
 *   FETCH RANK -- `Fetcher.rank` in registry.ts, one number, per wave. WHEN a request goes
 *   out, which decides which reply lands first.
 *
 *   ROW RANK -- the `rank` below, three parts, per layer. WHERE a layer's row sits in the
 *   control, which decides nothing but the order of a list of checkboxes.
 *
 * A feature declares both and they are free to disagree: the node dots are fetched first and
 * listed late.
 *
 * ROW RANK IS NOT DRAW ORDER either. Everything clickable shares one canvas, and that canvas
 * draws in the order paths were ADDED to it -- a list Leaflet maintains and this rank appears
 * nowhere in. `sortFunction` below is read by the control when it rebuilds its list and by
 * nothing else, which is why `raiseNodeDots` in markers.ts exists and why no band ordering
 * here could replace it.
 */

/* The bands: the sections of the legend as a reader sees it, top to bottom. The control
 * doubles as the map's legend and its only filter, so its order has to survive a reload --
 * `sortLayers` pins it to the rank each group is given when it is created, rather than to
 * whatever order parallel fetches resolved in.
 *
 * The base map has no row at all: it is the MODE radios at the top of the control, and
 * tiles.ts puts its layer on the map directly.
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
  /** Nothing declared a rank. A resting place rather than a category -- the control sorts
   *  such a row to the bottom rather than dropping it, and dev mode says so out loud. */
  unknown: 9,
};

/* [band, slot, name]. The slot orders one band's fixed members; the name breaks the tie
 * inside a slot, which is the whole of the ordering for the two data-driven bands, where
 * every member shares slot 0.
 *
 * Slots are spaced by TEN, the same convention as the fetch ranks they are declared beside,
 * so a layer can be inserted between two others without renumbering the band.
 */
export type Rank = [number, number, string];

var TICKS_KEY = "layer-ticks";

var UNREMEMBERED = ["pickup: ", "regions"];

function remembered(name: string): boolean {
  return !UNREMEMBERED.some(function (prefix) {
    return name.indexOf(prefix) === 0;
  });
}

function readTicks(): Record<string, boolean> {
  try {
    var saved = JSON.parse(localStorage.getItem(TICKS_KEY) || "{}");
    return saved && typeof saved === "object" ? saved : {};
  } catch (ignored) {
    return {};
  }
}

export function rememberTick(event: L.LeafletEvent): void {
  var name = state.layerName[L.Util.stamp((event as L.LayersControlEvent).layer)];
  if (!name || !remembered(name)) return;
  var ticks = readTicks();
  ticks[name] = event.type === "overlayadd";
  try {
    localStorage.setItem(TICKS_KEY, JSON.stringify(ticks));
  } catch (ignored) {
    return;
  }
}

/* A named layer that can be replaced wholesale on refetch without the checkbox forgetting
 * whether it was ticked -- which is why the LayerGroup identity is kept and only its contents
 * are cleared. `colour` puts a swatch in the control row, which is what makes the control
 * readable as a legend.
 *
 * `rank` is optional in the type and required in practice: a layer that declares none sorts
 * into BAND.unknown at the bottom, which is a place to land rather than a decision, and dev
 * mode names it. The name is repeated inside the rank so that the sort key reads as one whole
 * value at the call site; dev mode checks the two agree.
 */
export function layer(name: string, on?: boolean, colour?: string, rank?: Rank, title?: string): L.LayerGroup {
  if (!state.layers[name]) {
    if (import.meta.env.DEV) {
      if (!rank) {
        console.error('layer "' + name + '" declares no rank — it sorts to the bottom');
      } else if (rank[2] !== name) {
        console.error('layer "' + name + '" ranks itself as "' + rank[2] + '"');
      }
    }
    var group = L.layerGroup();
    group._rank = rank || [BAND.unknown, 0, name];
    state.layers[name] = group;
    // Keyed by the same stamp Leaflet writes onto the row's checkbox, so decorateControl
    // can read a row's name back without parsing the swatch markup out of its text.
    state.layerName[L.Util.stamp(group)] = name;
    var shown = esc(title || name);
    control.addOverlay(group, colour ? '<i class="swatch" style="background:' + colour + '"></i>' + shown : shown);
    var ticked = remembered(name) ? readTicks()[name] : undefined;
    if (typeof ticked === "boolean" ? ticked : on) group.addTo(map);
  }
  var group = state.layers[name]!;
  // The floor filter's undo goes with the contents it is an undo OF: a refetch is exactly the
  // moment `_floorAll` stops being about anything, and keeping it would let a later exit
  // restore a world that has been replaced. floors/filter.ts takes a fresh one on its next pass.
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
