import { describe, expect, it } from "vitest";

import {
  BAND_STEP,
  BAND_TIERS,
  bandLightness,
  bandVeil,
  FADED_VEIL_SHARE,
  lowestFirst,
  notchLocal,
  placeLocal,
  placeRings,
} from "../../../src/map/drawn/outline-geometry";

import type { PortRow } from "../../../src/api/shapes";

function close(actual: number[][], expected: number[][]): void {
  expect(actual.length).toBe(expected.length);
  actual.forEach(function (p, i) {
    expect(p[0]).toBeCloseTo(expected[i]![0]!, 6);
    expect(p[1]).toBeCloseTo(expected[i]![1]!, 6);
  });
}

function port(flow: PortRow["flow"]): PortRow {
  return { name: "Output0", kind: "belt", flow: flow, x_m: 0, y_m: 4, z_m: 1, yaw: 90 };
}

describe("placeLocal", () => {
  it("turns +X towards +Y, the save's yaw convention, then moves to the position", () => {
    close(placeLocal(100, 200, 90, [[2, 0], [0, 1]]), [[100, 202], [99, 200]]);
  });

  it("reads a null yaw as unturned", () => {
    close(placeLocal(5, 5, null, [[1, 1]]), [[6, 6]]);
  });
});

describe("placeRings", () => {
  it("places every ring of a shape", () => {
    const rings = placeRings(10, 0, 180, [[[1, 0], [0, 1], [-1, 0]], [[2, 2], [3, 2], [3, 3]]]);
    close(rings[0]!, [[9, 0], [10, -1], [11, 0]]);
    close(rings[1]!, [[8, -2], [7, -2], [7, -3]]);
  });
});

describe("notchLocal", () => {
  it("points an output's apex out of the building, along the port's facing", () => {
    const tip = notchLocal(port("out"))[1]!;
    expect(tip[1]).toBeGreaterThan(4);
    expect(tip[0]).toBeCloseTo(0, 6);
  });

  it("points an input's apex into the building", () => {
    const tip = notchLocal(port("in"))[1]!;
    expect(tip[1]).toBeLessThan(4);
  });

  it("draws a two-way port as a diamond", () => {
    expect(notchLocal(port("any")).length).toBe(4);
  });
});

describe("bandVeil", () => {
  /* What a stack of veils leaves of the fill, lowest first: 1 - prod(1 - opacity). */
  function stacked(tiers: number[], faded: boolean): number {
    let under = 0;
    let left = 1;
    tiers.forEach(function (tier) {
      left *= 1 - bandVeil(tier, under, faded);
      under = tier;
    });
    return 1 - left;
  }

  it("lightens each tier by one even step", () => {
    [1, 2, 3, 4].forEach(function (tier) {
      expect(stacked(Array.from({ length: tier }, (_, i) => i + 1), false)).toBeCloseTo(tier * BAND_STEP, 9);
    });
  });

  it("reaches a tier's lightness when a tier under it was dropped", () => {
    expect(stacked([2, 4], false)).toBeCloseTo(stacked([1, 2, 3, 4], false), 9);
    expect(stacked([3], false)).toBeCloseTo(bandLightness(3, false), 9);
  });

  it("reads a tier past the table as the tallest, and fades with an idle building", () => {
    expect(bandLightness(7, false)).toBe(bandLightness(BAND_TIERS, false));
    expect(stacked([1, 2], true)).toBeCloseTo(2 * BAND_STEP * FADED_VEIL_SHARE, 9);
  });
});

describe("lowestFirst", () => {
  it("orders by height, keeps ties in place and puts an unknown height on the ground", () => {
    const rows = [
      { id: "upper", z_m: 15.4 },
      { id: "a", z_m: -16.6 },
      { id: "none", z_m: null },
      { id: "b", z_m: -16.6 },
    ];
    expect(lowestFirst(rows).map((r) => r.id)).toEqual(["none", "a", "b", "upper"]);
    expect(rows[0]!.id).toBe("upper");
  });
});
