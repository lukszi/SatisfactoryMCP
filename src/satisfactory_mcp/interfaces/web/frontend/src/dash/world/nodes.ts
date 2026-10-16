/* World > nodes: nodes grouped by status or region, fields as hulls, and the best sites for one
 * resource, from /api/world/nodes, /sites and /regions. */

import { count, formatNumber, perMin } from "../../kit/format";
import { html, type Safe } from "../../kit/html";
import { chips, list, metric, nameLine } from "../../kit/ui/entry";
import { chip } from "../../kit/ui/marks";
import { dashH, emptyLine, icon, na } from "../../kit/ui/text";
import { knownNodes } from "../../map/drawn/markers";
import { pending, read } from "../factories/data";
import { fieldSelection, nodeSelection, siteSelection } from "./finds";
import { madeRead } from "./here";
import { fieldOutline, groupNodes, isFluid, keyOf, rateUnit, siteDistance, sortFields, sortNodes, type Mark } from "./model";
import { awayCell, legend, moreBtn, qs, row, type Ctx, type View } from "./view";

import type { Selection } from "../../app/selection";
import type { FoundField, FoundNode, NodeFindResponse, RankedSitesResponse, RegionTableResponse } from "../../api/shapes";

const SITES = "10";

function waiting(what: string, path: string, slot: ReturnType<typeof read>): View {
  return { title: what, body: pending(slot, what, path) || emptyLine("no " + what), marks: [], key: "", fitPlayer: false, legend: html``, picks: {} };
}

function nodeTitle(n: FoundNode): string {
  return [n.resource_name, n.purity, n.occupant ? n.occupant + (n.occupant_off ? " (off)" : "") : "no extractor", n.region ? n.region.name : "off the map", n.grid].join(" · ");
}

function nodeEntry(c: Ctx, n: FoundNode): Safe {
  const marks = html`${chip(n.purity, "muted")}${n.occupant_off ? chip("off", "mid", "the extractor on it is switched off") : ""}${n.moved ? chip("moved", "mid", "this node's position differs in the save") : ""}${n.status === "locked" ? chip("locked", "muted", "no extractor this world has unlocked can work it") : ""}`;
  const rate = n.kind === "geyser" ? html`<div class="metric">${na()}</div>` : metric(perMin(n.rate, false), rateUnit(n.resource));
  const inner = html`${icon(n.resource, n.resource_name)}<div class="id">${nameLine(n.resource_name, n.occupant || undefined)}</div>${chips(marks)}${rate}${awayCell(n.distance_m)}`;
  return row(c, keyOf.node(n), nodeTitle(n), inner, n.occupant_off ? "mid" : "", n.spoiler);
}

function nodeMark(n: { x_m: number; y_m: number; resource: string; purity: string; resource_name: string }, k: string, free: boolean): Mark {
  return { t: "node", k, x: n.x_m, y: n.y_m, resource: n.resource, purity: n.purity, free, tip: n.resource_name + " · " + n.purity + " · " + (free ? "free" : "tapped") };
}

function nodeLegend(c: Ctx, extra?: Parameters<typeof legend>[0]): Safe {
  return legend([["player", "player"], ["pure", "pure"], ["normal", "normal"], ["impure", "impure"], ["tapped", "tapped"], ...(extra || []), ...(c.factory ? [["factory", "factory"] as const] : [])] as Parameters<typeof legend>[0]);
}

function picksOf<R>(rows: R[], key: (r: R) => string, sel: (r: R) => Selection): Record<string, Selection> {
  const out: Record<string, Selection> = {};
  rows.forEach((r) => (out[key(r)] = sel(r)));
  return out;
}

function caveats(found: NodeFindResponse): string[] {
  const out: string[] = [];
  if (found.unit === "mixed") out.push("totals add items and m³ of fluid; choose one resource for a true total");
  if (found.save_error) out.push(found.save_error + "; occupancy unknown");
  return out.concat(found.notes);
}

