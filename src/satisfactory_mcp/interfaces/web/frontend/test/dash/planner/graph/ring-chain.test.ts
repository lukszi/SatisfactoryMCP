import { describe, expect, it } from "vitest";

import { chainOf } from "../../../../src/dash/planner/graph/chain";
import { lastShare } from "../../../../src/dash/planner/graph/model";
import { ringFor, ringPath } from "../../../../src/dash/planner/graph/ring";

describe("ringFor", () => {
  it("is a full ring for whole machines", () => {
    expect(ringFor(4, 1)).toEqual({ fraction: 1, tone: "full" });
    expect(ringFor(2, 0.9999)).toEqual({ fraction: 1, tone: "full" });
  });

  it("is the share of the last machine otherwise", () => {
    expect(ringFor(15.31, 0.31)).toEqual({ fraction: 0.31, tone: "part" });
    expect(ringFor(0.8, 0.8)).toEqual({ fraction: 0.8, tone: "most" });
  });

  it("splits work into the last machine's share", () => {
    expect(lastShare(16 * 0.957)).toBeCloseTo(0.312, 3);
    expect(lastShare(3)).toBe(1);
    expect(lastShare(2.9999)).toBe(1);
  });

  it("is empty with no machines", () => {
    expect(ringFor(0, 1).fraction).toBe(0);
  });
});

describe("ringPath", () => {
  it("draws a full circle as two half arcs back to the top", () => {
    expect(ringPath(26, 26, 20, 1)).toBe("M26,6 A20,20 0 1 1 26,46 A20,20 0 1 1 26,6");
  });

  it("ends a quarter at three o'clock with the short arc", () => {
    expect(ringPath(0, 0, 10, 0.25)).toBe("M0,-10 A10,10 0 0 1 10,0");
  });

  it("takes the long arc past half", () => {
    expect(ringPath(0, 0, 10, 0.75)).toBe("M0,-10 A10,10 0 1 1 -10,0");
  });

  it("draws nothing for nothing", () => {
    expect(ringPath(0, 0, 10, 0)).toBe("");
  });
});

describe("chainOf", () => {
  const edges = [
    { id: "a>b", source: "a", target: "b" },
    { id: "b>c", source: "b", target: "c" },
    { id: "x>c", source: "x", target: "c" },
    { id: "c>d", source: "c", target: "d" },
    { id: "b>e", source: "b", target: "e" },
  ];

  it("lights everything upstream and downstream of the node", () => {
    const chain = chainOf("c", edges);
    expect([...chain.nodes].sort()).toEqual(["a", "b", "c", "d", "x"]);
    expect([...chain.edges].sort()).toEqual(["a>b", "b>c", "c>d", "x>c"]);
  });

  it("leaves a sibling branch dark", () => {
    const chain = chainOf("c", edges);
    expect(chain.nodes.has("e")).toBe(false);
    expect(chain.edges.has("b>e")).toBe(false);
  });

  it("survives a cycle", () => {
    const chain = chainOf("a", [
      { id: "1", source: "a", target: "b" },
      { id: "2", source: "b", target: "a" },
    ]);
    expect([...chain.nodes].sort()).toEqual(["a", "b"]);
    expect(chain.edges.size).toBe(2);
  });
});
