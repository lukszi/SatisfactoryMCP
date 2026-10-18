/* A circuit's page (`dash=power/<n>`): will it hold, what makes it, what draws it, what to do.
 * A ledger: slim header, supply and demand with the draw split by factory, do next, then
 * generators | draw by factory on the same MW scale. docs/frontend_kit.md, "Power". */

import { html, type Safe } from "../../kit/html";
import { acts, entry, list } from "../../kit/ui/entry";
import { chip } from "../../kit/ui/marks";
import { blockHead, dashH, emptyLine, icon, pageHead } from "../../kit/ui/text";
import { count, mw } from "../../kit/format";
import { counted, WORDS } from "../../kit/words";
import { vitals } from "../../app/vitals";
import { hashFor } from "../../map/map";
import { factoryDash } from "../factories/address";
import { factoryIcon, factoryLead, iconLead } from "../factories/icon-choice";
import { read } from "../factories/data";
import { circuitDark, NONE, ratedCircuit, unrated } from "./readings";
import { buildOut, circuitDraws, circuitUncounted, isShort, problemsOf, type DrawSlice, type Problem } from "./model";
import { overLine, pctW, storageRow, supplyBlock, supplyScale, type Supply } from "./supply";
import { circuitMap, factoryMap, noMap, problemMap } from "./lists";
import { headroomFigs, missing, mountPage, supplyOf } from "./page";
import { clearTips, tipAttrs, wireTips } from "./seg-tip";

import type { CircuitRow, FactoryHealthRow, GeneratorCandidate } from "../../api/shapes";

const REST = "outside named factories";
const NUMBER_TIP = "circuit numbers follow size and can change with the next save";
const GENERATORS_PATH = "/api/power/generators";

type Scale = (v: number) => number;

interface Ctx {
  row: CircuitRow;
  supply: Supply;
  draws: DrawSlice[];
  health: FactoryHealthRow[];
  scale: Scale;
}

function healthOf(c: Ctx, name: string | null): FactoryHealthRow | undefined {
  return name === null ? undefined : c.health.find((r) => r.name === name);
}

function factoryHref(name: string): string {
  return hashFor(factoryDash(name, ""));
}

function headChip(row: CircuitRow): Safe {
  if (circuitDark(row)) return chip(WORDS.noGenerator, "bad");
  if (row.starved.length) return chip(counted(row.starved.length, WORDS.starvedGenerator), "bad");
  if (unrated(ratedCircuit(row))) return chip("output unknown", "muted", "game data cannot rate " + row.unmodellable.join(", "));
  return html``;
}

function headNote(row: CircuitRow): string {
  const factories = row.factory_count ? counted(row.factory_count, "factory", "factories") + " · " : "";
  const paused = row.ledger.paused ? ", " + count(row.ledger.paused) + " " + WORDS.paused : "";
  return factories + counted(row.consumers, "consumer") + paused + " · " + counted(row.poles, "pole or tower", WORDS.polesAndTowers);
}

/* ------------------------------------------------------------------ the segment popover */

function share(n: number, total: number): string {
  return total > 0 ? Math.round((n / total) * 100) + "%" : NONE;
}

function tipRow(key: string, value: Safe | string, unit: string, tail: Safe | string): Safe {
  return html`<span class="k">${key}</span><b>${value}</b><span class="u">${unit}</span><span>${tail}</span>`;
}

function drawTip(c: Ctx, d: DrawSlice): Safe {
  const of = html` <span class="u">of circuit</span>`;
  const led = c.row.ledger;
  let rows = html`${tipRow(WORDS.consumption, count(Math.round(d.ms)), "MW", html`${share(d.ms, led.measured_draw_mw)}${of}`)}${tipRow(WORDS.maxConsumption, count(Math.round(d.np)), "MW", html`${share(d.np, led.draw_mw)}${of}`)}`;
  const health = healthOf(c, d.name);
  if (health) {
    const paused = health.states.find((s) => s.state === "paused")?.count ?? 0;
    const unit = (n: number) => (n === 1 ? "machine" : "machines");
    const need = health.actionable ? html`<span class="bad-t">${health.actionable}</span>` : "0";
    rows = html`${rows}<span class="sep"></span>${tipRow(WORDS.paused, String(paused), unit(paused), "")}${tipRow(WORDS.needAction, need, unit(health.actionable), "")}`;
  }
  return html`<div class="nm">${d.name ?? REST}</div><div class="g">${rows}</div>`;
}

