import { describe, expect, it } from "vitest";

import { itemLines, layoutInput } from "../../../src/dash/prodgraph/draw";
import { builtLine, tipLines } from "../../../src/dash/prodgraph/interact";
import { readGraph } from "../../../src/dash/prodgraph/model";
import { measuredRing } from "../../../src/dash/prodgraph/ring";

import type { Loose } from "../../../src/dash/prodgraph/model";

const process = {
  id: "p:Recipe_IngotIron_C",
  kind: "process",
  label: "Iron Ingot",
  detail: "Iron Ingot",
  rank: 1,
  item_id: "Desc_IronIngot_C",
  item_name: "Iron Ingot",
  recipe_id: "Recipe_IngotIron_C",
  recipe_name: "Iron Ingot",
  building: "Smelter",
  building_icon: "Desc_SmelterMk1_C",
  built: 2,
  machines: 2,
  clock: 100,
  mw: -8,
  utilisation: 1,
  per_min: null,
  unit: "items",
  note: null,
  productivity: 0.75,
  monitored: 1,
  states: { saturated: 1, starved: 1 },
  bbox_m: [1, 2, 3, 4],
};

const box = { ...process, id: "box:Desc_IronIngot_C", kind: "export", rank: 2, building: null, building_icon: null, built: 0, machines: 0, clock: null, mw: 0, per_min: 45, note: "to storage", productivity: null, monitored: 0, states: {}, bbox_m: null };

const factory: Loose = {
  title: "t",
  nodes: [process, box],
  edges: [{ id: "p:Recipe_IngotIron_C>box:Desc_IronIngot_C:Desc_IronIngot_C", source: "p:Recipe_IngotIron_C", target: "box:Desc_IronIngot_C", item_id: "Desc_IronIngot_C", item_name: "Iron Ingot", item_group: "metal", per_min: 45, unit: "items", text: null }],
  buildings: [{ building: "Smelter", count: 2, groups: 1, mw: -8 }],
  mw_draw: 8,
  mw_generated: 0,
};

describe("a built factory's graph", function () {
  const view = readGraph(factory);
  const smelt = view.nodes[0]!;

  it("reads the factory fields beside the plan's", function () {
    expect(view.full).toBe(true);
    expect(smelt.productivity).toBe(0.75);
    expect(smelt.states).toEqual({ saturated: 1, starved: 1 });
    expect(smelt.bbox).toEqual([1, 2, 3, 4]);
    expect(smelt.clock).toBe(1);
    expect(view.edges[0]!.group).toBe("metal");
    expect(view.positions).toEqual({});
  });

  it("says where an end's rate goes", function () {
    expect(itemLines(view.nodes[1]!, view)).toEqual(["Iron Ingot", "45/min · to storage"]);
  });

  it("puts the measured share and the states in the tip", function () {
    expect(builtLine(smelt)).toBe("producing 75% of the time of 1 monitored · 1 saturated, 1 starved");
    expect(tipLines(view, smelt).slice(-1)[0]).toBe(builtLine(smelt));
  });

  it("lays out like a plan", function () {
    const input = layoutInput(view, {});
    expect(input.nodes.map((n) => n.rank)).toEqual([1, 2]);
    expect(input.edges).toHaveLength(1);
  });
});

describe("the measured ring", function () {
  it("is full flat out, an arc otherwise, empty when idle", function () {
    expect(measuredRing(1)).toEqual({ fraction: 1, tone: "full" });
    expect(measuredRing(0.6)).toEqual({ fraction: 0.6, tone: "most" });
    expect(measuredRing(0.2).tone).toBe("part");
    expect(measuredRing(0).fraction).toBe(0);
  });
});
