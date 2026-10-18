/* A factory page's aspects beside the plan: overview, machines, power, nodes, links, floors and
 * sites. Rows carry the plan keys they light in data-hl. docs/frontend_kit.md, "Factories". */

import { vitals } from "../../app/vitals";
import { count, formatNumber, metres, mw, pct } from "../../kit/format";
import { cls, html, type Hole, type Safe } from "../../kit/html";
import { check, selectBox, seg, sortSelect } from "../../kit/ui/controls";
import { entry, group, list, nameLine, type EntryTone } from "../../kit/ui/entry";
import { bar, chip, tagChip } from "../../kit/ui/marks";
import { dashH, emptyLine, icon, na, toolrow } from "../../kit/ui/text";
import { counted, WORDS } from "../../kit/words";
import { hashFor } from "../../map/map";
import { needsAction, stateTone } from "../machine-states";
import { factoryDash } from "./address";
import { pending } from "./data";
import { aspectHref, iconFor, onFloor, slug, type FCtx } from "./ctx";
import { boostsOf, worstState, type Boost, type MachineGroup, type PlanMachine } from "./model";
import { q, read } from "./data";

import type { ShardsResponse, SloopsResponse } from "../../api/shapes";

/** Page-session choices of the machines aspect. */
export const opts = { grain: "group", groupSort: "building", machineSort: "state", building: "all", boosted: false, more: new Set<string>() };

function toneOfState(state: string): "bad" | "blocked" | "" {
  const t = stateTone(state);
  return t === "bad" || t === "blocked" ? t : "";
}

function row(c: FCtx, hl: string, inner: Hole, o?: { tone?: EntryTone; go?: string; extra?: string }): Safe {
  const picked = !!hl && c.picked === hl;
  const go = o && o.go ? html` data-go="${o.go}"` : "";
  return html`<div class="${cls("entry", o && o.tone, picked && "picked", o && o.extra)}" data-hl="${hl}" tabindex="0"${go}>${inner}</div>`;
}

function floorScope(c: FCtx, aspect: string): Safe {
  if (c.floor < 0) return html``;
  return html`<p class="fscope">${chip("floor " + c.floor)}<a href="${aspectHref(c, aspect, "all")}">show all floors</a></p>`;
}

function shownMachines(c: FCtx): PlanMachine[] {
  return (c.machines || []).filter((m) => onFloor(c, m));
}

function waitFor(c: FCtx): Safe | null {
  return c.machines ? null : c.machinesWait;
}

/* ------------------------------------------------------------------ overview */

function causeOf(c: FCtx): Record<string, string> {
  const out: Record<string, string> = {};
  c.row.worst.concat(c.row.worst_actionable).forEach((i) => {
    if (i.cause.length) out[i.instance] = i.cause.join(", ");
  });
  return out;
}

export function overviewAspect(c: FCtx): Safe {
  const wait = waitFor(c);
  if (wait) return wait;
  const ms = shownMachines(c);
  if (!ms.length) return html`${floorScope(c, "")}${dashH("machines by state", 0)}${emptyLine("no machines on this floor")}`;
  const by: Record<string, number> = {};
  ms.forEach((m) => (by[m.row.state] = (by[m.row.state] || 0) + 1));
  const states = c.order.filter((s) => by[s]).concat(Object.keys(by).filter((s) => c.order.indexOf(s) < 0));
  const most = Math.max(...states.map((s) => by[s]!));
  const stateRows = states.map((s) => {
    const tone = toneOfState(s);
    return row(c, "st-" + slug(s), html`<span class="${cls("name", tone && "tone-" + tone)}">${s}</span><div class="metric"><b>${by[s]}</b></div>${bar((by[s]! / most) * 100, tone || "util")}`, { tone });
  });
  const causes = causeOf(c);
  const lines: { g: MachineGroup; state: string; n: number; cause: string }[] = [];
  c.groups.forEach((g) => {
    const here = ms.filter((m) => m.group === g.index && needsAction(m.row.state));
    const per: Record<string, PlanMachine[]> = {};
    here.forEach((m) => (per[m.row.state] = per[m.row.state] || []).push(m));
    Object.keys(per).forEach((s) => {
      const cause = per[s]!.map((m) => causes[m.id]).filter(Boolean)[0] || "";
      lines.push({ g, state: s, n: per[s]!.length, cause });
    });
  });
  lines.sort((a, b) => Number(a.state === "blocked") - Number(b.state === "blocked") || b.n - a.n);
  const need = lines.reduce((sum, l) => sum + l.n, 0);
  const go = aspectHref(c, "machines");
  const needRows = lines.map((l) => {
    const tone = toneOfState(l.state);
    return row(c, "g" + l.g.index, html`${icon(iconFor(c, l.g.cls, l.g.building), l.g.building)}<div class="id">${nameLine(html`<span class="name">${l.g.recipe || l.g.building}</span>`, l.cause)}</div><span class="${cls("st", "tone-" + tone)}">${l.state}</span><div class="metric"><b>${l.n}</b></div>`, { tone, go });
  });
  return html`${floorScope(c, "")}${dashH("machines by state", ms.length)}${list("fstate", "stacked", stateRows, "nolead")}${dashH(WORDS.needAction, need)}${needRows.length ? list("fiss", "stacked", needRows) : emptyLine("none " + WORDS.needAction)}`;
}

