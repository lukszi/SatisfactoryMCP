/* A factory's or an unnamed cluster's machines ringed on the map, and amending a factory by
 * drawing around machines. See docs/frontend_vision.md §9.9. */

import { get, latest, send } from "../../api/client";
import { button, chip, toggleButton } from "../../kit/dashkit";
import { LASSO_ATTR, make, onAttributeClick } from "../../kit/dom";
import { buildingCounts, tallyBy } from "../../kit/format";
import { L } from "../leaflet";
import { flyPadded, gameXY, latLngOf, map } from "../map";
import { cardLine, cardSubject, cardTitleBar, cardToolbar, closeOtherCards, mapCard } from "../mapcard";
import { makeRoom } from "../panel";
import { onVitals } from "../../app/vitals";
import { labelRefusal, labelsVersionToSend, recordLabelsVersion, refetchLabelledViews, staleWriteReason } from "../../dash/factories/rename";
import { state } from "../../app/state";
import { fail, friendlyError, notify } from "../../kit/toast";
import { counted, WORDS } from "../../kit/words";

import type { AmendedResponse, FactoryMachinesResponse, MachineSpot } from "../../api/shapes";
import type { PointM } from "../geometry";

type Mode = "add" | "drop";

const FLY_ZOOM = 2;

const STEP_PX = 4;

const pane = map.createPane("lasso");
pane.style.zIndex = "455";
pane.style.pointerEvents = "none";
const renderer = L.svg({ pane: "lasso", padding: 0.5 });
/** The rings on the machines, and the areas being drawn. */
const ringLayer = L.layerGroup();
const areaLayer = L.layerGroup();

const view = {
  title: "",
  kind: "",
  factory: "",
  /** The cluster selector an unnamed cluster's members are asked for by. */
  candidateSelector: "",
  mode: "add" as Mode,
  token: "",
  members: null as MachineSpot[] | null,
  error: "",
  areas: [] as PointM[][],
  /** Whether the next drag adds an area instead of starting over. */
  appendNextArea: false,
  preview: null as AmendedResponse | null,
  busy: false,
  world: "",
  epoch: 0,
};

let stroke: { points: L.LatLng[]; line: L.Polyline; last: L.Point } | null = null;

/** Set when a drag ends, so the click the browser fires after it does not reach the map. */
let swallowNextClick = false;

function lassoCard(): HTMLElement {
  return mapCard("lasso", "machines on the map", closeLasso);
}

function ring(spot: MachineSpot, className: string, radius: number): void {
  L.circleMarker(latLngOf(spot), {
    radius: radius,
    className: className,
    renderer: renderer,
    pane: "lasso",
    interactive: false,
  }).addTo(ringLayer);
}

function draw(): void {
  ringLayer.clearLayers();
  (view.members || []).forEach(function (spot) {
    ring(spot, "lasso-member", 6);
  });
  const preview = view.preview;
  if (preview) {
    preview.added.forEach(function (spot) {
      ring(spot, "lasso-add", 8);
    });
    preview.dropped.forEach(function (spot) {
      ring(spot, "lasso-drop", 8);
    });
  }
  if (!map.hasLayer(ringLayer)) ringLayer.addTo(map);
  if (!map.hasLayer(areaLayer)) areaLayer.addTo(map);
}

function fly(spots: MachineSpot[]): void {
  if (!spots.length) return;
  const bounds = L.latLngBounds(
    spots.map(function (spot) {
      return latLngOf(spot);
    })
  );
  flyPadded(bounds.pad(0.15), FLY_ZOOM);
}

/** "3× Constructor, 2× Smelter", most first. */
function buildings(spots: MachineSpot[]): string {
  return buildingCounts(
    tallyBy(spots, function (spot) {
      return spot.building;
    })
  );
}

function modeButton(text: string, title: string, mode: Mode): HTMLButtonElement {
  return toggleButton(
    text,
    view.mode === mode,
    function () {
      if (view.mode === mode) return;
      view.mode = mode;
      if (view.areas.length) previewAmendment();
      else render();
    },
    { title: title }
  );
}

