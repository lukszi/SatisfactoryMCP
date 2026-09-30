/* A factory's or an unnamed cluster's machines ringed on the map, and amending a factory by
 * drawing around machines. See docs/frontend_vision.md §9.9. */

import { get, latest, send } from "./api";
import { button, chip, pressed } from "./dashkit";
import { LASSO_ATTR, make } from "./dom";
import { L } from "./leaflet";
import { flyPadded, map } from "./map";
import { cardHead, cardLine, cardRow, cardSubject, claim, mapCard } from "./mapcard";
import { makeRoom, onVitals } from "./panel";
import { newest, refreshLabels, refusal, wrote } from "./rename";
import { state } from "./state";
import { fail, friendly, note as said } from "./toast";
import { counted, W } from "./words";

import type { AmendedResponse, FactoryMachinesResponse, MachineSpot } from "./api-shapes";

type Mode = "add" | "drop";

type Corner = [number, number];

var FLY_ZOOM = 2;

var STEP_PX = 4;

var pane = map.createPane("lasso");
pane.style.zIndex = "455";
pane.style.pointerEvents = "none";
var renderer = L.svg({ pane: "lasso", padding: 0.5 });
var rings = L.layerGroup();
var ink = L.layerGroup();

var view = {
  title: "",
  kind: "",
  factory: "",
  mode: "add" as Mode,
  token: "",
  members: null as MachineSpot[] | null,
  error: "",
  areas: [] as Corner[][],
  more: false,
  preview: null as AmendedResponse | null,
  busy: false,
  world: "",
  epoch: 0,
};

var stroke: { points: L.LatLng[]; line: L.Polyline; last: L.Point } | null = null;

var drew = false;

function card(): HTMLElement {
  return mapCard("lasso", "machines on the map", closeLasso);
}

function ring(spot: MachineSpot, className: string, radius: number): void {
  L.circleMarker([-spot.y_m, spot.x_m], {
    radius: radius,
    className: className,
    renderer: renderer,
    pane: "lasso",
    interactive: false,
  }).addTo(rings);
}

function draw(): void {
  rings.clearLayers();
  (view.members || []).forEach(function (s) {
    ring(s, "lasso-member", 6);
  });
  var p = view.preview;
  if (p) {
    p.added.forEach(function (s) {
      ring(s, "lasso-add", 8);
    });
    p.dropped.forEach(function (s) {
      ring(s, "lasso-drop", 8);
    });
  }
  if (!map.hasLayer(rings)) rings.addTo(map);
  if (!map.hasLayer(ink)) ink.addTo(map);
}

function fly(spots: MachineSpot[]): void {
  if (!spots.length) return;
  var bounds = L.latLngBounds(
    spots.map(function (s) {
      return [-s.y_m, s.x_m] as L.LatLngTuple;
    })
  );
  flyPadded(bounds.pad(0.15), FLY_ZOOM);
}

function buildings(spots: MachineSpot[]): string {
  var tally: Record<string, number> = {};
  spots.forEach(function (s) {
    tally[s.building] = (tally[s.building] || 0) + 1;
  });
  return Object.keys(tally)
    .sort(function (a, b) {
      return tally[b]! - tally[a]!;
    })
    .map(function (k) {
      return tally[k] + "× " + k;
    })
    .join(", ");
}

function toggle(text: string, title: string, mode: Mode): HTMLButtonElement {
  return pressed(
    text,
    view.mode === mode,
    function () {
      if (view.mode === mode) return;
      view.mode = mode;
      if (view.areas.length) check();
      else render();
    },
    { title: title }
  );
}

function previewBlock(box: HTMLElement, p: AmendedResponse): void {
  var changes = p.added.length + p.dropped.length;
  var chips = make("div", "panel-chips");
  if (p.added.length) chips.appendChild(chip("+" + counted(p.added.length, "machine") + " to add", "ok"));
  if (p.dropped.length) chips.appendChild(chip("−" + counted(p.dropped.length, "machine") + " to remove", "remove"));
  if (!changes) chips.appendChild(chip(view.mode === "add" ? "nothing new inside" : "none of its machines inside", "muted"));
  chips.appendChild(chip(p.before + " → " + counted(p.after, "anchor"), "muted"));
  if (view.areas.length > 1) chips.appendChild(chip(counted(view.areas.length, "area"), "muted"));
  box.appendChild(chips);
  var moved = p.added.length ? p.added : p.dropped;
  if (moved.length) cardLine(box, buildings(moved));
  var named = p.added.filter(function (s) {
    return s.factory !== null;
  });
  if (named.length) {
    var held: Record<string, number> = {};
    named.forEach(function (s) {
      held[s.factory!] = (held[s.factory!] || 0) + 1;
    });
    cardLine(
      box,
      Object.keys(held)
        .map(function (k) {
          return counted(held[k]!, "machine") + " already in “" + k + "”; both factories will hold them";
        })
        .join(" · "),
      "blocked"
    );
  }
  var acts = cardRow();
  acts.appendChild(button(view.busy ? "applying…" : "apply", apply, { title: "write this change to the factory", disabled: view.busy || !changes }));
  acts.appendChild(button("discard", discard, { title: "clear every area and draw again" }));
  acts.appendChild(
    pressed(
      "+ area",
      view.more,
      function () {
        view.more = !view.more;
        render();
      },
      { title: "the next drag adds another area instead of starting over" }
    )
  );
  box.appendChild(acts);
  cardLine(box, "shift-drag or + area adds another area");
}

