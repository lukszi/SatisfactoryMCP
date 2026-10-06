/* What the World finders' rows are called, where they ask, and what selecting one selects:
 * shared by the World tabs and the finder card on the map. */

import { formatNumber, perMin, roundHalfEven } from "../../kit/format";
import { knownNodes } from "../../map/drawn/markers";
import { pickupName } from "../../map/drawn/pickups";
import { withQuery } from "../../app/nav";
import { WORDS } from "../../kit/words";

import type { ApiPath, ApiUrl } from "../../api/client";
import type { CollectibleRow, FoundField, FoundNode, RankedSite, RunRow } from "../../api/shapes";
import type { Selection } from "../../app/selection";

export function worldUrl(path: ApiPath, params: Record<string, string>): ApiUrl {
  var query = withQuery("", params).slice(1);
  return query ? `${path}?${query}` : path;
}

export function resourceOptions(anyLabel: string, current: string): [string, string][] {
  var names: Record<string, string> = {};
  knownNodes().forEach(function (n) {
    if (n.kind !== "geyser") names[n.resource] = n.resource_name;
  });
  var options = Object.keys(names)
    .map(function (id): [string, string] {
      return [id, names[id]!];
    })
    .sort(function (a, b) {
      return a[1].localeCompare(b[1]);
    });
  if (current && !names[current]) options.unshift([current, current]);
  return [["", anyLabel] as [string, string]].concat(options);
}

export function nodeLabel(n: { resource_name: string; purity: string }): string {
  return n.resource_name + ", " + n.purity;
}

export function fieldLabel(f: FoundField): string {
  return f.resources.join(" + ") + " " + WORDS.field + " · " + (f.region || f.grid);
}

export function nodeRate(n: FoundNode): string {
  return n.kind === "geyser" ? "–" : perMin(n.rate, false);
}

export function carriesText(r: RunRow): string {
  var parts: string[] = [];
  if (r.carries) parts.push(r.carries);
  else if (r.kind === "pipe") parts.push("nothing known");
  if (r.rate !== null) parts.push((r.kind === "pipe" ? formatNumber(r.rate, 0) + " m³/min" : perMin(r.rate)) + " max");
  return parts.length ? parts.join(" · ") : "–";
}

export function runLabel(r: RunRow): string {
  return r.label && r.label !== r.id ? r.id + " · " + r.label : r.id;
}

export function nodeSelection(n: FoundNode): Selection {
  return { kind: "node", key: n.id, label: nodeLabel(n), x_m: n.x_m, y_m: n.y_m, ref: "node:" + n.name };
}

export function fieldSelection(f: FoundField): Selection {
  return { kind: "field", key: f.key, label: fieldLabel(f), x_m: f.x_m, y_m: f.y_m, ref: f.selector };
}

export function siteSelection(s: RankedSite): Selection {
  return { kind: "field", key: s.selector, label: "site " + s.rank + (s.region ? ", " + s.region : ""), x_m: s.x_m, y_m: s.y_m, ref: s.selector };
}

export function runSelection(r: RunRow): Selection {
  return { kind: "conduit", key: r.id, label: runLabel(r), x_m: r.a.x_m, y_m: r.a.y_m, ref: r.id };
}

export function pickupPlace(p: { x_m: number; y_m: number }): string {
  return roundHalfEven(p.x_m) + "," + roundHalfEven(p.y_m);
}

export function pickupSelection(p: CollectibleRow): Selection {
  return { kind: "pickup", key: p.name, label: pickupName(p.category), x_m: p.x_m, y_m: p.y_m, ref: pickupPlace(p) };
}