function regionLabels(c: Ctx, names: string[]): Mark[] {
  const path = madeRead(qs("/api/world/regions", { resource: c.a.res }));
  const slot = read<RegionTableResponse>(path);
  return (slot.data?.rows || []).filter((r) => names.indexOf(r.name) >= 0).map((r): Mark => ({ t: "label", x: r.anchor_m[0], y: r.anchor_m[1], text: r.name }));
}

function nodesView(c: Ctx, found: NodeFindResponse): View {
  const all = sortNodes(found.nodes, c.a.sort);
  const rows = all.slice(0, c.shown);
  const groups = groupNodes(rows, c.a.group);
  const region = c.a.group === "region";
  const lists = groups.map((g) => html`${region ? html`<div class="grp">${g.label} · ${g.rows.length}</div>` : dashH(g.label, g.rows.length, "h3")}${region ? g.rows.map((n) => nodeEntry(c, n)) : list("wnode", "stacked", g.rows.map((n) => nodeEntry(c, n)))}`);
  const body = rows.length
    ? html`${region ? list("wnode", "stacked", lists) : lists}${moreBtn(c, all.length)}`
    : emptyLine("no node matches", "pick another resource or filter");
  const marks: Mark[] = rows.map((n) => nodeMark(n, keyOf.node(n), n.status === "free"));
  if (region) marks.push(...regionLabels(c, groups.map((g) => g.label)));
  const lead = c.a.sort === "nearest" && found.where ? count(rows.length) + " nearest of " + count(found.count) + " nodes" : count(rows.length) + " of " + count(found.count) + " nodes";
  return {
    title: region ? "regions" : "nodes",
    n: region ? groups.length : found.count,
    body,
    note: [lead].concat(caveats(found)).join(" · "),
    marks,
    key: "nodes|" + found.description + "|" + found.where + "|" + c.a.group + "|" + c.a.sort + "|" + rows.length + "|" + marks.length,
    fitPlayer: c.a.near === "me",
    legend: nodeLegend(c),
    picks: picksOf(rows, keyOf.node, nodeSelection),
  };
}

function fieldEntry(c: Ctx, f: FoundField): Safe {
  const fluid = f.resources.every((r) => /oil|water|nitrogen/i.test(r));
  const mixed = !fluid && f.resources.some((r) => /oil|water|nitrogen/i.test(r));
  const purities = ["pure", "normal", "impure"].filter((p) => f.purities[p]).map((p) => f.purities[p] + " " + p).join(" · ");
  const title = [f.resources.join(" + "), purities, f.region || "off the map", f.grid, "spread " + formatNumber(f.spread_m, 0) + " m"].join(" · ");
  const duo = html`<span class="wduo">${f.resources.slice(0, 2).map((r) => icon(null, r))}</span>`;
  const unit = fluid ? "m³/min" : mixed ? "mixed" : "/min";
  const inner = html`${duo}<div class="id">${nameLine(f.resources.join(" + "), f.region || undefined)}</div>${metric(perMin(f.total, false), unit)}<div class="facts"><span>${f.free ? html`<em>${perMin(f.free, false)}</em>` : na()} free</span></div><div class="facts"><span><em>${f.size}</em> ${f.size === 1 ? "node" : "nodes"}</span></div>${awayCell(f.distance_m)}`;
  return row(c, keyOf.field(f), title, inner, "", f.spoiler);
}

function fieldsView(c: Ctx, found: NodeFindResponse): View {
  const all = sortFields(found.fields, c.a.sort);
  const rows = all.slice(0, c.shown);
  const placed: Record<string, { x_m: number; y_m: number; resource: string; purity: string; resource_name: string; occupied: boolean }> = {};
  knownNodes().forEach((n) => (placed[n.name] = n));
  const marks: Mark[] = [];
  rows.forEach(function (f) {
    const k = keyOf.field(f);
    marks.push({ t: "hull", k, pts: fieldOutline(f, (name) => (placed[name] ? [placed[name]!.x_m, placed[name]!.y_m] : null)), tip: f.resources.join(" + ") + " · " + f.size + " nodes" });
    f.members.forEach((name) => placed[name] && marks.push(nodeMark(placed[name]!, k, !placed[name]!.occupied)));
  });
  return {
    title: "fields",
    n: found.fields.length,
    body: rows.length ? html`${list("wfield", "stacked", rows.map((f) => fieldEntry(c, f)))}${moreBtn(c, all.length)}` : emptyLine("no field holds this resource", "pick another resource"),
    note: [count(rows.length) + " of " + count(found.fields.length) + " fields"].concat(caveats(found)).join(" · "),
    marks,
    key: "fields|" + found.description + "|" + found.where + "|" + c.a.sort + "|" + rows.length,
    fitPlayer: c.a.near === "me",
    legend: nodeLegend(c, [["hull", "field"]]),
    picks: picksOf(rows, keyOf.field, fieldSelection),
  };
}

