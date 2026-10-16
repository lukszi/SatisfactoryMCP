/* The target stop: the gate as a checklist of owed parts, and "what to do next" as a path. */

import { formatNumber } from "../../kit/format";
import { html, type Safe } from "../../kit/html";
import { chevron, entry, expandBody, list } from "../../kit/ui/entry";
import { bar, chip } from "../../kit/ui/marks";
import { emptyLine, fig, icon, na } from "../../kit/ui/text";
import { hashFor } from "../../map/map";
import { factoryDash } from "../factories/address";
import { coverTime, planHref, rate1, shortCount, stepWords } from "./model";

import type { GateMaker, GatePart, GateView, NextStep } from "../../api/shapes";

export interface SectionParts {
  n?: number;
  figs?: Safe;
  tools?: Safe;
  tip?: string;
  body: Safe;
}

const TIME_TIP = "time to cover what is short at the nameplate rate made now; measured output can be lower";

/** Who makes it: named factories with their rates, the rest of the world as "elsewhere". */
function madeAt(makers: GateMaker[], world: number): Safe {
  const named = makers.slice(0, 2).map((m) => html`<a href="${hashFor(factoryDash(m.factory, "flows"))}">${m.factory}</a> <em>${rate1(m.per_min)}</em>`);
  const listed = makers.reduce((sum, m) => sum + m.per_min, 0);
  const rest = world - listed > 0.01 ? html`${named.length ? ", " : ""}elsewhere <em>${rate1(world - listed)}</em>` : "";
  if (!named.length && !rest) return html`<span class="muted">not made anywhere</span>`;
  return html`${named.map((x, i) => html`${i ? ", " : ""}${x}`)}${makers.length > 2 ? html`, +${makers.length - 2}` : ""}${rest}`;
}

function cells(who: Safe, need: string, time: string | null, machines: Safe | string): Safe {
  const labels = ["made at", "per minute to deliver in time", "time to cover", "machines in the chain"];
  return html`${[who, html`<em>${need}</em>`, time === null ? na() : time === "covered" ? html`<span class="muted">covered</span>` : time, machines].map(
    (v, i) => html`<div class="${i ? "facts" : "facts start"}" title="${labels[i]!}">${v}</div>`
  )}`;
}

function colHead(h: number): Safe {
  const cols: [string, string][] = [
    ["made at", "factories with a surplus of it, nameplate /min at the saved clocks"],
    ["/min", "the rate that delivers what is short in " + h + " h"],
    ["time", TIME_TIP],
    ["machines", "the whole chain at that rate: the save's recipes and the default ones, nameplate"],
  ];
  return html`<div class="entry colh" aria-hidden="true"><span></span><span></span><span class="r">held</span>${cols.map((c, i) => html`<span class="${i ? "r" : ""}" title="${c[1]}">${c[0]}</span>`)}<span></span></div>`;
}

function partRow(p: GatePart, open: boolean): Safe {
  const key = "gate-" + p.item;
  const done = p.short <= 0;
  const lock = p.recipe_unlocked ? "" : chip("locked", "muted", p.unlock ? "unlocked by " + p.unlock + (p.unlock_system ? " (" + p.unlock_system + ")" : "") : "no known unlock");
  const subs = p.inputs.map(function (x) {
    const need = Math.max(0, x.needed - x.held);
    return html`<div class="sub"><span class="nl">${icon(x.item, x.name)}<span class="line1">${x.name}</span></span><span class="r"><b>${formatNumber(x.held)}</b> of ${formatNumber(x.needed)}</span>${cells(madeAt(x.makers, x.made_per_min), rate1(x.per_unit * p.need_per_min), coverTime(need, x.made_per_min), "")}</div>`;
  });
  const box = html`<span class="${done ? "chk ok" : "chk"}" aria-hidden="true">${done ? "✓" : ""}</span>`;
  const machines = p.machines === null ? (done ? "" : na()) : html`<em title="${formatNumber(p.mw || 0) + " MW nameplate"}">≈${formatNumber(p.machines)}</em>`;
  return entry(
    { key, toggle: true, open, tone: "" },
    html`<span class="lead-chk">${box}${icon(p.item, p.name)}</span><div class="id"><div class="nl"><span class="line1 name">${p.name}</span>${lock}</div></div>
    <div class="metric"><b>${formatNumber(p.held)}</b><span>of ${formatNumber(p.owed)}</span>${bar(p.owed ? (p.held / p.owed) * 100 : 100)}</div>
    ${cells(madeAt(p.makers, p.made_per_min), rate1(p.need_per_min), coverTime(p.short, p.made_per_min), machines)}
    <div class="acts">${chevron(key, p.name + " inputs", open)}</div>${expandBody(subs.length ? subs : html`<p class="note">no recipe input</p>`)}`
  );
}

export function gateSection(view: GateView | null, pendingLine: Safe | null, h: number, openKeys: Set<string>): SectionParts {
  if (!view) return { body: pendingLine || emptyLine("the gate could not be read") };
  if (!view.parts.length) return { body: emptyLine(view.phase === null ? "no target phase in this save" : "nothing owed to phase " + view.phase, "the space elevator takes no parts now") };
  const short = shortCount(view.parts);
  const locked = view.parts.filter((p) => !p.recipe_unlocked).length;
  const rows = view.parts.map((p) => partRow(p, openKeys.has("gate-" + p.item)));
  return {
    figs: html`${fig(short + " of " + view.parts.length, "parts short", short ? undefined : "ok")}${fig(locked, "recipes locked")}`,
    tip: "held from stock · rates nameplate at the saved clocks · machines: the whole chain at the rate for " + h + " h",
    body: list("gate", "stacked", html`${colHead(h)}${rows}`),
  };
}

function stepRow(s: NextStep, i: number, h: number): Safe {
  const w = stepWords(s);
  const href = s.kind === "unlock" ? hashFor("progress/reference") : hashFor(planHref(s, h));
  return entry(
    { href, title: w.why },
    html`<span class="lead-rank"><span class="rk">${i + 1}</span>${s.item ? icon(s.item, s.name) : html`<i class="mono" aria-hidden="true"></i>`}</span><div class="id"><div class="nl"><a class="line1 name" href="${href}">${w.what}</a>${w.tag ? chip(w.tag, "muted") : ""}</div></div><span class="go">${w.go} →</span>`
  );
}

export function nextSection(view: GateView | null, pendingLine: Safe | null): SectionParts {
  if (!view) return { body: pendingLine || emptyLine("the next steps could not be read") };
  if (!view.steps.length) return { n: 0, body: emptyLine("nothing to do for this phase", "every owed part has an unlocked recipe and a line fast enough") };
  return {
    n: view.steps.length,
    tip: "prerequisites first, then work that can start now, then what opens most of the bill",
    body: list("next path", "stacked", view.steps.map((s, i) => stepRow(s, i, view.horizon_h))),
  };
}
