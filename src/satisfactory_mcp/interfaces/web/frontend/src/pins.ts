/* Pins: numbered handles (pin:N) the player makes on the page and names to chat.
 * See docs/planner-p3_contract.md §4, §8 and §9. */

import { get, send } from "./api";
import { code, esc, html, popup } from "./dom";
import { L } from "./leaflet";
import { BAND, layer } from "./layers";
import { flyToPoint, map, xy } from "./map";
import { go } from "./nav";
import { registerFetch } from "./registry";
import { state } from "./state";
import { fail, friendly, note } from "./toast";
import { PIN_KIND } from "./words";

import type { ApiError, ApiPath, ApiUrl, StatusError } from "./api";
import type { Markup, Row } from "./dom";
import type { PinCreated, PinDropped, PinRef, PinRow, PinsResponse, PinStaleResponse } from "./api-shapes";

export interface PinTarget {
  kind: string;
  ref: PinRef;
  text: string;
}

export var LABEL_MAX = 80;

var PINS = "/api/pins" as ApiPath;
var PIN_ONE = "/api/pins/{n}" as ApiPath;
var KIND_ATTR = "data-pin-kind";
var REF_ATTR = "data-pin-ref";
var PIN_ZOOM = 1;

var store = { data: null as PinsResponse | null, error: "", world: "" };
var markers: Record<number, L.Marker> = {};
var listeners: Array<() => void> = [];
var inflight = false;
var again = false;

export function onPins(listener: () => void): void {
  listeners.push(listener);
}

function notify(): void {
  listeners.forEach(function (listener) {
    listener();
  });
}

export function pinStore(): { data: PinsResponse | null; error: string } {
  if (store.world !== state.world) return { data: null, error: "" };
  return store;
}

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
  store.data = data;
  store.error = "";
  store.world = state.world;
  var group = layer("pins", true, undefined, [BAND.chrome, 60, "pins"]);
  markers = {};
  data.pins.forEach(function (p) {
    if (p.x_m === null || p.y_m === null) return;
    var name = pinName(p) + (p.gone ? " (gone)" : "");
    var tag = L.marker(xy({ x_m: p.x_m, y_m: p.y_m }), {
      icon: L.divIcon({ className: "pin-tag" + (p.gone ? " gone" : ""), html: esc(p.n), iconSize: [28, 18], iconAnchor: [14, 9] }),
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
  notify();
}

registerFetch<PinsResponse>({
  wave: "live",
  rank: 90,
  path: PINS as string as ApiUrl,
  label: "pins",
  clears: ["pins"],
  refilters: false,
  draw: draw,
});

export function refetchPins(): void {
  if (inflight) {
    again = true;
    return;
  }
  inflight = true;
  var epoch = state.epoch;
  var done = function () {
    inflight = false;
    if (again) {
      again = false;
      refetchPins();
    }
  };
  get<PinsResponse>(PINS as string as ApiUrl)
    .then(function (data) {
      if (epoch === state.epoch) draw(data);
    })
    .catch(function (reason) {
      if (epoch !== state.epoch) return;
      store.error = friendly(reason);
      store.world = state.world;
      notify();
    })
    .then(done, done);
}

function copyText(text: string): Promise<void> {
  if (navigator.clipboard && navigator.clipboard.writeText) return navigator.clipboard.writeText(text);
  return Promise.reject(new Error("the browser refused to copy"));
}

export function pinThis(kind: string, ref: PinRef): void {
  send<PinCreated>("POST", PINS, { kind: kind, ref: ref })
    .then(function (pin) {
      var said = (pin.existing ? "already " : "pinned as ") + pin.id;
      copyText(pin.id).then(
        function () {
          note(said + " · copied");
        },
        function () {
          note(said + " · not copied: the browser refused");
        }
      );
      refetchPins();
    })
    .catch(function (reason) {
      fail("could not pin this: " + friendly(reason));
    });
}

function replaced(row: PinRow): void {
  if (!store.data) return;
  store.data.pins = store.data.pins.map(function (p) {
    return p.n === row.n ? row : p;
  });
  notify();
}

function refused(reason: unknown): boolean {
  var err = reason as StatusError;
  var body = err && (err.body as PinStaleResponse | undefined);
  if (err && err.status === 409 && body && body.pin) {
    fail(body.error + "; this is the current one");
    replaced(body.pin);
    return true;
  }
  fail(friendly(reason));
  refetchPins();
  return false;
}

export function renamePin(pin: PinRow, label: string): Promise<boolean> {
  return send<PinRow & ApiError>("PATCH", PIN_ONE, { rev: pin.rev, label: label }, String(pin.n))
    .then(function (row) {
      replaced(row);
      return true;
    })
    .catch(function (reason) {
      refused(reason);
      return false;
    });
}

export function dropPin(pin: PinRow): void {
  send<PinDropped>("DELETE", PIN_ONE, { rev: pin.rev }, String(pin.n))
    .then(function () {
      note("deleted " + pin.id);
      refetchPins();
    })
    .catch(refused);
}

export function showPin(pin: PinRow): void {
  if (pin.x_m === null || pin.y_m === null) return;
  var at = xy({ x_m: pin.x_m, y_m: pin.y_m });
  var fly = function () {
    var group = state.layers["pins"];
    if (group && !map.hasLayer(group)) group.addTo(map);
    map.invalidateSize();
    map.once("moveend", function () {
      var tag = markers[pin.n];
      if (tag && map.hasLayer(tag)) tag.openPopup();
    });
    flyToPoint(at, Math.max(map.getZoom(), PIN_ZOOM));
  };
  if (!state.dash) {
    fly();
    return;
  }
  window.addEventListener(
    "hashchange",
    function () {
      requestAnimationFrame(fly);
    },
    { once: true }
  );
  go("");
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
