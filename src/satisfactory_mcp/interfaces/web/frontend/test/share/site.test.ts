import { describe, expect, it } from "vitest";

import { hasTrees, siteProbe, siteTileUrl, useSite } from "../../src/share/site";

/* Tree tiles present: z0 (bit 0) and z1 x=1 y=0 (bit 1 + 0*2 + 1 = 2). */
const TREES = btoa(String.fromCharCode(0b101));

useSite({
  layers: {
    painted: {
      headers: { "X-Map-Tile-Max-Z": "7", "X-Map-Build": "abc" },
      dir: "t/painted-abc",
      light: "t/light-def",
      suffix: { unlit: ".webp", trees: ".webp" },
      trees: TREES,
    },
    terrain: { headers: { "X-Map-Build": "t1" }, dir: "t/terrain-t1", light: "", suffix: { unlit: ".png" } },
  },
});

describe("the shared map's files", () => {
  it("answers the probe from the manifest, and 204 for a layer it does not have", () => {
    const probe = siteProbe("painted");
    expect(probe.status).toBe(200);
    expect(probe.headers.get("X-Map-Build")).toBe("abc");
    expect(siteProbe("map").status).toBe(204);
  });

  it("builds plain paths, with no query string", () => {
    expect(siteTileUrl("painted", 3, 1, 2, "", false)).toBe("t/painted-abc/tiles/3/1_2.png");
    expect(siteTileUrl("painted", 3, 1, 2, "", true)).toBe("t/painted-abc/tiles@2x/3/1_2.png");
    expect(siteTileUrl("painted", 3, 1, 2, "unlit", false)).toBe("t/painted-abc/unlit/3/1_2.webp");
    expect(siteTileUrl("terrain", 0, 0, 0, "unlit", false)).toBe("t/terrain-t1/unlit/0/0_0.png");
    expect(siteTileUrl("painted", 4, 0, 1, "nrm", false)).toBe("t/light-def/4/0_1.nrm.webp");
    expect(siteTileUrl("painted", 4, 0, 1, "hz", false)).toBe("t/light-def/4/0_1.hz.webp");
    expect(siteTileUrl("nothing", 0, 0, 0, "", false)).toBe("");
  });

  it("asks only for the tree tiles the export lists, so an empty one is never a 404", () => {
    expect(hasTrees("painted", 0, 0, 0)).toBe(true);
    expect(hasTrees("painted", 1, 0, 0)).toBe(false);
    expect(hasTrees("painted", 1, 1, 0)).toBe(true);
    expect(hasTrees("painted", 7, 127, 127)).toBe(false);
    expect(siteTileUrl("painted", 1, 0, 0, "trees", false)).toBe("");
    expect(siteTileUrl("painted", 1, 1, 0, "trees", false)).toBe("t/painted-abc/trees/1/1_0.webp");
    expect(hasTrees("terrain", 5, 3, 3)).toBe(true);
  });
});