/* ------------------------------------------------------------------ machines */

function clockCell(clock: number): Safe {
  return html`<div class="metric"><b>${pct(clock)}</b><span>clock</span></div>`;
}

function groupStates(ms: PlanMachine[], g: MachineGroup): Record<string, number> {
  const out: Record<string, number> = {};
  ms.forEach((m) => {
    if (m.group === g.index) out[m.row.state] = (out[m.row.state] || 0) + 1;
  });
  return out;
}

function worstChip(states: Record<string, number>, n: number, order: string[]): Safe {
  const w = worstState(states, order);
  if (!w || (!needsAction(w) && w !== WORDS.paused)) return html``;
  return chip((states[w] === n ? "" : states[w] + " ") + w, w === "blocked" ? "blocked" : w === WORDS.paused ? "mid" : "bad");
}

function boostCell(b: Boost | undefined): Safe {
  if (!b) return html`<div class="facts boost"></div>`;
  return html`<div class="facts boost">${b.shards ? html`<span><em>${b.shards}</em> ${b.shards === 1 ? "shard" : "shards"}</span>` : ""}${b.idle ? chip(b.idle + " idle", "mid", counted(b.idle, "shard slot") + " not needed for the saved clock") : ""}${b.sloops ? html`<span><em>${b.sloops}</em> ${b.sloops === 1 ? "sloop" : "sloops"}</span>` : ""}${b.disagrees ? chip("boost disagrees", "bad", "the boost worked out here differs from the one in the save") : ""}</div>`;
}

function machinesPerGroup(c: FCtx, ms: PlanMachine[], tools: Safe): Safe {
  const shards = read<ShardsResponse>("/api/progress/shards").data;
  const sloops = read<SloopsResponse>("/api/progress/sloops").data;
  const boosts = boostsOf(c.groups, shards, sloops);
  const hasBoost = Object.keys(boosts).length > 0;
  const groups = c.groups
    .map((g) => ({ g, states: groupStates(ms, g), n: ms.filter((m) => m.group === g.index).length }))
    .filter((x) => x.n && (!opts.boosted || boosts[x.g.index]));
  const sorts: [string, string][] = [["building", "building"], ["machines", "machines"], ["worst", "worst state"]];
  const head = toolrow(dashH("machines", groups.reduce((s, x) => s + x.n, 0)), html`${tools}${hasBoost ? check("fboost", opts.boosted, "boosted only") : ""}${sortSelect("fgsort", sorts, opts.groupSort)}`);
  if (!groups.length) return html`${head}${emptyLine(opts.boosted ? "no boosted machines" : "no machines on this floor")}`;
  const one = (x: (typeof groups)[number]) => {
    const w = worstState(x.states, c.order);
    const tone = w ? toneOfState(w) : "";
    const name = x.g.recipe ? html`<span class="name">${x.g.recipe}</span>` : html`<span class="name muted">no recipe</span>`;
    const alt = x.g.recipe && /^alternate/i.test(x.g.recipe) ? tagChip("alt") : "";
    return row(c, "g" + x.g.index, html`${icon(iconFor(c, x.g.cls, x.g.building), x.g.building)}<div class="id">${nameLine(html`${name}${alt}`, x.g.building)}</div><div class="chips">${worstChip(x.states, x.n, c.order)}</div><div class="metric"><b>${x.n}</b></div>${clockCell(x.g.clock)}${hasBoost ? boostCell(boosts[x.g.index]) : ""}`, { tone });
  };
  let inner: Safe[];
  if (opts.groupSort === "building") {
    const buildings: string[] = [];
    groups.forEach((x) => buildings.indexOf(x.g.building) < 0 && buildings.push(x.g.building));
    inner = buildings.map((b) => {
      const xs = groups.filter((x) => x.g.building === b);
      return html`${group(b, xs.reduce((s, x) => s + x.n, 0))}${xs.map(one)}`;
    });
  } else {
    const rank = (x: (typeof groups)[number]) => {
      const w = worstState(x.states, c.order);
      return w ? c.order.indexOf(w) : 99;
    };
    inner = groups.slice().sort(opts.groupSort === "machines" ? (a, b) => b.n - a.n : (a, b) => rank(a) - rank(b)).map(one);
  }
  return html`${head}${list(hasBoost ? "fgroup has-boost" : "fgroup", "stacked", inner)}`;
}

