/* Factory labels: the map's index, the click that flies to one, and the pass that stops a
 * pile of them from being a broken index.
 *
 * `reveal` is here rather than with the layers because it is not a fact about layers -- it is
 * what the one gesture that changes the SCALE means, and the only gesture that does is a
 * click on a label. Keeping the two together is what stops "show me this factory" from
 * drifting away from the layers a factory is made of.
 */

import { code, esc, popup } from "./dom";
import { cardWithFloors } from "./floors";
import { batch } from "./layercontrol";
import { L } from "./leaflet";
import { BAND, layer } from "./layers";
import { map } from "./map";
import { registerFetch } from "./registry";
import { state } from "./state";
import { note } from "./toast";

import type { FactoriesResponse, FactoryRow, ProposalRow } from "./api-shapes";
import type { BboxM, PointM } from "./geometry";
import type { Row } from "./dom";

/* Turn on the factory-scale layers, once, and say so.
 *
 * TRIGGERED BY THE CLICK AND NOT BY THE ZOOM. A layer that ticked and unticked itself as the
 * map moved would be the only control on this page the player does not own, and the checkbox
 * would be lying about who decided. One function and one NOTE for the whole set, because the
 * player made one gesture.
 *
 * ...and ONE RENDER, which is what `batch` is for: three `addTo(map)` calls outside it are
 * three `overlayadd` events, each re-rendering the layer control twice and re-running the
 * declutter pass -- against a half-revealed map, so the "+n" badges would be computed from
 * views nobody is shown. */
export function reveal(names: string[]): void {
  var turned: string[] = [];
  batch(function () {
    names.forEach(function (name) {
      var group = state.layers[name];
      if (!group || map.hasLayer(group)) return;
      group.addTo(map);
      turned.push(name);
    });
  });
  if (!turned.length) return;
  note(
    // "a and b" for two, "a, b and c" for three -- an Oxford-less list rather than
    // "a and b and c", which is what a plain join gives once there are three of these.
    (turned.length > 1
      ? turned.slice(0, -1).join(", ") + " and " + turned[turned.length - 1]
      : turned[0]) +
      " turned on — untick " +
      (turned.length > 1 ? "those layers" : "the " + turned[0] + " layer") +
      " to hide them again"
  );
}

/* What "show me this factory" means, in layers: what a factory is MADE OF. Take the belts away
 * and the machines are a scatter of rectangles; take the pipes away and a refinery block is
 * half missing. All three are unreadable at the zoom the click starts from.
 *
 * STORAGE IS NOT THE FOURTH: containers are what is standing in a factory rather than what it
 * is made of, and the layer is a toggle a player asks for on purpose.
 *
 * POWER IS NOT EITHER, because it starts ticked -- so the only case where `reveal` would do
 * anything to it is a reader who had just unticked it, and re-ticking that box is the one
 * thing this function must never do. */
var FACTORY_LAYERS = ["machines", "belts", "pipes"];

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
  return L.marker([-centroid_m[1], centroid_m[0]], {
    icon: L.divIcon({ className: "factory-anchor", iconSize: [0, 0] }),
  });
}

/* A server bbox_m ([x_min, y_min, x_max, y_max], game axes) as Leaflet bounds. The y ends
 * swap, exactly as they do for the biome cells, because latitude is -y. */
function factoryBounds(bbox_m: BboxM | null | undefined): L.LatLngBounds | null {
  if (!bbox_m) return null;
  return L.latLngBounds(
    [-(bbox_m[3] + FACTORY_PAD_M), bbox_m[0] - FACTORY_PAD_M],
    [-(bbox_m[1] - FACTORY_PAD_M), bbox_m[2] + FACTORY_PAD_M]
  );
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
  var marker = anchorMarker(row.centroid_m);
  marker._labelWeight = row.machines || 0; // declutter priority: big factories win
  marker.bindTooltip(esc(text), {
    permanent: true,
    direction: "center",
    interactive: true, // the point of the whole function: a label you can click
    className: className,
  });
  // autoPan off: the card would otherwise shove the map sideways mid-flight, and the
  // flight already puts the factory in view.
  marker.bindPopup(cardFor(rows, factory), { autoPan: false });
  var bounds = factoryBounds(row.bbox_m);
  if (bounds) {
    var to = bounds;
    marker.on("click", function () {
      reveal(FACTORY_LAYERS);
      map.flyToBounds(to, { maxZoom: FACTORY_MAX_ZOOM });
    });
  }
  if (factory !== null) {
    var picked = factory;
    marker.on("click", function () {
      document.dispatchEvent(new CustomEvent(FACTORY_PICKED, { detail: picked }));
    });
  }
  return marker;
}

export function flyToFactory(bbox_m: BboxM | null | undefined): L.LatLngBounds | null {
  var bounds = factoryBounds(bbox_m);
  if (!bounds) return null;
  reveal(FACTORY_LAYERS);
  map.flyToBounds(bounds, { maxZoom: FACTORY_MAX_ZOOM });
  return bounds;
}

