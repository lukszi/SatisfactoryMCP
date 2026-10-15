import { describe, expect, it } from "vitest";

import { cursorFor, HANDLES, handleSpot, PAD_MIN_M, resize, sizeReadout, snapSize, toLocal, toWorld } from "../../../../src/dash/planner/pad/geometry";
import { siteOp } from "../../../../src/dash/planner/pad/op";

import type { Pad, ResizeKeys } from "../../../../src/dash/planner/pad/geometry";

const PLAIN: ResizeKeys = { aspect: false, centred: false, free: false };
const pad = (x: number, y: number, yaw: number, w: number, d: number): Pad => ({ x_m: x, y_m: y, yaw_deg: yaw, width_m: w, depth_m: d });

/** The pad's own (u, v) of a world point, rounded for comparison. */
function local(p: Pad, at: { x_m: number; y_m: number }): number[] {
  const q = toLocal(p, at.x_m, at.y_m);
  return [Math.round(q.u * 1000) / 1000, Math.round(q.v * 1000) / 1000];
}

/** Drag handle (sx, sy) of `p` to its spot plus (du, dv) in the pad's own frame. */
function pull(p: Pad, sx: number, sy: number, du: number, dv: number, keys: ResizeKeys, step: number): Pad {
  const at = toWorld(p, (sx * p.width_m) / 2 + du, (sy * p.depth_m) / 2 + dv);
  return resize(p, { sx: sx, sy: sy }, at.x_m, at.y_m, keys, step);
}

describe("snapSize", () => {
  it("lands on whole foundations, half ones, or the centimetre", () => {
    expect(snapSize(51, 8)).toBe(48);
    expect(snapSize(53, 8)).toBe(56);
    expect(snapSize(51, 4)).toBe(52);
    expect(snapSize(50.123, 0)).toBe(50.12);
  });

  it("never goes under one foundation or past 2 km", () => {
    expect(snapSize(3, 8)).toBe(PAD_MIN_M);
    expect(snapSize(-20, 4)).toBe(PAD_MIN_M);
    expect(snapSize(5, 0)).toBe(PAD_MIN_M);
    expect(snapSize(9000, 8)).toBe(2000);
  });
});

describe("resize, unturned", () => {
  const p = pad(100, 200, 0, 48, 32);

  it("an edge changes one side and holds the opposite edge", () => {
    const east = pull(p, 1, 0, 9, 0, PLAIN, 8);
    expect([east.width_m, east.depth_m]).toEqual([56, 32]);
    expect(east.x_m - east.width_m / 2).toBe(p.x_m - p.width_m / 2);
    expect(east.y_m).toBe(200);
    const north = pull(p, 0, -1, 0, -7, PLAIN, 8);
    expect([north.width_m, north.depth_m]).toEqual([48, 40]);
    expect(north.y_m + north.depth_m / 2).toBe(p.y_m + p.depth_m / 2);
  });

  it("every one of the eight handles holds the far side", () => {
    HANDLES.forEach(function (h) {
      const out = pull(p, h.sx, h.sy, 16 * h.sx, 8 * h.sy, PLAIN, 8);
      expect(out.width_m).toBe(h.sx ? 64 : 48);
      expect(out.depth_m).toBe(h.sy ? 40 : 32);
      const far = { sx: -h.sx, sy: -h.sy };
      expect(local(p, handleSpot(out, far))).toEqual(local(p, handleSpot(p, far)));
    });
  });

  it("a corner changes both sides", () => {
    const out = pull(p, 1, 1, 8, 16, PLAIN, 8);
    expect([out.width_m, out.depth_m]).toEqual([56, 48]);
    expect([out.x_m, out.y_m]).toEqual([104, 208]);
  });

  it("Alt resizes round the centre", () => {
    const out = pull(p, 1, 0, 8, 0, { ...PLAIN, centred: true }, 8);
    expect([out.x_m, out.y_m, out.width_m]).toEqual([100, 200, 64]);
  });

  it("Shift on a corner keeps the ratio; on an edge it does nothing", () => {
    const out = pull(p, 1, 1, 24, 0, { ...PLAIN, aspect: true }, 0);
    expect(out.width_m / out.depth_m).toBeCloseTo(48 / 32, 2);
    expect(out.width_m).toBeGreaterThan(48);
    const edge = pull(p, 1, 0, 24, 0, { ...PLAIN, aspect: true }, 8);
    expect([edge.width_m, edge.depth_m]).toEqual([72, 32]);
  });

  it("Ctrl skips the snap, and a 4 m step lands on halves", () => {
    expect(pull(p, 1, 0, 5.5, 0, { ...PLAIN, free: true }, 8).width_m).toBe(53.5);
    expect(pull(p, 1, 0, 5.5, 0, PLAIN, 4).width_m).toBe(52);
    expect(pull(p, 1, 0, 5.5, 0, PLAIN, 0).width_m).toBe(53.5);
  });

  it("a pointer dragged past the far side stops at one foundation, unflipped", () => {
    const out = pull(p, 1, 0, -100, 0, PLAIN, 8);
    expect(out.width_m).toBe(PAD_MIN_M);
    expect(out.x_m - out.width_m / 2).toBe(p.x_m - p.width_m / 2);
  });
});

describe("resize, turned", () => {
  it("pulls along the pad's own axes and holds the far corner in the world", () => {
    [30, 90, 217.5].forEach(function (yaw) {
      const p = pad(-500, 1200, yaw, 40, 24);
      const out = pull(p, 1, -1, 8, -8, PLAIN, 8);
      expect([out.width_m, out.depth_m]).toEqual([48, 32]);
      expect(out.yaw_deg).toBe(yaw);
      const before = handleSpot(p, { sx: -1, sy: 1 });
      const after = handleSpot(out, { sx: -1, sy: 1 });
      expect(after.x_m).toBeCloseTo(before.x_m, 1);
      expect(after.y_m).toBeCloseTo(before.y_m, 1);
    });
  });
});

describe("readout and cursor", () => {
  it("reads metres and foundations", () => {
    expect(sizeReadout(pad(0, 0, 0, 48, 32))).toBe("48 × 32 m · 6 × 4 foundations");
    expect(sizeReadout(pad(0, 0, 0, 52, 1000))).toBe("52 × 1,000 m · 6.5 × 125 foundations");
  });

  it("points the way the handle pulls, turned with the pad", () => {
    expect(cursorFor(0, { sx: 1, sy: 0 })).toBe("ew-resize");
    expect(cursorFor(0, { sx: 0, sy: -1 })).toBe("ns-resize");
    expect(cursorFor(0, { sx: 1, sy: 1 })).toBe("nwse-resize");
    expect(cursorFor(0, { sx: 1, sy: -1 })).toBe("nesw-resize");
    expect(cursorFor(90, { sx: 1, sy: 0 })).toBe("ns-resize");
    expect(cursorFor(45, { sx: 1, sy: 0 })).toBe("nwse-resize");
  });
});

describe("the op a resize writes", () => {
  it("is the one site op with the new footprint, given", () => {
    const p = pull(pad(100, 200, 15, 48, 32), 1, 0, 8, 0, PLAIN, 8);
    expect(siteOp(p, "given", "map")).toEqual({
      op: "site",
      value: {
        schema: 1,
        origin_m: [p.x_m, p.y_m, null],
        yaw_deg: 15,
        footprint_m: [56, 32],
        footprint_source: "given",
        origin_label: "map",
        when: "",
      },
    });
  });
});
