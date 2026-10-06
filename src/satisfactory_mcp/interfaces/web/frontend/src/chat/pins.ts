/* Pins: numbered handles (pin:N) the player makes on the page and names to chat.
 * See docs/planner-p3_contract.md §4, §8 and §9. */

import { send } from "../api/client";
import { copyText } from "../kit/copy";
import { code, dataButton, esc, html, onAttributeClick, popup } from "../kit/dom";
import { L } from "../map/leaflet";
import { BAND, clearedLayer } from "../map/layers";
import { liveStore } from "./livestore";
import { flyToPoint, map, latLngOf } from "../map/map";
import { goToMapThen } from "../app/nav";
import { registerFetch } from "../app/registry";
import { state } from "../app/state";
import { fail, friendlyError, notify } from "../kit/toast";
import { PIN_KIND } from "../kit/words";

import type { ApiError, ApiPath, StatusError } from "../api/client";
import type { Markup, Row } from "../kit/dom";
import type { PinCreated, PinDropped, PinRef, PinRow, PinsResponse, PinStaleResponse } from "../api/shapes";

export interface PinTarget {
  kind: string;
  ref: PinRef;
  text: string;
}

export var LABEL_MAX = 80;

var PINS_PATH: ApiPath = "/api/pins";
var PIN_PATH: ApiPath = "/api/pins/{n}";
var KIND_ATTR = "data-pin-kind";
var REF_ATTR = "data-pin-ref";
var PIN_ZOOM = 1;

var markers: Record<number, L.Marker> = {};
var store = liveStore<PinsResponse, PinRow>(
  PINS_PATH,
  function (data) {
    return data.pins;
  },
  "pin",
  drawPins
);

export var onPins = store.on;
export var pinStore = store.read;
export var refetchPins = store.refetch;

export function livePins(): PinRow[] {
  const data = pinStore().data;
  return data ? data.pins : [];
}

export function findPin(kind: string, match: (ref: PinRef) => boolean): PinRow | undefined {
  return livePins().filter(function (pin) {
    return pin.kind === kind && match(pin.ref);
  })[0];
}

/** The plan's process pins, by recipe id. */
export function processPinsByRecipe(planKey: string): Record<string, PinRow> {
  const out: Record<string, PinRow> = {};
  livePins().forEach(function (pin) {
    if (pin.kind === "process" && pin.ref.plan === planKey && pin.ref.recipe) out[pin.ref.recipe] = pin;
  });
  return out;
}

export function pinName(pin: PinRow): string {
  return pin.id + (pin.label ? " “" + pin.label + "”" : "");
}

function pinRows(pin: PinRow): Row[] {
  return [
    ["pin", code(pin.id)],
    ["label", pin.label || null],
    ["what", pin.text],
    ["selector", code(pin.selector)],
    ["gone", pin.gone ? pin.gone_why : null],
  ];
}

/* Tags at one spot stack upwards rather than covering each other. */
function drawPins(data: PinsResponse): void {
  const group = clearedLayer("pins", { on: true, rank: [BAND.chrome, 60, "pins"] });
  markers = {};
  const stacked: Record<string, number> = {};
  data.pins.forEach(function (pin) {
    if (pin.x_m === null || pin.y_m === null) return;
    const name = pinName(pin) + (pin.gone ? " (gone)" : "");
    const spot = Math.round(pin.x_m) + "," + Math.round(pin.y_m);
    const below = stacked[spot] || 0;
    stacked[spot] = below + 1;
    const tag = L.marker(latLngOf({ x_m: pin.x_m, y_m: pin.y_m }), {
      icon: L.divIcon({ className: "pin-tag" + (pin.gone ? " gone" : ""), html: esc(pin.n), iconSize: [28, 18], iconAnchor: [-4, 22 + below * 20] }),
      title: name,
      alt: name,
      keyboard: true,
    });
    tag.on("add", function () {
      const node = tag.getElement();
      if (node) node.setAttribute("aria-label", name);
    });
    tag.bindPopup(popup(pinRows(pin)));
    tag.addTo(group);
    markers[pin.n] = tag;
  });
  store.accept(data);
}

registerFetch<PinsResponse>({
  wave: "live",
  rank: 90,
  path: PINS_PATH,
  label: "pins",
  clears: ["pins"],
  refilters: false,
  draw: drawPins,
});

/** Pins a thing, or finds its existing pin, and copies pin:N for chat. */
export function createPin(kind: string, ref: PinRef): void {
  send<PinCreated>("POST", PINS_PATH, { kind: kind, ref: ref })
    .then(function (pin) {
      const said = (pin.existing ? "already " : "pinned as ") + pin.id;
      copyText(pin.id).then(
        function () {
          notify(said + " · copied");
        },
        function () {
          notify(said + " · not copied: the browser refused");
        }
      );
      refetchPins();
    })
    .catch(function (reason) {
      fail("could not pin this: " + friendlyError(reason));
    });
}

function onPinConflict(reason: unknown): void {
  const current = store.recoverFromConflict(reason);
  const body = current ? ((reason as StatusError).body as PinStaleResponse) : null;
  fail(body ? body.error + "; this is the current one" : friendlyError(reason));
}

export function renamePin(pin: PinRow, label: string): Promise<boolean> {
  return send<PinRow & ApiError>("PATCH", PIN_PATH, { rev: pin.rev, label: label }, String(pin.n))
    .then(function (row) {
      store.replace(row);
      return true;
    })
    .catch(function (reason) {
      onPinConflict(reason);
      return false;
    });
}

export function dropPin(pin: PinRow): void {
  store.deleteOnce(pin.n, function () {
    return send<PinDropped>("DELETE", PIN_PATH, { rev: pin.rev }, String(pin.n))
      .then(function () {
        notify("deleted " + pin.id);
        refetchPins();
      })
      .catch(onPinConflict);
  });
}

export function showPin(pin: PinRow): void {
  if (pin.x_m === null || pin.y_m === null) return;
  const at = latLngOf({ x_m: pin.x_m, y_m: pin.y_m });
  goToMapThen(function () {
    const group = state.layers["pins"];
    if (group && !map.hasLayer(group)) group.addTo(map);
    map.once("moveend", function () {
      const tag = markers[pin.n];
      if (tag && map.hasLayer(tag)) tag.openPopup();
    });
    flyToPoint(at, Math.max(map.getZoom(), PIN_ZOOM));
  });
}

/** [pin] buttons as popup markup; one delegated click (listenForPins) makes the pin. */
export function pinButtons(targets: PinTarget[]): Markup {
  return html(
    targets
      .map(function (target) {
        const attrs: Record<string, string> = {};
        attrs[KIND_ATTR] = target.kind;
        attrs[REF_ATTR] = JSON.stringify(target.ref);
        return dataButton(attrs, target.text, "pin this " + (PIN_KIND[target.kind] || target.kind) + " and copy its pin:N for chat");
      })
      .join(" ")
  );
}

export function onPlanChange(entry: { world: string; key: string }): void {
  if (entry.world !== state.world) return;
  const touched = livePins().some(function (pin) {
    return pin.ref.plan === entry.key;
  });
  if (touched) refetchPins();
}

export function listenForPins(): void {
  onAttributeClick(KIND_ATTR, function (hit) {
    createPin(hit.getAttribute(KIND_ATTR) || "", JSON.parse(hit.getAttribute(REF_ATTR) || "{}") as PinRef);
  });
}