function previewBlock(box: HTMLElement, preview: AmendedResponse): void {
  const changes = preview.added.length + preview.dropped.length;
  const chips = make("div", "panel-chips");
  if (preview.added.length) chips.appendChild(chip("+" + counted(preview.added.length, "machine") + " to add", "ok"));
  if (preview.dropped.length) chips.appendChild(chip("−" + counted(preview.dropped.length, "machine") + " to remove", "remove"));
  if (!changes) chips.appendChild(chip(view.mode === "add" ? "nothing new inside" : "none of its machines inside", "muted"));
  chips.appendChild(chip(preview.before + " → " + counted(preview.after, "anchor"), "muted"));
  if (view.areas.length > 1) chips.appendChild(chip(counted(view.areas.length, "area"), "muted"));
  box.appendChild(chips);
  const moved = preview.added.length ? preview.added : preview.dropped;
  if (moved.length) cardLine(box, buildings(moved));
  const named = preview.added.filter(function (spot) {
    return spot.factory !== null;
  });
  if (named.length) {
    const held: Record<string, number> = {};
    named.forEach(function (spot) {
      held[spot.factory!] = (held[spot.factory!] || 0) + 1;
    });
    cardLine(
      box,
      Object.keys(held)
        .map(function (factory) {
          return counted(held[factory]!, "machine") + " already in “" + factory + "”; both factories will hold them";
        })
        .join(" · "),
      "blocked"
    );
  }
  const acts = cardToolbar();
  acts.appendChild(button(view.busy ? "applying…" : "apply", applyAmendment, { title: "write this change to the factory", disabled: view.busy || !changes }));
  acts.appendChild(button("discard", discardAreas, { title: "clear every area and draw again" }));
  acts.appendChild(
    toggleButton(
      "+ area",
      view.appendNextArea,
      function () {
        view.appendNextArea = !view.appendNextArea;
        render();
      },
      { title: "the next drag adds another area instead of starting over" }
    )
  );
  box.appendChild(acts);
  cardLine(box, "shift-drag or + area adds another area");
}

function render(): void {
  const box = lassoCard();
  box.textContent = "";
  document.body.classList.toggle("lasso-on", !!view.factory);
  if (!view.title) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  const head = cardTitleBar(view.factory ? "Amend" : view.kind);
  if (view.factory) {
    head.appendChild(modeButton("+ add", "draw around machines to add them", "add"));
    head.appendChild(modeButton("− remove", "draw around machines to remove them", "drop"));
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
  else cardLine(box, "drag around machines on the map to " + (view.mode === "add" ? "add them to" : "remove them from") + " this " + WORDS.factory);
}

function membersPath(): `/api/factories/machines?${string}` {
  const query = view.factory
    ? "factory=" + encodeURIComponent(view.factory)
    : "candidate=" + encodeURIComponent(view.candidateSelector) + "&token=" + encodeURIComponent(view.token);
  return ("/api/factories/machines?" + query) as `/api/factories/machines?${string}`;
}

function fetchMembers(flyAfter: boolean): void {
  const ticket = latest("lasso");
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
      view.error = friendlyError(err);
      ringLayer.clearLayers();
      render();
    });
}

function begin(title: string, kind: string, factory: string): void {
  closeOtherCards("lasso");
  stopStroke();
  areaLayer.clearLayers();
  view.title = title;
  view.kind = kind;
  view.factory = factory;
  view.candidateSelector = "";
  view.mode = "add";
  view.members = null;
  view.error = "";
  view.areas = [];
  view.appendNextArea = false;
  view.preview = null;
  view.busy = false;
  view.world = state.world;
  view.epoch = state.epoch;
  ringLayer.clearLayers();
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
  begin(title, WORDS.unnamedCluster, "");
  view.candidateSelector = selector;
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
  view.appendNextArea = false;
  ringLayer.clearLayers();
  areaLayer.clearLayers();
  map.dragging.enable();
  render();
}

/** The `/api/labels/amend` body: a dry run previews against the members' token, a real write
 *  against the preview's. */
