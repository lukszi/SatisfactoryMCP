/* The one highlight on the map: a dashed outline round a box, or a ring on a point, in the
 * colour every "show it on the map" shares. Drawing a new one clears the last. */

import { esc } from "../kit/dom";
import { flyToBuiltArea, reveal } from "./labels";
import { L } from "./leaflet";
import { flyToPoint, latLngOf, map } from "./map";
import { declareColours } from "./palette";
import { machineSelection, select } from "../app/selection";

import type { Selection } from "../app/selection";

export var HIGHLIGHT = declareColours("map-highlight",{ highlight: "#ff4fd8" }).highlight;

var MACHINE_ZOOM = 2;

var highlightLayer = L.layerGroup();

/* The selection key the ring stands on, so a selection that is already ringed is not
 * redrawn. */
export var ringedKey = "";

export function outline(bounds: L.LatLngBounds): void {
  clearMark();
  L.rectangle(bounds, {
    color: HIGHLIGHT,
    weight: 2,
    dashArray: "6 4",
    fill: false,
    interactive: false,
  }).addTo(highlightLayer);
  if (!map.hasLayer(highlightLayer)) highlightLayer.addTo(map);
}

export function clearMark(): void {
  highlightLayer.clearLayers();
  ringedKey = "";
}

export function ringAt(x_m: number, y_m: number, label: string, key: string): void {
  clearMark();
  ringedKey = key;
  var ring = L.circleMarker(latLngOf([x_m, y_m]), {
    radius: 14,
    color: HIGHLIGHT,
    weight: 3,
    fill: false,
    interactive: false,
  });
  if (label) ring.bindTooltip(esc(label), { permanent: true, direction: "right", offset: [14, 0], className: "pin-label" });
  ring.addTo(highlightLayer);
  if (!map.hasLayer(highlightLayer)) highlightLayer.addTo(map);
}

export function selectAndRing(x_m: number, y_m: number, label?: string, stay?: boolean, as?: Selection): void {
  var s = as || { kind: "point", key: x_m + "," + y_m, label: label || "a point", x_m: x_m, y_m: y_m };
  select(s);
  ringAt(x_m, y_m, as ? s.label : label || "", s.kind + ":" + s.key);
  if (!stay) flyToPoint(latLngOf([x_m, y_m]), Math.max(map.getZoom(), MACHINE_ZOOM));
}

export function showPoint(x_m: number, y_m: number, options?: { label?: string; layers?: string[]; stay?: boolean }): void {
  if (options && options.layers) reveal(options.layers);
  selectAndRing(x_m, y_m, options && options.label, options && options.stay);
}

export function showMachine(instance: string, name: string, x_m: number, y_m: number, options?: { layers?: string[]; stay?: boolean }): void {
  if (options && options.layers) reveal(options.layers);
  selectAndRing(x_m, y_m, name, options && options.stay, machineSelection(instance, name, x_m, y_m));
}

export function showBox(bbox_m: [number, number, number, number], options?: { layers?: string[] }): void {
  if (options && options.layers) reveal(options.layers);
  var bounds = flyToBuiltArea(bbox_m);
  if (bounds) outline(bounds);
}