function render(): void {
  var box = card();
  box.textContent = "";
  document.body.classList.toggle("lasso-on", !!view.factory);
  if (!view.title) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  var head = cardHead(view.factory ? "Amend" : view.kind);
  if (view.factory) {
    head.appendChild(toggle("+ add", "draw around machines to add them", "add"));
    head.appendChild(toggle("− remove", "draw around machines to remove them", "drop"));
  }
  head.appendChild(button("×", closeLasso, { title: "stop", label: "clear the machines from the map" }));
  box.appendChild(head);
  cardSubject(box, view.title);
  if (view.error) {
    cardLine(box, view.error, "bad");
    return;
  }
  if (!view.members) {
    cardLine(box, "finding its machines…");
    return;
  }
  cardLine(box, counted(view.members.length, "machine") + " ringed" + (view.members.length ? ": " + buildings(view.members) : ""));
  if (!view.factory) return;
  if (view.preview) previewBlock(box, view.preview);
  else if (view.busy) cardLine(box, "checking what that area holds…");
  else cardLine(box, "drag around machines on the map to " + (view.mode === "add" ? "add them to" : "remove them from") + " this " + W.factory);
}

var candidate = "";

function membersPath(): `/api/factories/machines?${string}` {
  var q = view.factory
    ? "factory=" + encodeURIComponent(view.factory)
    : "candidate=" + encodeURIComponent(candidate) + "&token=" + encodeURIComponent(view.token);
  return ("/api/factories/machines?" + q) as `/api/factories/machines?${string}`;
}

function fetchMembers(flyAfter: boolean): void {
  var ticket = latest("lasso");
  get<FactoryMachinesResponse>(membersPath())
    .then(function (data) {
      if (!ticket.fresh()) return;
      view.members = data.machines;
      view.token = data.token;
      view.error = "";
      draw();
      render();
      if (flyAfter) fly(data.machines);
    })
    .catch(function (err) {
      if (!ticket.fresh()) return;
      view.members = null;
      view.error = friendly(err);
      rings.clearLayers();
      render();
    });
}

function begin(title: string, kind: string, factory: string): void {
  claim("lasso");
  stopStroke();
  ink.clearLayers();
  view.title = title;
  view.kind = kind;
  view.factory = factory;
  view.mode = "add";
  view.members = null;
  view.error = "";
  view.areas = [];
  view.more = false;
  view.preview = null;
  view.busy = false;
  view.world = state.world;
  view.epoch = state.epoch;
  rings.clearLayers();
  makeRoom("trace");
  if (factory) map.dragging.disable();
  else map.dragging.enable();
  render();
}

export function startLasso(name: string): void {
  begin(name, "Amend", name);
  fetchMembers(true);
}

export function ringCandidate(selector: string, token: string, title: string): void {
  begin(title, W.unnamedCluster, "");
  candidate = selector;
  view.token = token;
  fetchMembers(false);
}

export function closeLasso(): void {
  latest("lasso");
  latest("amend");
  stopStroke();
  view.title = "";
  view.factory = "";
  view.members = null;
  view.preview = null;
  view.areas = [];
  view.more = false;
  rings.clearLayers();
  ink.clearLayers();
  map.dragging.enable();
  render();
}

function body(dryRun: boolean): object {
  return {
    name: view.factory,
    area: view.areas[0],
    extra_areas: view.areas.slice(1),
    mode: view.mode,
    as_of: view.preview && !dryRun ? view.preview.token : view.token,
    version: newest(view.preview ? view.preview.version : 0),
    dry_run: dryRun,
  };
}

