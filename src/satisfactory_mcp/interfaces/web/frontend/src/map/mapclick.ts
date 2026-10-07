/* A plain click on empty map ground: clears the selection, or selects that spot as a point
 * while a planner context asks for one. See docs/frontend_vision.md §16.4. */

import { coords } from "../kit/format";
import { L } from "./leaflet";
import { gameXY, map } from "./map";
import { showPoint } from "./map-highlight";
import { select } from "../app/selection";

interface Marked extends Event {
  _onLayer?: boolean;
}

const pointOwners: Record<string, boolean> = {};

/** A planner context that wants empty clicks as points claims them here, by name. */
export function clicksPickPoints(owner: string, on: boolean): void {
  if (on) pointOwners[owner] = true;
  else delete pointOwners[owner];
}

function onLayer(e: L.LeafletMouseEvent): void {
  const dom = e.originalEvent as Marked | undefined;
  if (dom) dom._onLayer = true;
}

L.Path.addInitHook(function (this: L.Path) {
  this.on("click", onLayer);
});
L.Marker.addInitHook(function (this: L.Marker) {
  this.on("click", onLayer);
});

function clicked(e: L.LeafletMouseEvent): void {
  const dom = e.originalEvent as Marked | undefined;
  if (!dom || dom._onLayer || map.getContainer().classList.contains("map-picking")) return;
  if (Object.keys(pointOwners).length) {
    const at = gameXY(e.latlng);
    const x = Math.round(at[0]);
    const y = Math.round(at[1]);
    showPoint(x, y, { label: coords(x, y), stay: true });
  } else select(null);
}

export function listenForEmptyClicks(): void {
  map.on("click", clicked);
}
