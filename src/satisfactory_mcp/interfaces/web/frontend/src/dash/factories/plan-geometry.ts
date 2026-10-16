/* The floor plan's geometry: machines, decks and belts in metres, turned so the foundation grid
 * is square to the page, as SVG path data. Pure. docs/frontend_kit.md, "Factories". */

import { bandVeil } from "../../map/drawn/outline-geometry";

import type { BuildingShape } from "../../api/shapes";

export type XY = [number, number];

export interface PlanPiece {
  id: string;
  cls: string;
  x: number;
  y: number;
  yaw: number | null;
  /** Half extents of the measured box, for a class with no outline. */
  w: number | null;
  l: number | null;
}

export interface PlanDeck {
  floor: number;
  x: number;
  y: number;
  yaw: number | null;
}

/** A belt and the floors it is drawn on; null = every view of all floors only. */
export interface PlanBelt {
  floors: number[] | null;
  pts: XY[];
}

export interface ShapeDef {
  cls: string;
  outline: string;
  bands: { d: string; veil: number }[];
  /** Furthest outline point from the centre, metres. */
  reach: number;
}

export interface Placed {
  id: string;
  def: number;
  x: number;
  y: number;
  rot: number;
}

/** [x, y, width, height] of an SVG viewBox. */
export type Frame = [number, number, number, number];

export const TILE_M = 8;
const FALLBACK_HALF = 4;

/** The page turn that squares the grid: the most common yaw, modulo 90, of the decks (or of the
 *  machines when there are none), in whole degrees. */
export function pageTurn(decks: { yaw: number | null }[], pieces: { yaw: number | null }[]): number {
  const from = decks.some((d) => d.yaw !== null) ? decks : pieces;
  const tally: Record<number, number> = {};
  let best = 0;
  let most = 0;
  from.forEach(function (p) {
    if (p.yaw === null) return;
    const a = ((Math.round(p.yaw) % 90) + 90) % 90;
    tally[a] = (tally[a] || 0) + 1;
    if (tally[a]! > most) {
      most = tally[a]!;
      best = a;
    }
  });
  return best;
}

/** Turns a point by -deg about `c`. */
export function turned(p: XY, c: XY, deg: number): XY {
  const a = (-deg * Math.PI) / 180;
  const dx = p[0] - c[0];
  const dy = p[1] - c[1];
  return [dx * Math.cos(a) - dy * Math.sin(a), dx * Math.sin(a) + dy * Math.cos(a)];
}

function n(v: number): string {
  return (Math.round(v * 10) / 10).toString();
}

export function ringsPath(rings: number[][][]): string {
  return rings
    .map((ring) => ring.map((p, i) => (i ? "L" : "M") + n(p[0]!) + " " + n(p[1]!)).join("") + "Z")
    .join("");
}

function rectPath(w: number, l: number): string {
  return "M" + n(-w) + " " + n(-l) + "H" + n(w) + "V" + n(l) + "H" + n(-w) + "Z";
}

/** One SVG symbol per class: its outline and height bands, or the measured box. */
export function shapeDefs(pieces: PlanPiece[], shapes: Record<string, BuildingShape>): { defs: ShapeDef[]; of: Record<string, number> } {
  const of: Record<string, number> = {};
  const defs: ShapeDef[] = [];
  pieces.forEach(function (p) {
    if (of[p.cls] !== undefined) return;
    of[p.cls] = defs.length;
    const shape = shapes[p.cls];
    if (shape && shape.rings.length) {
      let reach = 0;
      shape.rings.forEach((r) => r.forEach((q) => (reach = Math.max(reach, Math.hypot(q[0]!, q[1]!)))));
      let under = 0;
      const bands = shape.bands.map(function (b) {
        const veil = bandVeil(b.tier, under, false);
        under = b.tier;
        return { d: ringsPath(b.rings), veil: Math.round(veil * 1000) / 1000 };
      });
      defs.push({ cls: p.cls, outline: ringsPath(shape.rings), bands, reach });
    } else {
      const w = p.w || FALLBACK_HALF;
      const l = p.l || FALLBACK_HALF;
      defs.push({ cls: p.cls, outline: rectPath(w, l), bands: [], reach: Math.hypot(w, l) });
    }
  });
  return { defs, of };
}