function rankView(c: Ctx): View {
  let path = qs("/api/world/sites", { resource: c.a.res, limit: SITES });
  const within = Number(c.a.extra.within) || 1000;
  if (c.a.extra.within && c.near) path += "&source=" + encodeURIComponent("near:" + c.near + "@" + within);
  if (c.a.extra.pure === "1") path += "&source=" + encodeURIComponent("purity:pure");
  const slot = read<RankedSitesResponse>(madeRead(path));
  if (!slot.data) return waiting("best sites", path, slot);
  const sites = slot.data;
  const fluid = isFluid(c.a.res);
  const weights = Object.keys(sites.weights || {}).map((k) => k.replace(/_/g, " ") + " " + formatNumber(sites.weights[k]!, 2)).join(" · ");
  const rows = sites.sites.map(function (s) {
    const title = [s.region || "off the map", s.grid, "spread " + formatNumber(s.spread_m, 0) + " m", s.to_infra_m === null ? "nothing built near" : formatNumber(s.to_infra_m, 0) + " m to the nearest building"].join(" · ");
    const inner = html`<span class="wrk">${s.rank}</span><div class="id">${nameLine(s.region || "off the map", s.grid)}</div><div class="metric" title="${"score weights: " + weights}"><b>${formatNumber(s.score, 2)}</b><span>score</span></div><div class="facts"><span><em>${perMin(s.untapped, false)}</em> ${fluid ? "m³/min" : "/min"} free</span></div><div class="facts"><span><em>${s.nodes}</em> ${s.nodes === 1 ? "node" : "nodes"}</span></div>${awayCell(siteDistance(s, c.from), true)}`;
    return row(c, keyOf.site(s), title, inner);
  });
  return {
    title: "best sites",
    n: sites.sites.length,
    body: rows.length ? list("wsite", "stacked", rows) : emptyLine("no build site found for " + sites.resource_name),
    note: [sites.sites.length < sites.count ? "top " + sites.sites.length + " of " + count(sites.count) + " candidate fields" : count(sites.count) + " candidate fields"].concat(sites.notes).join(" · "),
    marks: sites.sites.map((s): Mark => ({ t: "site", k: keyOf.site(s), x: s.x_m, y: s.y_m, r_m: Math.max(s.spread_m / 2 + 40, 60), rank: s.rank, tip: "#" + s.rank + " " + (s.region || "") + " · score " + formatNumber(s.score, 2) })),
    key: "sites|" + path,
    fitPlayer: false,
    legend: legend([["player", "player"], ["site", "site"], ...(c.factory ? [["factory", "factory"] as const] : [])] as Parameters<typeof legend>[0]),
    picks: picksOf(sites.sites, keyOf.site, siteSelection),
  };
}

export function nodesFinder(c: Ctx): View {
  if (c.a.sort === "rank") return rankView(c);
  const fields = c.a.group === "field";
  const path = madeRead(qs("/api/world/nodes", { view: fields ? "fields" : "nodes", resource: c.a.res, near: c.near, purity: c.a.extra.purity, status: c.a.extra.status }));
  const slot = read<NodeFindResponse>(path);
  if (!slot.data) return waiting(fields ? "fields" : "nodes", path, slot);
  return fields ? fieldsView(c, slot.data) : nodesView(c, slot.data);
}
