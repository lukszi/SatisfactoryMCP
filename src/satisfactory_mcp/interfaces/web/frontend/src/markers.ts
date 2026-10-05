/* The three things drawn as points rather than as shapes: resource nodes, pickups, and
 * where the player last stood.
 *
 * `raiseNodeDots` is the reason they share a file with each other rather than one apiece.
 * Everything clickable is on one canvas, hit-testing is draw order, and an extractor is
 * drawn exactly on the node it drains -- so the node dots have to be raised after ANY draw,
 * and the pass that does it belongs with the layers it raises.
 */

import { code, popup } from "./dom";
import { regionLine, shortResource } from "./format";
import { batch, control, registerSection } from "./layercontrol";
import { L } from "./leaflet";
import { BAND, layer } from "./layers";
import { map, xy } from "./map";
import { declareColours } from "./palette";
import { pinButtons } from "./pins";
import { registerFetch } from "./registry";
import { onSetting, setting } from "./settings";
import { parseList, state } from "./state";
import { fail } from "./toast";
import { onTone, toned } from "./tone";

import type { LayerInput, OnMap } from "./leaflet-private";

import type {
  CollectibleRow,
  CollectiblesResponse,
  NodeRow,
  NodesResponse,
  SummaryResponse,
} from "./api-shapes";

/* Raised explicitly after either draw, because otherwise whichever of /api/nodes and
 * /api/machines resolved last decides who takes the click on an occupied node. The dot wins;
 * the extractor keeps the rest of its rectangle. */
function raise(layerOf: L.Layer, when: (dot: L.Path & OnMap) => boolean): void {
  var path = layerOf as L.Path & OnMap;
  if (path.bringToFront && path._map && when(path)) path.bringToFront();
}

export function raiseNodeDots() {
  var extractors = state.layers["extractors"];
  if (extractors) {
    extractors.eachLayer(function (piece) {
      raise(piece, function () {
        return true;
      });
    });
  }
  Object.keys(state.layers).forEach(function (name) {
    if (name.indexOf("node: ") !== 0) return;
    state.layers[name]!.eachLayer(function (dot) {
      raise(dot, function (path) {
        return !path._occupied;
      });
    });
  });
}

// Ore colours follow the in-game item tints closely enough to be recognisable without shipping
// a single game asset: they are hex strings, not textures. That is also why they are the one
// family here that cannot MOVE when the audit objects -- an ore's colour is the ore's. Where
// they collide the other side moves, and where it cannot the pair is DISCHARGED in palette.ts
// with a measured warrant: coal against eight dark grounds, water against the machine blue,
// limestone against the fast belt.
export var RESOURCE_COLOUR: Record<string, string> = declareColours("markers", {
  Desc_OreIron_C: "#c8b6a6",
  Desc_OreCopper_C: "#e08a4b",
  Desc_Stone_C: "#cfcfcf",
  Desc_Coal_C: "#4c4c4c",
  Desc_OreGold_C: "#e3c74a",
  Desc_Sulfur_C: "#e8e35c",
  Desc_RawQuartz_C: "#e59ce0",
  Desc_OreBauxite_C: "#b06a4a",
  Desc_OreUranium_C: "#7ce07c",
  Desc_LiquidOil_C: "#6b4bb0",
  Desc_NitrogenGas_C: "#6ec5e0",
  Desc_Water_C: "#3f8fd0",
  Desc_SAM_C: "#b04bd0",
  Desc_Geyser_C: "#d97b4f", // synthetic label; a geyser is a placement target, not an item
});

/* How big a node dot is, in pixels, by purity -- the grammar POLE_RADIUS_PX in power.ts calls
 * "is there one here": a fixed size, because the question a dot answers is whether there is a
 * node, not how much room it takes up. */
var PURITY_RADIUS: Record<string, number> = { impure: 3, normal: 4.5, pure: 6 };

/* Tone values (docs/frontend_vision.md §19). On a dark base near-black coal vanishes, so it
 * turns light grey. A locked dot keeps its ore colour, hollow and dashed, on a dark ring: at 35%
 * opacity it was invisible on every base. */
