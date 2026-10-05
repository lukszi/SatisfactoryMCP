/* A plan's pad being moved on the map: handles, the crosshair mode, keyboard nudges and the
 * snap. Owns no fetch: it reports `step` while a gesture runs and `commit` when it ends.
 * docs/planner-p5_contract.md §3 is the specification. */

import { L } from "./leaflet";
import { footprintCorners, map, MAP_SQUARE_M, xy } from "./map";
import { PLAN_COLOUR } from "./plans";

export interface Pad {
  x: number;
  y: number;
  yaw: number;
  w: number;
  d: number;
}

export interface Hooks {
  step(pad: Pad): void;
  commit(pad: Pad, how: string): void;
  cancel(): void;
}

export interface Spot {
  x_m: number;
  y_m: number;
}

var GRID_M = 8;
var YAW_STEP = 15;
var NUDGE_M = 8;
var FINE_M = 1;
var BURST_MS = 600;
var TURN_REACH = 1.5;
var PANE = "sitedrag";

var pane = map.createPane(PANE);
pane.style.zIndex = "640";
map.getContainer().style.setProperty("--site-pad", PLAN_COLOUR);

var layer = L.layerGroup().addTo(map);
var ghostLayer = L.layerGroup().addTo(map);
var outline: L.Polygon | null = null;
var mover: L.Marker | null = null;
var turner: L.Marker | null = null;
var lines: L.Polyline[] = [];
var spots: Spot[] = [];
var hooks: Hooks | null = null;
var pad: Pad | null = null;
var held: Pad | null = null;
var gesture = "";
var aborted = false;
var burstTimer = 0;
var cross: HTMLElement | null = null;
var snapMode = function (): string {
  return "fine";
};

export function useSnap(mode: () => string): void {
  snapMode = mode;
}

export function same(a: Pad | null, b: Pad | null): boolean {
  return !!a && !!b && a.x === b.x && a.y === b.y && a.yaw === b.yaw && a.w === b.w && a.d === b.d;
}

function norm(yaw: number): number {
  return ((yaw % 360) + 360) % 360;
}

/** The `site_snap` rule; `siting.snap` on the server is the same. */
export function snap(p: Pad, free: boolean): Pad {
  if (free) return { x: Math.round(p.x * 100) / 100, y: Math.round(p.y * 100) / 100, yaw: norm(Math.round(p.yaw * 10) / 10), w: p.w, d: p.d };
  var x: number;
  var y: number;
  if (snapMode() === "grid8") {
    x = Math.round((p.x - p.w / 2) / GRID_M) * GRID_M + p.w / 2;
    y = Math.round((p.y - p.d / 2) / GRID_M) * GRID_M + p.d / 2;
  } else {
    x = Math.round(p.x);
    y = Math.round(p.y);
  }
  return { x: x, y: y, yaw: norm(Math.round(p.yaw / YAW_STEP) * YAW_STEP), w: p.w, d: p.d };
}

export function corners(p: Pad): L.LatLngTuple[] {
  return footprintCorners(p.x, p.y, p.w / 2, p.d / 2, p.yaw);
}

/** Whether any corner leaves the in-game map square: such a drop is refused. */
export function outsideMap(p: Pad): boolean {
  return corners(p).some(function (c) {
    var x = c[1];
    var y = -c[0];
    return x < MAP_SQUARE_M.x_min || x > MAP_SQUARE_M.x_max || y < MAP_SQUARE_M.y_min || y > MAP_SQUARE_M.y_max;
  });
}

function turnAt(p: Pad): L.LatLngTuple {
  var a = (p.yaw * Math.PI) / 180;
  var r = (TURN_REACH * p.d) / 2;
  return xy({ x_m: p.x - Math.sin(a) * r, y_m: p.y + Math.cos(a) * r });
}

function metres(at: L.LatLng): Spot {
  return { x_m: at.lng, y_m: -at.lat };
}

function drawLines(p: Pad): void {
  lines.forEach(function (line, i) {
    var s = spots[i]!;
    line.setLatLngs([xy({ x_m: p.x, y_m: p.y }), xy(s)]);
    line.setTooltipContent(Math.round(Math.hypot(s.x_m - p.x, s.y_m - p.y)).toLocaleString("en-GB") + " m, distance, not a route");
  });
}

function draw(p: Pad, moveHandles: boolean): void {
  pad = p;
  if (outline) outline.setLatLngs(corners(p));
  if (moveHandles && mover) mover.setLatLng(xy({ x_m: p.x, y_m: p.y }));
  if (moveHandles && turner) turner.setLatLng(turnAt(p));
  drawLines(p);
}

