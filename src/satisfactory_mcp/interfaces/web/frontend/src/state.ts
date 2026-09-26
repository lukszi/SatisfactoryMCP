/* What the page is currently showing, and where it was told to show it.
 *
 * One mutable object rather than module-level variables spread over the drawing modules,
 * because a world switch has to change all of it at once: the selection, the epoch that
 * makes a late reply from the old world droppable, and the layer registry the redraw writes
 * into.
 *
 * This module imports NOTHING at runtime, and that is load-bearing: `map.ts` reads `BOOT`
 * while it is building the map, so anything this file imported would have to be evaluated
 * before the map exists. Both imports below are `import type`, which is erased. The same
 * constraint is why a type belonging to one feature's view -- `PanelState`, `BaseMode`,
 * `FloorAddress` -- is declared here rather than beside the module that draws it.
 */

import type * as L from "leaflet";

import type { WorldRow } from "./api-shapes";

/* The panel's fold state, which layercontrol.ts owns and sets. It survives a world switch for
 * the same reason the checkboxes do: a switch replaces layer CONTENTS without rebuilding the
 * control or these flags. */
export interface PanelState {
  open: boolean;
  sections: Record<string, boolean>;
}

/* Which picture of this world the base map is: the modes tiles.ts offers, as radio semantics
 * -- exactly one, and `plain` is a real answer rather than the absence of one. */
export type BaseMode = "artwork" | "terrain" | "satellite" | "plain";

/* Which storey of which platform the page is slicing, and nothing else about it: everything
 * else about the view -- which ids are on which band, which runs leave it -- is floors.ts's,
 * because that is a payload rather than a selection.
 *
 * `band` is a band's ordinal as a string, or "ground": the pseudo-floor for what the
 * decomposition measured as standing on no band at all. A string because those are one
 * choice among the picker's rows and the fragment spells both the same way. */
export interface FloorAddress {
  platform: number;
  band: string;
}

export interface PageState {
  world: string;
  /** A pinned save's path; "" means "the newest, refetched on save events". */
  save: string;
  worlds: WorldRow[];
  /** Every named LayerGroup, by the name its control row carries. */
  layers: Record<string, L.LayerGroup>;
  /** Leaflet's layer stamp -> the name its control row carries. */
  layerName: Record<number, string>;
  control: L.Control.Layers | null;
  map: L.Map | null;
  /** Bumped on every world/save switch; a reply from an older epoch is dropped. */
  epoch: number;
  opened: number;
  panel: PanelState;
  /** The base-map mode; "" until the probes have said which ones exist. See tiles.ts. */
  mode: BaseMode | "";
  /** Whether that mode actually has a picture on the map -- which is the one thing the
   *  region tint has to know, and the reason it is a flag here rather than a question
   *  regions.ts asks tiles.ts (which would be a cycle: tiles.ts already imports it). */
  imagery: boolean;
  /** The storey being sliced, or null for the whole world. See FloorAddress. */
  floor: FloorAddress | null;
  /* Which collectible categories have their `pickup: ` row ticked, by the category name
   * `/api/collectibles` groups by. Every one of those rows is off by default and there are a
   * dozen of them, so this is the only part of the page a link about a slug or a drop pod can
   * ask for -- and it is held here rather than read off the map because a fragment can name a
   * category whose layer the current world has no rows for and therefore has not created. */
  pickups: string[];
  /** The dashboard address, `tab` or `tab/subject`; "" while the map is the view. */
  dash: string;
}

/* The selection lives in the URL fragment so a reload, a bookmark or a pasted link lands on the
 * same world, save, layers and viewport. Read before `state` is built, because the boot values
 * below are half of it. */
export var BOOT: Record<string, string> = parseHash(location.hash);

export var state: PageState = {
  world: "",
  save: "",
  worlds: [],
  layers: {},
  layerName: {},
  control: null,
  map: null,
  epoch: 0,
  opened: Date.now(),
  // A placeholder so the field is never undefined, not a second declaration of the defaults:
  // layercontrol.ts replaces it wholesale as it builds the control.
  panel: { open: true, sections: {} },
  // "" and false until loadBaseMap has probed: the page has not chosen a mode yet, and
  // writeHash must not pin one it has not chosen.
  mode: "",
  imagery: false,
  floor: null,
  pickups: parseList(BOOT.pickups),
  dash: dashOf(BOOT),
};

/* A function and not just `BOOT`, because the fragment is read more than once: `BOOT` is the one
 * the page opened on, and fragment.ts re-reads it whenever the address bar changes under an open
 * tab. One parser, so a hand-typed fragment is read exactly the way a bookmarked one is. */
export function parseHash(hash: string): Record<string, string> {
  var out: Record<string, string> = {};
  hash
    .replace(/^#/, "")
    .split("&")
    .forEach(function (piece) {
      var eq = piece.indexOf("=");
      if (eq > 0) out[piece.slice(0, eq)] = decodeURIComponent(piece.slice(eq + 1));
    });
  return out;
}

export function dashOf(asked: Record<string, string>): string {
  if (asked.dash) return asked.dash;
  var mapped = ["z", "c", "floor", "mode", "pickups"].some(function (key) {
    return key in asked;
  });
  return mapped ? "" : "overview";
}

/** A comma-separated fragment value as the list it spells, sorted and without blanks, so that
 *  what the page writes back is the same string whatever order it was typed in. */
export function parseList(raw: string | undefined): string[] {
  return (raw || "")
    .split(",")
    .filter(function (piece) {
      return !!piece;
    })
    .sort();
}

export function currentWorld(): WorldRow | null {
  var found: WorldRow | null = null;
  state.worlds.forEach(function (w) {
    if (w.world_id === state.world) found = w;
  });
  return found;
}

/* The two halves of the same lookup, and they are a pair on purpose: the fragment carries a
 * save's FILENAME (short, readable, and the thing a human editing the address bar would
 * type) while `state.save` is its PATH (unambiguous when two worlds hold a "save 1.sav").
 * Whoever writes the fragment converts one way and whoever reads one converts back. */
export function pinnedFilename(): string {
  var name = "";
  var w = currentWorld();
  if (!state.save || !w) return name;
  w.saves.forEach(function (s) {
    if ((s.path || s.filename) === state.save) name = s.filename;
  });
  return name;
}

/** A filename out of the fragment, as the pin `state.save` holds; "" if this world has no
 *  such save, which is how both callers say "follow the newest" without a second flag. */
export function pinnedPath(filename: string, w: WorldRow | null): string {
  var found = "";
  if (!filename || !w) return found;
  w.saves.forEach(function (s) {
    if (s.filename === filename) found = s.path || s.filename;
  });
  return found;
}