var TONED = declareColours("markers", { "coal dark": "#8c8f96", "locked casing": "#262040" });

function nodeColour(resource: string): string {
  if (resource === "Desc_Coal_C") return toned(RESOURCE_COLOUR[resource]!, TONED["coal dark"]);
  return RESOURCE_COLOUR[resource] || "#888";
}

var drawn = { nodes: null as NodesResponse | null, pickups: null as CollectiblesResponse | null };

export function knownNodes(): NodeRow[] {
  return drawn.nodes ? drawn.nodes.nodes : [];
}

export function nodeLayers(names: string[]): string[] {
  var out: string[] = [];
  knownNodes().forEach(function (n) {
    var name = "node: " + shortResource(n.resource);
    if (names.indexOf(n.name) >= 0 && out.indexOf(name) < 0) out.push(name);
  });
  return out;
}

export function drawNodes(data: NodesResponse): void {
  drawn.nodes = data;
  paintNodes(data);
  if (data.save_error && !state.noSaves) {
    fail("nodes: " + data.save_error + "; nodes drawn, occupancy unknown");
  }
}

function paintNodes(data: NodesResponse): void {
  var byResource: Record<string, NodeRow[]> = {};
  data.nodes.forEach(function (n) {
    (byResource[n.resource] = byResource[n.resource] || []).push(n);
  });
  Object.keys(state.layers).forEach(function (name) {
    if (name.indexOf("node: ") !== 0) return;
    var still = Object.keys(byResource).some(function (resource) {
      return "node: " + shortResource(resource) === name;
    });
    if (!still) state.layers[name]!.clearLayers();
  });
  Object.keys(byResource)
    .sort()
    .forEach(function (resource) {
      var short = shortResource(resource);
      var colour = nodeColour(resource);
      // Slot 0 for every member, so the band's whole ordering is the name: these rows are
      // DATA -- one per resource this world has -- and there is no editorial order to give
      // fourteen of them that a reader could predict. Alphabetical is predictable.
      var name = "node: " + short;
      var group = layer(name, true, colour, [BAND.node, 0, name], byResource[resource]![0]!.resource_name);
      byResource[resource]!.forEach(function (n) {
        // Null is "no save read", which is not a claim either way; only false is LOCKED.
        var locked = n.reachable === false;
        if (locked) {
          L.circleMarker(xy(n), {
            radius: PURITY_RADIUS[n.purity] || 4,
            color: TONED["locked casing"],
            weight: 3,
            fillOpacity: 0,
            interactive: false,
          }).addTo(group);
        }
        var dot = L.circleMarker(xy(n), {
          radius: PURITY_RADIUS[n.purity] || 4,
          color: colour,
          weight: n.occupied ? 2 : 1,
          // Locked keeps the ore colour, hollow and dashed: a grey would sink into the ground.
          fillOpacity: locked ? 0 : n.occupied ? 0.15 : 0.75,
          dashArray: locked ? "2 3" : undefined,
        }).bindPopup(
            popup([
              // The server's word, not the class id the layer key is cut from: the popup is
              // read next to an assistant that says "Iron Ore".
              ["node", n.resource_name + " (" + n.purity + ")"],
              // Only when it is locked: "free" is already said by the occupancy row below,
              // and a row saying "reachable: yes" on 600 dots is noise.
              [
                "status",
                locked ? "locked: no extractor this world has unlocked can work it" : null,
              ],
              // Joined server-side: the raster and its orientation trap stay on one side.
              ["region", regionLine(n.region)],
              // Always present, because the absence of a row cannot be told apart from a
              // broken join -- and "no extractor known" is the join's own honest limit:
              // it resolves extractors targeting a node key, never proves a node free.
              [
                "occupancy",
                n.occupied
                  ? "occupied by " + (n.occupant_name || n.occupant_cls)
                  : data.save_error
                    ? "unknown: the save could not be read"
                    : "no extractor known here",
              ],
              ["selector", code("node:" + n.name)],
              ["at", n.x_m + ", " + n.y_m + " m"],
              [
                "pin",
                pinButtons([
                  { kind: "node", ref: { node: n.name }, text: "pin node" },
                  { kind: "field", ref: { node: n.name }, text: "pin field" },
                ]),
              ],
            ])
          );
        dot._occupied = n.occupied;
        dot.addTo(group);
      });
    });
  raiseNodeDots();
}

