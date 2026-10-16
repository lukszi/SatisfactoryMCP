/* The World page's map: one small Leaflet map (map/mini-map.ts) kept across renders, drawing
 * only the current results, fitted to them when they change, and lit or picked from the rows.
 * The node and pickup colours and sizes are the main map's. */

import { L } from "../../map/leaflet";
import { latLngOf } from "../../map/map";
import { miniMap, type MiniMap } from "../../map/mini-map";
import { nodeColour, PLAYER_COLOUR, PURITY_RADIUS } from "../../map/drawn/markers";
import { pickupColour } from "../../map/drawn/pickups";
import { html, type Safe } from "../../kit/html";

import type { Mark, PointM } from "./model";

export interface MapScene {
  /** Changes when the results change; the map refits then and only then. */
  key: string;
  marks: Mark[];
  player: PointM | null;
  /** Fit the player in too: the list is measured from them. */
  fitPlayer: boolean;
  sel: string;
}

export interface MapHooks {
  pick(key: string): void;
  hover(key: string | null): void;
}

const FADE = 0.35;
const FIT_MAX_ZOOM = 1;

let mini: MiniMap | null = null;
let drawn = "";
let shownSel = "";
let lit: string | null = null;
let hooks: MapHooks | null = null;
const marksLayer = L.featureGroup();
let byKey: Record<string, L.Path[]> = {};

