import { describe, expect, it } from "vitest";

import { activityLines, builtOf, byDay, dayLabel, exportItems, factoryOf, planIcon, productItem, sortPlans, statusOf } from "../../src/dash/planner/plans/model";

import type { ActivityRow, PlanBuiltRow, PlanIndexRow } from "../../src/api/shapes";

function plan(over: Partial<PlanIndexRow>): PlanIndexRow {
  return {
    key: "p",
    name: "Plan",
    rev: 3,
    objective: "min_machines",
    target_item: null,
    exports: [],
    rates: {},
    sited: false,
    factory: "",
    plan_id: "id",
    icon: null,
    last: { rev: 3, ts: 100, actor: { kind: "page", client: "", pid: 0, display: "" }, text: "created" },
    status: [],
    recorded: true,
    ...over,
  };
}

function built(over: Partial<PlanBuiltRow>): PlanBuiltRow {
  return { key: "p", rev: 3, mode: "factory", confidence: "sure", built: 4, built_max: 4, total: 8, percent: null, percent_max: null, figure: "4 / 8", text: "at Steel Valley", ...over };
}

function act(over: Partial<ActivityRow>): ActivityRow {
  return { id: "a", ts: 0, source: "journal", actor: { kind: "chat", client: "", pid: 1, display: "chat" }, kind: "plan.solve", plan: null, name: null, rev: null, text: "solved", args: null, count: 1, ...over };
}

describe("plan cards", () => {
  it("lists rated exports first and skips MW", () => {
    const row = plan({ rates: { Motor: 10 }, exports: ["MW", "Rotor", "Motor"] });
    expect(exportItems(row)).toEqual([
      { name: "Motor", rate: 10 },
      { name: "Rotor", rate: null },
    ]);
    expect(productItem(row)).toBe("Motor");
    expect(productItem(plan({ target_item: "Fuel" }))).toBe("Fuel");
    expect(productItem(plan({ exports: ["MW"] }))).toBeNull();
    const cls = (name: string) => (name === "Fuel" ? "Desc_Fuel_C" : null);
    expect(planIcon(plan({ target_item: "Fuel" }), cls)).toBe("Desc_Fuel_C");
    expect(planIcon(plan({ target_item: "Fuel", icon: "Desc_Rotor_C" }), cls)).toBe("Desc_Rotor_C");
    expect(planIcon(plan({ target_item: "Fuel", icon: "" }), cls)).toBeNull();
  });

  it("links only a named factory", () => {
    expect(factoryOf(plan({ factory: "Steel Valley" }))).toBe("Steel Valley");
    expect(factoryOf(plan({ factory: "/world" }))).toBe("");
    expect(factoryOf(plan({ factory: "cluster:3" }))).toBe("");
  });

  it("reads the built row of the same version only", () => {
    const row = plan({ sited: true });
    expect(builtOf(row, undefined).kind).toBe("pending");
    expect(builtOf(row, built({ rev: 2 })).kind).toBe("pending");
    expect(builtOf(row, built({}))).toMatchObject({ kind: "figure", figure: "4 / 8", pct: 50, done: false });
    expect(builtOf(row, built({ figure: "?", mode: "" })).kind).toBe("nosolve");
    expect(builtOf(row, built({ figure: "?", confidence: "unsure" })).kind).toBe("ask");
    expect(builtOf(plan({}), built({ built: null, figure: "–" })).kind).toBe("nosite");
  });

  it("shows one status: what moved wins over done", () => {
    const done = built({ built: 8, figure: "8 / 8" });
    expect(statusOf(plan({}), done)).toMatchObject({ text: "done", tone: "muted" });
    expect(statusOf(plan({ status: ["world moved", "field 3->2"] }), done)).toMatchObject({ text: "world moved", tone: "mid", title: "world moved · field 3->2" });
    expect(statusOf(plan({}), built({}))).toBeNull();
  });

  it("sorts by last change, name and built", () => {
    const a = plan({ key: "a", name: "B", last: { ...plan({}).last, ts: 5 } });
    const b = plan({ key: "b", name: "A", last: { ...plan({}).last, ts: 9 } });
    const c = plan({ key: "c", name: "C", last: { ...plan({}).last, ts: 1 } });
    const rows = { a: built({ key: "a", built: 2 }), b: built({ key: "b", built: null, figure: "–" }), c: built({ key: "c", built: 8 }) };
    expect(sortPlans([a, b, c], rows, "last").map((r) => r.key)).toEqual(["b", "a", "c"]);
    expect(sortPlans([a, b, c], rows, "name").map((r) => r.key)).toEqual(["b", "a", "c"]);
    expect(sortPlans([a, b, c], rows, "built").map((r) => r.key)).toEqual(["c", "a", "b"]);
  });
});

describe("activity timeline", () => {
  it("filters, puts the newest first and folds repeated views", () => {
    const view = act({ kind: "plan.view", plan: "p", text: "viewed" });
    const mine = act({ id: "m", actor: { kind: "page", client: "", pid: 0, display: "" }, source: "plan", text: "v2 banned X" });
    const lines = activityLines([mine, view, view], "all");
    expect(lines.map((r) => r.kind)).toEqual(["plan.view", "plan.solve"]);
    expect(lines[0]!.count).toBe(2);
    expect(activityLines([mine, view], "you").map((r) => r.id)).toEqual(["m"]);
  });

  it("groups by day in the browser's zone", () => {
    const now = new Date(2026, 9, 16, 12, 0).getTime();
    const at = (d: number, h: number) => new Date(2026, 9, d, h, 0).getTime() / 1000;
    expect(dayLabel(at(16, 1), now)).toBe("today");
    expect(dayLabel(at(15, 23), now)).toBe("yesterday");
    expect(dayLabel(at(12, 9), now)).toMatch(/12 Oct/);
    const days = byDay([act({ ts: at(16, 9) }), act({ ts: at(16, 8) }), act({ ts: at(15, 8) })], now);
    expect(days.map((d) => [d.day, d.rows.length])).toEqual([
      ["today", 2],
      ["yesterday", 1],
    ]);
  });
});
