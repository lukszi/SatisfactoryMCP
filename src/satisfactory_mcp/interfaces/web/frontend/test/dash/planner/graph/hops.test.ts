import { describe, expect, it } from "vitest";

import { HOP_R, hopPath, hoppedPaths, hopsOn, hopTargets, verticalsOf } from "../../../../src/dash/planner/graph/hops";

import type { Pt } from "../../../../src/dash/planner/graph/router";

const arcs = (d: string) => (d.match(/A/g) || []).length;
const line = (...xy: number[]): Pt[] => {
  const out: Pt[] = [];
  for (let i = 0; i < xy.length; i += 2) out.push({ x: xy[i]!, y: xy[i + 1]! });
  return out;
};

describe("line jumps", () => {
  it("jumps a crossing vertical once, centred on it, arcing upwards", () => {
    const d = hopPath(line(0, 50, 100, 50), verticalsOf(line(40, 0, 40, 100)));
    expect(arcs(d)).toBe(1);
    expect(d).toBe("M0,50 L35.5,50 A4.5,4.5 0 0 1 44.5,50 L100,50");
  });

  it("arcs upwards when travelling leftwards too", () => {
    const d = hopPath(line(100, 50, 0, 50), verticalsOf(line(40, 0, 40, 100)));
    expect(d).toBe("M100,50 L44.5,50 A4.5,4.5 0 0 0 35.5,50 L0,50");
  });

  it("counts one jump per crossing and merges jumps closer than their width", () => {
    const walls = verticalsOf(line(20, 0, 20, 100)).concat(verticalsOf(line(60, 0, 60, 100)), verticalsOf(line(64, 0, 64, 100)));
    const d = hopPath(line(0, 50, 100, 50), walls);
    expect(hopsOn({ x: 0, y: 50 }, { x: 100, y: 50 }, walls, HOP_R)).toEqual([20, 60, 64]);
    expect(arcs(d)).toBe(2);
    expect(d).toContain("L55.5,50 A6.5,4.5 0 0 1 68.5,50");
  });

  it("never jumps where a line only ends on another, at its own bends, or along a shared corridor", () => {
    const own = line(0, 50, 50, 50, 50, 90, 100, 90);
    expect(hopPath(own, verticalsOf(line(30, 50, 30, 100)))).not.toContain("A");
    expect(hopPath(own, verticalsOf(line(30, 0, 30, 50)))).not.toContain("A");
    expect(hopPath(own, verticalsOf(line(50, 0, 50, 70)))).not.toContain("A");
    expect(hopPath(own, verticalsOf(line(48, 0, 48, 70)))).not.toContain("A");
    expect(hopPath(own, verticalsOf(line(0, 50, 50, 50)))).not.toContain("A");
    expect(hopPath(line(0, 0, 0, 100), verticalsOf(line(0, 20, 0, 80)))).not.toContain("A");
  });

  it("draws a line with no crossings exactly as its points", () => {
    expect(hopPath(line(0, 0, 10, 0, 10, 20), [])).toBe("M0,0 L10,0 L10,20");
  });

  it("jumps only other edges, the horizontal one over the vertical one, the same every time", () => {
    const routes = { a: line(0, 50, 100, 50), b: line(40, 0, 40, 100), c: line(0, 80, 30, 80, 30, 20, 100, 20) };
    const once = hoppedPaths(routes, Object.keys(routes));
    expect(once).toEqual(hoppedPaths(routes, Object.keys(routes)));
    expect(arcs(once.a!)).toBe(2);
    expect(arcs(once.b!)).toBe(0);
    expect(arcs(once.c!)).toBe(1);
  });

  it("redraws on a move only the moved edges and those crossing them before or after", () => {
    const before = { a: line(0, 50, 100, 50), b: line(40, 0, 40, 100), far: line(0, 300, 100, 300) };
    const after = { ...before, b: line(140, 0, 140, 100) };
    expect([...hopTargets(after, new Set(["b"]), before)].sort()).toEqual(["a", "b"]);
    expect([...hopTargets(before, new Set(["far"]), before)]).toEqual(["far"]);
  });
});