/* The `node: ` rows as a family: one fold, one tri-state box, one "n of 14". Declared here
 * because this is the file that makes those rows, and a prefix spelled in one file and created
 * in another is two edits for one feature. Shut by default, because fourteen rows that grow
 * with the world are a legend nobody can read, and the head's own count answers "are the ore
 * dots on?" without opening it.
 *
 * NOT the same statement as the row rank above: the rank puts these rows together and in order,
 * the section puts a head on them. */
registerSection({ key: "nodes", prefix: "node: ", title: "resource nodes", startOpen: false });

/* First of the static wave: the node dots are the layer every other placement is read against,
 * and the extractors drawn on top of them arrive with the live wave. `clears` carries the
 * trailing space because the layer names are data -- one per resource -- and "node:" alone is a
 * prefix of more than this.
 *
 * FETCH RANK, not row rank: this is the first request of the wave and its rows are the
 * second-to-last band in the control. See layers.ts. */
registerFetch<NodesResponse>({
  wave: "static",
  rank: 10,
  path: "/api/nodes",
  label: "nodes",
  clears: ["node: "],
  refilters: true,
  draw: drawNodes,
});

/* The player's last known position: the map's only you-are-here, and the reference every
 * "is this near me" judgement needs. Ring-styled so it reads as a position, not a node.
 *
 * NO ROW WHEN THERE IS NO POSITION. `layer()` both creates the control row and clears the
 * group, so calling it before the guard would give a dedicated-server save a "player" checkbox
 * that ticks nothing -- and the control is the map's legend, where an entry is a claim that the
 * thing exists. The empty case therefore reaches the registry directly: it clears a group that
 * exists and creates nothing if one does not, which is what stops a switch away from a world
 * with a pawn leaving its dot on the map.
 */
/* Near-white and warm, the one thing on the page that is not a colour ABOUT anything: it is
 * not an ore, not a tier and not a biome, so it is the value nothing else on the map spends. */
var PLAYER_COLOUR = declareColours("markers", { player: "#f5f0e8" }).player;

export function drawPlayer(p: SummaryResponse["player"]): void {
  // The object is always sent; its fields are what go null on a save with no pawn.
  if (p.x_m === null || p.y_m === null) {
    var stale = state.layers["player"];
    if (stale) stale.clearLayers();
    return;
  }
  // Chrome, not a placement: where you last stood is part of the frame the built world is
  // read against, which is why it sits with the biomes and the labels rather than with the
  // machines. Third of that band, under the two region rows it is a position within.
  var group = layer("player", true, PLAYER_COLOUR, [BAND.chrome, 20, "player"]);
  L.circleMarker(xy(p as { x_m: number; y_m: number }), {
    radius: 7,
    color: PLAYER_COLOUR,
    weight: 2,
    fillColor: "#4aa3df",
    fillOpacity: 0.9,
  })
    .bindPopup(
      popup([
        ["player", "where the player last stood in this save"],
        ["at", p.x_m + ", " + p.y_m + " m"],
      ])
    )
    .addTo(group);
}

// One colour per pickup category, so ten separate checkboxes stop drawing one indistinguishable
// teal dot. Unlisted categories share the fallback below.
//
// Handed out one per kind rather than measured against the page, except for the three the audit
// caught. The drop pod is the drab olive no network or ground spends: nearest cross-owner
// neighbour Dune Desert at dE 28.1, the three belt tones 50.8 to 53.5 away. The somersloop
// takes the rose the page's reds leave free, dE 28.3 from the stopped red and 35.9 from the
// lighter pipe tone. The hard drive is an indigo, dE 42.1 from the machine blue and 36.6 from the
// wire violet -- still blue enough to be the drive it is.
export var PICKUP_COLOUR: Record<string, string> = declareColours("markers", {
  somersloop: "#d84378",
  mercer_sphere: "#b06ae0",
  hard_drive: "#5468d4",
  loot_cache: "#d8b46e",
  crashed_drop_pod: "#838d3f",
  power_slug_blue: "#5cc8e8",
  power_slug_yellow: "#e8d55c",
  power_slug_purple: "#c85ce8",
  mushroom: "#a8c86e",
  tape_pickup: "#e09a6e",
});

