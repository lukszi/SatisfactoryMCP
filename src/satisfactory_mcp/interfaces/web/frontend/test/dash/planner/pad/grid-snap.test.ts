import { describe, expect, it } from "vitest";

import { toWorld } from "../../../../src/dash/planner/pad/geometry";
import { lineNear, moveStep, nearbyGrid, resizeStep, turnOff } from "../../../../src/dash/planner/pad/grid-snap";

import type { Pad, ResizeKeys } from "../../../../src/dash/planner/pad/geometry";
import type { Grid, SnapContext } from "../../../../src/dash/planner/pad/grid-snap";

const PLAIN: ResizeKeys = { aspect: false, centred: false, free: false };
const pad = (x: number, y: number, yaw: number, w: number, d: number): Pad => ({ x_m: x, y_m: y, yaw_deg: yaw, width_m: w, depth_m: d });

/** A grid of tiles round `anchor`, turned `yaw`, its box `reach` metres each way. */
function grid(ax: number, ay: number, yaw: number, reach: number, tiles = 9): Grid {
  return { yaw_deg: yaw, anchor_m: [ax, ay], bbox_m: [ax - reach, ay - reach, ax + reach, ay + reach], tiles: tiles };
}

const ctx = (grids: Grid[], step = 8, tolM = 1): SnapContext => ({ grids: grids, tolM: tolM, step: step });

/** World point at grid coordinates (u, v) of `g`. */
function atGrid(g: Grid, u: number, v: number): { x: number; y: number } {
  const a = (g.yaw_deg * Math.PI) / 180;
  return { x: g.anchor_m[0]! + u * Math.cos(a) - v * Math.sin(a), y: g.anchor_m[1]! + u * Math.sin(a) + v * Math.cos(a) };
}

function pull(p: Pad, sx: number, sy: number, du: number, dv: number, keys: ResizeKeys, c: SnapContext) {
  const at = toWorld(p, (sx * p.width_m) / 2 + du, (sy * p.depth_m) / 2 + dv);
  return resizeStep(p, { sx: sx, sy: sy }, at.x_m, at.y_m, keys, c);
}

describe("lines and turns", () => {
  it("lines sit half a tile off a tile centre, every step", () => {
    expect(lineNear(0.3, 8)).toBe(4);
    expect(lineNear(-3.9, 8)).toBe(-4);
    expect(lineNear(1.9, 4)).toBe(0);
    expect(lineNear(2.1, 4)).toBe(4);
  });

  it("measures the pad's turn off the grid modulo a quarter", () => {
    expect(turnOff(25, 30)).toBeCloseTo(-5);
    expect(turnOff(118, 30)).toBeCloseTo(-2);
    expect(turnOff(0, 89)).toBeCloseTo(1);
  });
});

describe("nearbyGrid", () => {
  const close = grid(3, -5.5, 0, 20, 4);
  const farther = grid(60, 0, 0, 20, 400);
  const remote = grid(5000, 5000, 0, 20, 900);

  it("takes the grid nearest the pad's centre, never one out of reach", () => {
    expect(nearbyGrid(pad(10, 0, 0, 16, 16), [remote, farther, close], 1, 10)).toBe(close);
    expect(nearbyGrid(pad(2000, 2000, 0, 16, 16), [remote, farther, close], 1, 10)).toBeNull();
  });

  it("skips a grid the pad is turned too far from", () => {
    expect(nearbyGrid(pad(10, 0, 20, 16, 16), [close], 1, 10)).toBeNull();
    expect(nearbyGrid(pad(10, 0, 95, 16, 16), [close], 1, 10)).toBe(close);
  });
});

