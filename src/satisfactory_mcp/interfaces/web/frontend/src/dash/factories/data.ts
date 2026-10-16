/* The reads the factory pages make beyond the health read: one fetch per path and save, kept
 * while the save stays, each landing asking the page to draw again. */

import { get, isNotFound } from "../../api/client";
import { state } from "../../app/state";
import { html, type Safe } from "../../kit/html";
import { btn } from "../../kit/ui/controls";
import { emptyLine } from "../../kit/ui/text";
import { friendlyError } from "../../kit/toast";
import { requestRender } from "../actions";

import type { ApiError, ApiUrl } from "../../api/client";

export interface Slot<T> {
  busy: boolean;
  failure: unknown;
  data: T | null;
  /** A 404 read as "none here". */
  missing: boolean;
}

interface Held {
  epoch: number;
  slot: Slot<unknown>;
}

const held: Record<string, Held> = {};

function start(path: string): Slot<unknown> {
  const slot: Slot<unknown> = { busy: true, failure: null, data: null, missing: false };
  const epoch = state.epoch;
  held[path] = { epoch, slot };
  get<ApiError>(path as ApiUrl)
    .then(function (body) {
      slot.data = body;
    })
    .catch(function (failure) {
      if (isNotFound(failure)) slot.missing = true;
      else slot.failure = failure;
    })
    .then(function () {
      slot.busy = false;
      if (held[path]?.slot === slot && epoch === state.epoch) requestRender();
    });
  return slot;
}

/** The read of `path` for this save, started on first use. */
export function read<T>(path: string): Slot<T> {
  const h = held[path];
  if (h && h.epoch === state.epoch) return h.slot as Slot<T>;
  return start(path) as Slot<T>;
}

/** Forget a read so the next use fetches it again. */
export function forget(path: string): void {
  delete held[path];
}

export function q(path: string, params: Record<string, string>): string {
  const pairs = Object.keys(params).map((k) => k + "=" + encodeURIComponent(params[k]!));
  return path + (pairs.length ? "?" + pairs.join("&") : "");
}

/** The loading or error line for a read that has no data yet; null once it has. */
export function pending(slot: Slot<unknown>, what: string, path: string): Safe | null {
  if (slot.data || slot.missing) return null;
  if (slot.failure) return html`<div class="fpend">${emptyLine(what + " could not be read", friendlyError(slot.failure))}${btn("try again", { data: { retry: path } })}</div>`;
  return html`<p class="empty fpend is-busy" aria-busy="true"><span class="spinner" aria-hidden="true"></span> loading ${what}…</p>`;
}

/** Wires the "try again" buttons `pending` draws. */
export function wireRetry(root: HTMLElement): void {
  root.addEventListener("click", function (event) {
    const b = (event.target as Element).closest<HTMLElement>("[data-retry]");
    if (!b || !root.contains(b)) return;
    forget(b.dataset.retry || "");
    requestRender();
  });
}
