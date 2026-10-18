/* The supply-and-demand block (production capacity by generator type, maximum consumption and
 * power consumption on one scale) and a circuit's utilisation bar. docs/frontend_kit.md, "Power". */

import { html, type Safe } from "../../kit/html";
import { chip } from "../../kit/ui/marks";
import { icon, na } from "../../kit/ui/text";
import { count, mw } from "../../kit/format";
import { counted, WORDS } from "../../kit/words";

import type { CircuitRow, CircuitsResponse, Ledger } from "../../api/shapes";
import type { Uncounted } from "./model";

type GeneratorGroup = CircuitsResponse["generators"][number];

export interface Supply {
  gens: GeneratorGroup[];
  /** Rated generation: the length the capacity row draws, starved part hatched. */
  gen: number;
  /** Production capacity: rated less what starved generators cannot run. */
  cap: number;
  /** Maximum consumption: every machine flat out at its saved clock. */
  np: number;
  /** Power consumption, as measured. */
  ms: number;
  /** The part of `np` behind a power switch standing off, drawn hatched at its end. */
  off?: number;
  /** Capacity unknown (a generator type game data cannot rate): no line, no overdraw. */
  unknown?: boolean;
}

/** The two consumption rows drawn split, one segment per factory, instead of one fill. */
export interface SplitDraw {
  np: (p: (v: number) => number) => Safe;
  ms: (p: (v: number) => number) => Safe;
}

export function pctW(value: number): string {
  return Math.max(0, Math.min(100, value)).toFixed(2) + "%";
}

function number(value: number): string {
  return count(Math.round(value));
}

function segment(g: GeneratorGroup, width: number): Safe {
  const starved = Math.min(g.starved_mw, g.mw);
  const label = width >= 12 ? html`<span class="lb">${icon(g.icon, g.name)}${g.count}×</span>` : "";
  const title = g.count + " " + g.name + " · " + mw(g.mw) + (starved ? ", " + mw(starved) + " of it starved" : "");
  return html`<span class="sg" style="width:${pctW(width)}" title="${title}"><i class="so" style="flex:${g.mw - starved}"></i>${starved ? html`<i class="ha" style="flex:${starved}"></i>` : ""}${label}</span>`;
}

/** The scale every bar of one block shares: 0..100 of the largest figure. */
export function supplyScale(s: Supply): (v: number) => number {
  const top = Math.max(s.gen, s.np, s.ms, 1);
  return (v: number) => (v / top) * 100;
}

/** The part of a row past production capacity, tinted `--bad`, over split segments. */
export function overLine(s: Supply, total: number, measured: boolean, p: (v: number) => number): Safe {
  if (s.unknown || s.gen <= 0 || total <= s.cap) return html``;
  return html`<i class="${measured ? "ovl ms" : "ovl"}" style="left:${pctW(p(s.cap))};width:${pctW(p(total - s.cap))}"></i>`;
}

interface Row {
  key: string;
  tip: string;
  track: Safe;
  split: boolean;
  value: number | null;
  over: boolean;
}

