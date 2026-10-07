import { afterEach, describe, expect, it } from "vitest";

import { actionTone, BLOCKED, isFine, learnStates, needsAction, stateSets, statesOf, stateTone } from "../../src/dash/machine-states";

afterEach(() => {
  learnStates({ ok_states: null, actionable_states: [] });
});

describe("machine states", () => {
  it("holds the defaults until the server says otherwise", () => {
    expect(stateSets()).toEqual({ ok: ["saturated", "unmonitored"], actionable: [] });
    expect(isFine("saturated")).toBe(true);
    expect(needsAction("starved")).toBe(false);
  });

  it("learns copies of the server's sets", () => {
    const actionable = ["starved", BLOCKED];
    learnStates({ ok_states: ["producing"], actionable_states: actionable });
    actionable.push("later");
    expect(stateSets()).toEqual({ ok: ["producing"], actionable: ["starved", BLOCKED] });
    learnStates({ actionable_states: [] });
    expect(stateSets().ok).toEqual(["saturated", "unmonitored"]);
  });

  it("tones blocked first, then action, then fine, else in between", () => {
    learnStates({ ok_states: ["producing"], actionable_states: ["starved"] });
    expect(stateTone(BLOCKED, false)).toBe("blocked");
    expect(stateTone("starved")).toBe("bad");
    expect(stateTone("starved", false)).toBe("mid");
    expect(stateTone("idle", true)).toBe("bad");
    expect(stateTone("producing")).toBe("ok");
    expect(stateTone("idle")).toBe("mid");
  });

  it("tones a list of counts: any action is bad, a blocked one alone is blocked", () => {
    learnStates({ ok_states: null, actionable_states: ["starved", BLOCKED] });
    const rows = [{ states: [{ state: BLOCKED, count: 2 }] }, { states: [{ state: "starved", count: 0 }] }];
    const all = statesOf(rows);
    expect(all).toHaveLength(2);
    expect(actionTone(all)).toBe("blocked");
    expect(actionTone([{ state: BLOCKED, count: 1 }, { state: "starved", count: 1 }])).toBe("bad");
    expect(actionTone([{ state: "producing", count: 4 }])).toBe("");
  });
});