function step(p: Pad): void {
  draw(p, false);
  if (hooks) hooks.step(p);
}

function end(how: string): void {
  var p = pad;
  gesture = "";
  if (!p || !hooks) return;
  draw(p, true);
  if (aborted) {
    aborted = false;
    if (held) draw(held, true);
    hooks.cancel();
    return;
  }
  hooks.commit(p, how);
}

function icon(kind: string, label: string): L.DivIcon {
  return L.divIcon({ className: "site-handle site-" + kind, iconSize: [16, 16], html: '<span class="dk-hidden">' + label + "</span>" });
}

function keyed(event: KeyboardEvent): void {
  if (!pad || !hooks) return;
  var dx = 0;
  var dy = 0;
  var turn = 0;
  var by = event.shiftKey ? FINE_M : NUDGE_M;
  if (event.key === "ArrowLeft") dx = -by;
  else if (event.key === "ArrowRight") dx = by;
  else if (event.key === "ArrowUp") dy = -by;
  else if (event.key === "ArrowDown") dy = by;
  else if (event.key === "[") turn = -YAW_STEP;
  else if (event.key === "]") turn = YAW_STEP;
  else if (event.key === "Enter" && gesture === "keys") {
    event.preventDefault();
    event.stopPropagation();
    flushBurst();
    return;
  } else if (event.key === "Escape" && gesture === "keys") {
    event.preventDefault();
    event.stopPropagation();
    clearTimeout(burstTimer);
    aborted = true;
    end("keys");
    return;
  } else return;
  event.preventDefault();
  event.stopPropagation();
  if (gesture !== "keys") {
    gesture = "keys";
    held = pad;
  }
  var next = { x: pad.x + dx, y: pad.y + dy, yaw: norm(pad.yaw + turn), w: pad.w, d: pad.d };
  draw(next, true);
  hooks.step(next);
  clearTimeout(burstTimer);
  burstTimer = window.setTimeout(flushBurst, BURST_MS);
}

function flushBurst(): void {
  clearTimeout(burstTimer);
  if (gesture === "keys") end("keys");
}

function handles(p: Pad, name: string): void {
  mover = L.marker(xy({ x_m: p.x, y_m: p.y }), { draggable: true, keyboard: true, pane: PANE, icon: icon("move", "move the pad"), autoPan: false });
  turner = L.marker(turnAt(p), { draggable: true, keyboard: false, pane: PANE, icon: icon("turn", "turn the pad"), autoPan: false });
  mover.on("dragstart", function () {
    gesture = "drag";
    held = pad;
    aborted = false;
  });
  mover.on("drag", function (e) {
    if (aborted || !pad) return;
    var at = metres((e as L.LeafletMouseEvent).latlng);
    var free = !!(e as unknown as { originalEvent?: MouseEvent }).originalEvent?.shiftKey;
    step(snap({ x: at.x_m, y: at.y_m, yaw: pad.yaw, w: pad.w, d: pad.d }, free));
    if (turner) turner.setLatLng(turnAt(pad!));
  });
  mover.on("dragend", function () {
    end("drag");
  });
  turner.on("dragstart", function () {
    gesture = "turn";
    held = pad;
    aborted = false;
  });
  turner.on("drag", function (e) {
    if (aborted || !pad) return;
    var at = metres((e as L.LeafletMouseEvent).latlng);
    var yaw = (Math.atan2(-(at.x_m - pad.x), at.y_m - pad.y) * 180) / Math.PI;
    var free = !!(e as unknown as { originalEvent?: MouseEvent }).originalEvent?.shiftKey;
    step(snap({ x: pad.x, y: pad.y, yaw: yaw, w: pad.w, d: pad.d }, free));
  });
  turner.on("dragend", function () {
    end("turn");
  });
  mover.addTo(layer);
  turner.addTo(layer);
  var el = mover.getElement();
  if (el) {
    el.setAttribute("aria-label", "pad of “" + name + "”, move with arrow keys, turn with [ and ]");
    el.setAttribute("data-ctl", "site-move");
    el.addEventListener("keydown", keyed);
    el.addEventListener("blur", flushBurst);
  }
}

/** Show the pad being edited, with handles unless `bare` (a coarse pointer). */
export function edit(p: Pad, name: string, how: Hooks, bare: boolean): void {
  stop();
  hooks = how;
  pad = p;
  outline = L.polygon(corners(p), { pane: PANE, color: PLAN_COLOUR, weight: 3, fill: false, dashArray: "8,5", interactive: false, className: "site-pad" });
  outline.addTo(layer);
  if (!bare) handles(p, name);
}