/* For the categories the table above does not name, and DECLARED rather than left a bare
 * literal: a stand-in that reaches the screen is a colour on the page and belongs in the
 * comparison. `customization_unlock_pickup` draws it on the reference save. */
var PICKUP_FALLBACK = declareColours("markers", { "pickup fallback": "#7fd1b9" })[
  "pickup fallback"
];

/* The prefix that makes a layer name a pickup row, and the whole of the join between a
 * category as `/api/collectibles` names it and a row in the control. Named because the
 * fragment speaks the category and the control speaks the row, and three literals is how the
 * two drift apart. The trailing space is load-bearing; see Section.prefix. */
var PICKUP_PREFIX = "pickup: ";

var labels: Record<string, string> = {};

var hiddenKinds: string[] = [];

export function pickupName(category: string): string {
  return labels[category] || category.replace(/_/g, " ");
}

export function layerWord(name: string): string {
  return name.indexOf(PICKUP_PREFIX) === 0 ? pickupName(name.slice(PICKUP_PREFIX.length)) : name;
}

export function hiddenPickups(): string[] {
  return hiddenKinds;
}

var HIDDEN_TITLE = "not found yet: turn spoilers on in Settings";

export function markHiddenRows(): void {
  var box = control.getContainer();
  if (!box) return;
  var inputs = box.querySelectorAll<LayerInput>("input.leaflet-control-layers-selector");
  Array.prototype.forEach.call(inputs, function (input: LayerInput) {
    var hidden = hiddenKinds.indexOf(pickupCategory(state.layerName[input.layerId] || "")) >= 0;
    if (input.disabled === hidden) return;
    input.disabled = hidden;
    var row = input.closest("label");
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
var POD_CATEGORY = "crashed_drop_pod";

/* A pickup that is still there. Fill is how much is in it, and pods are the only rows that
 * vary: solid is what every other category keeps. A hollow ring is a looted pod, which is what
 * a looted pod is -- the shell still standing with the drive gone. A faint disc is a pod no
 * save has had loaded, whose flag was never read, and a solid one there would promise a hard
 * drive nothing has seen.
 *
 * Fill and not a dash: dashed already means locked, paused or planned on this page, and an
 * emptied pod is none of those. */
/* The X over a collected pickup: dark on a light base, light on a dark one. */
var PICKUP_X = declareColours("markers", {
  "pickup collected": "#2a3147",
  "pickup collected dark": "#9aa0a8",
});

function pickupX(): string {
  return toned(PICKUP_X["pickup collected"], PICKUP_X["pickup collected dark"]);
}

function pickupDot(here: L.LatLngTuple, colour: string, r: CollectibleRow): L.CircleMarker {
  var pod = r.category === POD_CATEGORY;
  var hollow = pod && r.looted === true;
  var faint = pod && r.looted === null;
  return L.circleMarker(here, {
    radius: 4,
    color: colour,
    // A 4 px disc with its fill taken away is a smudge at weight 1.
    weight: hollow ? 2 : 1,
    fillOpacity: hollow ? 0 : faint ? 0.2 : 0.7,
  });
}

/** What a pod's loot flag says, or null for a row that never had one to read. */
export function lootLine(r: CollectibleRow): string | null {
  if (r.category !== POD_CATEGORY || r.collected) return null;
  if (r.looted === true) return "looted: the hard drive is already taken";
  if (r.looted === false) return "unlooted: the hard drive is still in it";
  return "unknown: no save has loaded this pod yet";
}

export function drawCollectibles(data: CollectiblesResponse): void {
  drawn.pickups = data;
  paintPickups(data);
}

function paintPickups(data: CollectiblesResponse): void {
  var spoilers = setting("spoilers");
  hiddenKinds = [];
  data.census.forEach(function (c) {
    labels[c.category] = c.label;
    if (c.spoiler && !spoilers) hiddenKinds.push(c.category);
  });
  var byCategory: Record<string, CollectibleRow[]> = {};
  data.rows.forEach(function (r) {
    if (hiddenKinds.indexOf(r.category) < 0) (byCategory[r.category] = byCategory[r.category] || []).push(r);
  });
  hiddenKinds.forEach(function (category) {
    var name = PICKUP_PREFIX + category;
    layer(name, false, PICKUP_COLOUR[category] || PICKUP_FALLBACK, [BAND.pickup, 0, name], pickupName(category));
  });
  Object.keys(state.layers).forEach(function (name) {
    // A category this world has none of (all collected, or never present) must not keep
    // showing another world's markers under a still-ticked box.
    var stale = pickupCategory(name);
    if (stale && !byCategory[stale]) state.layers[name]!.clearLayers();
  });
  Object.keys(byCategory)
    .sort()
    .forEach(function (category) {
      // One toggleable group per category, because "show me every hard drive" and "show
      // me everything" are different questions and the second one is unreadable.
      var colour = PICKUP_COLOUR[category] || PICKUP_FALLBACK;
      // Slot 0 and alphabetical for the same reason the node rows are, one band lower: ten
      // categories of thing lying on the ground, in no order anyone could guess at.
      var name = PICKUP_PREFIX + category;
      // `layer` honours the second argument only when it CREATES the group, which is exactly
      // right: the fragment decides what a fresh row opens as, and after that the checkbox
      // the reader clicked survives every refetch.
      var wanted = state.pickups.indexOf(category) >= 0;
      var group = layer(name, wanted, colour, [BAND.pickup, 0, name], pickupName(category));
      byCategory[category]!.forEach(function (r) {
        var here = xy(r);
        var mark: L.Path = r.collected
          ? L.polyline(
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
              { color: pickupX(), weight: 1 }
            )
          : pickupDot(here, colour, r);
        mark
          .bindPopup(
            popup([
              ["pickup", pickupName(category)],
              ["name", code(r.name)],
              ["state", r.collected ? "collected" : r.observed || "unknown"],
              ["holds", lootLine(r)],
              ["at", r.x_m + ", " + r.y_m + " m"],
            ])
          )
          .addTo(group);
      });
    });
  markHiddenRows();
}

function repaint(): void {
  if (drawn.nodes) paintNodes(drawn.nodes);
  if (drawn.pickups) paintPickups(drawn.pickups);
}

onSetting(repaint);
onTone(repaint);
new MutationObserver(markHiddenRows).observe(control.getContainer()!, { childList: true, subtree: true });

/* The `pickup: ` rows as a family, on the same terms as the node one above and shut for the
 * same reason -- ten rows, nine of them normally off, and a count that says so folded. */
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
  var group = (event as L.LayersControlEvent).layer;
  var category = pickupCategory(state.layerName[L.Util.stamp(group)] || "");
  if (!category) return;
  var kept = state.pickups.filter(function (other) {
    return other !== category;
  });
  if (event.type === "overlayadd") kept.push(category);
  state.pickups = kept.sort();
}

/** The `pickups=` half of a fragment: the categories to draw, as the whole truth about which
 *  rows are ticked. Absent means none, so deleting it from the address bar puts them away. */
export function applyPickupFragment(asked: string | undefined): void {
  // Snapshotted, because every add and remove below runs notePickupChoice on the way past and
  // that handler's whole job is to rewrite the list this loop is reading.
  var want = parseList(asked);
  state.pickups = want.slice();
  batch(function () {
    Object.keys(state.layers).forEach(function (name) {
      var category = pickupCategory(name);
      if (!category) return;
      var group = state.layers[name]!;
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
