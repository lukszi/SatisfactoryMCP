/* Factory labels: the map's index, the click that flies to one, and the pass that stops a
 * pile of them from being a broken index.
 *
 * `reveal` is here rather than with the layers because it is not a fact about layers -- it is
 * what the one gesture that changes the SCALE means, and the only gesture that does is a
 * click on a label. Keeping the two together is what stops "show me this factory" from
 * drifting away from the layers a factory is made of.
 */

import { code, esc, popup } from "../kit/dom";
import { cardWithFloors } from "./floors/floors";
import { coords, joinWithConjunction } from "../kit/format";
import { L } from "./leaflet";
import { BAND, BUILT_AREA_LAYERS, clearedLayer, turnOnLayers } from "./layers";
import { layerDisplayName } from "./drawn/pickups";
import { boundsOfBbox, flyPadded, latLngOf, map } from "./map";
import { regionLabels } from "./regions";
import { registerFetch } from "../app/registry";
import { state } from "../app/state";
import { notify } from "../kit/toast";
import { WORDS } from "../kit/words";

import type { FactoriesResponse, FactoryRow, ProposalRow } from "../api/shapes";
import type { BboxM, PointM } from "./geometry";
import type { Row } from "../kit/dom";

/* Turn these layers on, once, and say so. TRIGGERED BY THE CLICK AND NOT BY THE ZOOM: a layer
 * that ticked itself as the map moved would make the checkbox lie about who decided. One toast
 * for the set, because the player made one gesture. */
export function reveal(names: string[]): void {
  const turned = turnOnLayers(names).map(layerDisplayName);
  if (!turned.length) return;
  notify(
    joinWithConjunction(turned, "and") +
      " turned on; untick " +
      (turned.length > 1 ? "those layers" : "the " + turned[0] + " layer") +
      " to hide them again"
  );
}

export var FACTORY_PICKED = "factory-picked";

/* Factory labels: a permanent tooltip has to hang off something, and that something is a
 * zero-sized divIcon. The DEFAULT icon would request two image files and append 25x41 px of
 * <img> to the marker pane at zIndex 600, above the canvas everything clickable is drawn on --
 * invisible at opacity 0 and still a pointer target, so every label would punch a hole in the
 * map.
 *
 * The tooltip is `interactive`, which is what turns a label into the map's index: click it and
 * the map flies to the factory's own extent (the server's `bbox_m`, because the client is sent
 * a machine COUNT and never the machines) and opens the card. Flying to a bounding box rather
 * than a fixed zoom at the centroid is what makes one click work for a 40 m outpost and a 600 m
 * base alike.
 */

// Breathing room around a factory's extent, metres. A one-machine factory has a
// zero-size box, and flying to a zero-size box means flying to maxZoom on top of it.
var FACTORY_PAD_M = 40;

// Never closer than this when flying to a factory: a small cluster filling the screen
// loses the surroundings that say where it is.
var FACTORY_MAX_ZOOM = 1;

function anchorMarker(centroid_m: PointM): L.Marker {
  return L.marker(latLngOf(centroid_m), {
    icon: L.divIcon({ className: "factory-anchor", iconSize: [0, 0] }),
    keyboard: false,
  });
}

/** A server bbox_m as Leaflet bounds, with the breathing room every factory flight gets. */
export function paddedBounds(bbox_m: BboxM | null | undefined): L.LatLngBounds | null {
  return bbox_m ? boundsOfBbox(bbox_m, FACTORY_PAD_M) : null;
}

/** Fly to a built area and turn on what it is made of; null when there is no box to fly to. */
export function flyToBuiltArea(bbox_m: BboxM | null | undefined): L.LatLngBounds | null {
  const bounds = paddedBounds(bbox_m);
  if (!bounds) return null;
  reveal(BUILT_AREA_LAYERS);
  flyPadded(bounds, FACTORY_MAX_ZOOM);
  return bounds;
}

/* The card, and the one action on it. `factory` is a NAME for a named factory and null for a
 * proposal: a floor view is asked for by selector, and a proposal is a cluster this page
 * invented rather than something the player named, so only the named ones get the action. */
function cardFor(rows: Row[], factory: string | null): string | HTMLElement {
  return factory === null ? popup(rows) : cardWithFloors(rows, factory);
}