function machinesPerMachine(c: FCtx, ms: PlanMachine[], tools: Safe): Safe {
  const buildings: string[] = [];
  ms.forEach((m) => buildings.indexOf(m.row.name) < 0 && buildings.push(m.row.name));
  const choices: [string, string][] = [["all", "all buildings"]].concat(buildings.sort().map((b): [string, string] => [b, b])) as [string, string][];
  const sorts: [string, string][] = [["state", "state"], ["uptime", "uptime"], ["building", "building"]];
  let shown = ms.filter((m) => opts.building === "all" || m.row.name === opts.building);
  const head = toolrow(dashH("machines", shown.length), html`${tools}${selectBox("fmbld", choices, opts.building, "building")}${sortSelect("fmsort", sorts, opts.machineSort)}`);
  if (!shown.length) return html`${head}${emptyLine(c.floor >= 0 ? "no machines on this floor" : "no machines")}`;
  const one = (m: PlanMachine) => {
    const tone = toneOfState(m.row.state);
    const st = tone ? chip(m.row.state, tone) : html`<span class="st muted">${m.row.state}</span>`;
    const up = m.row.uptime === null ? html`<div class="metric none"><b>–</b></div>` : html`<div class="metric"><b>${pct(m.row.uptime)}</b><span>up</span></div>`;
    return row(c, "m" + m.id, html`${icon(iconFor(c, m.row.cls, m.row.name), m.row.name)}<div class="id">${nameLine(html`<span class="name">${m.row.recipe_name || "no recipe"}</span>`, m.row.name)}</div><div class="chips">${st}</div>${clockCell(m.row.clock ?? 1)}${up}`, { tone });
  };
  const cap = (key: string, xs: PlanMachine[]) => {
    const all = opts.more.has(key);
    const kept = xs.filter((m, i) => all || i < 6 || c.picked === "m" + m.id);
    const rest = xs.length - kept.length;
    return html`${kept.map(one)}${rest ? html`<button type="button" class="fmore" data-more="${key}">··· ${rest} more</button>` : ""}`;
  };
  let inner: Safe;
  if (opts.machineSort === "state") {
    const states = c.order.filter((s) => shown.some((m) => m.row.state === s));
    inner = html`${states.map((s) => {
      const xs = shown.filter((m) => m.row.state === s);
      return html`${group(s, xs.length)}${cap("st:" + s, xs)}`;
    })}`;
  } else {
    shown = shown.slice().sort(opts.machineSort === "uptime" ? (a, b) => (a.row.uptime ?? 9) - (b.row.uptime ?? 9) : (a, b) => a.row.name.localeCompare(b.row.name));
    inner = cap("all", shown);
  }
  return html`${head}${list("fmach", "stacked", inner)}`;
}

export function machinesAspect(c: FCtx): Safe {
  const wait = waitFor(c);
  if (wait) return wait;
  const ms = shownMachines(c);
  const grains = [
    { value: "group", label: "per recipe" },
    { value: "machine", label: "per machine" },
  ];
  const tools = seg("fgrain", grains, opts.grain, "list the machines");
  return html`${floorScope(c, "machines")}${opts.grain === "machine" ? machinesPerMachine(c, ms, tools) : machinesPerGroup(c, ms, tools)}`;
}