function amendRequest(dryRun: boolean): object {
  return {
    name: view.factory,
    area: view.areas[0],
    extra_areas: view.areas.slice(1),
    mode: view.mode,
    as_of: view.preview && !dryRun ? view.preview.token : view.token,
    version: labelsVersionToSend(view.preview ? view.preview.version : 0),
    dry_run: dryRun,
  };
}

/** A refused amend: a stale view starts over, anything else is said as it came. */
function reportAmendRefusal(err: unknown, what: string): void {
  const why = labelRefusal(err);
  if (why === "stale" || why === "pin") {
    fail(staleWriteReason(why) + ", so nothing was " + what + "; draw the area again");
    refetchLabelledViews();
    discardAreas();
    fetchMembers(false);
  } else fail(friendlyError(err));
}

function previewAmendment(): void {
  const ticket = latest("amend");
  view.busy = true;
  view.preview = null;
  render();
  send<AmendedResponse>("POST", "/api/labels/amend", amendRequest(true))
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
      reportAmendRefusal(err, "previewed");
    });
}

function applyAmendment(): void {
  if (!view.preview || view.busy) return;
  const ticket = latest("amend");
  const name = view.factory;
  view.busy = true;
  render();
  send<AmendedResponse>("POST", "/api/labels/amend", amendRequest(false))
    .then(function (reply) {
      if (!ticket.fresh()) return;
      view.busy = false;
      recordLabelsVersion(reply.version);
      notify("“" + name + "” now holds " + counted(reply.after, "machine") + " (+" + reply.added.length + " −" + reply.dropped.length + ")");
      refetchLabelledViews();
      discardAreas();
      fetchMembers(false);
    })
    .catch(function (err) {
      if (!ticket.fresh()) return;
      view.busy = false;
      render();
      reportAmendRefusal(err, "written");
    });
}

function discardAreas(): void {
  latest("amend");
  view.areas = [];
  view.appendNextArea = false;
  view.preview = null;
  view.busy = false;
  areaLayer.clearLayers();
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
  const target = event.target as Element | null;
  if (target?.closest(".leaflet-control-container, .leaflet-popup")) return;
  event.preventDefault();
  event.stopPropagation();
  if (!view.areas.length || !(event.shiftKey || view.appendNextArea)) discardAreas();
  const at = map.mouseEventToLatLng(event as unknown as MouseEvent);
  stroke = {
    points: [at],
    line: L.polyline([at], { className: "lasso-ink", renderer: renderer, pane: "lasso", interactive: false }).addTo(areaLayer),
    last: map.mouseEventToContainerPoint(event as unknown as MouseEvent),
  };
  map.getContainer().setPointerCapture(event.pointerId);
}

function onMove(event: PointerEvent): void {
  if (!stroke) return;
  event.preventDefault();
  const px = map.mouseEventToContainerPoint(event as unknown as MouseEvent);
  if (px.distanceTo(stroke.last) < STEP_PX) return;
  stroke.last = px;
  stroke.points.push(map.containerPointToLatLng(px));
  stroke.line.setLatLngs(stroke.points);
}

function onUp(): void {
  if (!stroke) return;
  const points = stroke.points;
  stroke.line.remove();
  stroke = null;
  swallowNextClick = true;
  if (points.length < 3) return;
  L.polygon(points, { className: "lasso-area", renderer: renderer, pane: "lasso", interactive: false }).addTo(areaLayer);
  view.areas.push(
    points.map(function (point) {
      return gameXY(point);
    })
  );
  view.appendNextArea = false;
  previewAmendment();
}

function wire(): void {
  const box = map.getContainer();
  box.addEventListener("pointerdown", onDown, true);
  box.addEventListener("pointermove", onMove, true);
  box.addEventListener("pointerup", onUp, true);
  box.addEventListener("pointercancel", stopStroke, true);
  box.addEventListener(
    "click",
    function (event) {
      if (!swallowNextClick) return;
      swallowNextClick = false;
      event.stopPropagation();
    },
    true
  );
  onAttributeClick(LASSO_ATTR, function (hit) {
    startLasso(hit.getAttribute(LASSO_ATTR) || "");
  });
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
    discardAreas();
    fetchMembers(false);
  });
}

wire();