export function supplyBlock(s: Supply, uncounted: Uncounted[], split?: SplitDraw, extra?: Safe): Safe {
  const known = !s.unknown;
  const p = supplyScale(s);
  const segs = s.gens.map((g) => segment(g, p(g.mw)));
  const draw = function (v: number, cls: string): Safe {
    if (!known || v <= s.cap) return html`<i class="${cls}" style="width:${pctW(p(v))}"></i>`;
    return html`<i class="${cls}" style="width:${pctW(p(s.cap))}"></i><i class="${cls} ov" style="left:${pctW(p(s.cap))};width:${pctW(p(v - s.cap))}"></i>`;
  };
  const offMw = s.off ?? 0;
  const off = offMw > 0 ? html`<i class="np off" style="left:${pctW(p(s.np - offMw))};width:${pctW(p(offMw))}" title="${mw(offMw) + " " + WORDS.behindSwitch}"></i>` : "";
  const rows: Row[] = [
    { key: WORDS.capacity, tip: "", track: segs.length ? html`${segs}` : html`<span class="none">${WORDS.noGenerator}</span>`, split: true, value: known && s.gen > 0 ? s.cap : null, over: false },
    { key: WORDS.maxConsumption, tip: WORDS.maxConsumptionTip, track: split ? split.np(p) : html`${draw(s.np - offMw, "np")}${off}`, split: !!split, value: s.np, over: known && s.np > s.cap },
    { key: WORDS.consumption, tip: WORDS.consumptionTip, track: split ? split.ms(p) : draw(s.ms, "ms"), split: !!split, value: s.ms, over: known && s.ms > s.cap },
  ];
  const cells = rows.map(function (r, i) {
    const at = "grid-row:" + (i + 1);
    const value = r.value === null ? na() : html`${number(r.value)} <span>MW</span>`;
    return html`<span class="k" style="${at}"${r.tip ? html` title="${r.tip}"` : ""}>${r.key}</span><div class="${r.split ? "tr g" : "tr"}" style="${at}">${r.track}</div><span class="${r.over ? "v bad-t" : "v"}" style="${at}">${value}</span>`;
  });
  const line = known && s.gen > 0 ? html`<span class="gl" style="grid-row:1 / ${rows.length + 1}"><i title="${WORDS.capacity}: the gap to it is the headroom" style="left:${pctW(p(s.cap))}"></i></span>` : "";
  const label = WORDS.capacity + " " + mw(s.cap) + ", " + WORDS.maxConsumption + " " + mw(s.np) + ", " + WORDS.consumption + " " + mw(s.ms);
  const chips = uncounted.length ? html`<div class="nc"><span class="k">not counted</span>${uncounted.map((u) => chip(u[0], "muted", u[1]))}</div>` : "";
  return html`<div class="sd" role="group" aria-label="${label}">${cells}${line}${extra}</div>${chips}`;
}

/** The supply block's three MW rows; a row passed in as `extra` goes below them, where the
 * capacity line does not reach. */
const SUPPLY_ROWS = 3;

/** "power storage": stored of capacity in MWh on its own scale, or nothing without batteries. */
export function storageRow(led: Ledger): Safe {
  if (!led.batteries) return html``;
  const at = "grid-row:" + (SUPPLY_ROWS + 1);
  const cap = led.storage_capacity_mwh;
  const tip = number(led.storage_mwh) + " of " + number(cap) + " MWh in " + counted(led.batteries, "battery", "batteries") + "; not in headroom";
  const fill = cap > 0 ? (led.storage_mwh / cap) * 100 : 0;
  return html`<span class="k" style="${at}">${WORDS.powerStorage}</span><div class="tr g" style="${at}" title="${tip}"><i class="st" style="width:${pctW(fill)}"></i><span class="cap">of ${number(cap)} MWh</span></div><span class="v" style="${at}">${number(led.storage_mwh)} <span>MWh</span></span>`;
}

/** 100% = the circuit's production capacity; `scale` is the shared overflow room (utilScale). */
export function utilBar(row: CircuitRow, scale: number, unknown: boolean): Safe {
  const cap = row.ledger.capacity_mw;
  const tick = html`<i class="tick" style="left:${pctW(100 / scale)}"></i>`;
  if (cap <= 0 || unknown) {
    const why = unknown ? WORDS.capacity + " not modelled" : row.ledger.generation_mw > 0 ? "no " + WORDS.capacity + ": every generator starved" : WORDS.noGenerator;
    return html`<div class="ubar" title="${why}">${tick}</div>`;
  }
  const p = (v: number) => (v / cap / scale) * 100;
  const part = function (v: number, cls: string): Safe {
    const base = html`<i class="${cls}" style="width:${pctW(p(Math.min(v, cap)))}"></i>`;
    return v > cap ? html`${base}<i class="${cls} ov" style="left:${pctW(p(cap))};width:${pctW(Math.min(p(v), 100) - p(cap))}"></i>` : base;
  };
  const title = WORDS.consumption + " " + Math.round((row.ledger.measured_draw_mw / cap) * 100) + "% · " + WORDS.maxConsumption + " " + Math.round((row.ledger.draw_mw / cap) * 100) + "% of " + WORDS.capacity;
  return html`<div class="ubar" title="${title}">${part(row.ledger.draw_mw, "np")}${part(row.ledger.measured_draw_mw, "ms")}${tick}</div>`;
}