/* ------------------------------------------------------------------ power */

function pctW(v: number): string {
  return Math.max(0, Math.min(100, v)).toFixed(2) + "%";
}

export function powerAspect(c: FCtx): Safe {
  const data = c.aspects.data;
  if (!data) return pending(c.aspects, "this factory's power", c.aspectsPath) || html``;
  const p = data.power;
  const top = Math.max(p.draw_mw, p.measured_draw_mw, p.generation_mw, 1);
  const rows: [string, string, number, string][] = [];
  if (p.generation_mw > 0) rows.push([WORDS.capacity, "", p.generation_mw, "gen"]);
  rows.push([WORDS.maxConsumption, WORDS.maxConsumptionTip, p.draw_mw, "np"], [WORDS.consumption, "", p.measured_draw_mw, "ms"]);
  const cells = rows.map((r, i) => {
    const at = "grid-row:" + (i + 1);
    return html`<span class="k" style="${at}"${r[1] ? html` title="${r[1]}"` : ""}>${r[0]}</span><div class="tr" style="${at}"><i class="${r[3]}" style="width:${pctW((r[2] / top) * 100)}"></i></div><span class="v" style="${at}">${count(Math.round(r[2]))} <span>MW</span></span>`;
  });
  const circuits = vitals().circuits;
  const mine = circuits ? circuits.circuits.filter((row) => row.factories.some((f) => f.name === c.name)) : [];
  const links = html`${mine.map((row) => html`<a href="${hashFor("power/" + (row.index + 1))}">circuit ${row.index + 1}</a> `)}`;
  const unmeasured = p.unmonitored ? html`<div class="nc"><span class="k">not counted</span>${chip(count(p.unmonitored) + " unmeasured", "muted", counted(p.unmonitored, "machine") + " keep no monitor; " + WORDS.consumption + " charges them in full")}</div>` : "";
  return html`<section class="card">${toolrow(dashH("draw"), links)}<div class="sd fdraw" role="group" aria-label="${WORDS.maxConsumption + " " + mw(p.draw_mw) + ", " + WORDS.consumption + " " + mw(p.measured_draw_mw)}">${cells}</div>${unmeasured}</section>`;
}

/* ------------------------------------------------------------------ nodes, links */

function itemIcon(c: FCtx, name: string): string | null {
  if (!c.graph) return null;
  const node = c.graph.nodes.filter((n) => n.item_name === name && n.item_id)[0];
  if (node) return node.item_id;
  const edge = c.graph.edges.filter((e) => e.item_name === name && e.item_id)[0];
  return edge ? edge.item_id : null;
}

export function nodesAspect(c: FCtx): Safe {
  const data = c.aspects.data;
  if (!data) return pending(c.aspects, "the nodes", c.aspectsPath) || html``;
  if (!data.nodes.length) return html`${dashH("resource nodes", 0)}${emptyLine("no resource nodes", "it is fed from outside, or its extractors are named as another factory")}`;
  const extractors = (c.machines || []).filter((m) => !m.row.recipe && m.row.x_m !== null);
  const near = (x: number | null, y: number | null) => {
    if (x === null || y === null) return "";
    let best = "";
    let dist = 15 * 15;
    extractors.forEach((m) => {
      const d = (m.row.x_m! - x) ** 2 + (m.row.y_m! - y) ** 2;
      if (d < dist) {
        dist = d;
        best = "m" + m.id;
      }
    });
    return best;
  };
  const rows = data.nodes
    .slice()
    .sort((a, b) => a.resource.localeCompare(b.resource))
    .map((n) => {
      const left = n.left === null ? html`<div class="metric none"><b>–</b><span>left</span></div>` : html`<div class="metric"><b>${formatNumber(n.left, 0)}</b><span>left</span></div>`;
      return row(c, near(n.x_m, n.y_m), html`${icon(itemIcon(c, n.resource), n.resource)}<div class="id">${nameLine(html`<span class="name">${n.resource}</span>`, n.purity + " · " + n.extractor)}</div>${clockCell(n.clock)}${left}`);
    });
  return html`${dashH("resource nodes", data.nodes.length)}${list("ffnode", "stacked", rows)}`;
}

