/* The pickups lying on the ground: one control row per category, the spoiler rows the settings
 * hide, and the `pickups=` half of the address bar. Their colours are their own owner in the
 * palette audit, so it measures them against the node dots. */

import { code, popup } from "../../kit/dom";
import { byCodeUnit } from "../../kit/format";
import { batch, control, registerSection } from "../layercontrol/control";
import { L } from "../leaflet";
import { BAND, clearedLayer } from "../layers";
import { latLngOf, map } from "../map";
import { byMapTone, onMapTone } from "../map-tone";
import { declareColours } from "../palette";
import { registerFetch } from "../../app/registry";
import { onSetting, settingOn } from "../../app/settings";
import { parseList, state } from "../../app/state";

import type { LayerInput } from "../leaflet-private";
import type { CollectibleRow, CollectiblesResponse } from "../../api/shapes";

// One colour per pickup category, so the category rows do not all draw one teal dot; unlisted
// categories share the fallback below. The slugs and the mercer sphere are picked against the
// node dots (docs/frontend_palette.md).
export const PICKUP_COLOUR: Record<string, string> = declareColours("pickups", {
  somersloop: "#d84378",
  mercer_sphere: "#9f87ff",
  hard_drive: "#5468d4",
  loot_cache: "#d8b46e",
  crashed_drop_pod: "#838d3f",
  power_slug_blue: "#81f6ff",
  power_slug_yellow: "#b4a200",
  power_slug_purple: "#ed00ff",
  mushroom: "#a8c86e",
  tape_pickup: "#e09a6e",
});

/* For the categories the table above does not name, and DECLARED rather than left a bare
 * literal: a stand-in that reaches the screen is a colour on the page and belongs in the
 * comparison. */
const PICKUP_FALLBACK = declareColours("pickups", { "pickup fallback": "#7fd1b9" })[
  "pickup fallback"
];

/* The X over a collected pickup: dark on a light base, light on a dark one. */
const COLLECTED_MARK_COLOURS = declareColours("pickups", {
  "pickup collected": "#2a3147",
  "pickup collected dark": "#9aa0a8",
});

function collectedMarkColour(): string {
  return byMapTone(COLLECTED_MARK_COLOURS["pickup collected"], COLLECTED_MARK_COLOURS["pickup collected dark"]);
}

/* The prefix that makes a layer name a pickup row, and the whole of the join between a
 * category as `/api/collectibles` names it and a row in the control. Named because the
 * fragment speaks the category and the control speaks the row, and three literals is how the
 * two drift apart. The trailing space is load-bearing; see Section.prefix. */
const PICKUP_PREFIX = "pickup: ";

/** Each category's own word, from the last census the server sent. */
const pickupLabels: Record<string, string> = {};

/** The categories the spoiler setting hides: rows drawn disabled, with nothing in them. */
let hiddenPickupCategories: string[] = [];

/** The last reply drawn, kept so a setting or a base-map tone can repaint without a fetch. */
let lastPayload: CollectiblesResponse | null = null;

export function pickupName(category: string): string {
  return pickupLabels[category] || category.replace(/_/g, " ");
}

/** A layer's name as a sentence says it: a pickup row by its category's word. */
export function layerDisplayName(name: string): string {
  return name.indexOf(PICKUP_PREFIX) === 0 ? pickupName(name.slice(PICKUP_PREFIX.length)) : name;
}

const HIDDEN_TITLE = "not found yet: turn spoilers on in Settings";

export function markHiddenRows(): void {
  const box = control.getContainer();
  if (!box) return;
  const inputs = box.querySelectorAll<LayerInput>("input.leaflet-control-layers-selector");
  Array.prototype.forEach.call(inputs, function (input: LayerInput) {
    const hidden = hiddenPickupCategories.indexOf(pickupCategory(state.layerName[input.layerId] || "")) >= 0;
    if (input.disabled === hidden) return;
    input.disabled = hidden;
    const row = input.closest("label");
    if (row) row.title = hidden ? HIDDEN_TITLE : "";
  });
}