export function setPad(p: Pad): void {
  if (gesture) return;
  draw(p, true);
}

export function current(): Pad | null {
  return pad;
}

export function busy(): boolean {
  return !!gesture;
}

export function nodes(list: Spot[]): void {
  lines.forEach(function (line) {
    layer.removeLayer(line);
  });
  spots = list.slice();
  lines = spots.map(function () {
    return L.polyline([], { pane: PANE, color: PLAN_COLOUR, weight: 1, opacity: 0.45, dashArray: "8,5", className: "site-line" }).bindTooltip("", { sticky: true }).addTo(layer);
  });
  if (pad) drawLines(pad);
}

export function stop(): void {
  clearTimeout(burstTimer);
  crossOff();
  layer.clearLayers();
  outline = null;
  mover = null;
  turner = null;
  lines = [];
  hooks = null;
  pad = null;
  gesture = "";
  aborted = false;
}

/* ---------------------------------------------------------------- crosshair */

function visibleCentre(): L.Point {
  var box = map.getContainer().getBoundingClientRect();
  var left = box.left;
  var right = box.right;
  var top = box.top;
  var bottom = box.bottom;
  var dash = document.getElementById("dash");
  if (dash && !dash.hidden) {
    var r = dash.getBoundingClientRect();
    if (r.left > box.left + 4 && r.left < box.right) right = r.left;
    else if (r.top > box.top + 4 && r.top < box.bottom) bottom = r.top;
  }
  return L.point((left + right) / 2 - box.left, (top + bottom) / 2 - box.top);
}

function follow(): void {
  if (gesture !== "cross" || !pad) return;
  var at = metres(map.containerPointToLatLng(visibleCentre()));
  step(snap({ x: at.x_m, y: at.y_m, yaw: pad.yaw, w: pad.w, d: pad.d }, false));
}

function placeCross(): void {
  if (!cross) return;
  var c = visibleCentre();
  cross.style.left = c.x + "px";
  cross.style.top = c.y + "px";
}

/** Crosshair mode: the pad sits under a fixed cross and the map pans beneath it. */
export function crossOn(): void {
  if (!pad || !hooks) return;
  gesture = "cross";
  held = pad;
  aborted = false;
  if (mover) mover.setOpacity(0);
  if (turner) turner.setOpacity(0);
  cross = document.createElement("div");
  cross.className = "site-cross";
  cross.setAttribute("aria-hidden", "true");
  map.getContainer().appendChild(cross);
  placeCross();
  map.panTo(xy({ x_m: pad.x, y_m: pad.y }), { animate: false });
  var centre = visibleCentre();
  var here = map.latLngToContainerPoint(xy({ x_m: pad.x, y_m: pad.y }));
  map.panBy(here.subtract(centre), { animate: false });
  map.on("move", follow);
  window.addEventListener("resize", placeCross);
}

function crossOff(): void {
  map.off("move", follow);
  window.removeEventListener("resize", placeCross);
  if (cross) cross.remove();
  cross = null;
  if (mover) mover.setOpacity(1);
  if (turner) turner.setOpacity(1);
}

export function crossing(): boolean {
  return gesture === "cross";
}

/** One 15° step while crossing; committed with the drop, not per tap. */
export function crossTurn(by: number): void {
  if (gesture !== "cross" || !pad) return;
  step({ x: pad.x, y: pad.y, yaw: norm(pad.yaw + by), w: pad.w, d: pad.d });
}

export function crossDrop(): void {
  if (gesture !== "cross") return;
  crossOff();
  end("cross");
}

export function crossCancel(): void {
  if (gesture !== "cross") return;
  crossOff();
  aborted = true;
  end("cross");
}

/* ---------------------------------------------------------------- ghost */

function ink(): string {
  return getComputedStyle(document.documentElement).getPropertyValue("--ink-hi").trim() || "#fff";
}

/** Chat's previewed pad, dashed and muted; null clears it. */
export function ghost(p: Pad | null): void {
  ghostLayer.clearLayers();
  if (!p) return;
  L.polygon(corners(p), { pane: PANE, color: ink(), weight: 2, opacity: 0.85, fill: false, dashArray: "4,4", interactive: false, className: "site-ghost" }).addTo(ghostLayer);
}

document.addEventListener("keydown", function (event) {
  if (event.key !== "Escape" || (gesture !== "drag" && gesture !== "turn")) return;
  event.preventDefault();
  aborted = true;
  if (held) draw(held, false);
});