function ink(name: string, fallback: string): string {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

/* A path's own look, kept so lighting can be undone. */
interface Look {
  color: string;
  weight: number;
  opacity: number;
  fillOpacity: number;
}

const LOOK = new WeakMap<L.Path, Look>();

function keep(path: L.Path, key: string): L.Path {
  const o = path.options;
  LOOK.set(path, { color: o.color || "", weight: o.weight || 1, opacity: o.opacity ?? 1, fillOpacity: o.fillOpacity ?? 0.2 });
  (byKey[key] = byKey[key] || []).push(path);
  path.on("click", () => hooks?.pick(key));
  path.on("mouseover", () => hooks?.hover(key));
  path.on("mouseout", () => hooks?.hover(null));
  return path;
}

function tip(path: L.Path, text: string): L.Path {
  return path.bindTooltip(text, { direction: "top", className: "wmap-tip" });
}

function drawMark(mk: Mark): void {
  const soft = ink("--ink-soft", "#b8bec6");
  const cool = ink("--ink-cool", "#cfd8e0");
  if (mk.t === "label") {
    L.tooltip({ permanent: true, direction: "center", className: "wmap-label" }).setLatLng(latLngOf([mk.x, mk.y])).setContent(mk.text).addTo(marksLayer);
    return;
  }
  if (mk.t === "factory") {
    const r = 40;
    L.rectangle(L.latLngBounds(latLngOf([mk.x - r, mk.y - r]), latLngOf([mk.x + r, mk.y + r])), { color: ink("--ink-hi", "#fff"), weight: 2, fill: false, interactive: false })
      .bindTooltip(mk.text, { permanent: true, direction: "top", className: "wmap-label" })
      .addTo(marksLayer);
    return;
  }
  let path: L.Path;
  if (mk.t === "node") {
    const colour = nodeColour(mk.resource);
    path = L.circleMarker(latLngOf([mk.x, mk.y]), { radius: PURITY_RADIUS[mk.purity] || 4, color: colour, weight: mk.free ? 1 : 2, fillColor: colour, fillOpacity: mk.free ? 0.8 : 0 });
  } else if (mk.t === "pick") {
    const colour = pickupColour(mk.category);
    path = L.circleMarker(latLngOf([mk.x, mk.y]), { radius: 4.5, color: colour, weight: mk.collected ? 2 : 1, fillColor: colour, fillOpacity: mk.collected ? 0 : 0.8 });
  } else if (mk.t === "hull") {
    const pts = mk.pts.map((p) => latLngOf(p));
    path = pts.length > 2
      ? L.polygon(pts, { color: soft, weight: 12, opacity: 0.16, fillOpacity: 0.16, lineJoin: "round" })
      : L.polyline(pts.length > 1 ? pts : [pts[0]!, pts[0]!], { color: soft, weight: 16, opacity: 0.16, lineCap: "round" });
  } else if (mk.t === "site") {
    path = L.circle(latLngOf([mk.x, mk.y]), { radius: mk.r_m, color: soft, weight: 1, opacity: 0.5, fillOpacity: 0.16 });
    path.bindTooltip(String(mk.rank), { permanent: true, direction: "center", className: "wmap-rank" });
  } else if (mk.t === "line") {
    path = L.polyline(mk.lines.map((line) => line.map((p) => latLngOf(p))), { color: cool, weight: 3, opacity: 0.85 });
  } else {
    path = L.circleMarker(latLngOf([mk.x, mk.y]), { radius: 6, color: cool, weight: 2, fillOpacity: 0 });
  }
  if (mk.t !== "site") tip(path, mk.tip);
  else path.bindPopup(mk.tip);
  keep(path, mk.k).addTo(marksLayer);
}

function drawPlayer(at: PointM): void {
  L.circleMarker(latLngOf(at), { radius: 7, color: PLAYER_COLOUR, weight: 2, fillColor: ink("--player-fill", "#4aa3df"), fillOpacity: 0.9, interactive: false }).addTo(marksLayer);
}

function fit(m: L.Map, scene: MapScene): void {
  const pts: L.LatLngTuple[] = [];
  scene.marks.forEach(function (mk) {
    if (mk.t === "label") return;
    if (mk.t === "hull") mk.pts.forEach((p) => pts.push(latLngOf(p)));
    else if (mk.t === "line") mk.lines.forEach((line) => line.forEach((p) => pts.push(latLngOf(p))));
    else pts.push(latLngOf([mk.x, mk.y]));
  });
  if (scene.player && (scene.fitPlayer || !pts.length)) pts.push(latLngOf(scene.player));
  if (!pts.length) return;
  m.fitBounds(L.latLngBounds(pts).pad(0.12), { maxZoom: FIT_MAX_ZOOM, animate: false });
}

/* Hover beats the pick, which beats the rest; with a pick, the rest fade. */
function restyle(): void {
  const hi = ink("--ink-hi", "#fff");
  Object.keys(byKey).forEach(function (key) {
    const on = key === lit || key === shownSel;
    byKey[key]!.forEach(function (path) {
      const look = LOOK.get(path)!;
      const faded = !!shownSel && !on;
      path.setStyle({
        color: on ? hi : look.color,
        weight: on ? look.weight + 2 : look.weight,
        opacity: on ? Math.max(look.opacity, 0.9) : faded ? look.opacity * FADE : look.opacity,
        fillOpacity: faded ? look.fillOpacity * FADE : look.fillOpacity,
      });
      if (on) path.bringToFront();
    });
  });
}

/** The panel's markup; `attachMap` puts the live map into its slot. */
export function mapPanel(legend: Safe): Safe {
  return html`<section class="wmap" aria-label="map of the results"><div class="wmap-slot"></div><div class="wmap-leg">${legend}</div></section>`;
}

export function attachMap(root: HTMLElement, scene: MapScene, next: MapHooks): void {
  const slot = root.querySelector<HTMLElement>(".wmap-slot");
  if (!slot) return;
  hooks = next;
  if (!mini) {
    mini = miniMap("wmap-live");
    marksLayer.addTo(mini.map);
  }
  const m = mini.map;
  slot.appendChild(mini.el);
  m.invalidateSize({ animate: false });
  mini.sync();
  const key = scene.key + "|" + (scene.player ? scene.player.join(",") : "");
  const fresh = key !== drawn;
  if (fresh) {
    drawn = key;
    marksLayer.clearLayers();
    byKey = {};
    lit = null;
    scene.marks.forEach(drawMark);
    if (scene.player) drawPlayer(scene.player);
    fit(m, scene);
  }
  const moved = scene.sel !== shownSel;
  shownSel = scene.sel;
  restyle();
  if (moved && !fresh && scene.sel) {
    const first = byKey[scene.sel]?.[0] as (L.Path & { getBounds?: () => L.LatLngBounds; getLatLng?: () => L.LatLng }) | undefined;
    if (first?.getLatLng) m.panInside(first.getLatLng(), { padding: [24, 24] });
    else if (first?.getBounds && !m.getBounds().contains(first.getBounds())) m.panTo(first.getBounds().getCenter());
  }
}

/** Light one result's mark, or none. */
export function lightMark(key: string | null): void {
  if (key === lit) return;
  lit = key;
  restyle();
}

/** Forget what was drawn, so the next attach draws and fits again. */
export function resetMap(): void {
  drawn = "";
}
