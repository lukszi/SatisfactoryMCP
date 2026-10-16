/* The pure half of the resource wells: which satellites a core reaches, and what its tooltip
 * says. No Leaflet, so vitest can hold it. Drawn by markers.ts. */

import type { PointM } from "../geometry";

/** The fields a well's drawing reads off a satellite node row. */
export interface SatelliteSpot {
  name: string;
  purity: string;
  x_m: number;
  y_m: number;
}

/** The fields a well's drawing reads off its core row. */
export interface CoreSpot {
  satellites: string[];
  x_m: number;
  y_m: number;
}

/** One spoke per satellite the core names and the payload carries, core end first. A name
 *  with no row (a resource filter, a table the save is behind) draws no spoke rather than a
 *  line to the origin. */
export function wellSpokes(core: CoreSpot, satellites: Record<string, SatelliteSpot>): [PointM, PointM][] {
  const spokes: [PointM, PointM][] = [];
  core.satellites.forEach(function (name) {
    const sat = satellites[name];
    if (sat) spokes.push([[core.x_m, core.y_m], [sat.x_m, sat.y_m]]);
  });
  return spokes;
}

const PURITY_ORDER = ["pure", "normal", "impure"];

/** "7 satellites: 3 pure, 2 normal, 2 impure", counted off the rows present. */
export function satelliteSummary(core: CoreSpot, satellites: Record<string, SatelliteSpot>): string {
  const counts: Record<string, number> = {};
  let total = 0;
  core.satellites.forEach(function (name) {
    const sat = satellites[name];
    if (!sat) return;
    counts[sat.purity] = (counts[sat.purity] || 0) + 1;
    total++;
  });
  const parts = PURITY_ORDER.filter(function (p) {
    return counts[p];
  }).map(function (p) {
    return counts[p] + " " + p;
  });
  return total + (total === 1 ? " satellite" : " satellites") + (parts.length ? ": " + parts.join(", ") : "");
}

/** A satellite's ring radius in pixels: a step under the node dot of the same purity, so a
 *  well spot never reads as a bigger claim than an ore node. */
export function satelliteRadius(purityRadius: number): number {
  return Math.max(2.5, purityRadius - 1.5);
}
