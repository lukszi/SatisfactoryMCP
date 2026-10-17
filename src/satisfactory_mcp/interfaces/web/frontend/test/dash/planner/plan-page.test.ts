import { describe, expect, it } from "vitest";

import { clockWords, landingTab } from "../../../src/dash/planner/plan/model";
import { ALL_STAGES, buildDash, legacyPlannerDash, parsePlannerAddress } from "../../../src/dash/planner/routes";

describe("plan addresses", () => {
  it("read the tab, the stage and the read-only version", () => {
    expect(parsePlannerAddress("p1").tab).toBe("");
    expect(parsePlannerAddress("p1/build?stage=3")).toMatchObject({ key: "p1", tab: "build", stage: 3 });
    expect(parsePlannerAddress("p1/build?stage=all").stage).toBe(ALL_STAGES);
    expect(parsePlannerAddress("p1/v4")).toMatchObject({ tab: "machines", viewedRev: 4 });
    expect(parsePlannerAddress("p1/place")).toMatchObject({ tab: "site", place: true });
  });

  it("send the old graph and track links to flows and to build", () => {
    expect(legacyPlannerDash("planner", "p1/graph")).toBe("planner/p1/flows");
    expect(legacyPlannerDash("planner", "p1/track/2")).toBe("planner/p1/build?stage=2");
    expect(legacyPlannerDash("planner", "p1/flows")).toBeNull();
    expect(buildDash("p1", 0, true)).toBe("planner/p1/build?pick=1");
  });
});

describe("the plan page", () => {
  it("lands on the plan's current step", () => {
    const done = { kind: "figure", figure: "4 / 4", pct: 100, percent: 100, done: true, text: "" } as const;
    expect(landingTab(false, "", { kind: "nosite" })).toBe("machines");
    expect(landingTab(true, "", { kind: "ask", text: "" })).toBe("build");
    expect(landingTab(true, "Rotor Hill", done)).toBe("flows");
  });

  it("writes the last machine's own clock on a second line", () => {
    expect(clockWords({ machines: 8, clock: 1, last_clock: null })).toEqual(["8 × 100%", ""]);
    expect(clockWords({ machines: 8, clock: 1, last_clock: 0.625 })).toEqual(["7 × 100%", "+ 1 × 62.5%"]);
  });
});
