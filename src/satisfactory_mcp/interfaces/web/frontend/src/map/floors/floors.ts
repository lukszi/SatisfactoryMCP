/* Floor mode: the same map, one storey at a time.
 *
 * NOT A SECOND RENDERER. Everything on screen in floor mode was drawn by the module that always
 * drew it, and this file only decides which of those pieces are on the floor being looked at.
 * `/api/floors` answers the one thing a client cannot derive -- WHICH floor each thing is on --
 * so the only geometry rule here is `standsOn`, for the layers the payload does not cover.
 *
 * THE JOINS, because they are all different:
 *
 *   * a machine, extractor, generator or belt attachment -- BY INSTANCE ID. A band lists them.
 *   * a belt run BY CHAIN, a pipe run BY ITS ROW, keyed by the number the belt and pipe
 *     payloads already carry.
 *   * a foundation piece -- BY POSITION in `/api/structures`. A lightweight buildable has no
 *     instance name at all, so `deck_rows` is the only name a deck can be listed by.
 *   * storage -- BY WHERE IT STANDS, because the decomposition does not decompose it.
 *   * the power grid -- BY WHERE ITS ENDS STAND. `/api/floors` has never grouped a wire, so
 *     this file places them with `standsOn`, twice, because a wire has two ends. Each end is
 *     judged at its ANCHOR rather than at its drawn endpoint, because an endpoint is a
 *     connector 7 m over a Mk1 pole and 24 m over a tower. See `onThisFloor` and
 *     `classifyPowerPiece` in filter.ts.
 *
 * THE GROUND is a pseudo-floor and not a band, because a miner stands on a resource node up to
 * 28 m off any deck and plumbing hugs the ground -- neither is on floor zero, and putting them
 * there would claim a measurement the page does not have. The row says WHICH claim it is,
 * because without a heightfield "on terrain" degrades to "off-deck".
 */

import { get, latest } from "../../api/client";
import { popup } from "../../kit/dom";
import { pct } from "../../kit/format";
import { batch, hideFloors, onFloorExit, onFloorPick, showFloors } from "../layercontrol/control";
import { BUILT_AREA_LAYERS, turnOnLayers } from "../layers";
import { L } from "../leaflet";
import { flyPadded, map, writeHash } from "../map";
import { state } from "../../app/state";
import { friendlyError, notify } from "../../kit/toast";
import { applyFilter, clearFilter, filterApplying, platformBounds, withFilterGuard } from "./filter";
import { deckHeightText, floorName } from "./glyphs";
import { bandOf, busiestBand, GROUND, groundPlacements, groundRuns, hasGround } from "./model";

import type { FloorPlatform, FloorsResponse } from "../../api/shapes";
import type { Row } from "../../kit/dom";
import type { FloorChoice } from "../layercontrol/control";

/* How close the flight to a platform may get: the factory-label flight's limit, for the same
 * reason as the padding in filter.ts. */
var FLOOR_MAX_ZOOM = 1;

/** One floor mode session: what was asked for, what came back, and what it changed. */
interface FloorView {
  /** The query string `/api/floors` was asked with, kept so a save write can re-ask it. */
  query: string;
  /** The selected platform, or null when the answer was a sentence instead of a list. */
  platform: FloorPlatform | null;
  body: FloorsResponse;
  /** What the picker is called: the factory's own name where the player gave one. */
  title: string;
  /** What could not be shown, as the API said it. Empty when there is nothing to say. */
  message: string;
  /** Layers this mode turned on, minus any the reader has since taken ownership of. */
  turned: string[];
  /** Whether the map has been taken to the platform yet. On a pasted `#floor=` link the
   *  decomposition can land before the concrete the flight is measured from, so the flight is
   *  owed until a pass can make it. */
  flown: boolean;
}

var view: FloorView | null = null;

/** Whether the page is currently slicing a factory. */
export function inFloorMode(): boolean {
  return view !== null;
}

/** Re-apply the filter to whatever was just redrawn -- called by load.ts after every draw,
 *  because a refetch replaces a layer's CONTENTS and the filter is a fact about contents.
 *  It is also where a flight the view still owes finally becomes possible; see `flown`. */
export function refilterFloors(): void {
  if (!view) return;
  applyFilter(view, state.floor);
  flyToPlatform();
}

/* A save write changes what is built, so it changes the decomposition the filter runs on:
 * without this, a machine placed since the view opened would be on no floor at all. Same
 * query, same band, no flight: a refresh of the answer, not a new question. */
