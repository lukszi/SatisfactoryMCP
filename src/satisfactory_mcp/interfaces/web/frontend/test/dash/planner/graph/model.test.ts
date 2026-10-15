import ELK from "elkjs/lib/elk.bundled.js";
import { describe, expect, it, vi } from "vitest";

vi.mock("../../../../src/dash/planner/writes", () => ({ applyOps: vi.fn() }));

import { itemBalances, powerTotals } from "../../../../src/dash/planner/graph/balance";
import { layoutInput } from "../../../../src/dash/planner/graph/draw";
import { completed } from "../../../../src/dash/planner/graph/layout";
import { elkGraph, readElk } from "../../../../src/dash/planner/graph/layout-input";
import { edgeLabel, nodeForPick, readPlanGraph } from "../../../../src/dash/planner/graph/model";
import { moveNode, placeOp, positionsFor, resetLayout, resetLayoutOp, settle } from "../../../../src/dash/planner/graph/positions";
import { applyOps } from "../../../../src/dash/planner/writes";
import sample from "./plan-graph.sample.json";

import type { SolveResponse } from "../../../../src/api/shapes";

type Input = Pick<SolveResponse, "graph" | "rows">;

const SAMPLE = sample as unknown as Input;

const ROW = {
  id: "r1",
  depth: 0,
  building: "Smelter",
  recipe: "Iron Ingot",
  recipe_id: "Recipe_IngotIron_C",
  item: "Iron Ingot",
  machines: 4,
  clock: 0.75,
  last_clock: null,
  overclock_option: null,
  mw: -12,
  inputs: [],
  outputs: [],
  required: false,
};

const LEGACY = {
  rows: [ROW],
  graph: {
    nodes: [
      { id: "in:Iron Ore", kind: "input", label: "Iron Ore", detail: "from outside the plan", rank: 0, row: null, item: "Desc_OreIron_C" },
      { id: "r1", kind: "process", label: "Iron Ingot", detail: "Smelter ×4 · 75%", rank: 1, row: "r1", item: "Desc_IronIngot_C" },
      { id: "ex:Iron Ingot", kind: "export", label: "Iron Ingot", detail: "exported 90/min", rank: 2, row: null, item: "Desc_IronIngot_C" },
      { id: "ex:MW", kind: "export", label: "power", detail: "exported 5 MW", rank: 2, row: null, item: null },
    ],
    edges: [
      { source: "in:Iron Ore", target: "r1", item: "Iron Ore", per_min: 90, text: null },
      { source: "r1", target: "ex:Iron Ingot", item: "Iron Ingot", per_min: 90, text: null },
      { source: "r1", target: "ex:MW", item: "MW", per_min: 5, text: "5 MW" },
    ],
  },
} as unknown as Input;

describe("readPlanGraph, the server's shape", () => {
  const view = readPlanGraph(SAMPLE);
  const byId = (id: string) => view.nodes.find((n) => n.id === id)!;

  it("reads work, whole machines, the last machine's share and a percent clock", () => {
    expect(view.full).toBe(true);
    expect(byId("p:Recipe_Alternate_EnrichedCoal_C")).toMatchObject({
      kind: "process",
      building: "Assembler",
      built: 87,
      machines: 86.4,
      utilisation: 0.4,
      mw: -1293.12,
      row: "Recipe_Alternate_EnrichedCoal_C",
    });
    expect(byId("p:Recipe_Alternate_EnrichedCoal_C").clock).toBeCloseTo(0.993103, 6);
    expect(byId("ex:Desc_Plastic_C").clock).toBeNull();
  });

  it("keeps every row of a grouped node and finds it by any of them", () => {
    const miners = byId("p:x:Build_MinerMk2_C:Desc_Sulfur_C");
    expect(miners.rows.length).toBe(3);
    expect(nodeForPick(view, miners.rows[2]!)!.id).toBe(miners.id);
    expect(nodeForPick(view, "ex:Desc_Plastic_C")!.kind).toBe("export");
    expect(nodeForPick(view, "")).toBeUndefined();
  });

  it("reads power in MW and fluids in m3", () => {
    expect(byId("ex:power")).toMatchObject({ unit: "MW", itemId: "power" });
    const fuel = view.edges.find((e) => e.itemId === "Desc_LiquidFuel_C")!;
    expect(fuel).toMatchObject({ unit: "m3", group: "oil" });
    expect(edgeLabel(fuel)).toMatch(/^Fuel [\d,.]+ m³\/min$/);
    expect(edgeLabel({ itemName: "power", perMin: 5, unit: "MW", text: "" })).toBe("power 5 MW");
  });

  it("takes the buildings list as sent, and balances items off the edges", () => {
    expect(view.buildings[0]).toEqual({ building: "Fuel-Powered Generator", count: 726, groups: 2, mw: 181500 });
    const resin = itemBalances(view).find((b) => b.key === "Desc_PolymerResin_C")!;
    expect(resin.exported).toBe(10);
    expect(resin.surplus).toBe(2570);
    expect(resin.produced).toBeCloseTo(resin.exported + resin.surplus + resin.consumed, 6);
    expect(itemBalances(view).some((b) => b.unit === "MW")).toBe(false);
    const power = powerTotals(view);
    expect(power.made - power.draw).toBeGreaterThan(0);
  });

  it("treats an unknown group as other", () => {
    const odd = JSON.parse(JSON.stringify(SAMPLE));
    odd.graph.edges[0].item_group = "plasma";
    expect(readPlanGraph(odd).edges[0]!.group).toBe("other");
  });

  it("lays the real graph out with every edge routed and labelled", async () => {
    const input = layoutInput(view, {});
    const layout = completed(input, readElk(await new ELK().layout(elkGraph(input))));
    view.edges.forEach((e) => {
      expect(layout.routes[e.id]!.points.length).toBeGreaterThan(1);
      expect(layout.routes[e.id]!.label).not.toBeNull();
    });
    expect(layout.boxes["in:Desc_Water_C"] ?? layout.boxes["p:x:Build_WaterPump_C:Desc_Water_C"]).toBeDefined();
  }, 60000);
});

