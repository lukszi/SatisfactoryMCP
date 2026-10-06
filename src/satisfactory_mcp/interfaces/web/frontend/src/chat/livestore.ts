/* One numbered list the page and chat both write (pins, asks): the refetch loop, the stale-row
 * refusal and the one-delete-at-a-time guard. See docs/planner-p4_contract.md §4. */

import { get } from "../api/client";
import { state } from "../app/state";
import { friendly } from "../kit/toast";

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
  refused: (reason: unknown) => R | null;
  once: (n: number, run: () => Promise<unknown>) => void;
}

export function liveStore<T extends ApiError, R extends Numbered>(path: ApiPath, rows: (data: T) => R[], field: string, fill?: (data: T) => void): LiveStore<T, R> {
  var data: T | null = null;
  var error = "";
  var world = "";
  var listeners: Array<() => void> = [];
  var inflight = false;
  var again = false;
  var dropping: Record<number, boolean> = {};

  function notify(): void {
    listeners.forEach(function (listener) {
      listener();
    });
  }

  function accept(next: T): void {
    data = next;
    error = "";
    world = state.world;
    notify();
  }

  function refetch(): void {
    if (inflight) {
      again = true;
      return;
    }
    inflight = true;
    var epoch = state.epoch;
    var asked = state.world;
    var done = function () {
      inflight = false;
      if (again) {
        again = false;
        refetch();
      }
    };
    var current = function () {
      return epoch === state.epoch && asked === state.world && !again;
    };
    get<T>(path)
      .then(function (got) {
        if (current()) (fill || accept)(got);
      })
      .catch(function (reason) {
        if (!current()) return;
        error = friendly(reason);
        world = asked;
        notify();
      })
      .then(done, done);
  }

  function replace(row: R): void {
    if (!data || world !== state.world) return;
    var list = rows(data);
    for (var i = 0; i < list.length; i++) if (list[i]!.n === row.n) list[i] = row;
    notify();
  }

  return {
    read: function () {
      return world === state.world ? { data: data, error: error } : { data: null, error: "" };
    },
    on: function (listener) {
      listeners.push(listener);
    },
    notify: notify,
    accept: accept,
    refetch: refetch,
    load: function () {
      if (world !== state.world && !inflight) refetch();
    },
    replace: replace,
    refused: function (reason) {
      var err = reason as StatusError;
      var body = err && (err.body as Record<string, unknown> | undefined);
      var row = err && err.status === 409 && body ? (body[field] as R | undefined) : undefined;
      if (row) {
        replace(row);
        return row;
      }
      refetch();
      return null;
    },
    once: function (n, run) {
      if (dropping[n]) return;
      dropping[n] = true;
      var done = function () {
        delete dropping[n];
        notify();
      };
      run().then(done, done);
    },
  };
}