function refused(err: unknown, what: string): void {
  var why = refusal(err);
  if (why === "stale" || why === "pin") {
    fail((why === "pin" ? "a newer save was written" : "factory names changed elsewhere") + ", so nothing was " + what + "; draw the area again");
    refreshLabels();
    discard();
    fetchMembers(false);
  } else fail(friendly(err));
}

function check(): void {
  var ticket = latest("amend");
  view.busy = true;
  view.preview = null;
  render();
  send<AmendedResponse>("POST", "/api/labels/amend", body(true))
    .then(function (reply) {
      if (!ticket.fresh()) return;
      view.preview = reply;
      view.busy = false;
      draw();
      render();
    })
    .catch(function (err) {
      if (!ticket.fresh()) return;
      view.busy = false;
      render();
      refused(err, "previewed");
    });
}

function apply(): void {
  if (!view.preview || view.busy) return;
  var ticket = latest("amend");
  var name = view.factory;
  view.busy = true;
  render();
  send<AmendedResponse>("POST", "/api/labels/amend", body(false))
    .then(function (reply) {
      if (!ticket.fresh()) return;
      view.busy = false;
      wrote(reply.version);
      said("“" + name + "” now holds " + counted(reply.after, "machine") + " (+" + reply.added.length + " −" + reply.dropped.length + ")");
      refreshLabels();
      discard();
      fetchMembers(false);
    })
    .catch(function (err) {
      if (!ticket.fresh()) return;
      view.busy = false;
      render();
      refused(err, "written");
    });
}

function discard(): void {
  latest("amend");
  view.areas = [];
  view.more = false;
  view.preview = null;
  view.busy = false;
  ink.clearLayers();
  draw();
  render();
}

function stopStroke(): void {
  if (!stroke) return;
  stroke.line.remove();
  stroke = null;
}

function onDown(event: PointerEvent): void {
  if (!view.factory || view.busy || event.button !== 0) return;
  var target = event.target as Element | null;
  if (target && target.closest(".leaflet-control-container, .leaflet-popup")) return;
  event.preventDefault();
  event.stopPropagation();
  if (!view.areas.length || !(event.shiftKey || view.more)) discard();
  var at = map.mouseEventToLatLng(event as unknown as MouseEvent);
  stroke = {
    points: [at],
    line: L.polyline([at], { className: "lasso-ink", renderer: renderer, pane: "lasso", interactive: false }).addTo(ink),
    last: map.mouseEventToContainerPoint(event as unknown as MouseEvent),
  };
  map.getContainer().setPointerCapture(event.pointerId);
}

function onMove(event: PointerEvent): void {
  if (!stroke) return;
  event.preventDefault();
  var px = map.mouseEventToContainerPoint(event as unknown as MouseEvent);
  if (px.distanceTo(stroke.last) < STEP_PX) return;
  stroke.last = px;
  stroke.points.push(map.containerPointToLatLng(px));
  stroke.line.setLatLngs(stroke.points);
}

function onUp(): void {
  if (!stroke) return;
  var points = stroke.points;
  stroke.line.remove();
  stroke = null;
  drew = true;
  if (points.length < 3) return;
  L.polygon(points, { className: "lasso-area", renderer: renderer, pane: "lasso", interactive: false }).addTo(ink);
  view.areas.push(
    points.map(function (p) {
      return [p.lng, -p.lat] as Corner;
    })
  );
  view.more = false;
  check();
}

function wire(): void {
  var box = map.getContainer();
  box.addEventListener("pointerdown", onDown, true);
  box.addEventListener("pointermove", onMove, true);
  box.addEventListener("pointerup", onUp, true);
  box.addEventListener("pointercancel", stopStroke, true);
  box.addEventListener(
    "click",
    function (event) {
      if (!drew) return;
      drew = false;
      event.stopPropagation();
    },
    true
  );
  document.addEventListener(
    "click",
    function (event) {
      var target = event.target as Element | null;
      var hit = target && target.closest ? target.closest("[" + LASSO_ATTR + "]") : null;
      if (!hit) return;
      event.stopPropagation();
      event.preventDefault();
      startLasso(hit.getAttribute(LASSO_ATTR) || "");
    },
    true
  );
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && view.title && !(event.target as Element).closest("input, textarea")) closeLasso();
  });
  onVitals(function () {
    if (!view.title) return;
    if (view.world !== state.world) {
      closeLasso();
      return;
    }
    if (view.epoch === state.epoch) return;
    view.epoch = state.epoch;
    if (!view.factory) {
      closeLasso();
      return;
    }
    discard();
    fetchMembers(false);
  });
}

wire();
