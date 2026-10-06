/* The base map's tone: light or dark, as its map type declares it (docs/maps_contract.md §3.2).
 * Overlay colours that cannot clear both kinds of ground pick a value per tone through `toned`,
 * and redraw when the tone changes. Imports nothing, so any drawing module may use it. */

export type Tone = "light" | "dark";

var current: Tone = "light";
var listeners: Array<() => void> = [];

export function tone(): Tone {
  return current;
}

/** The value for the base under the map now. */
export function toned<T>(light: T, dark: T): T {
  return current === "dark" ? dark : light;
}

export function onTone(listener: () => void): void {
  listeners.push(listener);
}

/** Set by tiles.ts on every base switch; mirrored to `<html data-map-tone>` for CSS. */
export function setTone(next: Tone): void {
  document.documentElement.dataset.mapTone = next;
  if (next === current) return;
  current = next;
  listeners.forEach(function (listener) {
    listener();
  });
}
