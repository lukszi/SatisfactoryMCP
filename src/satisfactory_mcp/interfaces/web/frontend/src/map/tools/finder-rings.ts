/* The finder's rings on the map: its results drawn in a pane of their own, and the flights to
 * them. See docs/world-finders_contract.md §2.5. */

import { L } from "../leaflet";
import { boundsOfBbox, FIT_SNAP, flyPadded, flyToBox, flyToPoint, map, latLngOf } from "../map";
import { knownNodes } from "../drawn/markers";
import { HIGHLIGHT } from "../map-highlight";

import type { BboxM } from "../geometry";
import type { FoundField, RunRow } from "../../api/shapes";
import type { FinderResults } from "./finder";

/** The zoom a flight to one row lands at, at least. */
export const POINT_ZOOM = 1;

const ALL_ZOOM = 1;

const ALL_PAD = 0.05;

const pane = map.createPane("finder");
pane.style.zIndex = "445";
pane.style.pointerEvents = "none";
const renderer = L.svg({ pane: "finder", padding: 0.5 });
const group = L.layerGroup();

export function ringPoint(at: { x_m: number; y_m: number }, selected: boolean): void {
  L.circleMarker(latLngOf(at), {
    radius: selected ? 11 : 7,
    color: HIGHLIGHT,
    weight: selected ? 4 : 2,
    fill: false,
    renderer: renderer,
    pane: "finder",
    interactive: false,
  }).addTo(group);
}

function outlineBox(bbox: BboxM): void {
  L.rectangle(boundsOfBbox(bbox), {
    color: HIGHLIGHT,
    weight: 2,
    dashArray: "6 4",
    fill: false,
    renderer: renderer,
    pane: "finder",
    interactive: false,
  }).addTo(group);
}

function latlngs(run: RunRow): L.LatLngTuple[][] {
  return run.lines_m.map(function (points) {
    return points.map(function (point) {
      return latLngOf(point);
    });
  });
}

function drawRunLine(run: RunRow, selected: boolean): L.Polyline {
  return L.polyline(latlngs(run), {
    color: HIGHLIGHT,
    weight: selected ? 5 : 3,
    opacity: 0.8,
    renderer: renderer,
    pane: "finder",
    interactive: false,
  }).addTo(group);
}

function runBounds(run: RunRow): L.LatLngBounds | null {
  const points = ([] as L.LatLngTuple[]).concat.apply([], latlngs(run));
  return points.length ? L.latLngBounds(points) : null;
}

/** Ring every node a field is made of, which only the drawn node dots know the places of. */
function ringFieldMembers(field: FoundField): void {
  const wanted: Record<string, boolean> = {};
  field.members.forEach(function (leaf) {
    wanted[leaf] = true;
  });
  knownNodes().forEach(function (node) {
    if (wanted[node.name]) ringPoint(node, false);
  });
}

/** Take every ring off the map. */
export function clearRings(): void {
  group.clearLayers();
}

/** The rings' layer on the map, once something is ringed. */
export function showRings(): void {
  if (!map.hasLayer(group)) group.addTo(map);
}

/** Ring every row, the selected one boldest; the bounds of them all. */
export function drawResults(results: FinderResults, selectedIndex: number): L.LatLngBounds | null {
  clearRings();
  let bounds: L.LatLngBounds | null = null;
  function grow(more: L.LatLngBounds | L.LatLngTuple): void {
    if (bounds) bounds.extend(more);
    else bounds = more instanceof L.LatLngBounds ? L.latLngBounds(more.getSouthWest(), more.getNorthEast()) : L.latLngBounds([more, more]);
  }
  if (results.kind === "nodes" || results.kind === "pickups" || results.kind === "sites") {
    (results.rows as { x_m: number; y_m: number }[]).forEach(function (row, i) {
      ringPoint(row, i === selectedIndex);
      grow(latLngOf(row));
    });
  } else if (results.kind === "fields") {
    results.rows.forEach(function (field, i) {
      outlineBox(field.bbox_m);
      if (i === selectedIndex) ringFieldMembers(field);
      grow(boundsOfBbox(field.bbox_m));
    });
  } else {
    results.rows.forEach(function (run, i) {
      drawRunLine(run, i === selectedIndex);
      const runBox = runBounds(run);
      if (runBox) grow(runBox);
    });
  }
  showRings();
  return bounds;
}

/** Fly to the selected row, or to every row when none is. */
export function flyToResults(results: FinderResults, selectedIndex: number, bounds: L.LatLngBounds | null): void {
  const row = selectedIndex >= 0 ? results.rows[selectedIndex] : undefined;
  if (row && results.kind === "fields") {
    flyToBox((row as FoundField).bbox_m, { maxZoom: POINT_ZOOM });
    return;
  }
  if (row && results.kind === "runs") {
    const runBox = runBounds(row as RunRow);
    if (runBox) flyPadded(runBox.pad(0.2), POINT_ZOOM);
    return;
  }
  if (row) {
    const at = row as { x_m: number; y_m: number };
    flyToPoint(latLngOf(at), Math.max(map.getZoom(), POINT_ZOOM));
    return;
  }
  if (bounds) flyPadded(bounds.pad(ALL_PAD), ALL_ZOOM, FIT_SNAP);
}