function factoryAnchor(
  row: FactoryRow | ProposalRow,
  text: string,
  className: string,
  rows: Row[],
  factory: string | null
): L.Marker {
  const marker = anchorMarker(row.centroid_m);
  marker._labelWeight = row.machines || 0; // declutter priority: big factories win
  marker._labelName = factory || "";
  marker.bindTooltip(esc(text), {
    permanent: true,
    direction: "center",
    interactive: true, // the point of the whole function: a label you can click
    className: className,
  });
  // autoPan off: the card would otherwise shove the map sideways mid-flight, and the
  // flight already puts the factory in view.
  marker.bindPopup(cardFor(rows, factory), { autoPan: false });
  const bbox = row.bbox_m;
  if (bbox) {
    marker.on("click", function () {
      flyToBuiltArea(bbox);
    });
  }
  if (factory !== null) {
    marker.on("click", function () {
      prioritiseLabel(factory);
      document.dispatchEvent(new CustomEvent(FACTORY_PICKED, { detail: factory }));
    });
  }
  return marker;
}

/** The factory whose label outranks every other one, because the reader just picked it. */
var prioritisedLabel = "";

export function prioritiseLabel(name: string): void {
  if (prioritisedLabel === name) return;
  prioritisedLabel = name;
  declutter();
}

export function drawFactories(data: FactoriesResponse): void {
  // Chrome rather than built: a label is the page's name for a place, not a thing standing
  // in it -- the same kind of row as the region names two slots up, and read the same way.
  const named = clearedLayer("factory labels", { on: true, rank: [BAND.chrome, 30, "factory labels"] });
  data.labels.forEach(function (factoryRow) {
    factoryAnchor(
      factoryRow,
      factoryRow.name,
      "factory-label",
      [
        ["factory", factoryRow.name],
        ["machines", factoryRow.machines],
        ["notes", factoryRow.notes],
        ["at", coords(factoryRow.centroid_m[0], factoryRow.centroid_m[1])],
        ["selector", code("label:" + factoryRow.name)],
      ],
      factoryRow.name
    ).addTo(named);
  });
  // Directly under the labels it is the machine-made version of, and last of the chrome:
  // a proposal names a place nobody has named yet, which is the weakest claim in the band.
  const proposed = clearedLayer("proposals", {
    on: false,
    rank: [BAND.chrome, 40, "proposals"],
    title: WORDS.unnamedClusters,
  });
  data.proposals.forEach(function (proposal) {
    // No cohesion row: the clusterer does not compute the score yet (every proposal
    // reports 0.0), and a constant 0 reads as "this cluster scored zero".
    factoryAnchor(
      proposal,
      proposal.label + " (" + proposal.machines + ")",
      "factory-label proposal",
      [
        [WORDS.unnamedCluster, proposal.label],
        ["machines", proposal.machines],
        ["spread", proposal.spread_m + " m"],
        ["selector", code("proposal:" + proposal.index)],
      ],
      null
    ).addTo(proposed);
  });
  declutter();
}

/* Last of the static wave, which is the one ordering decision in this file: `declutter` decides
 * which labels fit by measuring screen rectangles, so it wants a drawn map to measure on. Two
 * layers under one entry because /api/factories answers with both. */
registerFetch<FactoriesResponse>({
  wave: "static",
  rank: 70,
  path: "/api/factories",
  label: "factories",
  clears: ["factory labels", "proposals"],
  refilters: true,
  draw: drawFactories,
});

/* Labels are the map's index, so a pile of them is a broken index: overlapping tooltips give
 * every click to whichever was added last, and the largest factory opens a 2-machine outpost.
 *
 * THE RULE: show every label that fits, hide what it covers. Named labels outrank proposals,
 * bigger factories outrank smaller, and the test is the labels' actual screen rectangles,
 * re-run whenever zoom or the ticked layers change. A hidden label reappears the moment there
 * is room, and every VISIBLE label is clickable -- no dead-looking clickables, no invisible
 * click thieves.
 *
 * Applied out loud, because a player who named a factory cannot tell hidden from lost: every
 * label that covered something wears a "+n" badge counting the names folded under it, and
 * clicking it steps the map toward the group. */
/* One label, measured. `node` is the tooltip's own element -- the thing with a screen
 * rectangle -- and `marker` is what a badge click has to fly to. */
interface Entry {
  node: HTMLElement;
  marker: L.Marker | null;
  rank: number;
  weight: number;
}

/** A label that survived the pass, its rectangle, and everything it covered. */
interface Kept {
  rect: DOMRect;
  entry: Entry;
  hidden: Entry[];
}

/** One label and the rectangle it was measured at, before anything was hidden. */
interface Measured {
  entry: Entry;
  rect: DOMRect;
}

