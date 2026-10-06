/* A plan's pad being moved on the map: handles, the crosshair mode, keyboard nudges and the
 * snap. Owns no fetch: it reports `step` while a gesture runs and `commit` when it ends.
 * docs/planner-p5_contract.md §3 is the specification. */

import { L } from "../../map/leaflet";
import { footprintCorners, map, MAP_SQUARE_M, latLngOf } from "../../map/map";
import { PLAN_COLOUR } from "../../map/drawn/plan-sitings";

export interface Pad {
  x_m: number;
  y_m: number;
  yaw_deg: number;
  width_m: number;
  depth_m: number;
}

type Gesture = "" | "drag" | "turn" | "keys" | "cross";

export type GestureKind = Exclude<Gesture, "">;

export interface Hooks {
  step(pad: Pad): void;
  commit(pad: Pad, how: GestureKind): void;
  cancel(): void;
}

export interface Spot {
  x_m: number;
  y_m: number;
}

var GRID_M = 8;
var YAW_STEP = 15;
var GRID_YAW_STEP = 90;
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
var padBeforeGesture: Pad | null = null;
var gesture: Gesture = "";
var aborted = false;
var burstTimer = 0;
var cross: HTMLElement | null = null;
var snapMode = function (): string {
  return "fine";
};

export function useSnap(mode: () => string): void {
  snapMode = mode;
}

export function samePad(a: Pad | null, b: Pad | null): boolean {
  return !!a && !!b && a.x_m === b.x_m && a.y_m === b.y_m && a.yaw_deg === b.yaw_deg && a.width_m === b.width_m && a.depth_m === b.depth_m;
}

export function normaliseYaw(yaw: number): number {
  return ((yaw % 360) + 360) % 360;
}

function withPlace(p: Pad, x: number, y: number, yaw: number): Pad {
  return { x_m: x, y_m: y, yaw_deg: yaw, width_m: p.width_m, depth_m: p.depth_m };
}

/** Degrees one snapped turn moves by: 90° on the 8 m grid, else 15°. */
export function yawStep(): number {
  return snapMode() === "grid8" ? GRID_YAW_STEP : YAW_STEP;
}

/** The next lattice angle from `yaw` in direction `dir` (±1), so an off-lattice pad lands on it. */
function turned(yaw: number, dir: number): number {
  var step = yawStep();
  var k = yaw / step;
  return normaliseYaw((dir > 0 ? Math.floor(k + 1e-6) + 1 : Math.ceil(k - 1e-6) - 1) * step);
}

/** The `site_snap` rule; `siting.snap` on the server is the same. */
export function snap(p: Pad, free: boolean): Pad {
  if (free) return withPlace(p, Math.round(p.x_m * 100) / 100, Math.round(p.y_m * 100) / 100, normaliseYaw(Math.round(p.yaw_deg * 10) / 10));
  var step = yawStep();
  var yaw = normaliseYaw(Math.round(p.yaw_deg / step) * step);
  var x: number;
  var y: number;
  if (snapMode() === "grid8") {
    var quarter = yaw % 180 === 90;
    var across = quarter ? p.depth_m : p.width_m;
    var along = quarter ? p.width_m : p.depth_m;
    x = Math.round((p.x_m - across / 2) / GRID_M) * GRID_M + across / 2;
    y = Math.round((p.y_m - along / 2) / GRID_M) * GRID_M + along / 2;
  } else {
    x = Math.round(p.x_m);
    y = Math.round(p.y_m);
  }
  return withPlace(p, x, y, yaw);
}

export function padCorners(p: Pad): L.LatLngTuple[] {
  return footprintCorners(p.x_m, p.y_m, p.width_m / 2, p.depth_m / 2, p.yaw_deg);
}

/** Whether any corner leaves the in-game map square: such a drop is refused. */
export function outsideMap(p: Pad): boolean {
  return padCorners(p).some(function (corner) {
    var x = corner[1];
    var y = -corner[0];
    return x < MAP_SQUARE_M.x_min || x > MAP_SQUARE_M.x_max || y < MAP_SQUARE_M.y_min || y > MAP_SQUARE_M.y_max;
  });
}

function turnAt(p: Pad): L.LatLngTuple {
  var angle = (p.yaw_deg * Math.PI) / 180;
  var reach = (TURN_REACH * p.depth_m) / 2;
  return latLngOf({ x_m: p.x_m - Math.sin(angle) * reach, y_m: p.y_m + Math.cos(angle) * reach });
}

function gameXY(at: L.LatLng): Spot {
  return { x_m: at.lng, y_m: -at.lat };
}