/** The category a layer name is about, or "" for a layer that is not a pickup row. */
function pickupCategory(name: string): string {
  return name.indexOf(PICKUP_PREFIX) === 0 ? name.slice(PICKUP_PREFIX.length) : "";
}

/* The one category whose rows carry `looted`. Every other category sends null there for want
 * of the property, which is not the same claim as a null on a pod, so the two must not reach
 * the same style. See CollectibleRow for what null means. */
const POD_CATEGORY = "crashed_drop_pod";

function pickupFillOpacity(hollow: boolean, faint: boolean): number {
  if (hollow) return 0;
  return faint ? 0.2 : 0.7;
}

/* A pickup that is still there. Fill is how much is in it, and pods are the only rows that
 * vary: solid is what every other category keeps. A hollow ring is a looted pod, which is what
 * a looted pod is -- the shell still standing with the drive gone. A faint disc is a pod no
 * save has had loaded, whose flag was never read, and a solid one there would promise a hard
 * drive nothing has seen.
 *
 * Fill and not a dash: dashed already means locked, paused or planned on this page, and an
 * emptied pod is none of those. */
function pickupDot(here: L.LatLngTuple, colour: string, r: CollectibleRow): L.CircleMarker {
  const pod = r.category === POD_CATEGORY;
  const hollow = pod && r.looted === true;
  const faint = pod && r.looted === null;
  return L.circleMarker(here, {
    radius: 4,
    color: colour,
    // A 4 px disc with its fill taken away is a smudge at weight 1.
    weight: hollow ? 2 : 1,
    fillOpacity: pickupFillOpacity(hollow, faint),
  });
}

/** A collected pickup: an X, 8 latlng units across, over where it lay. */
function collectedMark(here: L.LatLngTuple): L.Polyline {
  return L.polyline(
    [
      [
        [here[0] - 4, here[1] - 4],
        [here[0] + 4, here[1] + 4],
      ],
      [
        [here[0] - 4, here[1] + 4],
        [here[0] + 4, here[1] - 4],
      ],
    ],
    { color: collectedMarkColour(), weight: 1 }
  );
}

/** What a pod's loot flag says, or null for a row that never had one to read. */
export function lootLine(r: CollectibleRow): string | null {
  if (r.category !== POD_CATEGORY || r.collected) return null;
  if (r.looted === true) return "looted: the hard drive is already taken";
  if (r.looted === false) return "unlooted: the hard drive is still in it";
  return "unknown: no save has loaded this pod yet";
}

export function drawCollectibles(data: CollectiblesResponse): void {
  lastPayload = data;
  paintPickups(data);
}

