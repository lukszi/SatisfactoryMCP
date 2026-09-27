/* Three ways of saying what the data means, in words rather than in identifiers.
 *
 * Pure, and shared: the node dot's popup and the right-click inspector both name a resource
 * and a region, and disagreeing about how would be the page contradicting itself about the
 * same fact at two different clicks.
 */

import { count } from "./dom";

import type { Region } from "./api-shapes";

export { count };

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

export function measured(value: number | null | undefined, dp: number, unit: string): string {
  return value === null || value === undefined ? "–" : num(value, dp) + unit;
}

export function metres(value: number | null | undefined, dp?: number): string {
  return measured(value, dp || 0, " m");
}

export function rounded(value: number, dp?: number): number {
  var places = dp || 0;
  if (!isFinite(value) || Math.abs(value) >= 1e15) return Math.round(value);
  var exact = Math.abs(value).toFixed(100);
  var point = exact.indexOf(".");
  var whole = Number(exact.slice(0, point) + exact.slice(point + 1, point + 1 + places));
  var rest = exact.slice(point + 1 + places);
  var first = rest.charAt(0);
  if (first > "5" || (first === "5" && (/[1-9]/.test(rest.slice(1)) || whole % 2 === 1))) whole += 1;
  var out = whole / Math.pow(10, places);
  return (value < 0 ? -out : out) + 0;
}

export function coords(x: number, y: number): string {
  return rounded(x) + ", " + rounded(y) + " m";
}

export function mw(value: number, options?: { signed?: boolean }): string {
  var whole = (value < 0 ? -Math.round(-value) : Math.round(value)) + 0;
  return (options && options.signed && whole > 0 ? "+" : "") + count(whole) + " MW";
}

export function num(value: number, dp?: number): string {
  return count(rounded(value, dp === undefined ? 1 : dp));
}

export function amount(value: number, fluid?: boolean): string {
  return fluid ? num(value, 1) + " m³" : num(value, 0);
}

export function perMin(value: number, unit?: boolean): string {
  return num(value, 1) + (unit === false ? "" : "/min");
}

export function flow(item: string, value: number, dp?: number): string {
  return num(value, dp) + " " + item + "/min";
}

export function pct(value: number | null | undefined, dp?: number): string {
  if (value === null || value === undefined) return "–";
  return (dp ? num(value * 100, dp) : String(Math.round(value * 100))) + "%";
}

export function spoken(names: string[], last: string): string {
  if (names.length < 2) return names.join("");
  return names.slice(0, -1).join(", ") + " " + last + " " + names[names.length - 1];
}