function drawLines(p: Pad): void {
  lines.forEach(function (line, i) {
    var spot = spots[i]!;
    line.setLatLngs([latLngOf(p), latLngOf(spot)]);
    line.setTooltipContent(Math.round(Math.hypot(spot.x_m - p.x_m, spot.y_m - p.y_m)).toLocaleString("en-GB") + " m, distance, not a route");
  });
}

function draw(p: Pad, moveHandles: boolean): void {
  pad = p;
  if (outline) outline.setLatLngs(padCorners(p));
  if (moveHandles && mover) mover.setLatLng(latLngOf(p));
  if (moveHandles && turner) turner.setLatLng(turnAt(p));
  drawLines(p);
}

function step(p: Pad): void {
  draw(p, false);
  if (hooks) hooks.step(p);
}

function end(how: GestureKind): void {
  var p = pad;
  gesture = "";
  if (!p || !hooks) return;
  draw(p, true);
  if (aborted) {
    aborted = false;
    if (padBeforeGesture) draw(padBeforeGesture, true);
    hooks.cancel();
    return;
  }
  hooks.commit(p, how);
}

function beginGesture(kind: GestureKind): void {
  gesture = kind;
  padBeforeGesture = pad;
  aborted = false;
}

function icon(kind: string, label: string): L.DivIcon {
  return L.divIcon({ className: "site-handle site-" + kind, iconSize: [16, 16], html: '<span class="dk-hidden">' + label + "</span>" });
}

function onHandleKeydown(event: KeyboardEvent): void {
  if (!pad || !hooks) return;
  var dx = 0;
  var dy = 0;
  var turn = 0;
  var by = event.shiftKey ? FINE_M : NUDGE_M;
  if (event.key === "ArrowLeft") dx = -by;
  else if (event.key === "ArrowRight") dx = by;
  else if (event.key === "ArrowUp") dy = -by;
  else if (event.key === "ArrowDown") dy = by;
  else if (event.key === "[") turn = -1;
  else if (event.key === "]") turn = 1;
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
    padBeforeGesture = pad;
  }
  var yaw = !turn ? pad.yaw_deg : event.shiftKey ? pad.yaw_deg + turn * YAW_STEP : turned(pad.yaw_deg, turn);
  var next = snap(withPlace(pad, pad.x_m + dx, pad.y_m + dy, yaw), event.shiftKey);
  draw(next, true);
  hooks.step(next);
  clearTimeout(burstTimer);
  burstTimer = window.setTimeout(flushBurst, BURST_MS);
}

function flushBurst(): void {
  clearTimeout(burstTimer);
  if (gesture === "keys") end("keys");
}

function shiftHeld(event: L.LeafletEvent): boolean {
  return !!(event as unknown as { originalEvent?: MouseEvent }).originalEvent?.shiftKey;
}

function handles(p: Pad, name: string): void {
  mover = L.marker(latLngOf(p), { draggable: true, keyboard: true, pane: PANE, icon: icon("move", "move the pad"), autoPan: false });
  turner = L.marker(turnAt(p), { draggable: true, keyboard: false, pane: PANE, icon: icon("turn", "turn the pad"), autoPan: false });
  mover.on("dragstart", function () {
    beginGesture("drag");
  });
  mover.on("drag", function (e) {
    if (aborted || !pad) return;
    var at = gameXY((e as L.LeafletMouseEvent).latlng);
    step(snap(withPlace(pad, at.x_m, at.y_m, pad.yaw_deg), shiftHeld(e)));
    if (turner) turner.setLatLng(turnAt(pad!));
  });
  mover.on("dragend", function () {
    end("drag");
  });
  turner.on("dragstart", function () {
    beginGesture("turn");
  });
  turner.on("drag", function (e) {
    if (aborted || !pad) return;
    var at = gameXY((e as L.LeafletMouseEvent).latlng);
    var yaw = (Math.atan2(-(at.x_m - pad.x_m), at.y_m - pad.y_m) * 180) / Math.PI;
    step(snap(withPlace(pad, pad.x_m, pad.y_m, yaw), shiftHeld(e)));
  });
  turner.on("dragend", function () {
    end("turn");
  });
  mover.addTo(layer);
  turner.addTo(layer);
  var handle = mover.getElement();
  if (handle) {
    handle.setAttribute("aria-label", "pad of “" + name + "”, move with arrow keys, turn with [ and ]");
    handle.setAttribute("data-ctl", "site-move");
    handle.addEventListener("keydown", onHandleKeydown);
    handle.addEventListener("blur", flushBurst);
  }
}

