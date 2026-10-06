/* One numbered list the page and chat both write (pins, asks): the refetch loop, the stale-row
 * refusal and the one-delete-at-a-time guard. See docs/planner-p4_contract.md §4. */

import { get } from "../api/client";
import { createListeners } from "../app/listeners";
import { state } from "../app/state";
import { friendlyError } from "../kit/toast";

import type { ApiError, ApiPath, StatusError } from "../api/client";

interface Numbered {
  n: number;
}

export interface LiveStore<T extends ApiError, R extends Numbered> {
  read: () => { data: T | null; error: string };
  on: (listener: () => void) => void;
  notify: () => void;
  accept: (data: T) => void;
  refetch: () => void;
  load: () => void;
  replace: (row: R) => void;
  recoverFromConflict: (reason: unknown) => R | null;
  deleteOnce: (n: number, run: () => Promise<unknown>) => void;
}

/** `conflictRowField` names the row a 409 answers with; `acceptOverride` takes each fetched
 *  list instead of `accept`, for a store that draws before it accepts. */
export function liveStore<T extends ApiError, R extends Numbered>(path: ApiPath, rows: (data: T) => R[], conflictRowField: string, acceptOverride?: (data: T) => void): LiveStore<T, R> {
  let data: T | null = null;
  let error = "";
  let world = "";
  const listeners = createListeners();
  let inflight = false;
  let refetchQueued = false;
  const deletesInFlight: Record<number, boolean> = {};

  function notify(): void {
    listeners.emit();
  }

  function accept(next: T): void {
    data = next;
    error = "";
    world = state.world;
    notify();
  }

  function refetch(): void {
    if (inflight) {
      refetchQueued = true;
      return;
    }
    inflight = true;
    const epoch = state.epoch;
    const asked = state.world;
    const done = function () {
      inflight = false;
      if (refetchQueued) {
        refetchQueued = false;
        refetch();
      }
    };
    const current = function () {
      return epoch === state.epoch && asked === state.world && !refetchQueued;
    };
    get<T>(path)
      .then(function (got) {
        if (current()) (acceptOverride || accept)(got);
      })
      .catch(function (reason) {
        if (!current()) return;
        error = friendlyError(reason);
        world = asked;
        notify();
      })
      .then(done, done);
  }

  function replace(row: R): void {
    if (!data || world !== state.world) return;
    const list = rows(data);
    for (let i = 0; i < list.length; i++) if (list[i]!.n === row.n) list[i] = row;
    notify();
  }

  return {
    read: function () {
      return world === state.world ? { data: data, error: error } : { data: null, error: "" };
    },
    on: listeners.on,
    notify: notify,
    accept: accept,
    refetch: refetch,
    load: function () {
      if (world !== state.world && !inflight) refetch();
    },
    replace: replace,
    recoverFromConflict: function (reason) {
      const err = reason as StatusError;
      const body = err && (err.body as Record<string, unknown> | undefined);
      const row = err && err.status === 409 && body ? (body[conflictRowField] as R | undefined) : undefined;
      if (row) {
        replace(row);
        return row;
      }
      refetch();
      return null;
    },
    deleteOnce: function (n, run) {
      if (deletesInFlight[n]) return;
      deletesInFlight[n] = true;
      const done = function () {
        delete deletesInFlight[n];
        notify();
      };
      run().then(done, done);
    },
  };
}
