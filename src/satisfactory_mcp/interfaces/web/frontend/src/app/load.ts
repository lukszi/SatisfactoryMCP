/* When the page fetches, and what a reply is allowed to do when it lands.
 *
 * WHAT is fetched is declared per feature in registry.ts; what is here is the machinery every
 * entry shares -- the two waves, the epoch guard that drops a reply nobody is waiting for, the
 * clear a failure owes the layers it was going to draw into, and the mark on screen while a
 * switch is in flight.
 *
 * THIS FILE MUST IMPORT NO MODULE THAT REGISTERS A FETCH: those imports are what would make a
 * registration reachable without main.ts's FEATURES block naming it. See registry.ts.
 * `regions.ts` is named below because /api/regions is the one fetch outside both waves.
 */

import { dropToken, get } from "../api/client";
import { el } from "../kit/dom";
import { inFloorMode, leaveFloors, refilterFloors } from "../map/floors/floors";
import { clearPrefixed } from "../map/layers";
import { L } from "../map/leaflet";
import { map, writeHash } from "../map/map";
import { createListeners } from "./listeners";
import { fetcherFor, fetchersOf } from "./registry";
import { drawRegions } from "../map/regions";
import { state } from "./state";
import { fail, friendlyError } from "../kit/toast";

import type { ApiError, ApiUrl } from "../api/client";
import type { Registered } from "./registry";

export function loadRegions() {
  // Geography, not save state: no world parameter, fetched once, never refetched.
  return fetch("/api/regions")
    .then(function (r) {
      return r.json().then(function (body) {
        if (!r.ok || body.error) throw new Error(body.error || r.status + " /api/regions");
        return body;
      });
    })
    .then(drawRegions)
    .catch(function (e) {
      fail("regions: " + friendlyError(e));
    });
}

/* One registered fetch, from the request to whatever the reply is allowed to do.
 *
 * THE EPOCH GUARD is why this is one function rather than ten. A switch bumps `state.epoch`,
 * and a reply for an earlier epoch is dropped instead of drawn -- without it, whichever world
 * answered LAST owns the map. It is checked in both places a reply can arrive, and the catch
 * checks it BEFORE clearing anything: a failure belonging to a world nobody is looking at must
 * not empty the layers the current world has just filled.
 *
 * `after` runs inside the same guarded block as the draw rather than in a `.then` of its own,
 * which would be a microtask later and would need a guard of its own. */
function runFetch(fetcher: Registered): void {
  const epoch = state.epoch;
  const live = function () {
    return epoch === state.epoch;
  };
  get<ApiError>(fetcher.query ? (`${fetcher.path}?${fetcher.query()}` as ApiUrl) : fetcher.path)
    .then(function (body) {
      if (!live()) return;
      if (fetcher.settles) markSwitching(false);
      fetcher.draw(body);
      if (fetcher.refilters) refilterFloors();
      if (fetcher.after) fetcher.after();
    })
    .catch(function (e) {
      if (!live()) return;
      if (fetcher.settles) markSwitching(false);
      clearPrefixed(fetcher.clears);
      if (fetcher.failed) fetcher.failed();
      fail(fetcher.label + ": " + friendlyError(e));
    });
}

export function loadStatic(): void {
  fetchersOf("static").forEach(runFetch);
}

export function loadLive(): void {
  dropToken();
  fetchersOf("live").forEach(runFetch);
}

/** One registered fetch on its own, guarded and cleared exactly as its wave would have done
 *  it, for the caller that wants a single layer outside both waves. Nothing happens if no
 *  feature claimed this path -- which is what main.ts's FEATURES block prevents. */
export function loadOne(path: ApiUrl): void {
  const fetcher = fetcherFor(path);
  if (fetcher) runFetch(fetcher);
}

/* A switch in progress is marked on screen -- header says so, map dims -- because the old
 * world's layers stay visible until the new responses land, and an unmarked blend of two
 * worlds reads as data. Cleared when this epoch's settling fetch lands, either way. */
function markSwitching(on: boolean): void {
  const container = el("map");
  if (on) L.DomUtil.addClass(container, "busy");
  else L.DomUtil.removeClass(container, "busy");
}

const reloadListeners = createListeners();

export function onReload(listener: () => void): void {
  reloadListeners.on(listener);
}

export function reload(note?: string): void {
  state.epoch += 1;
  dropToken();
  map.closePopup(); // an open card is a claim about the previous world/save
  // ...and so is an open floor view: a platform index is what ONE decomposition handed out,
  // and the ids on its bands name machines in the save being left.
  if (inFloorMode()) leaveFloors();
  // The header's tooltip is a claim about the previous world too, and it outlives the switch
  // by the whole length of a 3 s parse if it is not replaced here alongside the text.
  const loading = note || "loading…";
  el("summary").textContent = loading;
  el("summary").title = loading;
  markSwitching(true);
  writeHash();
  loadStatic();
  loadLive();
  reloadListeners.emit();
}
