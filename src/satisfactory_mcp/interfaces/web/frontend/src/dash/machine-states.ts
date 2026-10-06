/* One reading of a machine state: fine, needs action, blocked or in between.
 * The sets come from /api/factories/health; the defaults hold until the first reply. */

export type StateTone = "bad" | "blocked" | "mid" | "ok";

export interface StateSets {
  ok: string[];
  actionable: string[];
}

export const BLOCKED = "blocked";

const OK_DEFAULT = ["saturated", "unmonitored"];

let sets: StateSets = { ok: OK_DEFAULT, actionable: [] };

export function learnStates(health: { ok_states?: string[] | null; actionable_states: string[] }): void {
  sets = {
    ok: (health.ok_states || OK_DEFAULT).slice(),
    actionable: health.actionable_states.slice(),
  };
}

export function stateSets(): StateSets {
  return sets;
}

export function isFine(state: string): boolean {
  return sets.ok.indexOf(state) >= 0;
}

export function needsAction(state: string): boolean {
  return sets.actionable.indexOf(state) >= 0;
}

export function stateTone(state: string, actionable?: boolean): StateTone {
  if (state === BLOCKED) return "blocked";
  if (actionable === undefined ? needsAction(state) : actionable) return "bad";
  return isFine(state) ? "ok" : "mid";
}

export interface StateCount {
  state: string;
  count: number;
}

export function statesOf(rows: { states: StateCount[] }[]): StateCount[] {
  let all: StateCount[] = [];
  rows.forEach(function (row) {
    all = all.concat(row.states);
  });
  return all;
}

export function actionTone(states: StateCount[]): "bad" | "blocked" | "" {
  let found: "bad" | "blocked" | "" = "";
  for (const entry of states) {
    if (!entry.count || !needsAction(entry.state)) continue;
    if (entry.state !== BLOCKED) return "bad";
    found = "blocked";
  }
  return found;
}
