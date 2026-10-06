/* The resource node dots and where the player last stood: the points every placement is read
 * against.
 *
 * `raiseNodeDots` lives with the dots it raises. Everything clickable is on one canvas,
 * hit-testing is draw order, and an extractor is drawn exactly on the node it drains -- so the
 * node dots have to be raised after ANY draw. The pickups are pickups.ts.
 */

import { code, popup } from "../../kit/dom";
import { regionLine, shortResource } from "../../kit/format";
import { registerSection } from "../layercontrol/control";
import { L } from "../leaflet";
import { BAND, clearedLayer } from "../layers";
import { latLngOf } from "../map";
import { declareColours } from "../palette";
import { pinButtons } from "../../chat/pins";
import { registerFetch } from "../../app/registry";
import { onSetting } from "../../app/settings";
import { state } from "../../app/state";
import { fail } from "../../kit/toast";
import { byMapTone, onMapTone } from "../map-tone";

import type { OnMap } from "../leaflet-private";

import type { NodeRow, NodesResponse, SummaryResponse } from "../../api/shapes";

/* Raised explicitly after either draw, because otherwise whichever of /api/nodes and
 * /api/machines resolved last decides who takes the click on an occupied node. The dot wins;
 * the extractor keeps the rest of its rectangle. */
function bringToFrontIf(layer: L.Layer, when: (dot: L.Path & OnMap) => boolean): void {
  const path = layer as L.Path & OnMap;
  if (path.bringToFront && path._map && when(path)) path.bringToFront();
}

export function raiseNodeDots() {
  const extractors = state.layers["extractors"];
  if (extractors) {
    extractors.eachLayer(function (piece) {
      bringToFrontIf(piece, function () {
        return true;
      });
    });
  }
  Object.keys(state.layers).forEach(function (name) {
    if (name.indexOf("node: ") !== 0) return;
    state.layers[name]!.eachLayer(function (dot) {
      bringToFrontIf(dot, function (path) {
        return !path._occupied;
      });
    });
  });
}

// The in-game item tints, as hex strings rather than game assets: the one family that cannot
// move when the audit objects (docs/frontend_palette.md).
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

/* How big a node dot is, in pixels, by purity -- the grammar POLE_RADIUS_PX in power-wires.ts calls
 * "is there one here": a fixed size, because the question a dot answers is whether there is a
 * node, not how much room it takes up. */
var PURITY_RADIUS: Record<string, number> = { impure: 3, normal: 4.5, pure: 6 };

/* Tone values (docs/frontend_vision.md §19). On a dark base near-black coal vanishes, so it
 * turns light grey. A locked dot keeps its ore colour, hollow and dashed, on a dark ring: at 35%
 * opacity it was invisible on every base. */
var TONED = declareColours("markers", { "coal dark": "#8c8f96", "locked casing": "#262040" });

function nodeColour(resource: string): string {
  if (resource === "Desc_Coal_C") return byMapTone(RESOURCE_COLOUR[resource]!, TONED["coal dark"]);
  return RESOURCE_COLOUR[resource] || "#888";
}

/** The last reply drawn, kept so a setting or a base-map tone can repaint without a fetch. */
var lastPayload: NodesResponse | null = null;

export function knownNodes(): NodeRow[] {
  return lastPayload ? lastPayload.nodes : [];
}

export function nodeLayers(names: string[]): string[] {
  const layers: string[] = [];
  knownNodes().forEach(function (node) {
    const name = "node: " + shortResource(node.resource);
    if (names.indexOf(node.name) >= 0 && layers.indexOf(name) < 0) layers.push(name);
  });
  return layers;
}

export function drawNodes(data: NodesResponse): void {
  lastPayload = data;
  paintNodes(data);
  if (data.save_error && !state.noSaves) {
    fail("nodes: " + data.save_error + "; nodes drawn, occupancy unknown");
  }
}