describe("readPlanGraph, older shape", () => {
  const view = readPlanGraph(LEGACY);

  it("falls back and fills process nodes from the build rows", () => {
    expect(view.full).toBe(false);
    expect(view.nodes[1]).toMatchObject({ kind: "process", building: "Smelter", built: 4, machines: 3, utilisation: 1, clock: 0.75, row: "r1" });
    expect(view.positions).toEqual({});
    expect(view.buildings).toEqual([{ building: "Smelter", count: 4, groups: 1, mw: -12 }]);
  });

  it("gives edges ids and puts power in its own group", () => {
    expect(view.edges.map((e) => e.id)).toEqual(["in:Iron Ore>r1:Iron Ore", "r1>ex:Iron Ingot:Iron Ingot", "r1>ex:MW:MW"]);
    expect(view.edges[2]).toMatchObject({ group: "power", unit: "MW", text: "5 MW" });
    expect(itemBalances(view).map((b) => b.name)).toEqual(["Iron Ingot", "Iron Ore"]);
  });

  it("keeps parallel edges apart", () => {
    const twice = JSON.parse(JSON.stringify(LEGACY));
    twice.graph.edges.push({ ...twice.graph.edges[0] });
    const ids = readPlanGraph(twice).edges.map((e) => e.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("copes with an empty graph", () => {
    expect(readPlanGraph({ rows: [], graph: { nodes: [], edges: [] } } as unknown as Input).nodes).toEqual([]);
  });
});

describe("node positions", () => {
  it("builds one place op per node, rounded, and the reset op", () => {
    expect(placeOp("p:a", [10.6, 2.2])).toEqual({ op: "place", node: "p:a", value: [11, 2] });
    expect(placeOp("p:a", null)).toEqual({ op: "place", node: "p:a", value: null });
    expect(resetLayoutOp()).toEqual({ op: "reset_layout" });
  });

  it("stores a drop where the server keeps places, and shows it at once", () => {
    vi.mocked(applyOps).mockClear();
    moveNode("plan-a", 7, "p:x", [10.4, 20.6], true);
    expect(applyOps).toHaveBeenCalledWith([{ op: "place", node: "p:x", value: [10, 21] }]);
    expect(positionsFor("plan-a", { "p:y": [1, 1] })).toEqual({ "p:x": [10, 21], "p:y": [1, 1] });
  });

  it("hands over to the stored set once a newer head has seen the drop", () => {
    moveNode("plan-b", 3, "p:x", [5, 5], true);
    settle("plan-b", 3);
    expect(positionsFor("plan-b", {})).toEqual({ "p:x": [5, 5] });
    settle("plan-b", 4);
    expect(positionsFor("plan-b", {})).toEqual({});
  });

  it("keeps places on the page alone against a server that stores none", () => {
    vi.mocked(applyOps).mockClear();
    moveNode("plan-c", 1, "p:x", [5, 5], false);
    settle("plan-c", 99);
    expect(applyOps).not.toHaveBeenCalled();
    expect(positionsFor("plan-c", {})).toEqual({ "p:x": [5, 5] });
  });

  it("resets", () => {
    vi.mocked(applyOps).mockClear();
    moveNode("plan-d", 1, "p:x", [7, 7], false);
    resetLayout("plan-d", true);
    expect(positionsFor("plan-d", {})).toEqual({});
    expect(applyOps).toHaveBeenCalledWith([{ op: "reset_layout" }]);
  });
});
