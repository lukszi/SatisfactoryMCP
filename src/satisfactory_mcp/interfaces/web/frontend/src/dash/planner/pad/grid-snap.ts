/* Aligning a pad to the foundations already built near it: their turn and their 8 m phase,
 * read from the save (`foundation_grids`), never a world-wide grid. Pure arithmetic;
 * docs/planner-p5_contract.md §5.4 is the specification. */

import { FOUNDATION_M, PAD_MAX_M, PAD_MIN_M, resize, resized } from "./geometry";

import type { Pad } from "./geometry";
import type { Handle, ResizeKeys } from "./geometry";

/** One lattice: lines at `anchor_m ± 4 + k·8` along the axes turned by `yaw_deg`. */
export interface Grid {
  yaw_deg: number;
  anchor_m: number[];
  bbox_m: number[];
  tiles: number;
}

export interface Aligned {
  pad: Pad;
  snapped: boolean;
}

/** What a step snaps with: the built grids, the reach in metres (a few pixels at this zoom),
 *  and the size step, 8, 4 or 0 for no snap at all. */
export interface SnapContext {
  grids: Grid[];
  tolM: number;
  step: number;
}

/** A moved pad within this many degrees of a nearby grid's turn takes that turn. */
export const YAW_CATCH_DEG = 10;
/** A resize never turns the pad: it aligns only to a grid it is already square with. */
const YAW_SAME_DEG = 0.05;

function rad(deg: number): number {
  return (deg * Math.PI) / 180;
}

/** The pad's turn off the grid's, in (−45°, 45°]. */
export function turnOff(padYaw: number, gridYaw: number): number {
  const d = (((padYaw - gridYaw) % 90) + 90) % 90;
  return d > 45 ? d - 90 : d;
}

function padBox(p: Pad): number[] {
  const a = rad(p.yaw_deg);
  const ex = (Math.abs(Math.cos(a)) * p.width_m + Math.abs(Math.sin(a)) * p.depth_m) / 2;
  const ey = (Math.abs(Math.sin(a)) * p.width_m + Math.abs(Math.cos(a)) * p.depth_m) / 2;
  return [p.x_m - ex, p.y_m - ey, p.x_m + ex, p.y_m + ey];
}

function gap(box: number[], x: number, y: number): number {
  return Math.hypot(Math.max(box[0]! - x, 0, x - box[2]!), Math.max(box[1]! - y, 0, y - box[3]!));
}

/** The grid whose foundations come within `reachM` of the pad and whose turn it can take:
 *  the one nearest the pad's centre, the larger on a tie. */
export function nearbyGrid(p: Pad, grids: Grid[], reachM: number, catchDeg: number): Grid | null {
  const box = padBox(p);
  let best: Grid | null = null;
  let bestGap = Infinity;
  grids.forEach(function (g) {
    const b = g.bbox_m;
    const apart = b[0]! > box[2]! + reachM || b[2]! < box[0]! - reachM || b[1]! > box[3]! + reachM || b[3]! < box[1]! - reachM;
    if (apart || Math.abs(turnOff(p.yaw_deg, g.yaw_deg)) > catchDeg) return;
    const d = gap(b, p.x_m, p.y_m);
    if (d < bestGap || (d === bestGap && best && g.tiles > best.tiles)) {
      best = g;
      bestGap = d;
    }
  });
  return best;
}

/** The nearest grid line to `t`, lines at half a tile plus whole `step`s from the anchor. */
export function lineNear(t: number, step: number): number {
  const half = FOUNDATION_M / 2;
  return half + Math.round((t - half) / step) * step;
}

/* For each pad axis, which grid axis it lies along and which way: a quarter-turned pad's
 * width runs along the grid's v. */
function axes(p: Pad, g: Grid): { index: number; sign: number }[] {
  const q = ((Math.round((p.yaw_deg - g.yaw_deg) / 90) % 4) + 4) % 4;
  const ex = [
    [1, 0],
    [0, 1],
    [-1, 0],
    [0, -1],
  ][q]!;
  const ey = [-ex[1]!, ex[0]!];
  return [ex, ey].map(function (e) {
    const index = e[0] !== 0 ? 0 : 1;
    return { index: index, sign: e[index]! };
  });
}

/** Where the pad's centre lies in the grid's frame, measured from the anchor. */
function gridCoords(g: Grid, x: number, y: number): number[] {
  const a = rad(g.yaw_deg);
  const dx = x - g.anchor_m[0]!;
  const dy = y - g.anchor_m[1]!;
  return [dx * Math.cos(a) + dy * Math.sin(a), -dx * Math.sin(a) + dy * Math.cos(a)];
}

