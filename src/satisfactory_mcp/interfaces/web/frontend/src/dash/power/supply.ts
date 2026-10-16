/* The supply-and-demand block (three bars on one scale: generation by generator type, if all
 * run, drawing now) and a circuit's utilisation bar. docs/frontend_kit.md, "Power". */

import { html, type Safe } from "../../kit/html";
import { chip } from "../../kit/ui/marks";
import { icon, na } from "../../kit/ui/text";
import { count, mw } from "../../kit/format";
import { WORDS } from "../../kit/words";

import type { CircuitRow, CircuitsResponse } from "../../api/shapes";
import type { Uncounted } from "./model";

type GeneratorGroup = CircuitsResponse["generators"][number];

export interface Supply {
  gens: GeneratorGroup[];
  gen: number;
  /** Nameplate draw: every machine flat out at its saved clock. */
  np: number;
  /** Measured draw. */
  ms: number;
  /** Generation unknown (a generator type game data cannot rate): no line, no overdraw. */
  unknown?: boolean;
}

function pctW(value: number): string {
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

interface Row {
  key: string;
  tip: string;
  track: Safe;
  value: number | null;
  over: boolean;
  gen?: boolean;
}

export function supplyBlock(s: Supply, uncounted: Uncounted[]): Safe {
  const known = !s.unknown;
  const top = Math.max(s.gen, s.np, s.ms, 1);
  const p = (v: number) => (v / top) * 100;
  const segs = s.gens.map((g) => segment(g, p(g.mw)));
  const draw = function (v: number, cls: string): Safe {
    if (!known || v <= s.gen) return html`<i class="${cls}" style="width:${pctW(p(v))}"></i>`;
    return html`<i class="${cls}" style="width:${pctW(p(s.gen))}"></i><i class="${cls} ov" style="left:${pctW(p(s.gen))};width:${pctW(p(v - s.gen))}"></i>`;
  };
  const rows: Row[] = [
    { key: WORDS.generation, tip: "", track: segs.length ? html`${segs}` : html`<span class="none">${WORDS.noGenerator}</span>`, value: known && s.gen > 0 ? s.gen : null, over: false, gen: true },
    { key: WORDS.ifAllRun, tip: WORDS.ifAllRunTip, track: draw(s.np, "np"), value: s.np, over: known && s.np > s.gen },
    { key: WORDS.drawingNow, tip: "", track: draw(s.ms, "ms"), value: s.ms, over: known && s.ms > s.gen },
  ];
  const cells = rows.map(function (r, i) {
    const at = "grid-row:" + (i + 1);
    const value = r.value === null ? na() : html`${number(r.value)} <span>MW</span>`;
    return html`<span class="k" style="${at}"${r.tip ? html` title="${r.tip}"` : ""}>${r.key}</span><div class="${r.gen ? "tr g" : "tr"}" style="${at}">${r.track}</div><span class="${r.over ? "v bad-t" : "v"}" style="${at}">${value}</span>`;
  });
  const line = known && s.gen > 0 ? html`<span class="gl" style="grid-row:1 / ${rows.length + 1}"><i title="generation: the gap to it is the headroom" style="left:${pctW(p(s.gen))}"></i></span>` : "";
  const label = WORDS.generation + " " + mw(s.gen) + ", " + WORDS.ifAllRun + " " + mw(s.np) + ", " + WORDS.drawingNow + " " + mw(s.ms);
  const chips = uncounted.length ? html`<div class="nc"><span class="k">not counted</span>${uncounted.map((u) => chip(u[0], "muted", u[1]))}</div>` : "";
  return html`<div class="sd" role="group" aria-label="${label}">${cells}${line}</div>${chips}`;
}

/** 100% = the circuit's generation; `scale` is the shared overflow room (utilScale). */
export function utilBar(row: CircuitRow, scale: number, unknown: boolean): Safe {
  const gen = row.ledger.generation_mw;
  const tick = html`<i class="tick" style="left:${pctW(100 / scale)}"></i>`;
  if (gen <= 0 || unknown) return html`<div class="ubar" title="${unknown ? "generation not modelled" : WORDS.noGenerator}">${tick}</div>`;
  const p = (v: number) => (v / gen / scale) * 100;
  const part = function (v: number, cls: string): Safe {
    const base = html`<i class="${cls}" style="width:${pctW(p(Math.min(v, gen)))}"></i>`;
    return v > gen ? html`${base}<i class="${cls} ov" style="left:${pctW(p(gen))};width:${pctW(Math.min(p(v), 100) - p(gen))}"></i>` : base;
  };
  const title = WORDS.drawingNow + " " + Math.round((row.ledger.measured_draw_mw / gen) * 100) + "% · " + WORDS.ifAllRun + " " + Math.round((row.ledger.draw_mw / gen) * 100) + "% of generation";
  return html`<div class="ubar" title="${title}">${part(row.ledger.draw_mw, "np")}${part(row.ledger.measured_draw_mw, "ms")}${tick}</div>`;
}