export function refreshFloors(): void {
  if (!view) return;
  const was = view;
  get<FloorsResponse>(floorsUrl(was.query))
    .then(function (body) {
      if (view !== was) return; // left, or moved on, while this was in the air
      view.body = body;
      view.platform = (body.platforms || [])[0] || null;
      applyFilter(view, state.floor);
      showPicker();
    })
    .catch(function () {
      /* the view on screen stays the view on screen; the next save write tries again */
    });
}

/* -------------------------------------------------------------------- the picker */

/* The ground row's sentence says whether the ground was MEASURED: without a heightfield the
 * honest answer is `off-deck`, and calling it "on the ground" would upgrade the weaker claim. */
function groundDetail(body: FloorsResponse): string {
  const how = body.terrain_measured
    ? "on terrain, measured"
    : "off-deck: no heightfield here to measure against";
  return how + " · " + groundPlacements(body) + " placed · " + groundRuns(body) + " runs";
}

function floorChoices(body: FloorsResponse, platform: FloorPlatform | null): FloorChoice[] {
  const rows: FloorChoice[] = [];
  if (!platform) return rows;
  if (hasGround(body)) {
    rows.push({
      key: GROUND,
      label: "Ground",
      detail: groundDetail(body),
      // A different KIND of answer rather than a small one, so not dimmed as a mezzanine.
      minor: false,
      note:
        "everything over this factory's footprint that the decomposition put on no band: a " +
        "miner stands on a resource node and a pump on water, and plumbing hugs the ground",
    });
  }
  platform.bands.forEach(function (band) {
    rows.push({
      key: String(band.ordinal),
      label: floorName(band),
      detail: deckHeightText(band.top_m) + " · " + band.cells + " cells · " + band.machine_count + " machines",
      minor: band.minor,
      note: band.minor
        ? "a mezzanine: " +
          pct(band.share) +
          " of this platform's largest deck, so a ledge rather than a storey"
        : band.area_m2 +
          " m² of deck, " +
          band.pieces +
          " foundation pieces, " +
          band.attachment_count +
          " belt junctions",
    });
  });
  return rows;
}

function busiest(platform: FloorPlatform): string {
  const band = busiestBand(platform);
  return band ? String(band.ordinal) : GROUND;
}

function showPicker(): void {
  if (!view) return;
  showFloors(
    "floors · " + view.title,
    floorChoices(view.body, view.platform),
    state.floor ? state.floor.band : "",
    view.message
  );
}

/* --------------------------------------------------------------- entering, leaving */

/** Take the map to the platform, once, as soon as there is a drawn deck to measure it from. */
function flyToPlatform(): void {
  if (!view || view.flown || !view.platform) return;
  const bounds = platformBounds(view.platform);
  if (!bounds) return;
  view.flown = true;
  flyPadded(bounds, FLOOR_MAX_ZOOM);
}

/* Turn on what a floor is made of, and return what this mode turned on. Usually nothing: the
 * card is reached by clicking a factory label, which has already revealed these layers.
 * Storage is still filtered when ticked: a gesture turns layers on, a mode filters them. */
function revealFor(): string[] {
  return withFilterGuard(function () {
    return turnOnLayers(BUILT_AREA_LAYERS);
  });
}

/* A tick the reader makes during floor mode is theirs, so leaving must not undo it: a layer
 * stops being on loan to this mode the moment the reader touches its box. */
export function noteFloorChoice(event: L.LeafletEvent): void {
  if (!view || filterApplying()) return;
  const touched = (event as L.LayersControlEvent).layer;
  view.turned = view.turned.filter(function (name) {
    return state.layers[name] !== touched;
  });
  // A layer ticked ON mid-mode arrives holding every piece in it, so it owes the filter a pass.
  refilterFloors();
}

/** `/api/floors` with a query, spelled once so the template type stays checked. */
function floorsUrl(query: string): `/api/floors?${string}` {
  return ("/api/floors?" + query) as `/api/floors?${string}`;
}

/* Enter floor mode for one factory or one platform; without a `band` the busiest storey opens.
 *
 * A 4xx IS NOT A FAILURE HERE: a factory standing on no platform is answered with a sentence,
 * and the mode is entered either way so the picker shows it with a way out. */
export function enterFloors(query: string, title: string, band?: string): void {
  const ticket = latest("floors");
  get<FloorsResponse>(floorsUrl(query))
    .then(function (body) {
      if (ticket.fresh()) openFloorView(query, body, title, band);
    })
    .catch(function (error) {
      if (!ticket.fresh()) return;
      // The server's own `error` string, e.g. "no platform matches factory 'x'", is the answer.
      openFloorView(query, { platforms: [], note: friendlyError(error) } as unknown as FloorsResponse, title, band);
    });
}

