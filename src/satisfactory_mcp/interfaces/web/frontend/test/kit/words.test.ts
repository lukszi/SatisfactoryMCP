import { describe, expect, it } from "vitest";

import { counted, gapText, objectiveText, staleText, version, WORDS } from "../../src/kit/words";

import type { TableAge } from "../../src/api/shapes";

function age(notes: string[], gap: string | null): TableAge {
  return { table: "nodes", behind: true, gap: gap, moved: 0, unjoinable: 0, notes: notes } as TableAge;
}

describe("words", () => {
  it("counts a noun, with a regular or a given plural", () => {
    expect(counted(1, "machine")).toBe("1 machine");
    expect(counted(0, "machine")).toBe("0 machines");
    expect(counted(2500, "machine")).toBe("2,500 machines");
    expect(counted(2, "kind not found yet", "kinds not found yet")).toBe("2 kinds not found yet");
    expect(counted(1, "kind not found yet", "kinds not found yet")).toBe("1 kind not found yet");
  });

  it("names an objective in words and leaves an unknown one alone", () => {
    expect(objectiveText("Plan A (max_mw)")).toBe("Plan A (max MW)");
    expect(objectiveText("Plan A (fastest)")).toBe("Plan A (fastest)");
    expect(objectiveText("max_mw")).toBe("max_mw");
  });

  it("reads a version gap in the game's terms", () => {
    expect(gapText("buildVersion 1->2, saveVersion 3->4")).toBe("build 1→2, save format 3->4");
    expect(version(12)).toBe("version v12");
  });

  it("says why map data is stale: its notes first, else the gap", () => {
    expect(staleText(age(["re-run mapgen", "then reload"], "buildVersion 1->2"))).toBe("re-run mapgen then reload");
    expect(staleText(age([], "buildVersion 1->2"))).toBe(WORDS.mapDataBehind + " (build 1→2)");
    expect(staleText(age([], null))).toBe(WORDS.mapDataBehind);
  });
});
