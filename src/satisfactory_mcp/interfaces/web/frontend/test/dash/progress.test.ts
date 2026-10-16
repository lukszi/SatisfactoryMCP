import { describe, expect, it } from "vitest";

import { redirectFor } from "../../src/app/shell/router";
import { altGroups, buyLists, coverTime, hoursMinutes, legacyProgressDash, mamTrees, openingPhase, planHref, progressSection, roadOf, stepWords } from "../../src/dash/progress/model";

import type { AlternateRow, MamRow, MilestoneRow, NextStep } from "../../src/api/shapes";

function step(over: Partial<NextStep>): NextStep {
  return { kind: "make", item: "Desc_X_C", name: "Supercomputer", amount: 150, per_min: 0, made_per_min: 0, system: null, status: null, for_unlocks: [], parts: ["Nuclear Pasta"], after: [], inputs_held: null, short: [], ...over };
}

function alt(name: string, source: string, machines: number, mw: number, raw: number): AlternateRow {
  return { recipe: "Recipe_" + name, name, item: "Desc_I_C", item_name: "Item", machine: "Assembler", source, drive: source === "drive" ? 16 : null, other: null, machines, mw, raw, parts: [] };
}

function ms(name: string, over: Partial<MilestoneRow>): MilestoneRow {
  return { cls: name, tier: 8, name, status: "READY", cost: [], short: [], unlocks: 0, unlock_names: [], blocked_by: [], opens_at: null, spoiler: false, ...over };
}

function node(name: string, over: Partial<MamRow>): MamRow {
  return { cls: name, name, tree: "Quartz", status: "READY", running_s: null, elapsed_pct: null, capability: null, cost: [], short: [], unlocks: 0, unlock_names: [], blocked_by: [], spoiler: false, ...over };
}

const short1 = [{ item: "Desc_S_C", name: "Supercomputer", amount: 50 }];

describe("progress address", () => {
  it("reads a section and sends the old sections to their new places", () => {
    expect(progressSection("alternates")).toBe("alternates");
    expect(progressSection("mam")).toBe("");
    expect(legacyProgressDash("progress", "mam")).toBe("progress/reference");
    expect(legacyProgressDash("progress", "elevator")).toBe("progress/gate");
    expect(legacyProgressDash("progress", "drives")).toBe("progress/alternates");
    expect(legacyProgressDash("progress", "shards")).toContain("item=power-shard");
    expect(legacyProgressDash("progress", "gate")).toBeNull();
    expect(legacyProgressDash("progress-sloops", "")).toContain("item=somersloop");
    expect(legacyProgressDash("progress-next", "")).toBe("progress/next");
    expect(redirectFor("progress/milestones")).toEqual({ dash: "progress/reference" });
    expect(redirectFor("progress/buy")).toBeNull();
  });

  it("links a step to the planner's new-plan card with its item and rate", () => {
    expect(planHref(step({ kind: "line", name: "Assembly Director System", per_min: 3.3333 }), 20)).toBe("planner?item=Assembly%20Director%20System&rate=3.333");
    expect(planHref(step({ amount: 150 }), 20)).toBe("planner?item=Supercomputer&rate=0.125");
  });
});

describe("the roadmap band", () => {
  const tiers = [1, 2, 3, 4, 5, 6, 7, 8, 9].map((t) => ({ tier: t, done: t < 7 ? 5 : 0, total: 5 }));

  it("hangs each tier under the phase that opens it", () => {
    expect([1, 2, 3, 4, 7, 8, 9].map(openingPhase)).toEqual([0, 0, 1, 1, 3, 3, 4]);
  });

  it("stops at the target while spoilers are off and folds the rest", () => {
    const road = roadOf(3, 4, 5, tiers, false);
    expect(road.stops.map((s) => s.state)).toEqual(["done", "done", "done", "done", "on"]);
    expect(road.later).toBe(1);
    expect(road.stops[4]!.tiers.map((t) => t.tier)).toEqual([9]);
    expect(roadOf(3, 4, 5, tiers, true).stops.length).toBe(6);
  });
});

describe("times and steps", () => {
  it("says covered, a time, or nothing", () => {
    expect(coverTime(0, 0)).toBe("covered");
    expect(coverTime(100, 0)).toBeNull();
    expect(coverTime(150, 2.5)).toBe("1 h");
    expect(hoursMinutes(95)).toBe("1 h 35 min");
    expect(hoursMinutes(12)).toBe("12 min");
  });

  it("tags each step with its one reason", () => {
    expect(stepWords(step({ for_unlocks: ["Nuclear Power", "Leading-Edge Production"] })).tag).toBe("for 2 unlocks");
    expect(stepWords(step({ for_unlocks: ["A"], inputs_held: true })).tag).toBe("inputs held");
    expect(stepWords(step({ kind: "unlock", item: null, name: "Leading-Edge Production", after: [1, 2], system: "milestone" }))).toMatchObject({ what: "unlock Leading-Edge Production", tag: "after 1, 2", go: "milestone" });
    expect(stepWords(step({ kind: "unlock", item: null, name: "X", status: "READY" })).tag).toBe("affordable");
    expect(stepWords(step({ kind: "line", name: "Assembly Director System", per_min: 3.3 })).what).toBe("build an Assembly Director System line");
    expect(stepWords(step({ kind: "grow", per_min: 3.3, made_per_min: 2.5 })).tag).toBe("2.5 of 3.3 /min");
  });
});

describe("alternates", () => {
  const rows = [alt("A", "unlocked", -10, -5, 3), alt("B", "drive", -2, -50, -1), alt("C", "drive", -8, 4, -9), alt("D", "pool", 3, -1, -1)];

  it("puts drives first, then the pool, then unlocked, each by the chosen cut", () => {
    const g = altGroups(rows, "machines");
    expect(g.map((x) => x[0])).toEqual(["offered by a drive on hand", "unlocked already"]);
    expect(g[0]![1].map((r) => r.name)).toEqual(["C", "B"]);
    expect(altGroups(rows, "power")[0]![1].map((r) => r.name)).toEqual(["B"]);
    expect(altGroups(rows, "raw").map((x) => x[1].map((r) => r.name))).toEqual([["C", "B"], ["D"]]);
  });
});

describe("buy now", () => {
  it("mixes affordable milestones and MAM nodes; almost is short of one item", () => {
    const m = [ms("Hoverpack", { tier: 7 }), ms("Nuclear Power", { status: "short", short: short1 }), ms("Two Short", { status: "short", short: [...short1, ...short1] }), ms("Shut", { opens_at: 4 }), ms("Done", { status: "DONE" })];
    const r = [node("Signal Systems", {}), node("Busy", { status: "RUNNING" }), node("Fabric", { status: "short", short: short1, tree: "Mycelia" })];
    const lists = buyLists(m, r);
    expect(lists.now.map((b) => b.name + "|" + b.system + "|" + b.group)).toEqual(["Hoverpack|milestone|tier 7", "Signal Systems|MAM|quartz"]);
    expect(lists.almost.map((b) => b.name)).toEqual(["Nuclear Power", "Fabric"]);
    expect(lists.almost[0]!.short).toEqual({ name: "Supercomputer", item: "Desc_S_C", amount: 50 });
  });

  it("counts MAM trees and names the running node with its share", () => {
    const trees = mamTrees([node("A", { status: "DONE" }), node("B", { status: "RUNNING", elapsed_pct: 42.5 }), node("C", {})]);
    expect(trees).toEqual([{ tree: "quartz", done: 1, total: 3, open: ["C"], running: { name: "B", pct: 42.5 } }]);
  });
});
