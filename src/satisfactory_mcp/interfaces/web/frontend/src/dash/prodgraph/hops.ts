/* Line jumps: where a horizontal stretch of one edge crosses a vertical stretch of another, the
 * horizontal one arcs over it. Pure: routes in, SVG path data out. The rules are in
 * docs/planner_vision.md §4.2. */

import type { Pt } from "./router";

export interface Vertical {
  x: number;
  top: number;
  bottom: number;
}

/** The jump's radius in layout units, so it scales with the zoom like the lines do. */
export const HOP_R = 4.5;
/** How far a crossing must be inside a vertical's ends to count: a line that only ends on another is a join. */
const EPS = 0.5;

export function verticalsOf(points: Pt[]): Vertical[] {
  const out: Vertical[] = [];
  for (let i = 1; i < points.length; i++) {
    const a = points[i - 1]!;
    const b = points[i]!;
    if (a.x === b.x && a.y !== b.y) out.push({ x: a.x, top: Math.min(a.y, b.y), bottom: Math.max(a.y, b.y) });
  }
  return out;
}

/** Where the horizontal a→b crosses `walls`, in travel order; never within `r` of its own ends. */
export function hopsOn(a: Pt, b: Pt, walls: Vertical[], r: number): number[] {
  const lo = Math.min(a.x, b.x) + r;
  const hi = Math.max(a.x, b.x) - r;
  const xs: number[] = [];
  walls.forEach(function (v) {
    if (v.x > lo && v.x < hi && v.top < a.y - EPS && v.bottom > a.y + EPS && xs.indexOf(v.x) < 0) xs.push(v.x);
  });
  xs.sort(function (p, q) {
    return p - q;
  });
  return b.x < a.x ? xs.reverse() : xs;
}

/** The path through `points` with a jump over each of `walls` its horizontals cross; jumps
 *  closer than their width merge into one wider arc. Always arcs upwards. */
export function hopPath(points: Pt[], walls: Vertical[], r: number = HOP_R): string {
  let d = "";
  points.forEach(function (p, i) {
    if (!i) {
      d = "M" + p.x + "," + p.y;
      return;
    }
    const a = points[i - 1]!;
    if (a.y === p.y && a.x !== p.x) {
      const dir = p.x > a.x ? 1 : -1;
      const xs = hopsOn(a, p, walls, r);
      let k = 0;
      while (k < xs.length) {
        const start = xs[k]! - dir * r;
        let end = xs[k]! + dir * r;
        k++;
        while (k < xs.length && (xs[k]! - dir * r - end) * dir <= 0) {
          end = xs[k]! + dir * r;
          k++;
        }
        d += " L" + start + "," + p.y + " A" + Math.abs(end - start) / 2 + "," + r + " 0 0 " + (dir > 0 ? 1 : 0) + " " + end + "," + p.y;
      }
    }
    d += " L" + p.x + "," + p.y;
  });
  return d;
}

/** Path data for the edges in `which`, each jumping the verticals of every other edge. */
export function hoppedPaths(routes: Record<string, Pt[]>, which: Iterable<string>, r: number = HOP_R): Record<string, string> {
  const walls: Record<string, Vertical[]> = {};
  Object.keys(routes).forEach(function (id) {
    walls[id] = verticalsOf(routes[id]!);
  });
  const out: Record<string, string> = {};
  for (const id of which) {
    const points = routes[id];
    if (!points) continue;
    const others: Vertical[] = [];
    Object.keys(walls).forEach(function (other) {
      if (other !== id) others.push(...walls[other]!);
    });
    out[id] = hopPath(points, others, r);
  }
  return out;
}

/** Whether any horizontal of `points` jumps one of `walls`. */
function jumps(points: Pt[], walls: Vertical[], r: number): boolean {
  for (let i = 1; i < points.length; i++) {
    const a = points[i - 1]!;
    const b = points[i]!;
    if (a.y === b.y && a.x !== b.x && hopsOn(a, b, walls, r).length) return true;
  }
  return false;
}

/** The edges whose jumps a change to `changed` can move: those edges, and every other edge
 *  crossing one of their verticals before (`before`) or after (`routes`) the change. */
export function hopTargets(routes: Record<string, Pt[]>, changed: Set<string>, before: Record<string, Pt[]>, r: number = HOP_R): Set<string> {
  const walls: Vertical[] = [];
  changed.forEach(function (id) {
    if (before[id]) walls.push(...verticalsOf(before[id]!));
    if (routes[id]) walls.push(...verticalsOf(routes[id]!));
  });
  const out = new Set(changed);
  if (!walls.length) return out;
  Object.keys(routes).forEach(function (id) {
    if (!out.has(id) && jumps(routes[id]!, walls, r)) out.add(id);
  });
  return out;
}
