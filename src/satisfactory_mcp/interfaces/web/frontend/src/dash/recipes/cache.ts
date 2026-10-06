/* The Recipes codex's reads: each answer kept per save and path, so moving between views
 * draws from memory, and the unlocked-alternates read primed in the live wave. */

import { get, latest } from "../../api/client";
import { pendingNotice } from "../../kit/dashkit";
import { registerFetch } from "../../app/registry";
import { state } from "../../app/state";

import type { ApiError, ApiUrl } from "../../api/client";
import type { UnlockedResponse } from "../../api/shapes";

export interface CachedFetch<T> {
  data: T | null;
  failure: unknown;
  retry: () => void;
}

export const PRIMED_PATH: ApiUrl = "/api/gamedata/unlocked?only_alternates=true";

const cache: Record<string, unknown> = {};
const failed: Record<string, unknown> = {};
const pending: Record<string, boolean> = {};
let shown: Record<string, unknown> = {};
let shownEpoch = -1;
let generation = 0;
let primedEpoch = -1;
let redrawHook: () => void = function () {};
let iconsHere: boolean | null = null;

export function setRedraw(hook: () => void): void {
  redrawHook = hook;
}

export function redraw(): void {
  redrawHook();
}

/* A primed read that landed twice in one save is a new answer; `generation` keys it apart. */
function keyOf(path: ApiUrl): string {
  return state.epoch + "|" + generation + "|" + path;
}

/* `keepPrevious` shows the slot's last answer while a new path loads, so a filter keystroke
 * does not blank the table. */
export function cachedFetch<T extends ApiError>(slot: string, path: ApiUrl, opts?: { keepPrevious?: boolean }): CachedFetch<T> {
  if (shownEpoch !== state.epoch) {
    shownEpoch = state.epoch;
    shown = {};
  }
  const key = keyOf(path);
  const fetched: CachedFetch<T> = {
    data: null,
    failure: null,
    retry: function () {
      delete failed[key];
      redraw();
    },
  };
  if (key in cache) {
    shown[slot] = cache[key];
    fetched.data = cache[key] as T;
    return fetched;
  }
  if (key in failed) {
    fetched.failure = failed[key];
    return fetched;
  }
  if (opts && opts.keepPrevious && shown[slot]) fetched.data = shown[slot] as T;
  if (path === PRIMED_PATH && primedEpoch !== state.epoch) return fetched;
  if (!pending[key]) {
    pending[key] = true;
    const ticket = latest(slot);
    get<T>(path)
      .then(
        function (body) {
          cache[key] = body;
        },
        function (reason: unknown) {
          failed[key] = reason;
        }
      )
      .then(function () {
        delete pending[key];
        if (ticket.fresh()) redraw();
      });
  }
  return fetched;
}

/* True while there is nothing to draw yet; the loading or error line is drawn instead. */
export function waiting<T>(parent: HTMLElement, fetched: CachedFetch<T>, thing: string): boolean {
  if (fetched.data) return false;
  pendingNotice(parent, thing, fetched.failure, !!fetched.failure, fetched.retry);
  return true;
}

export function probeIcons(): void {
  if (iconsHere !== null) return;
  iconsHere = false;
  fetch("/api/icons/Desc_IronPlate_C", { method: "HEAD" })
    .then(function (response) {
      iconsHere = response.status === 200;
      if (iconsHere) redraw();
    })
    .catch(function () {});
}

export function iconsServed(): boolean {
  return !!iconsHere;
}

registerFetch<UnlockedResponse>({
  wave: "live",
  rank: 70,
  path: PRIMED_PATH,
  label: "unlocked recipes",
  clears: [],
  refilters: false,
  draw: function (data) {
    if (primedEpoch === state.epoch) generation += 1;
    primedEpoch = state.epoch;
    cache[keyOf(PRIMED_PATH)] = data;
    redraw();
  },
  failed: function () {
    if (primedEpoch === state.epoch) generation += 1;
    primedEpoch = state.epoch;
    redraw();
  },
});
