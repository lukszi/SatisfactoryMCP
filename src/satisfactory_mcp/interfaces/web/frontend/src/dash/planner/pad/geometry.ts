/* A pad's own frame and its resize: which side a handle pulls, the size snap, the readout.
 * Pure arithmetic; docs/planner-p5_contract.md §3 F2a is the specification. */

/** A pad: its centre, its turn, and width (along its own x) by depth, in metres. */
export interface Pad {
  x_m: number;
  y_m: number;
  yaw_deg: number;
  width_m: number;
  depth_m: number;
}

/** Every foundation in the game data is 8 × 8 m; a half step is 4 m. */
export const FOUNDATION_M = 8;
export const HALF_M = 4;
/** `siting.FOOTPRINT_MIN_M` and `FOOTPRINT_MAX_M`: one foundation up to 2 km. */
export const PAD_MIN_M = 8;
export const PAD_MAX_M = 2000;

/** The side a handle pulls along the pad's width (`sx`) and depth (`sy`): −1, 0 or 1. */
export interface Handle {
  sx: number;
  sy: number;
}

export const HANDLES: Handle[] = [
  { sx: -1, sy: -1 },
  { sx: 0, sy: -1 },
  { sx: 1, sy: -1 },
  { sx: 1, sy: 0 },
  { sx: 1, sy: 1 },
  { sx: 0, sy: 1 },
  { sx: -1, sy: 1 },
  { sx: -1, sy: 0 },
];

/** Shift keeps the ratio (corners only), Alt resizes round the centre, Ctrl skips every snap. */
export interface ResizeKeys {
  aspect: boolean;
  centred: boolean;
  free: boolean;
}

export interface Local {
  u: number;
  v: number;
}

function turn(p: Pad): { cos: number; sin: number } {
  const a = (p.yaw_deg * Math.PI) / 180;
  return { cos: Math.cos(a), sin: Math.sin(a) };
}

/** A world point in the pad's frame: `u` along its width, `v` along its depth. */
export function toLocal(p: Pad, x: number, y: number): Local {
  const t = turn(p);
  const dx = x - p.x_m;
  const dy = y - p.y_m;
  return { u: dx * t.cos + dy * t.sin, v: -dx * t.sin + dy * t.cos };
}

/** The inverse of `toLocal`; the same rotation `footprintCorners` draws with. */
export function toWorld(p: Pad, u: number, v: number): { x_m: number; y_m: number } {
  const t = turn(p);
  return { x_m: p.x_m + u * t.cos - v * t.sin, y_m: p.y_m + u * t.sin + v * t.cos };
}

export function isCorner(h: Handle): boolean {
  return h.sx !== 0 && h.sy !== 0;
}

/** Where a handle sits: a corner, or the middle of an edge. */
export function handleSpot(p: Pad, h: Handle): { x_m: number; y_m: number } {
  return toWorld(p, (h.sx * p.width_m) / 2, (h.sy * p.depth_m) / 2);
}

function hundredths(n: number): number {
  return Math.round(n * 100) / 100;
}

/** A side length on `step` metres (0: free, to the centimetre), one foundation at least. */
export function snapSize(m: number, step: number): number {
  const v = step > 0 ? Math.round(m / step) * step : hundredths(m);
  return Math.min(PAD_MAX_M, Math.max(PAD_MIN_M, v));
}

/** The pad `start` becomes with handle `h` dragged to world (x, y). The opposite side stays put,
 *  or with `centred` the centre does; a pointer past the far side gives the minimum, never a flip. */
export function resize(start: Pad, h: Handle, x: number, y: number, keys: ResizeKeys, step: number): Pad {
  const q = toLocal(start, x, y);
  let w = start.width_m;
  let d = start.depth_m;
  if (h.sx) w = keys.centred ? 2 * h.sx * q.u : h.sx * q.u + start.width_m / 2;
  if (h.sy) d = keys.centred ? 2 * h.sy * q.v : h.sy * q.v + start.depth_m / 2;
  if (keys.aspect && isCorner(h)) {
    // The pointer projected on the pad's diagonal, so both sides scale by one factor.
    const k = (w * start.width_m + d * start.depth_m) / (start.width_m ** 2 + start.depth_m ** 2);
    w = k * start.width_m;
    d = k * start.depth_m;
  }
  const s = keys.free ? 0 : step;
  if (h.sx) w = snapSize(w, s);
  if (h.sy) d = snapSize(d, s);
  return resized(start, h, w, d, keys.centred);
}

/** `start` at width `w` and depth `d`, its far side (or with `centred` its centre) held. */
export function resized(start: Pad, h: Handle, w: number, d: number, centred: boolean): Pad {
  const shift = centred ? { u: 0, v: 0 } : { u: (h.sx * (w - start.width_m)) / 2, v: (h.sy * (d - start.depth_m)) / 2 };
  const at = toWorld(start, shift.u, shift.v);
  return { x_m: hundredths(at.x_m), y_m: hundredths(at.y_m), yaw_deg: start.yaw_deg, width_m: w, depth_m: d };
}

function figure(n: number): string {
  return (Math.round(n * 100) / 100).toLocaleString("en-GB");
}

/** `48 × 32 m · 6 × 4 foundations`. */
export function sizeReadout(p: Pad): string {
  return figure(p.width_m) + " × " + figure(p.depth_m) + " m · " + figure(p.width_m / FOUNDATION_M) + " × " + figure(p.depth_m / FOUNDATION_M) + " foundations";
}

const CURSORS = ["ew-resize", "nwse-resize", "ns-resize", "nesw-resize"];

/** The resize cursor that points the way handle `h` pulls on screen. Screen y runs down, as the
 *  save's +y does, so the pad's own turn carries straight over. */
export function cursorFor(yawDeg: number, h: Handle): string {
  const pull = (Math.atan2(h.sy, h.sx) * 180) / Math.PI + yawDeg;
  const half = (((pull % 180) + 180) % 180) + 22.5;
  return CURSORS[Math.floor(half / 45) % 4]!;
}
