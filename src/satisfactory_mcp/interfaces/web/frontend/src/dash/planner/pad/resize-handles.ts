/* The pad's eight resize handles and the size readout beside the one being dragged. Owns no
 * pad state: `ResizeHost` (pad-drag.ts) holds the pad and the gesture. docs/planner-p5_contract.md
 * §3 F2a is the specification. */

import { L } from "../../../map/leaflet";
import { latLngOf, map } from "../../../map/map";
import { cursorFor, HANDLES, handleSpot, isCorner, sizeReadout } from "./geometry";
import { resizeStep } from "./grid-snap";

import type { Pad } from "./geometry";
import type { Handle, ResizeKeys } from "./geometry";
import type { SnapContext } from "./grid-snap";

export interface ResizeHost {
  pad(): Pad | null;
  begin(): void;
  step(p: Pad, snapped: boolean): void;
  end(): void;
  aborted(): boolean;
  snapContext(at: L.LatLng): SnapContext;
}

/** An edge shorter than this on screen hides its handle, so the corners stay reachable. */
const EDGE_MIN_PX = 44;
const READOUT_OFFSET_PX = 18;

interface Grip {
  handle: Handle;
  marker: L.Marker;
}

let grips: Grip[] = [];
let host: ResizeHost | null = null;
let active: Grip | null = null;
let start: Pad | null = null;
let lastAt: L.LatLng | null = null;
let keys: ResizeKeys = { aspect: false, centred: false, free: false };
let readout: HTMLElement | null = null;

function icon(h: Handle): L.DivIcon {
  const kind = isCorner(h) ? "corner" : "edge";
  return L.divIcon({ className: "site-handle site-resize site-resize-" + kind, iconSize: [12, 12], html: '<span class="dk-hidden">resize the pad</span>' });
}

function keysOf(event: { shiftKey?: boolean; altKey?: boolean; ctrlKey?: boolean; metaKey?: boolean } | undefined): ResizeKeys {
  return { aspect: !!event?.shiftKey, centred: !!event?.altKey, free: !!(event?.ctrlKey || event?.metaKey) };
}

function showReadout(text: string, snapped: boolean): void {
  if (!readout) {
    readout = document.createElement("div");
    readout.className = "site-readout";
    readout.setAttribute("role", "status");
    map.getContainer().appendChild(readout);
  }
  readout.textContent = text + (snapped ? " · on the foundations" : "");
  readout.classList.toggle("site-snapped", snapped);
  if (active) {
    const at = map.latLngToContainerPoint(active.marker.getLatLng());
    readout.style.left = at.x + READOUT_OFFSET_PX + "px";
    readout.style.top = at.y + READOUT_OFFSET_PX + "px";
  }
}

function hideReadout(): void {
  if (readout) readout.remove();
  readout = null;
}

function frame(): void {
  if (!host || !active || !start || !lastAt || host.aborted()) return;
  const step = resizeStep(start, active.handle, lastAt.lng, -lastAt.lat, keys, host.snapContext(lastAt));
  host.step(step.pad, step.snapped);
  placeResizeHandles(step.pad);
  showReadout(sizeReadout(step.pad), step.snapped);
}

/* A modifier pressed or let go mid-drag re-runs the frame where the pointer is. Alt is held
 * back from the browser, which would otherwise focus its menu on release. */
function onKey(event: KeyboardEvent): void {
  if (!active) return;
  if (event.key === "Alt") event.preventDefault();
  if (event.key !== "Shift" && event.key !== "Alt" && event.key !== "Control" && event.key !== "Meta") return;
  keys = keysOf(event);
  frame();
}

/* Leaflet will not start a drag with Shift held, so a Shift press-and-drag on a corner is
 * passed on without it; the drag reads the real key on every move. */
function unshift(event: MouseEvent): void {
  if (!event.shiftKey || !event.isTrusted) return;
  event.stopPropagation();
  event.preventDefault();
  const plain = new MouseEvent("mousedown", { bubbles: true, cancelable: true, clientX: event.clientX, clientY: event.clientY, screenX: event.screenX, screenY: event.screenY, button: event.button, buttons: event.buttons, altKey: event.altKey, ctrlKey: event.ctrlKey, metaKey: event.metaKey });
  (event.target as Element).dispatchEvent(plain);
}

function grip(h: Handle): Grip {
  const marker = L.marker([0, 0], { draggable: true, keyboard: false, pane: "sitedrag", icon: icon(h), autoPan: false });
  const g: Grip = { handle: h, marker: marker };
  marker.on("add", function () {
    const el = marker.getElement();
    if (el) el.addEventListener("mousedown", unshift, true);
  });
  marker.on("dragstart", function () {
    if (!host) return;
    start = host.pad();
    active = g;
    host.begin();
    document.addEventListener("keydown", onKey, true);
    document.addEventListener("keyup", onKey, true);
  });
  marker.on("drag", function (e) {
    lastAt = (e as L.LeafletMouseEvent).latlng;
    keys = keysOf((e as unknown as { originalEvent?: MouseEvent }).originalEvent);
    frame();
  });
  marker.on("dragend", function () {
    document.removeEventListener("keydown", onKey, true);
    document.removeEventListener("keyup", onKey, true);
    active = null;
    start = null;
    lastAt = null;
    hideReadout();
    if (host) host.end();
  });
  return g;
}

/** The handles on `layer`, driven through `how`. */
export function addResizeHandles(layer: L.LayerGroup, how: ResizeHost, p: Pad): void {
  host = how;
  grips = HANDLES.map(grip);
  grips.forEach(function (g) {
    g.marker.addTo(layer);
  });
  placeResizeHandles(p);
  map.on("zoomend", refresh);
}

function refresh(): void {
  const p = host?.pad();
  if (p) placeResizeHandles(p);
}

function edgePx(p: Pad, h: Handle): number {
  const a = map.latLngToContainerPoint(latLngOf(handleSpot(p, { sx: h.sx || -1, sy: h.sy || -1 })));
  const b = map.latLngToContainerPoint(latLngOf(handleSpot(p, { sx: h.sx || 1, sy: h.sy || 1 })));
  return a.distanceTo(b);
}

/** Puts every handle on `p`, the dragged one too (it shows the snapped corner, not the
 *  pointer), with the cursor its pull points. */
export function placeResizeHandles(p: Pad): void {
  grips.forEach(function (g) {
    g.marker.setLatLng(latLngOf(handleSpot(p, g.handle)));
    const el = g.marker.getElement();
    if (!el) return;
    el.style.cursor = cursorFor(p.yaw_deg, g.handle);
    el.hidden = !isCorner(g.handle) && edgePx(p, g.handle) < EDGE_MIN_PX;
  });
}

/** Hides the handles while the crosshair holds the pad. */
export function showResizeHandles(on: boolean): void {
  grips.forEach(function (g) {
    g.marker.setOpacity(on ? 1 : 0);
  });
}

export function clearResizeHandles(): void {
  map.off("zoomend", refresh);
  document.removeEventListener("keydown", onKey, true);
  document.removeEventListener("keyup", onKey, true);
  hideReadout();
  grips = [];
  host = null;
  active = null;
  start = null;
}