function whichTip(candidates: GeneratorCandidate[]): Safe {
  const rows = candidates.map((g) => html`<span class="k">${g.name}</span><b>${count(g.mw)}</b><span class="u">MW</span><span title="${g.why}">${g.verdict}</span>`);
  return html`<div class="nm">which generator</div><div class="g">${rows}</div>`;
}

/* ------------------------------------------------------------------ supply and demand */

function drawSegs(c: Ctx, key: "np" | "ms", p: Scale): Safe {
  const cls = key === "np" ? "sg da" : "sg dn";
  const segs = c.draws.map(function (d, i) {
    if (!d[key]) return "";
    const width = p(d[key]);
    const name = d.name ?? REST;
    const at = html`${tipAttrs(key + ":" + i, drawTip(c, d))} aria-label="${name}" style="width:${pctW(width)}"`;
    if (d.name === null) return html`<span class="${cls}" tabindex="0"${at}><i></i></span>`;
    const health = healthOf(c, d.name);
    const ic = health && factoryIcon(health) ? icon(factoryIcon(health), name) : "";
    const label = width >= 14 ? html`<span class="lb">${ic}<span class="t">${name}</span></span>` : "";
    return html`<a class="${cls}" href="${factoryHref(d.name)}"${at}><i></i>${label}</a>`;
  });
  const total = key === "np" ? c.row.ledger.draw_mw : c.row.ledger.measured_draw_mw;
  return html`${segs}${overLine(c.supply, total, key === "ms", p)}`;
}

function supplyCard(c: Ctx): Safe {
  const rated = ratedCircuit(c.row);
  const figs = circuitDark(c.row) ? headroomFigs(rated, { value: NONE, bad: false, why: WORDS.noGenerator }) : headroomFigs(rated);
  const split = { np: (p: Scale) => drawSegs(c, "np", p), ms: (p: Scale) => drawSegs(c, "ms", p) };
  return html`<section class="card">${blockHead("supply and demand", figs)}${supplyBlock(c.supply, circuitUncounted(c.row), split, storageRow(c.row.ledger))}</section>`;
}

/* ------------------------------------------------------------------ do next */

function starvedMw(row: CircuitRow, p: Problem): number {
  const mine = new Set(p.refs.map((r) => r.instance));
  return row.starved.filter((s) => mine.has(s.instance)).reduce((sum, s) => sum + s.mw, 0);
}

function problemRow(c: Ctx, p: Problem, i: number): Safe {
  const n = p.refs.length;
  const starved = p.kind === WORDS.starvedGenerator;
  const name = starved ? "feed " + p.building : "wire " + p.building + " to a generator";
  const unit = starved ? (n === 1 ? "generator" : "generators") : n === 1 ? "machine" : "machines";
  const ctx = [count(n) + " " + unit, p.cause, p.where.where].filter(Boolean).join(" · ");
  const metric = starved ? html`<div class="metric"><b>+${count(Math.round(starvedMw(c.row, p)))}</b><span>MW</span></div>` : html`<div class="metric"><b>${count(n)}</b><span>${unit}</span></div>`;
  return entry({ tone: "bad" }, html`${icon(p.icon, p.building)}<div class="id"><span class="name">${name}</span><div class="ctx">${ctx}</div></div><div class="chips">${chip(p.kind, "bad")}</div>${metric}${acts(problemMap(p, "do:" + i))}`);
}

function buildRow(c: Ctx): Safe | null {
  if (!isShort(c.row)) return null;
  const slot = read<GeneratorCandidate[]>(GENERATORS_PATH);
  if (!slot.data) return null;
  const out = buildOut(c.row, slot.data);
  if (!out) return null;
  const g = out.pick;
  const what = "build " + count(out.n) + " " + g.name;
  const ctx = mw(g.mw) + " each" + (c.row.starved.length ? " · after feeding" : "");
  const passes = (out.now ? WORDS.consumption : WORDS.maxConsumption) + " passes rated generation";
  return entry(
    { tone: "bad" },
    html`${icon(g.icon, g.name)}<div class="id"><span class="name" tabindex="0"${tipAttrs("which", whichTip(slot.data))}>${what}</span><div class="ctx">${ctx}</div></div><div class="chips">${chip("short " + mw(out.short), "bad", passes)}</div><div class="metric"><b>+${count(out.n * g.mw)}</b><span>MW</span></div>${acts(noMap())}`
  );
}

