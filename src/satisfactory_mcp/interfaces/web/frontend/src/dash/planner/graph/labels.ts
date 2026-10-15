/* Where an edge label goes when the layout did not place it: on the edge's own line, longest
 * stretch first and nearest its middle, at the first spot clear of every box and label so far. */

import type { Pt, Rect } from "./router";

const STOPS = [0.5, 0.35, 0.65, 0.2, 0.8];

export function overlaps(a: Rect, b: Rect): boolean {
  return a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
}

function segments(points: Pt[]): { a: Pt; b: Pt; length: number; flat: boolean }[] {
  const out: { a: Pt; b: Pt; length: number; flat: boolean }[] = [];
  for (let i = 1; i < points.length; i++) {
    const a = points[i - 1]!;
    const b = points[i]!;
    const length = Math.abs(b.x - a.x) + Math.abs(b.y - a.y);
    if (length > 0) out.push({ a: a, b: b, length: length, flat: a.y === b.y });
  }
  return out.sort(function (p, q) {
    return Number(q.flat) - Number(p.flat) || q.length - p.length;
  });
}

export function placeOnLine(points: Pt[], w: number, h: number, occupied: Rect[]): Rect | null {
  let spot: Rect | null = null;
  segments(points).some(function (s) {
    if (s.flat && s.length < w) return false;
    return STOPS.some(function (t) {
      const cx = s.a.x + (s.b.x - s.a.x) * t;
      const cy = s.a.y + (s.b.y - s.a.y) * t;
      const box = { x: Math.round(cx - w / 2), y: Math.round(cy - h / 2), w: w, h: h };
      if (
        occupied.some(function (o) {
          return overlaps(box, o);
        })
      )
        return false;
      spot = box;
      return true;
    });
  });
  return spot;
}
