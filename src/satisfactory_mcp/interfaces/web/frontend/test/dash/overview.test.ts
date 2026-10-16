import { describe, expect, it } from "vitest";

import { learnStates } from "../../src/dash/machine-states";
import { cellOrder, gateTally, machineTotals, moreDash, plansTally, todoOf } from "../../src/dash/overview/model";

import type { CircuitsResponse, FactoryHealthRow, GateView, MachineIssue, MachineRef, PlanBuiltRow, PlanIndexRow } from "../../src/api/shapes";

learnStates({ ok_states: ["saturated", "unmonitored"], actionable_states: ["starved", "blocked", "no recipe"] });

function issue(state: string, what: string): MachineIssue {
  return { instance: what + state, state, uptime: null, what, cause: [], x_m: null, y_m: null };
}

function factory(name: string, states: [string, number][], over?: Partial<FactoryHealthRow>): FactoryHealthRow {
  const actionable = states.filter((s) => s[0] !== "running" && s[0] !== "saturated").reduce((n, s) => n + s[1], 0);
  return {
    name,
    machines: states.reduce((n, s) => n + s[1], 0),
    states: states.map(([state, count]) => ({ state, count })),
    actionable,
    uptime: 0.9,
    worst_actionable: [],
    ...over,
  } as FactoryHealthRow;
}

function ref(instance: string, name: string, place: Partial<MachineRef>): MachineRef {
  return { instance, name, circuit: null, factory: null, region: null, icon: null, x_m: null, y_m: null, ...place };
}

function circuits(over: Partial<CircuitsResponse>): CircuitsResponse {
  return { starved: [], unwired: [], unwired_generators: [], no_generator: [], ...over } as unknown as CircuitsResponse;
}

describe("factory cells", () => {
  it("puts need action first, blocked after it, then the fine ones by uptime", () => {
    const rows = [
      factory("Fine low", [["running", 4]], { uptime: 0.5 }),
      factory("Blocked", [["blocked", 5]]),
      factory("Starved", [["starved", 1]]),
      factory("Fine high", [["running", 4]], { uptime: 0.99 }),
      factory("Starved more", [["starved", 3]]),
    ];
    expect(cellOrder(rows).map((r) => r.name)).toEqual(["Starved more", "Starved", "Blocked", "Fine low", "Fine high"]);
    expect(machineTotals(rows)).toEqual({ machines: 17, need: 9 });
  });
});

describe("do next", () => {
  const rows = [
    factory("Rotors", [["blocked", 2]], { worst_actionable: [issue("blocked", "Assembler"), issue("blocked", "Assembler")] }),
    factory("Plates", [["starved", 1]], { worst_actionable: [issue("starved", "Constructor")] }),
  ];
  const power = circuits({
    unwired: [ref("a", "Smelter", { factory: "Ingots" })],
    starved: [{ instance: "g", name: "Coal Generator", mw: 75, missing: [], cause: "no fuel", circuit: 0, factory: null, region: null, icon: null, x_m: 1, y_m: 2 }],
    unwired_generators: [ref("g2", "Coal Generator", {})],
  });

  it("ranks machines stopped by power, then need action, starved generators, blocked last", () => {
    const todo = todoOf(power, rows);
    expect(todo.map((t) => t.what)).toEqual(["no wire", "starved", "starved generator", "blocked"]);
    expect(todo[0]!.dash).toBe("factories/Ingots/power");
    expect(todo[1]!.dash).toBe("factories/Plates/machines");
    expect(todo[2]!.dash).toBe("power/1");
    expect(todo[3]!.n).toBe(2);
  });

  it("works from either read alone and sends +N more where the rest is", () => {
    expect(todoOf(null, rows).map((t) => t.what)).toEqual(["starved", "blocked"]);
    const powerOnly = todoOf(power, null);
    expect(powerOnly.every((t) => t.power)).toBe(true);
    expect(moreDash(powerOnly)).toBe("power");
    expect(moreDash(todoOf(power, rows).slice(3))).toBe("factories");
  });
});

describe("progress tallies", () => {
  it("counts each owed part up to what is owed", () => {
    const view = { parts: [{ held: 120, owed: 100 }, { held: 10, owed: 50 }] } as unknown as GateView;
    expect(gateTally(view)).toEqual({ done: 110, of: 150 });
  });

  it("sums machines built over plans whose figure counts them at this version", () => {
    const plans = [
      { key: "a", rev: 2 },
      { key: "b", rev: 1 },
      { key: "c", rev: 1 },
      { key: "d", rev: 1 },
    ] as PlanIndexRow[];
    const built: Record<string, PlanBuiltRow> = {
      a: { key: "a", rev: 2, built: 3, total: 5, figure: "3 / 5" } as PlanBuiltRow,
      b: { key: "b", rev: 0, built: 4, total: 4, figure: "4 / 4" } as PlanBuiltRow,
      c: { key: "c", rev: 1, built: null, total: 6, figure: "â€“" } as PlanBuiltRow,
      d: { key: "d", rev: 1, built: 9, total: 8, figure: "9 / 8" } as PlanBuiltRow,
    };
    expect(plansTally(plans, built)).toEqual({ done: 11, of: 13 });
  });
});
