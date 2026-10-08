import { describe, expect, it, vi } from "vitest";

import { castsTreeShadows, FS, lightSwitches, parseLight, UNIFORMS } from "../../src/map/litlayer";

import type { LightHeader } from "../../src/map/litlayer";
import type { Sun } from "../../src/map/sun";

/* Importing these creates the map and reads the address bar; the switches need neither. */
vi.mock("../../src/map/map", () => ({ MAP_SHEET_PX: 8192, map: {} }));
vi.mock("../../src/map/leaflet", () => ({ L: {} }));
vi.mock("../../src/api/client", () => ({ tilePath: () => "" }));

function header(crowns: boolean, crownCell?: number): LightHeader {
  return {
    build: "b",
    max_z: 7,
    unlit_max_z: 7,
    params: { space: "linear", ambient: 0.3, sky: [1, 1, 1], sun: [1, 1, 1], tone_knee: 0.8, tone_white: 1.2, crowns: crowns },
    baked_sun: [225, 62.25],
    model: {
      dirs: 32,
      normalise_min_el: 35,
      shadow_soft_deg: 6,
      shadow_floor: 0.36,
      shadow_floor_knee: 0.1,
      shadow_fill: 0.35,
      hz_cells: 64,
      crown_cell: crownCell,
    },
  };
}

const ALL_ON: Sun = { azimuthDeg: 225, elevationDeg: 62, hour: null, shade: true, terrainShadows: true, treeShadows: true, sky: true };

describe("the shader's switches", () => {
  it("are all on for a crowned layer under the default settings", () => {
    expect(lightSwitches(header(true, 32), ALL_ON)).toEqual({ uLightOn: 1, uGroundSh: 1, uCrownSh: 1, uSkyOn: 1 });
  });

  it("follow each setting on its own", () => {
    const light = header(true, 32);
    expect(lightSwitches(light, { ...ALL_ON, shade: false })).toEqual({ uLightOn: 0, uGroundSh: 1, uCrownSh: 1, uSkyOn: 1 });
    expect(lightSwitches(light, { ...ALL_ON, terrainShadows: false })).toEqual({ uLightOn: 1, uGroundSh: 0, uCrownSh: 1, uSkyOn: 1 });
    expect(lightSwitches(light, { ...ALL_ON, treeShadows: false })).toEqual({ uLightOn: 1, uGroundSh: 1, uCrownSh: 0, uSkyOn: 1 });
    expect(lightSwitches(light, { ...ALL_ON, sky: false })).toEqual({ uLightOn: 1, uGroundSh: 1, uCrownSh: 1, uSkyOn: 0 });
  });

  it("read no crown cells on a layer that draws no crowns, or a pyramid that has none", () => {
    expect(lightSwitches(header(false, 32), ALL_ON).uCrownSh).toBe(0);
    expect(lightSwitches(header(true), ALL_ON).uCrownSh).toBe(0);
    expect(castsTreeShadows(header(true, 32))).toBe(true);
    expect(castsTreeShadows(header(false, 32))).toBe(false);
    expect(castsTreeShadows(header(true))).toBe(false);
  });

  it("are each a uniform the program looks up and the shader reads", () => {
    for (const name of Object.keys(lightSwitches(header(true, 32), ALL_ON))) {
      expect(UNIFORMS).toContain(name);
      expect(FS).toMatch(new RegExp("uniform float [^;]*\\b" + name + "\\b"));
    }
    expect(FS).not.toMatch(/uShadowOn|uCrownOn/);
  });
});

describe("the light header", () => {
  it("reads a well-formed one and drops anything else", () => {
    const light = header(true, 32);
    expect(parseLight(JSON.stringify(light))).toEqual(light);
    expect(parseLight(null)).toBeNull();
    expect(parseLight("{not json")).toBeNull();
    expect(parseLight(JSON.stringify({ params: {}, model: {} }))).toBeNull();
  });
});
