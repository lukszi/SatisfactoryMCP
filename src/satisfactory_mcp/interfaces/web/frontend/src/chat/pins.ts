/* Pins: numbered handles (pin:N) the player makes on the page and names to chat.
 * See docs/planner-p3_contract.md §4, §8 and §9. */

import { send } from "../api/client";
import { copyText } from "../kit/copy";
import { code, esc, html, popup } from "../kit/dom";
import { L } from "../map/leaflet";
import { BAND, layer } from "../map/layers";
import { liveStore } from "./livestore";
import { flyToPoint, map, xy } from "../map/map";
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

var PINS: ApiPath = "/api/pins";
var PIN_ONE: ApiPath = "/api/pins/{n}";
var KIND_ATTR = "data-pin-kind";
var REF_ATTR = "data-pin-ref";
var PIN_ZOOM = 1;

var markers: Record<number, L.Marker> = {};
var store = liveStore<PinsResponse, PinRow>(
  PINS,
  function (data) {
    return data.pins;
  },
  "pin",
  draw
);

export var onPins = store.on;
export var pinStore = store.read;
export var refetchPins = store.refetch;

export function livePins(): PinRow[] {
  var data = pinStore().data;
  return data ? data.pins : [];
}

export function pinFor(kind: string, match: (ref: PinRef) => boolean): PinRow | undefined {
  return livePins().filter(function (p) {
    return p.kind === kind && match(p.ref);
  })[0];
}

export function pinsFor(planKey: string): Record<string, PinRow> {
  var out: Record<string, PinRow> = {};
  livePins().forEach(function (p) {
    if (p.kind === "process" && p.ref.plan === planKey && p.ref.recipe) out[p.ref.recipe] = p;
  });
  return out;
}

export function pinName(p: PinRow): string {
  return p.id + (p.label ? " “" + p.label + "”" : "");
}

function pinRows(p: PinRow): Row[] {
  return [
    ["pin", code(p.id)],
    ["label", p.label || null],
    ["what", p.text],
    ["selector", code(p.selector)],
    ["gone", p.gone ? p.gone_why : null],
  ];
}

function draw(data: PinsResponse): void {
  var group = layer("pins", true, undefined, [BAND.chrome, 60, "pins"]);
  markers = {};
  var stacked: Record<string, number> = {};
  data.pins.forEach(function (p) {
    if (p.x_m === null || p.y_m === null) return;
    var name = pinName(p) + (p.gone ? " (gone)" : "");
    var spot = Math.round(p.x_m) + "," + Math.round(p.y_m);
    var below = stacked[spot] || 0;
    stacked[spot] = below + 1;
    var tag = L.marker(xy({ x_m: p.x_m, y_m: p.y_m }), {
      icon: L.divIcon({ className: "pin-tag" + (p.gone ? " gone" : ""), html: esc(p.n), iconSize: [28, 18], iconAnchor: [-4, 22 + below * 20] }),
      title: name,
      alt: name,
      keyboard: true,
    });
    tag.on("add", function () {
      var node = tag.getElement();
      if (node) node.setAttribute("aria-label", name);
    });
    tag.bindPopup(popup(pinRows(p)));
    tag.addTo(group);
    markers[p.n] = tag;
  });
  store.accept(data);
}

registerFetch<PinsResponse>({
  wave: "live",
  rank: 90,
  path: PINS,
  label: "pins",
  clears: ["pins"],
  refilters: false,
  draw: draw,
});

export function pinThis(kind: string, ref: PinRef): void {
  send<PinCreated>("POST", PINS, { kind: kind, ref: ref })
    .then(function (pin) {
      var said = (pin.existing ? "already " : "pinned as ") + pin.id;
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

function refused(reason: unknown): void {
  var current = store.refused(reason);
  var body = current ? ((reason as StatusError).body as PinStaleResponse) : null;
  fail(body ? body.error + "; this is the current one" : friendlyError(reason));
}

export function renamePin(pin: PinRow, label: string): Promise<boolean> {
  return send<PinRow & ApiError>("PATCH", PIN_ONE, { rev: pin.rev, label: label }, String(pin.n))
    .then(function (row) {
      store.replace(row);
      return true;
    })
    .catch(function (reason) {
      refused(reason);
      return false;
    });
}

export function dropPin(pin: PinRow): void {
  store.once(pin.n, function () {
    return send<PinDropped>("DELETE", PIN_ONE, { rev: pin.rev }, String(pin.n))
      .then(function () {
        notify("deleted " + pin.id);
        refetchPins();
      })
      .catch(refused);
  });
}

export function showPin(pin: PinRow): void {
  if (pin.x_m === null || pin.y_m === null) return;
  var at = xy({ x_m: pin.x_m, y_m: pin.y_m });
  goToMapThen(function () {
    var group = state.layers["pins"];
    if (group && !map.hasLayer(group)) group.addTo(map);
    map.once("moveend", function () {
      var tag = markers[pin.n];
      if (tag && map.hasLayer(tag)) tag.openPopup();
    });
    flyToPoint(at, Math.max(map.getZoom(), PIN_ZOOM));
  });
}

export function pinButtons(targets: PinTarget[]): Markup {
  return html(
    targets
      .map(function (t) {
        return (
          '<button type="button" class="btn" ' +
          KIND_ATTR +
          '="' +
          esc(t.kind) +
          '" ' +
          REF_ATTR +
          '="' +
          esc(JSON.stringify(t.ref)) +
          '" title="' +
          esc("pin this " + (PIN_KIND[t.kind] || t.kind) + " and copy its pin:N for chat") +
          '">' +
          esc(t.text) +
          "</button>"
        );
      })
      .join(" ")
  );
}

export function onPlanChange(entry: { world: string; key: string }): void {
  if (entry.world !== state.world) return;
  var touched = livePins().some(function (p) {
    return p.ref.plan === entry.key;
  });
  if (touched) refetchPins();
}

export function onActivity(entry: { world: string; kind: string }): void {
  if (entry.world === state.world && entry.kind.indexOf("pin.") === 0) refetchPins();
}

export function listenForPins(): void {
  document.addEventListener(
    "click",
    function (event) {
      var target = event.target as Element | null;
      var hit = target && target.closest ? target.closest("[" + KIND_ATTR + "]") : null;
      if (!hit) return;
      event.stopPropagation();
      event.preventDefault();
      pinThis(hit.getAttribute(KIND_ATTR) || "", JSON.parse(hit.getAttribute(REF_ATTR) || "{}") as PinRef);
    },
    true
  );
}