/** Show the pad being edited, with handles unless `bare` (a coarse pointer). */
export function startPadEdit(p: Pad, name: string, how: Hooks, bare: boolean): void {
  stopPadEdit();
  hooks = how;
  pad = p;
  outline = L.polygon(padCorners(p), { pane: PANE, color: PLAN_COLOUR, weight: 3, fill: false, dashArray: "8,5", interactive: false, className: "site-pad" });
  outline.addTo(layer);
  if (!bare) handles(p, name);
}

/** Moves the drawn pad; ignored while a gesture holds it. */
export function setPad(p: Pad): void {
  if (gesture) return;
  draw(p, true);
}

export function padGestureActive(): boolean {
  return !!gesture;
}

/** Dashed lines from the pad to each resource node, labelled with the straight distance. */
export function showNodeLines(list: Spot[]): void {
  lines.forEach(function (line) {
    layer.removeLayer(line);
  });
  spots = list.slice();
  lines = spots.map(function () {
    return L.polyline([], { pane: PANE, color: PLAN_COLOUR, weight: 1, opacity: 0.45, dashArray: "8,5", className: "site-line" }).bindTooltip("", { sticky: true }).addTo(layer);
  });
  if (pad) drawLines(pad);
}

export function stopPadEdit(): void {
  clearTimeout(burstTimer);
  stopCrosshair();
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
    var panel = dash.getBoundingClientRect();
    if (panel.left > box.left + 4 && panel.left < box.right) right = panel.left;
    else if (panel.top > box.top + 4 && panel.top < box.bottom) bottom = panel.top;
  }
  return L.point((left + right) / 2 - box.left, (top + bottom) / 2 - box.top);
}

function follow(): void {
  if (gesture !== "cross" || !pad) return;
  var at = gameXY(map.containerPointToLatLng(visibleCentre()));
  step(snap(withPlace(pad, at.x_m, at.y_m, pad.yaw_deg), false));
}

function placeCross(): void {
  if (!cross) return;
  var centre = visibleCentre();
  cross.style.left = centre.x + "px";
  cross.style.top = centre.y + "px";
}

/** Crosshair mode: the pad sits under a fixed cross and the map pans beneath it. */
export function startCrosshair(): void {
  if (!pad || !hooks) return;
  beginGesture("cross");
  if (mover) mover.setOpacity(0);
  if (turner) turner.setOpacity(0);
  cross = document.createElement("div");
  cross.className = "site-cross";
  cross.setAttribute("aria-hidden", "true");
  map.getContainer().appendChild(cross);
  placeCross();
  map.panTo(latLngOf(pad), { animate: false });
  var centre = visibleCentre();
  var here = map.latLngToContainerPoint(latLngOf(pad));
  map.panBy(here.subtract(centre), { animate: false });
  map.on("move", follow);
  window.addEventListener("resize", placeCross);
}

function stopCrosshair(): void {
  map.off("move", follow);
  window.removeEventListener("resize", placeCross);
  if (cross) cross.remove();
  cross = null;
  if (mover) mover.setOpacity(1);
  if (turner) turner.setOpacity(1);
}

export function crosshairActive(): boolean {
  return gesture === "cross";
}

/** One snap step (`yawStep`) towards `dir` (±1) while crossing; committed with the drop. */
export function crossTurn(dir: number): void {
  if (gesture !== "cross" || !pad) return;
  step(snap(withPlace(pad, pad.x_m, pad.y_m, turned(pad.yaw_deg, dir)), false));
}

export function crossDrop(): void {
  if (gesture !== "cross") return;
  stopCrosshair();
  end("cross");
}

export function crossCancel(): void {
  if (gesture !== "cross") return;
  stopCrosshair();
  aborted = true;
  end("cross");
}

/* ---------------------------------------------------------------- ghost */

function ink(): string {
  return getComputedStyle(document.documentElement).getPropertyValue("--ink-hi").trim() || "#fff";
}

/** Chat's previewed pad, dashed and muted; null clears it. */
export function showGhostPad(p: Pad | null): void {
  ghostLayer.clearLayers();
  if (!p) return;
  L.polygon(padCorners(p), { pane: PANE, color: ink(), weight: 2, opacity: 0.85, fill: false, dashArray: "4,4", interactive: false, className: "site-ghost" }).addTo(ghostLayer);
}

document.addEventListener("keydown", function (event) {
  if (event.key !== "Escape" || (gesture !== "drag" && gesture !== "turn")) return;
  event.preventDefault();
  aborted = true;
  if (padBeforeGesture) draw(padBeforeGesture, false);
});