describe("moveStep", () => {
  const g = grid(3, -5.5, 0, 40);

  it("lands the pad's edges on the built grid's phase, not the world's", () => {
    // Lines run at x = 7 + 8k and y = -1.5 + 8k.
    const out = moveStep(pad(15.4, 6, 0, 16, 16), false, ctx([g]));
    expect(out.snapped).toBe(true);
    expect([out.pad.x_m, out.pad.y_m, out.pad.width_m]).toEqual([15, 6.5, 16]);
  });

  it("leaves the pad alone with no grid near, with Ctrl, or with snapping off", () => {
    const p = pad(15.4, 6, 0, 16, 16);
    expect(moveStep(p, false, ctx([]))).toEqual({ pad: p, snapped: false });
    expect(moveStep(p, true, ctx([g]))).toEqual({ pad: p, snapped: false });
    expect(moveStep(p, false, ctx([g], 0))).toEqual({ pad: p, snapped: false });
    expect(moveStep(pad(15.4 + 3.5, 6 + 3.5, 0, 16, 16), false, ctx([g])).snapped).toBe(false);
  });

  it("takes a turned grid's turn and phase", () => {
    const turned = grid(100, 200, 30, 60);
    const near = atGrid(turned, 12.3, 8.2);
    const out = moveStep(pad(near.x, near.y, 25, 16, 8), false, ctx([turned]));
    const want = atGrid(turned, 12, 8);
    expect(out.snapped).toBe(true);
    expect(out.pad.yaw_deg).toBe(30);
    expect(out.pad.x_m).toBeCloseTo(want.x, 1);
    expect(out.pad.y_m).toBeCloseTo(want.y, 1);
  });

  it("a quarter-turned pad lays its width along the grid's other axis", () => {
    const turned = grid(100, 200, 30, 60);
    const near = atGrid(turned, 8.2, 12.3);
    const out = moveStep(pad(near.x, near.y, 120, 16, 8), false, ctx([turned]));
    const want = atGrid(turned, 8, 12);
    expect(out.pad.yaw_deg).toBe(120);
    expect(out.pad.x_m).toBeCloseTo(want.x, 1);
    expect(out.pad.y_m).toBeCloseTo(want.y, 1);
  });

  it("half steps land on 4 m lines the whole-foundation step misses", () => {
    const at0 = grid(0, 0, 0, 40);
    const p = pad(4.3, 4, 0, 8, 8);
    expect(moveStep(p, false, ctx([at0], 4)).pad.x_m).toBe(4);
    expect(moveStep({ ...p, y_m: 0 }, false, ctx([at0], 8)).pad.x_m).toBe(4.3);
  });
});

describe("resizeStep", () => {
  const g = grid(3, -5.5, 0, 40);

  it("a pad already on the grid keeps whole foundations and shows the snap", () => {
    const out = pull(pad(11, 2.5, 0, 8, 8), 1, 0, 9, 0, PLAIN, ctx([g]));
    expect(out.snapped).toBe(true);
    expect([out.pad.x_m, out.pad.width_m]).toEqual([15, 16]);
  });

  it("the pulled edge of a pad off the grid lands on a built line", () => {
    const out = pull(pad(11.5, 2.5, 0, 8, 8), 1, 0, 8.6, 0, PLAIN, ctx([g]));
    expect(out.snapped).toBe(true);
    expect(out.pad.x_m + out.pad.width_m / 2).toBe(23);
    expect(out.pad.x_m - out.pad.width_m / 2).toBe(7.5);
  });

  it("Alt keeps the centre while both edges land", () => {
    const out = pull(pad(11, 2.5, 0, 8, 8), 1, 0, 8.4, 0, { ...PLAIN, centred: true }, ctx([g]));
    expect(out.snapped).toBe(true);
    expect([out.pad.x_m, out.pad.width_m]).toEqual([11, 24]);
  });

  it("never turns the pad, Ctrl snaps nothing, and snapping off is free", () => {
    const turned = pull(pad(11, 2.5, 5, 8, 8), 1, 0, 9, 0, PLAIN, ctx([g]));
    expect([turned.snapped, turned.pad.yaw_deg, turned.pad.width_m]).toEqual([false, 5, 16]);
    const free = pull(pad(11, 2.5, 0, 8, 8), 1, 0, 9.3, 0, { ...PLAIN, free: true }, ctx([g]));
    expect([free.snapped, free.pad.width_m]).toEqual([false, 17.3]);
    const off = pull(pad(11, 2.5, 0, 8, 8), 1, 0, 9.3, 0, PLAIN, ctx([g], 0));
    expect([off.snapped, off.pad.width_m]).toEqual([false, 17.3]);
  });

  it("aligns a turned pad square with a turned grid along its own axes", () => {
    const turned = grid(100, 200, 30, 80);
    const centre = atGrid(turned, 8, 12);
    const p = pad(centre.x, centre.y, 120, 16, 8);
    const out = pull(p, 0, 1, 0, 8.3, PLAIN, ctx([turned]));
    expect(out.snapped).toBe(true);
    expect([out.pad.width_m, out.pad.depth_m]).toEqual([16, 16]);
  });
});
