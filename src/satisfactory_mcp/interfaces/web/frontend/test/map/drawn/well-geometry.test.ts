import { describe, expect, it } from "vitest";

import { satelliteRadius, satelliteSummary, wellSpokes } from "../../../src/map/drawn/well-geometry";

import type { SatelliteSpot } from "../../../src/map/drawn/well-geometry";

const SATS: Record<string, SatelliteSpot> = {
  a: { name: "a", purity: "pure", x_m: 10, y_m: 0 },
  b: { name: "b", purity: "impure", x_m: 0, y_m: 20 },
  c: { name: "c", purity: "pure", x_m: -5, y_m: -5 },
};

describe("wellSpokes", () => {
  it("runs one spoke from the core to each satellite it names, core end first", () => {
    const core = { satellites: ["a", "b"], x_m: 1, y_m: 2 };
    expect(wellSpokes(core, SATS)).toEqual([
      [
        [1, 2],
        [10, 0],
      ],
      [
        [1, 2],
        [0, 20],
      ],
    ]);
  });

  it("draws nothing to a satellite the payload does not carry", () => {
    expect(wellSpokes({ satellites: ["a", "gone"], x_m: 0, y_m: 0 }, SATS)).toHaveLength(1);
    expect(wellSpokes({ satellites: [], x_m: 0, y_m: 0 }, SATS)).toEqual([]);
  });
});

describe("satelliteSummary", () => {
  it("counts the satellites by purity, best first", () => {
    const core = { satellites: ["b", "a", "c"], x_m: 0, y_m: 0 };
    expect(satelliteSummary(core, SATS)).toBe("3 satellites: 2 pure, 1 impure");
  });

  it("says one satellite in the singular and counts only rows it has", () => {
    expect(satelliteSummary({ satellites: ["a", "gone"], x_m: 0, y_m: 0 }, SATS)).toBe("1 satellite: 1 pure");
    expect(satelliteSummary({ satellites: [], x_m: 0, y_m: 0 }, SATS)).toBe("0 satellites");
  });
});

describe("satelliteRadius", () => {
  it("sits a step under the node dot of the same purity, never below a visible ring", () => {
    expect(satelliteRadius(6)).toBe(4.5);
    expect(satelliteRadius(4.5)).toBe(3);
    expect(satelliteRadius(3)).toBe(2.5);
  });
});
