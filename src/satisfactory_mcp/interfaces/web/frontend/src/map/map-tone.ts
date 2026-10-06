/* The base map's tone: light or dark, as its map type declares it (docs/maps_contract.md §3.2).
 * Overlay colours that cannot clear both kinds of ground pick a value per tone through
 * `byMapTone`, and redraw when the tone changes. Imports only listeners.ts, which imports
 * nothing, so any drawing module may use it. */

import { createListeners } from "../app/listeners";

export type MapTone = "light" | "dark";

var current: MapTone = "light";
var listeners = createListeners();

export function mapTone(): MapTone {
  return current;
}

/** The value for the base under the map now. */
export function byMapTone<T>(light: T, dark: T): T {
  return current === "dark" ? dark : light;
}

export var onMapTone = listeners.on;

/** Set by tiles.ts on every base switch; mirrored to `<html data-map-tone>` for CSS. */
export function setMapTone(next: MapTone): void {
  document.documentElement.dataset.mapTone = next;
  if (next === current) return;
  current = next;
  listeners.emit();
}
