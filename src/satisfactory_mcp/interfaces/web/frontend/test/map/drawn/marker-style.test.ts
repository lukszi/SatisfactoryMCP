import { describe, expect, it } from "vitest";

import { casingFor, luminance } from "../../../src/map/drawn/marker-style";

const LIGHT = "#f6e6ff";
const DARK = "#3b1030";

describe("casingFor", () => {
  it("cases the game's default grey markers light", () => {
    expect(casingFor("#575757", LIGHT, DARK)).toBe(LIGHT);
    expect(casingFor("#666667", LIGHT, DARK)).toBe(LIGHT);
  });

  it("cases a light marker dark", () => {
    expect(casingFor("#ffffff", LIGHT, DARK)).toBe(DARK);
    expect(casingFor("#ffd000", LIGHT, DARK)).toBe(DARK);
  });

  it("falls back to light for a colour it cannot read", () => {
    expect(casingFor(null, LIGHT, DARK)).toBe(LIGHT);
    expect(casingFor("red", LIGHT, DARK)).toBe(LIGHT);
  });
});

describe("luminance", () => {
  it("runs from black to white", () => {
    expect(luminance("#000000")).toBe(0);
    expect(luminance("#ffffff")).toBeCloseTo(1, 6);
  });
});
