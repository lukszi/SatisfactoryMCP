import { describe, expect, it, vi } from "vitest";

import { castsTreeShadows, FS, lightControls, lightSwitches, parseLight, UNIFORMS } from "../../src/map/litlayer";
import { partState, TREES_IN_COLOUR } from "../../src/map/layercontrol/part-picker";

import type { LightHeader } from "../../src/map/litlayer";
import type { Sun } from "../../src/map/sun";

/* Importing these creates the map and reads the address bar; the switches need neither. */
vi.mock("../../src/map/map", () => ({ MAP_SHEET_PX: 8192, map: {} }));
vi.mock("../../src/map/leaflet", () => ({ L: {} }));
vi.mock("../../src/api/client", () => ({ tilePath: () => "" }));

function header(crowns: boolean, crownCell?: number, apart = false): LightHeader {
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
      hz_cells: apart ? 97 : 64,
      crown_cell: crownCell,
      ...(apart ? { titan_cell: 64, ao_cell: 96 } : {}),
    },
    ...(apart ? { parts: { trees: { max_z: 7, sparse: true } } } : {}),
  };
}

const ALL_ON: Sun = {
  azimuthDeg: 225, elevationDeg: 62, hour: null, shade: true, trees: true, terrainShadows: true, treeShadows: true, sky: true,
};
const ON = { uLightOn: 1, uGroundSh: 1, uCrownSh: 1, uSkyOn: 1, uTreesOn: 1 };

describe("the shader's switches", () => {
  it("are all on for a crowned layer under the default settings", () => {
    expect(lightSwitches(header(true, 32), ALL_ON)).toEqual(ON);
    expect(lightSwitches(header(true, 32, true), ALL_ON)).toEqual(ON);
  });

  it("follow each setting on its own", () => {
    const light = header(true, 32, true);
    expect(lightSwitches(light, { ...ALL_ON, shade: false })).toEqual({ ...ON, uLightOn: 0 });
    expect(lightSwitches(light, { ...ALL_ON, terrainShadows: false })).toEqual({ ...ON, uGroundSh: 0 });
    expect(lightSwitches(light, { ...ALL_ON, treeShadows: false })).toEqual({ ...ON, uCrownSh: 0 });
    expect(lightSwitches(light, { ...ALL_ON, sky: false })).toEqual({ ...ON, uSkyOn: 0 });
    expect(lightSwitches(light, { ...ALL_ON, trees: false })).toEqual({ ...ON, uTreesOn: 0 });
  });

  it("keep the trees on a layer whose trees are in its colour, whatever the setting", () => {
    expect(lightSwitches(header(true, 32), { ...ALL_ON, trees: false }).uTreesOn).toBe(1);
    expect(lightSwitches(header(false, 32), { ...ALL_ON, trees: false }).uTreesOn).toBe(1);
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

  it("read the trees, their cells and their sky occlusion only while the trees are on", () => {
    for (const name of ["tTrees", "uTitan", "uAo"]) expect(UNIFORMS).toContain(name);
    expect(FS).toMatch(/uniform int [^;]*\buTitan\b[^;]*\buAo\b/);
    expect(FS).toContain("if(uCrownSh*uTreesOn>0.5)");
    expect(FS).toContain("max(uGroundSh,uCrownSh*uTreesOn)");
    expect(FS).toContain("(uAo>0 && uTreesOn>0.5) ? raw(uAo) : 0.0");
    expect(FS).toContain("float a=t4.a*uTreesOn;");
  });
});

describe("the controls a light offers", () => {
  it("say whether the trees can be switched and whether their shadows are whole alone", () => {
    expect(lightControls(header(true, 32, true), "")).toEqual({ trees: true, apart: true, alone: true, off: "" });
    expect(lightControls(header(true, 32), "why")).toEqual({ trees: true, apart: false, alone: false, off: "why" });
    expect(lightControls(header(false, 32), "")).toMatchObject({ trees: false, apart: false });
  });

  it("show the trees row live, greyed or not at all", () => {
    expect(partState("mapTrees", lightControls(header(true, 32, true), ""))).toBe("");
    expect(partState("mapTrees", lightControls(header(true, 32), ""))).toBe(TREES_IN_COLOUR);
    expect(partState("mapTrees", lightControls(header(true, 32, true), "no WebGL2"))).toBe("no WebGL2");
    expect(partState("mapTrees", lightControls(header(false, 32), ""))).toBeNull();
    expect(partState("mapShade", lightControls(header(false, 32), ""))).toBe("");
    expect(partState("mapShade", null)).toBeNull();
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