function openFloorView(query: string, body: FloorsResponse, title: string, band?: string): void {
  const platform = (body.platforms || [])[0] || null;
  view = {
    query: query,
    platform: platform,
    body: body,
    title: platform && platform.label ? platform.label : title,
    message: platform ? "" : body.note || "no floors here",
    turned: [],
    flown: false,
  };
  if (!platform) {
    // Nothing to slice: no storey is being looked at, so the fragment must not claim one.
    state.floor = null;
    showPicker();
    return;
  }
  view.turned = revealFor();
  let wanted = band || busiest(platform);
  if (wanted === GROUND ? !hasGround(body) : !bandOf(platform, wanted)) wanted = busiest(platform);
  state.floor = { platform: platform.index, band: wanted };
  showPicker();
  applyFilter(view, state.floor);
  flyToPlatform();
  writeHash();
}

/* Leave, putting back exactly what this mode took and nothing the reader has since claimed.
 * The viewport stays where it is: the reader may have panned on purpose, and the house button
 * is the page's way of asking for the world. */
export function leaveFloors(): void {
  if (!view) return;
  const turned = view.turned;
  clearFilter();
  view = null;
  state.floor = null;
  hideFloors();
  withFilterGuard(function () {
    batch(function () {
      turned.forEach(function (name) {
        const group = state.layers[name];
        if (group && map.hasLayer(group)) map.removeLayer(group);
      });
    });
  });
  writeHash();
}

/** Switch storey. Neither the layers nor the map move: one floor of a factory is the same
 *  place as the next one, and re-flying between them would be motion for its own sake. */
export function pickBand(key: string): void {
  if (!view || !view.platform || !state.floor) return;
  state.floor = { platform: state.floor.platform, band: key };
  applyFilter(view, state.floor);
  showPicker();
  writeHash();
}

/* ---------------------------------------------------------------- the fragment */

/** `floor=<platform>/<band>`, where band is an ordinal or `ground`. Anything else is ignored
 *  rather than resolved to a guess -- the same refusal `knownMode` makes about a mode this
 *  server does not serve. */
export function parseFloorFragment(raw: string | undefined): { platform: number; band: string } | null {
  if (!raw) return null;
  const parts = raw.split("/");
  if (parts.length !== 2) return null;
  const platform = +parts[0]!;
  const band = parts[1]!;
  if (!isFinite(platform) || platform < 0 || Math.floor(platform) !== platform) return null;
  if (band !== GROUND && !/^\d+$/.test(band)) return null;
  return { platform: platform, band: band };
}

/* One fragment's floor half, applied; returns whether anything moved, because the caller owes a
 * write if nothing else did. A platform INDEX rather than a factory name, so the link survives a
 * rename: `/api/floors` promises the index is stable over one save. */
export function applyFloorFragment(asked: string | undefined): boolean {
  const want = parseFloorFragment(asked);
  const have = state.floor;
  if (!want) {
    if (!view) return false;
    leaveFloors();
    return true;
  }
  if (have && have.platform === want.platform && have.band === want.band) return false;
  if (have && have.platform === want.platform) {
    pickBand(want.band);
    return true;
  }
  enterFloors("platform=" + want.platform, "platform " + want.platform, want.band);
  return true;
}

/* ------------------------------------------------------------------- the card */

/* The action on a factory card, and the page's required way in. An element rather than markup,
 * because Leaflet keeps the element it is handed, so the listener is bound once instead of on
 * every `popupopen`. The rows still go through `popup()`, which escapes everything. */
export function cardWithFloors(rows: Row[], factory: string): HTMLElement {
  const card = document.createElement("div");
  card.innerHTML = popup(rows);
  const action = document.createElement("button");
  action.type = "button";
  action.className = "card-action";
  action.textContent = "floors";
  action.title = "show this factory one storey at a time";
  L.DomEvent.on(action, "click", function (event) {
    L.DomEvent.stop(event);
    map.closePopup();
    enterFloors("factory=" + encodeURIComponent("label:" + factory), factory);
  });
  card.appendChild(action);
  return card;
}

/* ------------------------------------------------------------------- the wiring */

/** ESC leaves; registered in main.ts beside the map's own listeners. ESC over an open card
 *  closes the card first, and only a second press leaves the mode. */
export function escapeLeavesFloorMode(event: KeyboardEvent): void {
  if (event.key !== "Escape" || !view) return;
  if (document.querySelector(".leaflet-popup")) {
    map.closePopup();
    return;
  }
  leaveFloors();
  notify("left floor mode: the whole world again");
}

onFloorPick(pickBand);
onFloorExit(leaveFloors);