function paintNodes(data: NodesResponse): void {
  const byResource: Record<string, NodeRow[]> = {};
  data.nodes.forEach(function (node) {
    (byResource[node.resource] = byResource[node.resource] || []).push(node);
  });
  Object.keys(state.layers).forEach(function (name) {
    if (name.indexOf("node: ") !== 0) return;
    const stillPresent = Object.keys(byResource).some(function (resource) {
      return "node: " + shortResource(resource) === name;
    });
    if (!stillPresent) state.layers[name]!.clearLayers();
  });
  Object.keys(byResource)
    .sort()
    .forEach(function (resource) {
      const colour = nodeColour(resource);
      // Slot 0 for every member, so the band's whole ordering is the name: these rows are
      // DATA -- one per resource this world has -- and there is no editorial order to give
      // them that a reader could predict. Alphabetical is predictable.
      const name = "node: " + shortResource(resource);
      const group = clearedLayer(name, {
        on: true,
        colour: colour,
        rank: [BAND.node, 0, name],
        title: byResource[resource]![0]!.resource_name,
      });
      byResource[resource]!.forEach(function (node) {
        nodeDot(node, colour, !!data.save_error, group);
      });
    });
  raiseNodeDots();
}

/* One node: its dot, and a dark ring under it when it is locked. Null `reachable` is "no save
 * read", which is not a claim either way; only false is LOCKED. */
function nodeDot(n: NodeRow, colour: string, saveUnread: boolean, group: L.LayerGroup): void {
  const locked = n.reachable === false;
  if (locked) {
    L.circleMarker(latLngOf(n), {
      radius: PURITY_RADIUS[n.purity] || 4,
      color: TONED["locked casing"],
      weight: 3,
      fillOpacity: 0,
      interactive: false,
    }).addTo(group);
  }
  const dot = L.circleMarker(latLngOf(n), {
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
      // and a row saying "reachable: yes" on every dot is noise.
      ["status", locked ? "locked: no extractor this world has unlocked can work it" : null],
      // Joined server-side: the raster and its orientation trap stay on one side.
      ["region", regionLine(n.region)],
      // Always present, because the absence of a row cannot be told apart from a
      // broken join -- and "no extractor known" is the join's own honest limit:
      // it resolves extractors targeting a node key, never proves a node free.
      [
        "occupancy",
        n.occupied
          ? "occupied by " + (n.occupant_name || n.occupant_cls)
          : saveUnread
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
}

function repaint(): void {
  if (lastPayload) paintNodes(lastPayload);
}

onSetting(repaint);
onMapTone(repaint);

/* The `node: ` rows as a family: one fold, one tri-state box, one "n of m". Declared here
 * because this is the file that makes those rows, and a prefix spelled in one file and created
 * in another is two edits for one feature. Shut by default, because rows that grow with the
 * world are a legend nobody can read, and the head's own count answers "are the ore dots on?"
 * without opening it.
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
 * second-to-last band in the control. See "Two ranks" in frontend/README.md. */
registerFetch<NodesResponse>({
  wave: "static",
  rank: 10,
  path: "/api/nodes",
  label: "nodes",
  clears: ["node: "],
  refilters: true,
  draw: drawNodes,
});

/* Near-white and warm, the one thing on the page that is not a colour ABOUT anything: it is
 * not an ore, not a tier and not a biome, so it is the value nothing else on the map spends. */
var PLAYER_COLOUR = declareColours("markers", { player: "#f5f0e8" }).player;

/* The player's last known position: the map's only you-are-here, and the reference every
 * "is this near me" judgement needs. Ring-styled so it reads as a position, not a node.
 *
 * NO ROW WHEN THERE IS NO POSITION. `clearedLayer()` both creates the control row and clears
 * the group, so calling it before the guard would give a dedicated-server save a "player"
 * checkbox that ticks nothing -- and the control is the map's legend, where an entry is a claim
 * that the thing exists. The empty case therefore reaches the registry directly: it clears a
 * group that exists and creates nothing if one does not, which is what stops a switch away
 * from a world with a pawn leaving its dot on the map.
 */
export function drawPlayer(p: SummaryResponse["player"]): void {
  // The object is always sent; its fields are what go null on a save with no pawn.
  if (p.x_m === null || p.y_m === null) {
    const stale = state.layers["player"];
    if (stale) stale.clearLayers();
    return;
  }
  // Chrome, not a placement: where you last stood is part of the frame the built world is
  // read against, which is why it sits with the biomes and the labels rather than with the
  // machines. Third of that band, under the two region rows it is a position within.
  const group = clearedLayer("player", { on: true, colour: PLAYER_COLOUR, rank: [BAND.chrome, 20, "player"] });
  L.circleMarker(latLngOf(p as { x_m: number; y_m: number }), {
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