export function linksAspect(c: FCtx): Safe {
  const data = c.aspects.data;
  if (!data) return pending(c.aspects, "the links", c.aspectsPath) || html``;
  if (!data.links.length) return html`${dashH("material links", 0)}${emptyLine("no material links", "no belt or pipe leaves this factory for another machine")}`;
  const rows = data.links.map((l) => {
    const href = l.factory ? hashFor(factoryDash(l.factory, "")) : "";
    const name = l.factory ? html`<a class="name" href="${href}">${l.factory}</a>` : html`<span class="name muted">machines no factory covers</span>`;
    return entry({ href: href || undefined }, html`<div class="id">${name}</div><div class="metric" title="machines reached on the far side through belts and pipes"><b>${count(l.machines)}</b><span>reached</span></div>`);
  });
  return html`${dashH("material links", data.links.length)}${list("flink", "stacked", rows, "nolead")}`;
}

/* ------------------------------------------------------------------ floors, sites */

function kindPills(kinds: [string, number, string | null][]): Safe {
  return html`<div class="pills kinds">${kinds.map((k) => html`<span class="kp" title="${k[0]}">${icon(k[2], k[0])}<b>${k[1]}</b></span>`)}</div>`;
}

function kindsOf(c: FCtx, ms: PlanMachine[]): [string, number, string | null][] {
  const by: Record<string, [string, number, string | null]> = {};
  ms.forEach((m) => {
    const k = (by[m.row.name] = by[m.row.name] || [m.row.name, 0, iconFor(c, m.row.cls, m.row.name)]);
    k[1]++;
  });
  return Object.keys(by)
    .map((k) => by[k]!)
    .sort((a, b) => b[1] - a[1]);
}

export function floorsAspect(c: FCtx): Safe {
  const wait = waitFor(c);
  if (wait) return wait;
  if (!c.floors.length) return html`${dashH("floors", 0)}${emptyLine("no floors", "the machines stand on terrain")}`;
  const rows = c.floors.map((f) => {
    const ms = (c.machines || []).filter((m) => m.floor === f.ordinal);
    const inner = html`<div class="id"><span class="name">floor ${f.ordinal}</span></div><div class="chips">${f.minor ? chip("minor", "muted", "under a quarter of the largest deck: a mezzanine or a plinth") : ""}</div>${kindPills(kindsOf(c, ms))}<div class="metric"><b>${ms.length}</b></div><div class="metric">${f.deck === null ? na() : html`<b>${metres(f.deck, 1).replace(/ m$/, "")}</b>`}<span>m</span></div><div class="metric"><b>${formatNumber(f.area, 0)}</b><span>m²</span></div>`;
    return row(c, "fl" + f.ordinal, inner, { extra: c.floor === f.ordinal ? "picked" : "" });
  });
  return html`${dashH("floors", c.floors.length)}${list("ffloor", "stacked", rows, "nolead")}`;
}

export function sitesAspect(c: FCtx): Safe {
  const path = q("/api/factories/sites", { factory: c.name });
  const slot = read<NonNullable<FCtx["sites"]>>(path);
  if (!slot.data) return pending(slot, "the sites", path) || html``;
  const sites = slot.data.sites.filter((s) => s.mine > 0);
  if (!sites.length) return html`${dashH("sites", 0)}${emptyLine("no site holds this factory's machines")}`;
  const iconOfName: Record<string, string | null> = {};
  (c.machines || []).forEach((m) => (iconOfName[m.row.name] = iconFor(c, m.row.cls, m.row.name)));
  const rows = sites.map((s, i) => {
    const kinds = s.buildings.map((b): [string, number, string | null] => [b.name, b.count, iconOfName[b.name] ?? null]);
    const shared = s.count > s.mine ? chip("shared", "muted", "other buildings stand in this site too") : "";
    return row(c, "s" + i, html`<div class="id"><span class="name">${s.direction} · ${s.grid}</span></div><div class="chips">${shared}</div>${kindPills(kinds)}<div class="metric"><b>${s.mine}</b><span>of ${s.count}</span></div><div class="metric"><b>${formatNumber(s.diameter_m, 0)}</b><span>m</span></div>`);
  });
  return html`${dashH("sites", sites.length + " of " + slot.data.total)}${list("fsite", "stacked", rows, "nolead")}`;
}