/** Every label on a ticked layer: factories by name, proposals after them, region names last. */
function collectLabelEntries(): Entry[] {
  const entries: Entry[] = [];
  ["factory labels", "proposals"].forEach(function (name, groupRank) {
    const group = state.layers[name];
    if (!group || !map.hasLayer(group)) return;
    group.eachLayer(function (layer) {
      const marker = layer as L.Marker;
      const tip = marker.getTooltip && marker.getTooltip();
      const node = tip && tip.getElement && tip.getElement();
      if (node) {
        entries.push({
          node: node,
          marker: marker,
          rank: prioritisedLabel && marker._labelName === prioritisedLabel ? -1 : groupRank,
          weight: marker._labelWeight || 0,
        });
      }
    });
  });
  const names = state.layers["region names"];
  if (names && map.hasLayer(names)) {
    regionLabels().forEach(function (node) {
      entries.push({ node: node, marker: null, rank: 2, weight: 0 });
    });
  }
  return entries;
}

/** Undo the last pass: every label shown, unmarked, and without a badge. */
function resetLabelDecorations(entries: Entry[]): void {
  entries.forEach(function (entry) {
    L.DomUtil.removeClass(entry.node, "label-hidden");
    L.DomUtil.removeClass(entry.node, "label-chosen");
    const old = entry.node.querySelector(".label-more");
    if (old) old.parentNode!.removeChild(old);
  });
}

/* READ EVERYTHING, THEN WRITE, which is the only reason this is its own function.
 *
 * `getBoundingClientRect` cannot be answered while a style change is pending, so hiding inside
 * the measuring loop makes every rectangle after the first hidden label cost a forced reflow.
 * Measuring first costs exactly one flush and answers the same question with the same numbers:
 * nothing in the overlap test depends on what the loop has already hidden, because a hidden
 * label is never a cover -- only `kept` is. */
function measureAll(entries: Entry[]): Measured[] {
  return entries.map(function (entry): Measured {
    return { entry: entry, rect: entry.node.getBoundingClientRect() };
  });
}

/* Keep every label no higher-ranked kept label overlaps, in rank order, and hide the rest. The
 * first overlap found is the highest-ranked cover, so it owns the badge. */
function keepNonOverlapping(measured: Measured[]): Kept[] {
  const kept: Kept[] = [];
  measured.forEach(function (label) {
    const rect = label.rect;
    const cover = kept.find(function (other) {
      const box = other.rect;
      return rect.left < box.right && box.left < rect.right && rect.top < box.bottom && box.top < rect.bottom;
    });
    if (cover) {
      L.DomUtil.addClass(label.entry.node, "label-hidden");
      if (label.entry.marker) cover.hidden.push(label.entry);
    } else {
      kept.push({ rect: rect, entry: label.entry, hidden: [] });
    }
  });
  return kept;
}

export function declutter(): void {
  const entries = collectLabelEntries();
  resetLabelDecorations(entries);
  entries.sort(function (a, b) {
    return a.rank - b.rank || b.weight - a.weight;
  });
  // Sorted before measuring, so the kept list is built in rank order.
  keepNonOverlapping(measureAll(entries)).forEach(function (label) {
    if (label.entry.rank < 0) L.DomUtil.addClass(label.entry.node, "label-chosen");
    if (label.hidden.length) addHiddenCountBadge(label.entry, label.hidden);
  });
}

/* One click on a badge is a STEP toward the group, not a teleport: two labels 40 m apart do not
 * separate until zoom 3, and flying six levels in one go from the whole-world view loses every
 * landmark on the way. If the group is still covered when the flight ends the badge is still
 * there, so the step simply repeats. */
var LABEL_STEP_ZOOM = 2;

function addHiddenCountBadge(entry: Entry, hidden: Entry[]): void {
  // Absolutely positioned, so it hangs off the label's corner without changing the
  // rectangle this same pass just measured -- a badge that grew the box would make the
  // next run hide a label because of the badge on the one before it.
  const badge = L.DomUtil.create("span", "label-more", entry.node);
  badge.textContent = "+" + hidden.length;
  badge.title =
    hidden.length === 1
      ? "1 more factory label is hidden under this one; click to zoom in"
      : hidden.length + " more factory labels are hidden here; click to zoom in";
  const points = [entry.marker!.getLatLng()];
  hidden.forEach(function (other) {
    points.push(other.marker!.getLatLng());
  });
  L.DomEvent.on(badge, "click", function (event) {
    // Without this the label's own click wins and flies to the covering factory's extent,
    // which is the one place the hidden names are guaranteed still to be hidden.
    L.DomEvent.stop(event);
    const bounds = L.latLngBounds(points);
    const fit = map.getBoundsZoom(bounds, false, L.point(80, 80));
    let zoom = Math.min(fit, map.getZoom() + LABEL_STEP_ZOOM);
    zoom = Math.min(Math.max(zoom, map.getZoom() + 1), map.getMaxZoom());
    map.flyTo(bounds.getCenter(), zoom);
  });
}
