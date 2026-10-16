import { describe, expect, it } from "vitest";

import { pickCopyMode } from "../../src/map/copy-mode";

describe("pickCopyMode", () => {
  const drawable = ["map", "painted", "terrain"];

  it("copies the main map's picture when it has one", () => {
    expect(pickCopyMode("terrain", "painted", "map", drawable)).toBe("terrain");
  });

  it("uses the default before the main map has chosen, or when it is plain", () => {
    expect(pickCopyMode("", "painted", "map", drawable)).toBe("painted");
    expect(pickCopyMode("plain", "painted", "map", drawable)).toBe("painted");
  });

  it("falls back to the artwork, then any picture, then none", () => {
    expect(pickCopyMode("", "plain", "map", drawable)).toBe("map");
    expect(pickCopyMode("", null, "map", ["terrain"])).toBe("terrain");
    expect(pickCopyMode("", "painted", "map", [])).toBeNull();
  });
});