function paintPickups(data: CollectiblesResponse): void {
  const spoilers = settingOn("spoilers");
  hiddenPickupCategories = [];
  data.census.forEach(function (entry) {
    pickupLabels[entry.category] = entry.label;
    if (entry.spoiler && !spoilers) hiddenPickupCategories.push(entry.category);
  });
  const byCategory: Record<string, CollectibleRow[]> = {};
  data.rows.forEach(function (row) {
    if (hiddenPickupCategories.indexOf(row.category) < 0) {
      (byCategory[row.category] = byCategory[row.category] || []).push(row);
    }
  });
  hiddenPickupCategories.forEach(function (category) {
    const name = PICKUP_PREFIX + category;
    clearedLayer(name, {
      on: false,
      colour: PICKUP_COLOUR[category] || PICKUP_FALLBACK,
      rank: [BAND.pickup, 0, name],
      title: pickupName(category),
    });
  });
  Object.keys(state.layers).forEach(function (name) {
    // A category this world has none of (all collected, or never present) must not keep
    // showing another world's markers under a still-ticked box.
    const stale = pickupCategory(name);
    if (stale && !byCategory[stale]) state.layers[name]!.clearLayers();
  });
  Object.keys(byCategory)
    .sort(byCodeUnit)
    .forEach(function (category) {
      // One toggleable group per category, because "show me every hard drive" and "show
      // me everything" are different questions and the second one is unreadable.
      const colour = PICKUP_COLOUR[category] || PICKUP_FALLBACK;
      // Slot 0 and alphabetical for the same reason the node rows are, one band lower: kinds
      // of thing lying on the ground, in no order anyone could guess at.
      const name = PICKUP_PREFIX + category;
      // `clearedLayer` honours `on` only when it CREATES the group, which is exactly
      // right: the fragment decides what a fresh row opens as, and after that the checkbox
      // the reader clicked survives every refetch.
      const wanted = state.pickups.indexOf(category) >= 0;
      const group = clearedLayer(name, {
        on: wanted,
        colour: colour,
        rank: [BAND.pickup, 0, name],
        title: pickupName(category),
      });
      byCategory[category]!.forEach(function (row) {
        const here = latLngOf(row);
        const mark: L.Path = row.collected ? collectedMark(here) : pickupDot(here, colour, row);
        mark
          .bindPopup(
            popup([
              ["pickup", pickupName(category)],
              ["name", code(row.name)],
              ["state", row.collected ? "collected" : row.observed || "unknown"],
              ["holds", lootLine(row)],
              ["at", row.x_m + ", " + row.y_m + " m"],
            ])
          )
          .addTo(group);
      });
    });
  markHiddenRows();
}

function repaint(): void {
  if (lastPayload) paintPickups(lastPayload);
}

onSetting(repaint);
onMapTone(repaint);
new MutationObserver(markHiddenRows).observe(control.getContainer()!, { childList: true, subtree: true });

/* The `pickup: ` rows as a family, on the same terms as the node rows and shut for the same
 * reason: most of them are normally off, and a folded count says so. */
registerSection({ key: "pickups", prefix: PICKUP_PREFIX, title: "pickups", startOpen: false });

/* Which pickup rows are ticked, kept in `state.pickups` so that writeHash can put them in the
 * address bar without map.ts having to know what a pickup is.
 *
 * One category per event rather than a re-read of every row, and that is the whole reason this
 * is an event handler at all: a fragment may name a category the loaded world has no rows for,
 * whose row therefore does not exist, and a re-read would drop that request on the first tick
 * of any other layer -- including the ticks `drawCollectibles` itself causes as it creates the
 * rows the fragment asked for. */
export function notePickupChoice(event: L.LeafletEvent): void {
  const group = (event as L.LayersControlEvent).layer;
  const category = pickupCategory(state.layerName[L.Util.stamp(group)] || "");
  if (!category) return;
  const kept = state.pickups.filter(function (other) {
    return other !== category;
  });
  if (event.type === "overlayadd") kept.push(category);
  state.pickups = kept.sort(byCodeUnit);
}

/** The `pickups=` half of a fragment: the categories to draw, as the whole truth about which
 *  rows are ticked. Absent means none, so deleting it from the address bar puts them away. */
export function applyPickupFragment(asked: string | undefined): void {
  // Snapshotted, because every add and remove below runs notePickupChoice on the way past and
  // that handler's whole job is to rewrite the list this loop is reading.
  const want = parseList(asked);
  state.pickups = want.slice();
  batch(function () {
    Object.keys(state.layers).forEach(function (name) {
      const category = pickupCategory(name);
      if (!category) return;
      const group = state.layers[name]!;
      if (want.indexOf(category) >= 0) map.addLayer(group);
      else map.removeLayer(group);
    });
  });
}

/* The live wave, because a pickup is collected between one autosave and the next, and
 * `mode=remaining` because the question the layer answers is "what is left". */
registerFetch<CollectiblesResponse>({
  wave: "live",
  rank: 20,
  path: "/api/collectibles?mode=remaining",
  label: "collectibles",
  clears: [PICKUP_PREFIX],
  refilters: true,
  draw: drawCollectibles,
});
