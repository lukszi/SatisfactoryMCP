import { describe, expect, it } from "vitest";

import { routeOrthogonal, simplify } from "../../../src/dash/prodgraph/router";

import type { Pt, Rect } from "../../../src/dash/prodgraph/router";

function crosses(points: Pt[], r: Rect): boolean {
  for (let i = 1; i < points.length; i++) {
    const a = points[i - 1]!;
    const b = points[i]!;
    const x0 = Math.min(a.x, b.x);
    const x1 = Math.max(a.x, b.x);
    const y0 = Math.min(a.y, b.y);
    const y1 = Math.max(a.y, b.y);
    if (x0 < r.x + r.w && x1 > r.x && y0 < r.y + r.h && y1 > r.y) return true;
  }
  return false;
}

function orthogonal(points: Pt[]): boolean {
  for (let i = 1; i < points.length; i++) {
    if (points[i]!.x !== points[i - 1]!.x && points[i]!.y !== points[i - 1]!.y) return false;
  }
  return true;
}

describe("routeOrthogonal", () => {
  it("goes straight when nothing is in the way", () => {
    const path = routeOrthogonal({ x: 0, y: 50 }, { x: 300, y: 50 }, []);
    expect(path).toEqual([
      { x: 0, y: 50 },
      { x: 300, y: 50 },
    ]);
  });

  it("goes round a box sitting on the straight line", () => {
    const wall = { x: 120, y: 20, w: 60, h: 60 };
    const path = routeOrthogonal({ x: 0, y: 50 }, { x: 300, y: 50 }, [wall]);
    expect(orthogonal(path)).toBe(true);
    expect(crosses(path, wall)).toBe(false);
    expect(path[0]).toEqual({ x: 0, y: 50 });
    expect(path[path.length - 1]).toEqual({ x: 300, y: 50 });
  });

  it("keeps the margin clear and finds the gap between two boxes", () => {
    const walls = [
      { x: 100, y: 0, w: 40, h: 80 },
      { x: 100, y: 120, w: 40, h: 80 },
    ];
    const path = routeOrthogonal({ x: 0, y: 100 }, { x: 300, y: 100 }, walls, { margin: 10 });
    walls.forEach((w) => expect(crosses(path, { x: w.x - 9, y: w.y - 9, w: w.w + 18, h: w.h + 18 })).toBe(false));
    expect(path.length).toBe(2);
  });

  it("routes a backward edge around both ends", () => {
    const source = { x: 300, y: 0, w: 50, h: 50 };
    const target = { x: 0, y: 0, w: 50, h: 50 };
    const path = routeOrthogonal({ x: 350, y: 25 }, { x: 0, y: 25 }, [source, target]);
    expect(orthogonal(path)).toBe(true);
    expect(crosses(path.slice(1, -1), source)).toBe(false);
    expect(crosses(path.slice(1, -1), target)).toBe(false);
  });

  it("is deterministic and avoids every box in a crowded field", () => {
    const walls: Rect[] = [];
    for (let i = 0; i < 12; i++) for (let j = 0; j < 12; j++) if ((i + j) % 3) walls.push({ x: 60 + i * 70, y: j * 70, w: 40, h: 40 });
    const run = () => routeOrthogonal({ x: 0, y: 15 }, { x: 960, y: 785 }, walls, { margin: 6 });
    const t0 = performance.now();
    const one = run();
    const ms = performance.now() - t0;
    expect(run()).toEqual(one);
    expect(orthogonal(one)).toBe(true);
    walls.forEach((w) => expect(crosses(one, w)).toBe(false));
    expect(ms).toBeLessThan(200);
  });

  it("falls back to a plain elbow when the target is walled in", () => {
    const cage = [
      { x: 180, y: -100, w: 20, h: 300 },
      { x: 180, y: -100, w: 300, h: 20 },
      { x: 180, y: 180, w: 300, h: 20 },
      { x: 460, y: -100, w: 20, h: 300 },
    ];
    const path = routeOrthogonal({ x: 0, y: 50 }, { x: 300, y: 80 }, cage, { margin: 4 });
    expect(orthogonal(path)).toBe(true);
    expect(path[path.length - 1]).toEqual({ x: 300, y: 80 });
  });
});

describe("simplify", () => {
  it("drops repeats and collinear middles", () => {
    expect(
      simplify([
        { x: 0, y: 0 },
        { x: 0, y: 0 },
        { x: 5, y: 0 },
        { x: 10, y: 0 },
        { x: 10, y: 5 },
      ])
    ).toEqual([
      { x: 0, y: 0 },
      { x: 10, y: 0 },
      { x: 10, y: 5 },
    ]);
  });
});
