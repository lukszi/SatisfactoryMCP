/* Orthogonal edge routing around node boxes, for edges whose ends the player has moved: A* over
 * the sparse grid of every box's padded sides plus the midlines between them, with a penalty per
 * bend. The midlines are what make "both grid points free" enough to prove a step clear. */

export interface Pt {
  x: number;
  y: number;
}

export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface RouteOptions {
  /** Clearance kept from every box, and the length of the straight stub out of each port. */
  margin?: number;
  /** Extra cost of one turn, in the same units as length. */
  bend?: number;
}

const H = 0;
const V = 1;

class Heap {
  private items: { f: number; seq: number; state: number }[] = [];

  get size(): number {
    return this.items.length;
  }

  push(f: number, seq: number, state: number): void {
    const items = this.items;
    items.push({ f: f, seq: seq, state: state });
    let i = items.length - 1;
    while (i > 0) {
      const up = (i - 1) >> 1;
      if (!this.less(i, up)) break;
      [items[i], items[up]] = [items[up]!, items[i]!];
      i = up;
    }
  }

  pop(): number {
    const items = this.items;
    const top = items[0]!;
    const last = items.pop()!;
    if (items.length) {
      items[0] = last;
      let i = 0;
      for (;;) {
        const l = 2 * i + 1;
        const r = l + 1;
        let best = i;
        if (l < items.length && this.less(l, best)) best = l;
        if (r < items.length && this.less(r, best)) best = r;
        if (best === i) break;
        [items[i], items[best]] = [items[best]!, items[i]!];
        i = best;
      }
    }
    return top.state;
  }

  private less(a: number, b: number): boolean {
    const x = this.items[a]!;
    const y = this.items[b]!;
    return x.f < y.f || (x.f === y.f && x.seq < y.seq);
  }
}

function axis(values: number[]): number[] {
  const sorted = Array.from(new Set(values.map(function (v) {
    return Math.round(v * 2) / 2;
  }))).sort(function (a, b) {
    return a - b;
  });
  const out: number[] = [];
  sorted.forEach(function (v, i) {
    if (i) out.push((sorted[i - 1]! + v) / 2);
    out.push(v);
  });
  return out;
}

function indexOf(values: number[], v: number): number {
  const target = Math.round(v * 2) / 2;
  let lo = 0;
  let hi = values.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (values[mid]! < target) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

function firstAbove(values: number[], v: number): number {
  let lo = 0;
  let hi = values.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (values[mid]! <= v) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

/** Grid points strictly inside a padded box are blocked; points on its sides are free. */
function blockedGrid(xs: number[], ys: number[], boxes: Rect[]): Uint8Array {
  const grid = new Uint8Array(xs.length * ys.length);
  boxes.forEach(function (b) {
    for (let i = firstAbove(xs, b.x); i < xs.length && xs[i]! < b.x + b.w; i++) {
      for (let j = firstAbove(ys, b.y); j < ys.length && ys[j]! < b.y + b.h; j++) grid[j * xs.length + i] = 1;
    }
  });
  return grid;
}

export function simplify(points: Pt[]): Pt[] {
  const out: Pt[] = [];
  points.forEach(function (p) {
    const n = out.length;
    if (n && out[n - 1]!.x === p.x && out[n - 1]!.y === p.y) return;
    if (n >= 2) {
      const a = out[n - 2]!;
      const b = out[n - 1]!;
      if ((a.x === b.x && b.x === p.x) || (a.y === b.y && b.y === p.y)) out[n - 1] = p;
      else out.push(p);
    } else out.push(p);
  });
  return out;
}

function fallback(start: Pt, s1: Pt, e1: Pt, end: Pt): Pt[] {
  const mid = (s1.x + e1.x) / 2;
  return simplify([start, s1, { x: mid, y: s1.y }, { x: mid, y: e1.y }, e1, end]);
}

/** A path from `start` (leaving rightwards) to `end` (entering rightwards) that keeps `margin`
 *  clear of every box; the same input always gives the same path. */
export function routeOrthogonal(start: Pt, end: Pt, obstacles: Rect[], options?: RouteOptions): Pt[] {
  const m = options?.margin ?? 12;
  const bend = options?.bend ?? 24;
  const s1 = { x: start.x + m, y: start.y };
  const e1 = { x: end.x - m, y: end.y };
  const boxes = obstacles.map(function (r) {
    return { x: r.x - m, y: r.y - m, w: r.w + 2 * m, h: r.h + 2 * m };
  });
  let left = Math.min(s1.x, e1.x);
  let top = Math.min(s1.y, e1.y);
  let right = Math.max(s1.x, e1.x);
  let bottom = Math.max(s1.y, e1.y);
  const xv = [s1.x, e1.x];
  const yv = [s1.y, e1.y];
  boxes.forEach(function (b) {
    xv.push(b.x, b.x + b.w);
    yv.push(b.y, b.y + b.h);
    left = Math.min(left, b.x);
    top = Math.min(top, b.y);
    right = Math.max(right, b.x + b.w);
    bottom = Math.max(bottom, b.y + b.h);
  });
  xv.push(left - m, right + m);
  yv.push(top - m, bottom + m);
  const xs = axis(xv);
  const ys = axis(yv);
  const nx = xs.length;
  const blocked = blockedGrid(xs, ys, boxes);
  const si = indexOf(xs, s1.x) + nx * indexOf(ys, s1.y);
  const ei = indexOf(xs, e1.x) + nx * indexOf(ys, e1.y);
  blocked[si] = 0;
  blocked[ei] = 0;
  const ex = xs[ei % nx]!;
  const ey = ys[Math.floor(ei / nx)]!;

  const g = new Map<number, number>();
  const from = new Map<number, number>();
  const heap = new Heap();
  let seq = 0;
  const startState = si * 2 + H;
  g.set(startState, 0);
  heap.push(Math.abs(xs[si % nx]! - ex) + Math.abs(ys[Math.floor(si / nx)]! - ey), seq++, startState);
  let found = -1;
  while (heap.size) {
    const state = heap.pop();
    const cell = state >> 1;
    const dir = state & 1;
    const cost = g.get(state)!;
    if (cell === ei) {
      found = state;
      break;
    }
    const ix = cell % nx;
    const iy = Math.floor(cell / nx);
    const steps: [number, number, number][] = [
      [ix + 1, iy, H],
      [ix - 1, iy, H],
      [ix, iy + 1, V],
      [ix, iy - 1, V],
    ];
    steps.forEach(function ([jx, jy, d]) {
      if (jx < 0 || jy < 0 || jx >= nx || jy >= ys.length) return;
      const next = jy * nx + jx;
      if (blocked[next]) return;
      const length = Math.abs(xs[jx]! - xs[ix]!) + Math.abs(ys[jy]! - ys[iy]!);
      const turn = d === dir ? 0 : bend;
      const arriving = next === ei && d === V ? bend : 0;
      const total = cost + length + turn + arriving;
      const key = next * 2 + d;
      const known = g.get(key);
      if (known !== undefined && known <= total) return;
      g.set(key, total);
      from.set(key, state);
      heap.push(total + Math.abs(xs[jx]! - ex) + Math.abs(ys[jy]! - ey), seq++, key);
    });
  }
  if (found < 0) return fallback(start, s1, e1, end);
  const cells: Pt[] = [];
  for (let at: number | undefined = found; at !== undefined; at = from.get(at)) {
    const cell = at >> 1;
    cells.push({ x: xs[cell % nx]!, y: ys[Math.floor(cell / nx)]! });
  }
  cells.reverse();
  return simplify([start, s1].concat(cells, [e1, end]));
}
