/* The Overview's map: a small Leaflet map (map/mini-map.ts) kept across renders, one dot per
 * named factory (coloured only when it needs action) and the player. A dot opens its factory;
 * anywhere else opens the main map there. It does not pan or zoom: it is a picture. */

import { L } from "../../map/leaflet";
import { hashFor, latLngOf, map as mainMap } from "../../map/map";
import { miniMap, type MiniMap } from "../../map/mini-map";
import { PLAYER_COLOUR } from "../../map/drawn/markers";
import { leaveDashThen } from "../actions";
import { factoryDash } from "../factories/address";
import { actionTone } from "../machine-states";
import { WORDS } from "../../kit/words";

import type { FactoryHealthRow } from "../../api/shapes";
import type { PointM } from "../world/model";

const FIT_MAX_ZOOM = 0;

let mini: MiniMap | null = null;
let drawn = "";
const dots = L.featureGroup();

function ink(name: string, fallback: string): string {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

function make(): MiniMap {
  const m = miniMap("ovm-live");
  m.map.zoomControl.remove();
  m.map.dragging.disable();
  m.map.scrollWheelZoom.disable();
  m.map.doubleClickZoom.disable();
  m.map.touchZoom.disable();
  m.map.boxZoom.disable();
  m.map.keyboard.disable();
  m.map.on("click", function (e: L.LeafletMouseEvent) {
    const at = e.latlng;
    leaveDashThen(() => mainMap.panTo(at, { animate: false }));
  });
  dots.addTo(m.map);
  return m;
}

function dot(row: FactoryHealthRow): L.CircleMarker {
  const tone = actionTone(row.states);
  const fill = tone === "bad" ? ink("--bad", "#e5484d") : tone === "blocked" ? ink("--blocked", "#c98a2b") : ink("--ink-soft", "#b8bec6");
  const marker = L.circleMarker(latLngOf(row.centroid_m), { radius: tone ? 6 : 4.5, color: ink("--bg-deep", "#0b0d10"), weight: 2, fillColor: fill, fillOpacity: 1 });
  marker.bindTooltip(row.name + (row.actionable ? " · " + row.actionable + " " + WORDS.needAction : ""), { direction: "top", className: "wmap-tip" });
  marker.on("click", function (e: L.LeafletMouseEvent) {
    L.DomEvent.stopPropagation(e);
    location.hash = hashFor(factoryDash(row.name, ""));
  });
  return marker;
}

function draw(m: L.Map, rows: FactoryHealthRow[], player: PointM | null): void {
  dots.clearLayers();
  const pts: L.LatLngTuple[] = [];
  /* the worst on top */
  rows
    .slice()
    .sort((a, b) => (actionTone(a.states) ? 1 : 0) - (actionTone(b.states) ? 1 : 0))
    .forEach(function (row) {
      dot(row).addTo(dots);
      pts.push(latLngOf(row.centroid_m));
    });
  if (player) {
    L.circleMarker(latLngOf(player), { radius: 6, color: PLAYER_COLOUR, weight: 2, fillColor: ink("--player-fill", "#4aa3df"), fillOpacity: 0.9, interactive: false }).addTo(dots);
    pts.push(latLngOf(player));
  }
  if (pts.length) m.fitBounds(L.latLngBounds(pts).pad(0.15), { maxZoom: FIT_MAX_ZOOM, animate: false });
}

/** Puts the live map into `slot` and draws the factories and the player when they changed. */
export function attachOverviewMap(slot: HTMLElement, rows: FactoryHealthRow[], player: PointM | null): void {
  if (!mini) mini = make();
  slot.appendChild(mini.el);
  mini.map.invalidateSize({ animate: false });
  mini.sync();
  const key = rows.map((r) => r.name + ":" + r.centroid_m.join(",") + ":" + actionTone(r.states) + ":" + r.actionable).join("|") + "|" + (player ? player.join(",") : "");
  if (key === drawn) return;
  drawn = key;
  draw(mini.map, rows, player);
}
