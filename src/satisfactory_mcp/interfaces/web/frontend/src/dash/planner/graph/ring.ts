/* The utilisation ring round a process node: full while the group is whole machines, otherwise
 * an arc for the share of the last machine still needed. See docs/planner_vision.md §4.2. */

export type RingTone = "full" | "most" | "part";

export interface Ring {
  /** 0..1 of the circle drawn, starting at twelve o'clock and running clockwise. */
  fraction: number;
  tone: RingTone;
}

const WHOLE = 1e-3;

/** `work` is machines at 100%; `utilisation` the last machine's share of it, 1 when whole. */
export function ringFor(work: number, utilisation: number): Ring {
  if (work <= 0) return { fraction: 0, tone: "part" };
  const last = Math.max(0, Math.min(1, utilisation));
  if (last <= WHOLE || 1 - last < WHOLE) return { fraction: 1, tone: "full" };
  return { fraction: last, tone: last >= 0.5 ? "most" : "part" };
}

function point(cx: number, cy: number, r: number, turn: number): [number, number] {
  const a = turn * 2 * Math.PI - Math.PI / 2;
  return [round(cx + r * Math.cos(a)), round(cy + r * Math.sin(a))];
}

function round(v: number): number {
  return Math.round(v * 100) / 100;
}

/** An SVG path for `fraction` of the circle; a full circle is two half arcs, since one arc
 *  whose ends meet draws nothing. */
export function ringPath(cx: number, cy: number, r: number, fraction: number): string {
  const f = Math.max(0, Math.min(1, fraction));
  if (f <= 0) return "";
  const [x0, y0] = point(cx, cy, r, 0);
  if (f >= 1) {
    const [x1, y1] = point(cx, cy, r, 0.5);
    return "M" + x0 + "," + y0 + " A" + r + "," + r + " 0 1 1 " + x1 + "," + y1 + " A" + r + "," + r + " 0 1 1 " + x0 + "," + y0;
  }
  const [x1, y1] = point(cx, cy, r, f);
  return "M" + x0 + "," + y0 + " A" + r + "," + r + " 0 " + (f > 0.5 ? 1 : 0) + " 1 " + x1 + "," + y1;
}