function fromGrid(g: Grid, c: number[]): { x: number; y: number } {
  const a = rad(g.yaw_deg);
  return { x: g.anchor_m[0]! + c[0]! * Math.cos(a) - c[1]! * Math.sin(a), y: g.anchor_m[1]! + c[0]! * Math.sin(a) + c[1]! * Math.cos(a) };
}

function round2(n: number): number {
  return Math.round(n * 100) / 100;
}

/** A moved pad: takes the grid's turn, then on each axis the nearer of its two edges within
 *  `tolM` of a line lands on it. Size never changes. */
export function alignMove(p: Pad, g: Grid, tolM: number, step: number): Aligned {
  const yaw = (((p.yaw_deg - turnOff(p.yaw_deg, g.yaw_deg)) % 360) + 360) % 360;
  const turned: Pad = { ...p, yaw_deg: Math.round(yaw * 1e6) / 1e6 };
  const c = gridCoords(g, p.x_m, p.y_m);
  const halves = [p.width_m / 2, p.depth_m / 2];
  let snapped = false;
  axes(turned, g).forEach(function (axis, i) {
    const lo = c[axis.index]! - halves[i]!;
    const hi = c[axis.index]! + halves[i]!;
    const dlo = lineNear(lo, step) - lo;
    const dhi = lineNear(hi, step) - hi;
    const delta = Math.abs(dlo) <= Math.abs(dhi) ? dlo : dhi;
    if (Math.abs(delta) > tolM) return;
    c[axis.index] = c[axis.index]! + delta;
    snapped = true;
  });
  if (!snapped) return { pad: p, snapped: false };
  const at = fromGrid(g, c);
  return { pad: { ...turned, x_m: round2(at.x), y_m: round2(at.y) }, snapped: true };
}

/** A resized pad: the edges handle `h` pulls land on a line within `tolM`; the held side (or
 *  with `centred` the centre) stays. A pad not square with the grid is left alone. */
export function alignResize(p: Pad, h: Handle, g: Grid, tolM: number, step: number, centred: boolean): Aligned {
  if (Math.abs(turnOff(p.yaw_deg, g.yaw_deg)) > YAW_SAME_DEG) return { pad: p, snapped: false };
  const c = gridCoords(g, p.x_m, p.y_m);
  const pulls = [h.sx, h.sy];
  const sizes = [p.width_m, p.depth_m];
  let snapped = false;
  axes(p, g).forEach(function (axis, i) {
    const s = pulls[i]!;
    if (!s) return;
    const edge = c[axis.index]! + (axis.sign * s * sizes[i]!) / 2;
    const delta = lineNear(edge, step) - edge;
    const grown = sizes[i]! + (centred ? 2 : 1) * s * axis.sign * delta;
    if (Math.abs(delta) > tolM || grown < PAD_MIN_M - 1e-9 || grown > PAD_MAX_M) return;
    sizes[i] = round2(grown);
    snapped = true;
  });
  if (!snapped) return { pad: p, snapped: false };
  return { pad: resized(p, h, sizes[0]!, sizes[1]!, centred), snapped: true };
}


/** One frame of a resize drag: the size on whole steps, then the pulled edges onto the built
 *  grid when one is in reach. `free` (Ctrl) or a zero step snaps nothing. */
export function resizeStep(start: Pad, h: Handle, x: number, y: number, keys: ResizeKeys, ctx: SnapContext): Aligned {
  const p = resize(start, h, x, y, keys, ctx.step);
  if (keys.free || !ctx.step) return { pad: p, snapped: false };
  const g = nearbyGrid(p, ctx.grids, ctx.tolM, YAW_SAME_DEG);
  return g ? alignResize(p, h, g, ctx.tolM, ctx.step, keys.centred) : { pad: p, snapped: false };
}

/** One frame of a move, after the 1 m / 15° snap: onto the built grid when one is in reach. */
export function moveStep(p: Pad, free: boolean, ctx: SnapContext): Aligned {
  if (free || !ctx.step) return { pad: p, snapped: false };
  const g = nearbyGrid(p, ctx.grids, ctx.tolM, YAW_CATCH_DEG);
  return g ? alignMove(p, g, ctx.tolM, ctx.step) : { pad: p, snapped: false };
}