export function paddedBounds(bbox_m: BboxM | null | undefined): L.LatLngBounds | null {
  return factoryBounds(bbox_m);
}

export function drawFactories(data: FactoriesResponse): void {
  // Chrome rather than built: a label is the page's name for a place, not a thing standing
  // in it -- the same kind of row as the region names two slots up, and read the same way.
  var named = layer("factory labels", true, undefined, [BAND.chrome, 30, "factory labels"]);
  data.labels.forEach(function (f) {
    factoryAnchor(
      f,
      f.name,
      "factory-label",
      [
        ["factory", f.name],
        ["machines", f.machines],
        ["notes", f.notes],
        ["at", f.centroid_m[0] + ", " + f.centroid_m[1] + " m"],
        ["selector", code("label:" + f.name)],
      ],
      f.name
    ).addTo(named);
  });
  // Directly under the labels it is the machine-made version of, and last of the chrome:
  // a proposal names a place nobody has named yet, which is the weakest claim in the band.
  var proposed = layer("proposals", false, undefined, [BAND.chrome, 40, "proposals"]);
  data.proposals.forEach(function (p) {
    var title = "#" + p.index + " " + p.label;
    // No cohesion row: the clusterer does not compute the score yet (every proposal
    // reports 0.0), and a constant 0 reads as "this cluster scored zero".
    factoryAnchor(
      p,
      title + " (" + p.machines + ")",
      "factory-label proposal",
      [
        ["proposal", title],
        ["machines", p.machines],
        ["spread", p.spread_m + " m"],
        ["selector", code("proposal:" + p.index)],
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
  marker: L.Marker;
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

export function declutter(): void {
  var entries: Entry[] = [];
  ["factory labels", "proposals"].forEach(function (name, groupRank) {
    var group = state.layers[name];
    if (!group || !map.hasLayer(group)) return;
    group.eachLayer(function (layer) {
      var marker = layer as L.Marker;
      var tip = marker.getTooltip && marker.getTooltip();
      var node = tip && tip.getElement && tip.getElement();
      if (node) {
        entries.push({
          node: node,
          marker: marker,
          rank: groupRank,
          weight: marker._labelWeight || 0,
        });
      }
    });
  });
  entries.forEach(function (entry) {
    L.DomUtil.removeClass(entry.node, "label-hidden");
    var old = entry.node.querySelector(".label-more");
    if (old) old.parentNode!.removeChild(old);
  });
  entries.sort(function (a, b) {
    return a.rank - b.rank || b.weight - a.weight;
  });
  // Sorted before measuring, so `kept` is built in rank order and `find` below can stop at
  // the first overlap; measured before deciding, so the decisions cost no layout. See above.
  var measured = measureAll(entries);
  var kept: Kept[] = [];
  measured.forEach(function (m) {
    var r = m.rect;
    // `find`, because the highest-ranked cover owns the badge and `kept` is already in rank
    // order, so the first overlap is the right one.
    var covered = kept.find(function (k) {
      var b = k.rect;
      return r.left < b.right && b.left < r.right && r.top < b.bottom && b.top < r.bottom;
    });
    if (covered) {
      L.DomUtil.addClass(m.entry.node, "label-hidden");
      covered.hidden.push(m.entry);
    } else {
      kept.push({ rect: r, entry: m.entry, hidden: [] });
    }
  });
  kept.forEach(function (k) {
    if (k.hidden.length) badgeHidden(k.entry, k.hidden);
  });
}

/* One click on a badge is a STEP toward the group, not a teleport: two labels 40 m apart do not
 * separate until zoom 3, and flying six levels in one go from the whole-world view loses every
 * landmark on the way. If the group is still covered when the flight ends the badge is still
 * there, so the step simply repeats. */
var LABEL_STEP_ZOOM = 3;

function badgeHidden(entry: Entry, hidden: Entry[]): void {
  // Absolutely positioned, so it hangs off the label's corner without changing the
  // rectangle this same pass just measured -- a badge that grew the box would make the
  // next run hide a label because of the badge on the one before it.
  var badge = L.DomUtil.create("span", "label-more", entry.node);
  badge.textContent = "+" + hidden.length;
  badge.title =
    hidden.length === 1
      ? "1 more factory label is hidden under this one — click to zoom in"
      : hidden.length + " more factory labels are hidden here — click to zoom in";
  var points = [entry.marker.getLatLng()];
  hidden.forEach(function (other) {
    points.push(other.marker.getLatLng());
  });
  L.DomEvent.on(badge, "click", function (event) {
    // Without this the label's own click wins and flies to the covering factory's extent,
    // which is the one place the hidden names are guaranteed still to be hidden.
    L.DomEvent.stop(event);
    var bounds = L.latLngBounds(points);
    var fit = map.getBoundsZoom(bounds, false, L.point(80, 80));
    var zoom = Math.min(fit, map.getZoom() + LABEL_STEP_ZOOM);
    zoom = Math.min(Math.max(zoom, map.getZoom() + 1), map.getMaxZoom());
    map.flyTo(bounds.getCenter(), zoom);
  });
}