/** Pieces moved into the turned page frame. */
export function place(pieces: PlanPiece[], of: Record<string, number>, centre: XY, turn: number): Placed[] {
  return pieces.map(function (p) {
    const at = turned([p.x, p.y], centre, turn);
    return { id: p.id, def: of[p.cls]!, x: at[0], y: at[1], rot: (p.yaw || 0) - turn };
  });
}

/** A frame around the placed pieces with a margin, at least `min` metres across. */
export function frameAround(placed: Placed[], defs: ShapeDef[], extra?: XY[]): Frame | null {
  let x0 = Infinity;
  let y0 = Infinity;
  let x1 = -Infinity;
  let y1 = -Infinity;
  placed.forEach(function (p) {
    const r = defs[p.def]!.reach;
    x0 = Math.min(x0, p.x - r);
    y0 = Math.min(y0, p.y - r);
    x1 = Math.max(x1, p.x + r);
    y1 = Math.max(y1, p.y + r);
  });
  (extra || []).forEach(function (q) {
    x0 = Math.min(x0, q[0]);
    y0 = Math.min(y0, q[1]);
    x1 = Math.max(x1, q[0]);
    y1 = Math.max(y1, q[1]);
  });
  if (!isFinite(x0)) return null;
  const pad = Math.max(x1 - x0, y1 - y0) * 0.04 + 4;
  return [x0 - pad, y0 - pad, x1 - x0 + 2 * pad, y1 - y0 + 2 * pad];
}

export interface DeckCells {
  /** One square per grid cell. */
  cells: string;
  /** The cell sides with no neighbour: the deck's outer edge. */
  edge: string;
  corners: XY[];
}

/** Foundation pieces snapped to the turned grid, as cells and their outer edge. */
export function deckCells(decks: PlanDeck[], centre: XY, turn: number, origin: XY): DeckCells {
  const has: Record<string, boolean> = {};
  const keys: XY[] = [];
  decks.forEach(function (d) {
    const at = turned([d.x, d.y], centre, turn);
    const k: XY = [Math.round((at[0] - origin[0]) / TILE_M), Math.round((at[1] - origin[1]) / TILE_M)];
    const key = k[0] + "," + k[1];
    if (has[key]) return;
    has[key] = true;
    keys.push(k);
  });
  let cells = "";
  let edge = "";
  const corners: XY[] = [];
  keys.forEach(function (k) {
    const x = origin[0] + k[0] * TILE_M - TILE_M / 2;
    const y = origin[1] + k[1] * TILE_M - TILE_M / 2;
    const X = n(x);
    const Y = n(y);
    cells += "M" + X + " " + Y + "h8v8h-8z";
    if (!has[k[0] + "," + (k[1] - 1)]) edge += "M" + X + " " + Y + "h8";
    if (!has[k[0] + "," + (k[1] + 1)]) edge += "M" + X + " " + n(y + TILE_M) + "h8";
    if (!has[k[0] - 1 + "," + k[1]]) edge += "M" + X + " " + Y + "v8";
    if (!has[k[0] + 1 + "," + k[1]]) edge += "M" + n(x + TILE_M) + " " + Y + "v8";
    corners.push([x, y], [x + TILE_M, y + TILE_M]);
  });
  return { cells, edge, corners };
}

/** The grid's origin in the turned frame: the first deck piece's centre. */
export function gridOrigin(decks: PlanDeck[], centre: XY, turn: number): XY {
  const first = decks[0];
  return first ? turned([first.x, first.y], centre, turn) : [0, 0];
}

export function beltPath(pts: XY[], centre: XY, turn: number): string {
  return pts.map((p, i) => {
    const at = turned(p, centre, turn);
    return (i ? "L" : "M") + n(at[0]) + " " + n(at[1]);
  }).join("");
}

/** The centre of the pieces' positions. */
export function centreOf(pieces: { x: number; y: number }[]): XY {
  if (!pieces.length) return [0, 0];
  let x = 0;
  let y = 0;
  pieces.forEach((p) => {
    x += p.x;
    y += p.y;
  });
  return [x / pieces.length, y / pieces.length];
}

export function segmentInBox(pts: XY[], box: [number, number, number, number]): boolean {
  return pts.some((p) => p[0] >= box[0] && p[0] <= box[2] && p[1] >= box[1] && p[1] <= box[3]);
}
