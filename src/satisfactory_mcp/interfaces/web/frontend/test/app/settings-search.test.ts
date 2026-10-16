import { describe, expect, it } from "vitest";

import { inSection, searchSettings } from "../../src/app/settings-dialog/sections";

const ROWS = [
  { key: "a", group: "spoilers", label: "show what is not unlocked yet", hint: "later tiers" },
  { key: "b", group: "power", label: "count biomass burners", hint: "hand-fed" },
  { key: "c", group: "map", label: "sun", hint: "where the sun stands" },
  { key: "d", group: "map", label: "tree shadows", hint: "trees shade the ground" },
];

describe("the settings search", () => {
  it("matches label or hint, grouped in section order", () => {
    const hits = searchSettings(ROWS, "Shad");
    expect(hits.map((h) => h.section.id)).toEqual(["map"]);
    expect(hits[0]!.rows.map((r) => r.key)).toEqual(["d"]);
    expect(searchSettings(ROWS, "e").map((h) => h.section.id)).toEqual(["spoilers", "power", "map"]);
  });

  it("returns nothing for a blank query or no match", () => {
    expect(searchSettings(ROWS, "  ")).toEqual([]);
    expect(searchSettings(ROWS, "zzz")).toEqual([]);
  });

  it("lists a section's rows by its group", () => {
    expect(inSection(ROWS, "map").map((r) => r.key)).toEqual(["c", "d"]);
  });
});
