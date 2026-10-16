/* A building's outline and port notches placed in the world, in game metres. Pure: no Leaflet,
 * no map. The outline's source and frame: docs/building-shapes.md. */

import type { PortRow } from "../../api/shapes";

export type XY = [number, number];

/** Local actor-frame points turned by `yaw` (degrees, +X towards +Y) and moved to (x, y). */
export function placeLocal(x: number, y: number, yaw: number | null | undefined, local: XY[]): XY[] {
  const a = ((yaw || 0) * Math.PI) / 180;
  const cos = Math.cos(a);
  const sin = Math.sin(a);
  return local.map(function (p): XY {
    return [x + p[0] * cos - p[1] * sin, y + p[0] * sin + p[1] * cos];
  });
}

/** Every ring of a shape placed in the world. */
export function placeRings(x: number, y: number, yaw: number | null | undefined, rings: number[][][]): XY[][] {
  return rings.map(function (ring) {
    return placeLocal(
      x,
      y,
      yaw,
      ring.map(function (p): XY {
        return [p[0]!, p[1]!];
      })
    );
  });
}

/** Rows in drawing order, lowest first, so a building on an upper deck is painted over the one
 *  under it; a row with no height counts as the ground. Stable, and a new array. */
export function lowestFirst<T extends { z_m: number | null }>(rows: readonly T[]): T[] {
  return rows
    .map(function (row, at) {
      return { row: row, at: at };
    })
    .sort(function (a, b) {
      return (a.row.z_m ?? -Infinity) - (b.row.z_m ?? -Infinity) || a.at - b.at;
    })
    .map(function (entry) {
      return entry.row;
    });
}

/* A notch's size in metres: about a belt's width across, so it reads as the belt's mouth. */
export const NOTCH_HALF_WIDTH_M = 0.8;
const NOTCH_OUT_M = 0.7;
const NOTCH_IN_M = 0.3;

/** A port's notch in the actor frame: a triangle pointing out of the building for an output,
 *  into it for an input, and a diamond for a port that runs either way. */
export function notchLocal(port: PortRow): XY[] {
  const a = (port.yaw * Math.PI) / 180;
  const f: XY = [Math.cos(a), Math.sin(a)];
  const n: XY = [-f[1], f[0]];
  const at = function (along: number, across: number): XY {
    return [port.x_m + f[0] * along + n[0] * across, port.y_m + f[1] * along + n[1] * across];
  };
  const w = NOTCH_HALF_WIDTH_M;
  if (port.flow === "out") return [at(-NOTCH_IN_M, -w), at(NOTCH_OUT_M, 0), at(-NOTCH_IN_M, w)];
  if (port.flow === "in") return [at(NOTCH_OUT_M, -w), at(-NOTCH_IN_M, 0), at(NOTCH_OUT_M, w)];
  return [at(-NOTCH_IN_M - 0.2, 0), at(0.2, -w), at(NOTCH_OUT_M, 0), at(0.2, w)];
}
