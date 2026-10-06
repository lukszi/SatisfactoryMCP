/* One reading of a machine state: fine, needs action, blocked or in between.
 * The sets come from /api/factories/health; the defaults hold until the first reply. */

export type Tone = "bad" | "blocked" | "mid" | "ok";

export interface StateSets {
  ok: string[];
  actionable: string[];
}

export var BLOCKED = "blocked";

var OK_DEFAULT = ["saturated", "unmonitored"];

var sets: StateSets = { ok: OK_DEFAULT, actionable: [] };

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

export function tone(state: string, actionable?: boolean): Tone {
  if (state === BLOCKED) return "blocked";
  if (actionable === undefined ? needsAction(state) : actionable) return "bad";
  return isFine(state) ? "ok" : "mid";
}

export interface StateCount {
  state: string;
  count: number;
}

export function statesOf(rows: { states: StateCount[] }[]): StateCount[] {
  var all: StateCount[] = [];
  rows.forEach(function (r) {
    all = all.concat(r.states);
  });
  return all;
}

export function actionTone(states: StateCount[]): "bad" | "blocked" | "" {
  var found: "bad" | "blocked" | "" = "";
  for (var i = 0; i < states.length; i++) {
    var s = states[i]!;
    if (!s.count || !needsAction(s.state)) continue;
    if (s.state !== BLOCKED) return "bad";
    found = "blocked";
  }
  return found;
}

export function toneClass(t: Tone): string {
  return t;
}
