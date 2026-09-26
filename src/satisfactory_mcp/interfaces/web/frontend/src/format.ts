/* Three ways of saying what the data means, in words rather than in identifiers.
 *
 * Pure, and shared: the node dot's popup and the right-click inspector both name a resource
 * and a region, and disagreeing about how would be the page contradicting itself about the
 * same fact at two different clicks.
 */

import { count } from "./dom";

import type { Region } from "./api-shapes";

/* A resource class as the short name the whole page uses: Desc_OreIron_C -> OreIron. */
export function shortResource(resource: string | null | undefined): string {
  return String(resource || "")
    .replace(/^Desc_/, "")
    .replace(/_C$/, "");
}

/* A region lookup as one line: "Northern Forest, interior".
 *
 * The confidence word is never dropped, not even for an interior hit. The raster is 256 m per
 * cell, so the name and how far it can be trusted are one claim, and a name printed bare next
 * to a MEASURED elevation would borrow that measurement's authority. `null` is the
 * ocean-or-off-map answer, said plainly rather than softened into the nearest bit of land. */
export function regionLine(region: Region | null | undefined): string {
  return region ? region.name + ", " + region.confidence : "off the map";
}

/* The engine's phase asset name as words: GP_Project_Assembly_Phase_3 ->
 * "Project Assembly phase 3". Null for the pre-1.0 saves that carry no phase at all,
 * so the header can omit the segment instead of printing "phase " and a hole. */
export function phaseText(raw: string | null | undefined): string | null {
  if (!raw) return null;
  var match = /^GP_(.+)_Phase_(\d+)$/.exec(raw);
  if (match) return match[1]!.replace(/_/g, " ") + " phase " + match[2];
  return raw;
}

export function mw(value: number): string {
  return count(Math.round(value)) + " MW";
}

export function pct(value: number | null | undefined): string {
  return value === null || value === undefined ? "–" : Math.round(value * 100) + "%";
}

export function spoken(names: string[], last: string): string {
  if (names.length < 2) return names.join("");
  return names.slice(0, -1).join(", ") + " " + last + " " + names[names.length - 1];
}
