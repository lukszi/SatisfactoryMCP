/* Every plan of the world as /api/plans lists it, with the built figure /api/plan/built adds:
 * the plans table, plan titles in the header and chat's cards all read it here. */

import { get } from "../../api/client";
import { state } from "../../app/state";
import { friendlyError } from "../../kit/toast";
import { changed } from "./state";

import type { PlanBuiltRow, PlansBuiltResponse, PlansResponse } from "../../api/shapes";

export const planIndex = {
  world: "",
  data: null as PlansResponse | null,
  built: {} as Record<string, PlanBuiltRow>,
  /** The built read answered, or failed: a plan page lands on its tab either way. */
  builtDone: false,
  error: "",
};

let seq = 0;

export function loadList(): void {
  const mine = ++seq;
  const world = state.world;
  get<PlansResponse>("/api/plans")
    .then(function (data) {
      if (mine !== seq) return;
      planIndex.world = world;
      planIndex.data = data;
      planIndex.builtDone = false;
      planIndex.error = "";
      changed();
      loadBuilt(mine);
    })
    .catch(function (reason) {
      if (mine !== seq) return;
      planIndex.error = friendlyError(reason);
      changed();
    });
}

function loadBuilt(mine: number): void {
  get<PlansBuiltResponse>("/api/plan/built")
    .then(function (data) {
      if (mine !== seq) return;
      const rows: Record<string, PlanBuiltRow> = {};
      data.rows.forEach(function (row) {
        rows[row.key] = row;
      });
      planIndex.built = rows;
      planIndex.builtDone = true;
      changed();
    })
    .catch(function () {
      /* the column stays "…"; the list itself is fine */
      if (mine !== seq) return;
      planIndex.builtDone = true;
      changed();
    });
}

/** The plan's name, or "" while the index has not listed it. */
export function planTitle(key: string): string {
  const row = planIndex.data
    ? planIndex.data.index.filter(function (r) {
        return r.key === key;
      })[0]
    : undefined;
  return row ? row.name : "";
}
