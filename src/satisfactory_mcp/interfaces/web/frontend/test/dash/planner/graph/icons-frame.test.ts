import { describe, expect, it } from "vitest";

import { fitScale } from "../../../../src/dash/frame-fit";
import { iconIdFor } from "../../../../src/dash/planner/graph/icons";
import { readPlanGraph } from "../../../../src/dash/planner/graph/model";

import type { SolveResponse } from "../../../../src/api/shapes";

describe("node pictures", () => {
  it("shows a process's building, an item node's item, and nothing for power", () => {
    expect(iconIdFor({ kind: "process", buildingIcon: "Desc_GeneratorFuel_C", itemId: null })).toBe("Desc_GeneratorFuel_C");
    expect(iconIdFor({ kind: "process", buildingIcon: null, itemId: "Desc_Plastic_C" })).toBe("Desc_Plastic_C");
    expect(iconIdFor({ kind: "export", buildingIcon: null, itemId: "Desc_Plastic_C" })).toBe("Desc_Plastic_C");
    expect(iconIdFor({ kind: "sink", buildingIcon: "Desc_ResourceSink_C", itemId: "Desc_PolymerResin_C" })).toBe("Desc_PolymerResin_C");
    expect(iconIdFor({ kind: "export", buildingIcon: null, itemId: "power" })).toBeNull();
  });

  it("reads the building's picture off a process node", () => {
    const view = readPlanGraph({
      rows: [],
      graph: {
        nodes: [{ id: "p:g", kind: "process", building: "Fuel-Powered Generator", building_icon: "Desc_GeneratorFuel_C", utilisation: 1 }],
        edges: [],
      },
    } as unknown as Pick<SolveResponse, "graph" | "rows">);
    expect(view.nodes[0]!.buildingIcon).toBe("Desc_GeneratorFuel_C");
  });
});

describe("fitting a graph into its frame", () => {
  it("shrinks a graph wider than the room so every node shows", () => {
    expect(fitScale(1250, 700, 1809, 472, true)).toBeCloseTo(1250 / 1809, 6);
  });

  it("shrinks a tall graph to the room's height", () => {
    expect(fitScale(1250, 700, 900, 1400, true)).toBeCloseTo(0.5, 6);
  });

  it("grows a small graph only a little and never below the zoom floor", () => {
    expect(fitScale(1250, 700, 400, 200, true)).toBeCloseTo(14 / 12, 6);
    expect(fitScale(1250, 700, 40000, 300, true)).toBe(0.25);
  });

  it("keeps the old fill-the-width rule without fitAll", () => {
    expect(fitScale(1250, 700, 1809, 472, false)).toBe(1);
    expect(fitScale(1250, 700, 1200, 472, false)).toBeCloseTo(1250 / 1200, 6);
  });
});