function doNext(c: Ctx): Safe {
  const data = vitals().circuits!;
  const kinds = new Set<string>([WORDS.starvedGenerator, WORDS.noGenerator]);
  const problems = problemsOf(data, c.row.index).filter((p) => kinds.has(p.kind));
  const rows = problems.map((p, i) => problemRow(c, p, i));
  const build = buildRow(c);
  if (build) rows.push(build);
  return rows.length ? html`<section>${dashH("do next", rows.length)}${list("cdo", "stacked", rows)}</section>` : html``;
}

/* ------------------------------------------------------------------ the two columns */

function generatorList(c: Ctx): Safe {
  const rows = c.row.generators.map(function (g) {
    const starved = g.starved_mw > 0 ? chip(mw(g.starved_mw) + " starved", "bad") : "";
    const ctx = html`${starved} ${counted(g.count, "generator")}`;
    return entry({}, html`${icon(g.icon, g.name)}<div class="id"><span class="name" title="${g.name}">${g.name}</span><div class="ctx">${ctx}</div></div><div class="ubar" title="${mw(g.mw)}"><i class="so" style="width:${pctW(c.scale(g.mw))}"></i></div><div class="metric"><b>${count(Math.round(g.mw))}</b><span>MW</span></div>`);
  });
  return html`<section>${dashH("generators", rows.length)}${rows.length ? list("cgen", "stacked", rows) : emptyLine(WORDS.noGenerator)}</section>`;
}

function drawList(c: Ctx): Safe {
  const rows = c.draws.map(function (d) {
    const health = healthOf(c, d.name);
    const lead = health ? factoryLead(health) : iconLead(null, "");
    const name = d.name === null ? html`<span class="name">${REST}</span>` : html`<a class="name" href="${factoryHref(d.name)}" title="${d.name}">${d.name}</a>`;
    const title = mw(d.ms) + " " + WORDS.consumption + " · " + mw(d.np) + " " + WORDS.maxConsumption;
    const bar = html`<div class="ubar" title="${title}"><i class="np" style="width:${pctW(c.scale(d.np))}"></i><i class="ms" style="width:${pctW(c.scale(d.ms))}"></i></div>`;
    const metric = html`<div class="metric"><b>${count(Math.round(d.ms))}</b><span>MW</span><span class="line2">${count(Math.round(d.np))} ${WORDS.maxConsumptionShort}</span></div>`;
    const map = d.name === null ? noMap() : factoryMap(d.name, health);
    return entry({ href: d.name === null ? undefined : factoryHref(d.name) }, html`${lead}<div class="id">${name}</div>${bar}${metric}${acts(map)}`);
  });
  return html`<section>${dashH("draw by factory", rows.length)}${rows.length ? list("cdraw", "stacked", rows) : emptyLine("nothing draws")}</section>`;
}

export function renderCircuit(body: HTMLElement, subject: string): void {
  if (missing(body)) return;
  clearTips();
  const data = vitals().circuits!;
  const row = data.circuits[+subject - 1];
  const crumbs: [string, string][] = [["Power", hashFor("power")]];
  if (!row) {
    mountPage(body, html`${pageHead({ crumbs, name: "circuit " + subject })}${emptyLine("no circuit " + subject + " in this world", "circuit numbers can change between saves; pick one on Power")}`);
    return;
  }
  const supply = supplyOf(row.generators, row.ledger, ratedCircuit(row));
  const health = vitals().health;
  const c: Ctx = { row, supply, draws: circuitDraws(row), health: health ? health.factories : [], scale: supplyScale(supply) };
  const head = pageHead({ crumbs, name: "circuit " + (row.index + 1), nameTip: NUMBER_TIP, chips: headChip(row), acts: circuitMap(row), note: headNote(row) });
  const cols = html`<div class="ccols">${generatorList(c)}${drawList(c)}</div>`;
  mountPage(body, html`${head}<div class="cpage">${supplyCard(c)}${doNext(c)}${cols}</div>`);
  wireTips();
}
