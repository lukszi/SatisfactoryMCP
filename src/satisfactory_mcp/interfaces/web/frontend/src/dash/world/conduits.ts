/* World > conduits: belt and pipe runs near a place, or the fluid networks, from
 * /api/world/conduits. */

import { count, formatNumber, metres } from "../../kit/format";
import { html, type Safe } from "../../kit/html";
import { selectBox } from "../../kit/ui/controls";
import { chips, list, metric, nameLine } from "../../kit/ui/entry";
import { emptyLine, icon, na } from "../../kit/ui/text";
import { pending, read } from "../factories/data";
import { runLabel, runSelection } from "./finds";
import { madeRead } from "./here";
import { keyOf, sortNetworks, sortRuns, type Mark } from "./model";
import { awayCell, legend, moreBtn, qs, row, type Ctx, type View } from "./view";

import type { Selection } from "../../app/selection";
import type { ConduitsResponse, NetworkRow, RunRow } from "../../api/shapes";

const LIMIT = "200";

export const RADII = ["100", "250", "500", "1000", "2000"];

const KINDS: [string, string][] = [
  ["", "belts and pipes"],
  ["belt", "belts"],
  ["pipe", "pipes"],
];

function tools(c: Ctx): Safe {
  const radius = c.a.radius || "250";
  return html`<label class="wfield">within ${selectBox("w-radius", RADII.map((r): [string, string] => [r, r + " m"]), radius, "search radius")}</label>${selectBox("w-ckind", KINDS, c.a.kind, "belts or pipes")}`;
}

function runEntry(c: Ctx, r: RunRow): Safe {
  const title = [r.id, r.network === null ? "" : "network " + r.network, "height " + formatNumber(r.z_min_m, 0) + (Math.round(r.z_max_m) !== Math.round(r.z_min_m) ? " to " + formatNumber(r.z_max_m, 0) : "") + " m", r.directed ? "" : "no direction"].filter(Boolean).join(" · ");
  const name = r.label && r.label !== r.id ? r.label : (r.kind === "pipe" ? "pipe" : r.kind === "lift" ? "lift" : "belt") + " " + r.id.replace(/^\w+:/, "");
  const carries = r.carries ? icon(null, r.carries, "") : na();
  const lead = icon(r.kind === "pipe" ? "Desc_Pipeline_C" : "Desc_ConveyorBeltMk1_C", r.kind);
  return row(c, keyOf.run(r), title, html`${lead}<div class="id">${nameLine(name, r.carries || undefined)}</div>${chips(carries)}${metric(metres(r.length_m))}${awayCell(r.distance_m)}`);
}

function netEntry(c: Ctx, n: NetworkRow): Safe {
  const title = ["touches " + (n.touches.length ? n.touches.join(", ") : "nothing"), "height " + formatNumber(n.z_min_m, 0) + " to " + formatNumber(n.z_max_m, 0) + " m"].join(" · ");
  const name = n.network === null ? "no network" : "network " + n.network;
  return row(c, keyOf.net(n), title, html`${icon(null, n.carries || "pipe")}<div class="id">${nameLine(name, n.carries || "nothing known")}</div>${metric(metres(n.length_m))}<div class="facts"><span><em>${n.pieces}</em> pieces</span></div>${awayCell(n.distance_m)}`);
}

function netSelection(n: NetworkRow): Selection {
  const id = n.network === null ? "none" : String(n.network);
  return { kind: "conduit", key: "network:" + id, label: "network " + id, x_m: n.x_m, y_m: n.y_m };
}

function picks<R>(rows: R[], key: (r: R) => string, sel: (r: R) => Selection): Record<string, Selection> {
  const out: Record<string, Selection> = {};
  rows.forEach((r) => (out[key(r)] = sel(r)));
  return out;
}

function scope(c: Ctx, data: ConduitsResponse): string {
  const parts = ["near " + (data.where || "–") + " within " + metres(data.radius_m)];
  if (c.a.extra.net) parts[0] = "network " + c.a.extra.net;
  return parts.concat(data.bridged, data.notes).join(" · ");
}

export function conduitsFinder(c: Ctx): View {
  const networks = c.a.group === "network";
  const path = madeRead(
    qs("/api/world/conduits", { near: c.near, radius_m: c.a.radius, conduit_kind: c.a.kind, view: networks ? "networks" : "", network: c.a.extra.net, limit: LIMIT })
  );
  const slot = read<ConduitsResponse>(path);
  if (!slot.data) {
    return { title: networks ? "fluid networks" : "runs", tools: tools(c), body: pending(slot, networks ? "fluid networks" : "belt and pipe runs", path) || emptyLine("nothing here"), marks: [], key: "", fitPlayer: false, legend: html``, picks: {} };
  }
  const data = slot.data;
  if (networks) {
    const all = sortNetworks(data.networks, c.a.sort);
    const rows = all.slice(0, c.shown);
    return {
      title: "fluid networks",
      n: all.length,
      tools: tools(c),
      body: rows.length ? html`${list("wnet", "stacked", rows.map((n) => netEntry(c, n)))}${moreBtn(c, all.length)}` : emptyLine("no fluid network here"),
      note: "every fluid network, distance from " + (data.where || "the player"),
      marks: rows.map((n): Mark => ({ t: "spot", k: keyOf.net(n), x: n.x_m, y: n.y_m, tip: (n.network === null ? "no network" : "network " + n.network) + (n.carries ? " · " + n.carries : "") })),
      key: "nets|" + path + "|" + c.a.sort + "|" + rows.length,
      fitPlayer: c.a.near === "me",
      legend: legend([["player", "player"], ["spot", "network"]]),
      picks: picks(rows, keyOf.net, netSelection),
    };
  }
  const all = sortRuns(data.runs, c.a.sort);
  const rows = all.slice(0, c.shown);
  const lead = count(data.belts) + " belt runs · " + count(data.pipes) + " pipe runs" + (data.total > data.runs.length ? " · " + count(data.runs.length) + " of " + count(data.total) + " listed" : "");
  return {
    title: "runs",
    n: data.total,
    tools: tools(c),
    body: rows.length ? html`${list("wrun", "stacked", rows.map((r) => runEntry(c, r)))}${moreBtn(c, all.length)}` : emptyLine("no belt or pipe run here", "widen the radius or search near another place"),
    note: lead + " · " + scope(c, data),
    marks: rows.map((r): Mark => ({ t: "line", k: keyOf.run(r), lines: r.lines_m, tip: runLabel(r) + " · " + metres(r.length_m) })),
    key: "runs|" + path + "|" + c.a.sort + "|" + rows.length,
    fitPlayer: c.a.near === "me",
    legend: legend([["player", "player"], ["line", "belt or pipe"]]),
    picks: picks(rows, keyOf.run, runSelection),
  };
}
