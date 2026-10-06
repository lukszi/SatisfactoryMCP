/* Numbers, places, times and names as the page prints them.
 *
 * Pure and shared, so two views showing one fact cannot word it two ways. Imports nothing at
 * runtime, which lets tests/test_web_format.py run it under node.
 */

import type { Region } from "../api/shapes";

/** Thousands separators, because these are counts of things and they get large: a full
 *  Industrial Storage Container holds 24,000 Wire, and a badge whose digits have to be counted
 *  is not a reading. Shared, so a tile's badge and the total under the grid cannot disagree. */
export function count(n: number): string {
  return n.toLocaleString("en-GB");
}

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

export function withUnit(value: number | null | undefined, decimals: number, unit: string): string {
  return value === null || value === undefined ? "–" : formatNumber(value, decimals) + unit;
}

export function metres(value: number | null | undefined, decimals?: number): string {
  return withUnit(value, decimals || 0, " m");
}

/* Half to even on the exact decimal expansion, matching Python's round() on the server. */
export function roundHalfEven(value: number, decimals?: number): number {
  var places = decimals || 0;
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
  return "x " + formatNumber(x, 0) + ", y " + formatNumber(y, 0) + " m";
}

export function signed(value: number, say: (magnitude: number) => string): string {
  var text = say(Math.abs(value));
  if (text === say(0)) return text;
  return (value < 0 ? "-" : "+") + text;
}

export function mw(value: number, options?: { signed?: boolean }): string {
  var say = function (magnitude: number): string {
    return count(Math.round(magnitude)) + " MW";
  };
  var text = signed(value, say);
  return options && options.signed ? text : text.replace(/^\+/, "");
}

export function formatNumber(value: number, decimals?: number): string {
  return count(roundHalfEven(value, decimals === undefined ? 1 : decimals));
}

export function countRange(lo: number, hi: number | null | undefined): string {
  if (hi === null || hi === undefined || hi === lo) return count(lo);
  return count(lo) + "–" + count(hi);
}

export function amount(value: number, fluid?: boolean): string {
  return fluid ? formatNumber(value, 1) + " m³" : formatNumber(value, 0);
}

export function perMin(value: number, unit?: boolean): string {
  return formatNumber(value, 1) + (unit === false ? "" : "/min");
}

export function flow(item: string, value: number, decimals?: number): string {
  return formatNumber(value, decimals) + " " + item + "/min";
}

export function pct(value: number | null | undefined, decimals?: number): string {
  if (value === null || value === undefined) return "–";
  return (decimals ? formatNumber(value * 100, decimals) : String(Math.round(value * 100))) + "%";
}

export function timeOfDay(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
}

/* "3× Constructor, 2× Smelter". `formatCount` defaults to the bare number, which is what most
 * callers print today. */
export function buildingCounts(
  entries: { count: number; name: string }[],
  joiner?: string,
  limit?: number,
  formatCount?: (n: number) => string
): string {
  var shown = limit === undefined ? entries : entries.slice(0, limit);
  return shown
    .map(function (entry) {
      return (formatCount ? formatCount(entry.count) : String(entry.count)) + "× " + entry.name;
    })
    .join(joiner === undefined ? ", " : joiner);
}

/* How many rows share each key, most first; ties keep the order the keys were first seen. */
export function tallyBy<R>(rows: R[], key: (row: R) => string): { name: string; count: number }[] {
  var counts = new Map<string, { name: string; count: number }>();
  rows.forEach(function (row) {
    var name = key(row);
    var entry = counts.get(name);
    if (!entry) {
      entry = { name: name, count: 0 };
      counts.set(name, entry);
    }
    entry.count += 1;
  });
  return Array.from(counts.values()).sort(function (a, b) {
    return b.count - a.count;
  });
}

export function nowSeconds(): number {
  return Date.now() / 1000;
}

export function ageShort(ts: number): string {
  var s = Math.max(0, Math.round(nowSeconds() - ts));
  if (s < 60) return s + "s";
  if (s < 3600) return Math.round(s / 60) + "m";
  if (s < 86400) return Math.round(s / 3600) + "h";
  return Math.round(s / 86400) + "d";
}

export function bytes(n: number | null | undefined): string {
  var value = n || 0;
  if (value >= 1e9) return (value / 1e9).toFixed(1) + " GB";
  if (value >= 1e6) return Math.round(value / 1e6) + " MB";
  if (value >= 1e3) return Math.round(value / 1e3) + " kB";
  return value + " B";
}

export function duration(seconds: number | null | undefined): string {
  var s = Math.max(0, Math.round(seconds || 0));
  if (s < 90) return s + " s";
  if (s < 5400) return Math.round(s / 60) + " min";
  return (s / 3600).toFixed(1) + " h";
}

export function isoDate(ts: number | null | undefined): string {
  if (!ts) return "–";
  var d = new Date(ts * 1000);
  var pad = function (n: number) {
    return (n < 10 ? "0" : "") + n;
  };
  return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate());
}

export function joinWithConjunction(names: string[], last: string): string {
  if (names.length < 2) return names.join("");
  return names.slice(0, -1).join(", ") + " " + last + " " + names[names.length - 1];
}
