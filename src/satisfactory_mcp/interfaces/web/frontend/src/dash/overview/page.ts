/* The Overview page, `dash=overview`: four glanceable blocks (factories, power, do next,
 * progress), each clicking through to its page. Desktop: two independent column stacks;
 * phones: one column, do next first. docs/frontend_kit.md, "Overview". */

import "../factories/factories.css";
import "../power/power.css";
import "./overview.css";
import { loadOne } from "../../app/load";
import { vitals } from "../../app/vitals";
import { state } from "../../app/state";
import { count, pct } from "../../kit/format";
import { html, mount, type Safe } from "../../kit/html";
import { btn } from "../../kit/ui/controls";
import { entry, list, nameLine } from "../../kit/ui/entry";
import { bar, mixBar } from "../../kit/ui/marks";
import { blockHead, emptyLine, fig, icon } from "../../kit/ui/text";
import { counted, WORDS } from "../../kit/words";
import { hashFor } from "../../map/map";
import { requestRender } from "../actions";
import { factoryDash } from "../factories/address";
import { factoryLead } from "../factories/icon-choice";
import { mixParts } from "../factories/model";
import { pending, q, read, wireRetry } from "../factories/data";
import { actionTone, stateTone } from "../machine-states";
import { CIRCUITS_TIP, headroomFigs } from "../power/page";
import { ratedCircuit, ratedWorld, readHeadroomNow, unrated } from "../power/readings";
import { utilBar } from "../power/supply";
import { utilScale, worstFirst } from "../power/model";
import { DEFAULT_HORIZON, planHref, stepWords } from "../progress/model";
import { loadList, planIndex } from "../planner/plan-index";
import { onBench } from "../planner/state";
import { cellOrder, gateTally, machineTotals, moreDash, plansTally, SHOWN, todoOf, type Tally, type Todo } from "./model";

import type { ApiUrl } from "../../api/client";
import type { FactoryHealthRow, GateView } from "../../api/shapes";

const PHONE = window.matchMedia("(max-width: 600px)");

/* The world and save the plan list was last asked for in, so a render does not ask again. */
let askedPlans = "";

function shown(): boolean {
  return state.dash === "overview";
}

PHONE.addEventListener("change", () => shown() && requestRender());
onBench(() => shown() && requestRender());

function card(cls: string, inner: Safe): Safe {
  return html`<section class="${"card ov-b " + cls}">${inner}</section>`;
}

/* The loading or error line of a vitals read, with its retry. */
function waiting(what: string, failed: string, path: string): Safe {
  if (failed) return html`<div class="fpend">${emptyLine(what + " could not be read", failed)}${btn("try again", { data: { "ov-retry": path } })}</div>`;
  return html`<p class="empty fpend is-busy" aria-busy="true"><span class="spinner" aria-hidden="true"></span> loading ${what}…</p>`;
}

/* ------------------------------------------------------------------ factories */

function cell(row: FactoryHealthRow): Safe {
  const tone = actionTone(row.states);
  const big = row.actionable ? html`<span class="${"ov-n " + tone}"><b>${count(row.actionable)}</b><span>${WORDS.needAction}</span></span>` : html`<span class="ov-n"><b>${pct(row.uptime)}</b><span>up</span></span>`;
  const mix = mixBar(mixParts(row.states, (s) => stateTone(s)), "machines by state: " + row.states.map((s) => s.count + " " + s.state).join(", "));
  return html`<a class="${"ov-cell " + tone}" href="${hashFor(factoryDash(row.name, ""))}" title="${row.name}"><span class="nm">${factoryLead(row)}<span>${row.name}</span></span>${big}${mix}</a>`;
}

function factoriesBlock(): Safe {
  const v = vitals();
  const href = hashFor("factories");
  if (!v.health) return card("ov-fac", html`${blockHead("factories", undefined, href)}${waiting("factory health", v.healthError, "/api/factories/health")}`);
  const rows = v.health.factories;
  const t = machineTotals(rows);
  const figs = html`${fig(count(t.machines), "machines")}${fig(count(t.need), WORDS.needAction, t.need ? "bad" : undefined)}`;
  const body = rows.length ? html`<div class="ov-cells">${cellOrder(rows).map(cell)}</div>` : emptyLine("no factories named yet", html`<a href="${href}">detect them on Factories</a>`);
  return card("ov-fac", html`${blockHead("factories", rows.length ? figs : undefined, href)}${body}`);
}

/* ------------------------------------------------------------------ power */

function more(n: number, dash: string): Safe | "" {
  return n > 0 ? html`<a class="ov-more" href="${hashFor(dash)}">+${n} more</a>` : "";
}

function powerBlock(): Safe {
  const v = vitals();
  const href = hashFor("power");
  if (!v.circuits) return card("ov-pow", html`${blockHead("power", undefined, href)}${waiting("power circuits", v.circuitsError, "/api/power/circuits")}`);
  const data = v.circuits;
  const scale = utilScale(data.circuits);
  const rows = worstFirst(data.circuits)
    .slice(0, SHOWN)
    .map(function (row) {
      const rated = ratedCircuit(row);
      const now = readHeadroomNow(rated);
      return html`<a class="ov-row" href="${hashFor("power/" + (row.index + 1))}"><span class="k">circuit ${row.index + 1}</span>${utilBar(row, scale, unrated(rated))}<b class="${now.bad ? "bad-t" : ""}" title="${now.why || WORDS.headroomNow}">${now.value}</b></a>`;
    });
  const body = data.circuits.length ? html`<div class="ov-rows" title="${CIRCUITS_TIP}">${rows}</div>${more(data.circuits.length - SHOWN, "power")}` : emptyLine("no circuits", "nothing stands on a wire yet");
  return card("ov-pow", html`${blockHead("power", headroomFigs(ratedWorld(data)), href)}${body}`);
}

/* ------------------------------------------------------------------ do next */

function todoRow(t: Todo): Safe {
  const where = t.whereDash ? html`<a href="${hashFor(t.whereDash)}" title="${t.where}">${t.where}</a>` : t.where;
  return entry(
    { tone: t.tone, href: hashFor(t.dash) },
    html`${icon(t.icon, t.sub)}<div class="id">${nameLine(html`<span class="name">${t.what}</span>`, t.sub)}</div><div class="ctx">${where}</div><div class="${"metric " + t.tone}"><b>${count(t.n)}</b></div>`
  );
}

function doBlock(): Safe {
  const v = vitals();
  if (!v.health && !v.circuits) return card("ov-do", html`${blockHead("do next")}${waiting("factory health and power", v.healthError && v.circuitsError, "/api/factories/health")}`);
  const all = todoOf(v.circuits, v.health ? v.health.factories : null);
  const body = all.length ? list("do", "stacked", all.slice(0, SHOWN).map(todoRow)) : emptyLine("nothing to do", "every machine runs and every circuit has power");
  return card("ov-do", html`${blockHead("do next", all.length ? fig(count(all.length), all.length === 1 ? "problem" : "problems", "bad") : undefined)}${body}${more(all.length - SHOWN, moreDash(all.slice(SHOWN)))}`);
}

/* ------------------------------------------------------------------ progress */

function tallyRow(href: string, k: string, t: Tally, unit?: string): Safe {
  return html`<a class="ov-row" href="${href}"><span class="k" title="${k}">${k}</span>${bar(t.of ? (t.done / t.of) * 100 : 100)}<b>${count(t.done)} <span class="dim">/ ${count(t.of)}${unit ? " " + unit : ""}</span></b></a>`;
}

function gateRows(view: GateView): Safe {
  const step = view.steps[0];
  const gate = view.parts.length
    ? tallyRow(hashFor("progress/gate"), "phase " + view.phase + " parts", gateTally(view))
    : html`<a class="ov-row" href="${hashFor("progress/gate")}"><span class="k">phase</span><span class="nx">${view.phase === null ? "no target phase in this save" : "nothing owed to phase " + view.phase}</span></a>`;
  if (!step) return gate;
  const w = stepWords(step);
  const href = step.kind === "unlock" ? hashFor("progress/reference") : hashFor(planHref(step, view.horizon_h));
  return html`${gate}<a class="ov-row ov-next" href="${href}" title="${w.why}"><span class="k">next</span><span class="nx">${w.what}</span></a>`;
}

function plansRow(): Safe {
  const data = planIndex.data;
  if (!data || planIndex.world !== state.world) {
    const ask = state.world + "|" + state.epoch;
    if (state.world && askedPlans !== ask) {
      askedPlans = ask;
      loadList();
    }
    return html`<p class="note">${planIndex.error ? "the plans did not load" : "loading plans…"}</p>`;
  }
  const rows = data.index;
  const k = counted(rows.length, "plan");
  const t = plansTally(rows, planIndex.built);
  if (!t.of) return html`<a class="ov-row" href="${hashFor("planner")}"><span class="k">${k}</span><span class="nx dim">${rows.length ? "none placed yet" : "make one in the Planner"}</span></a>`;
  return tallyRow(hashFor("planner"), k, t, "built");
}

function progressBlock(): Safe {
  const path = q("/api/progress/gate", { horizon_h: DEFAULT_HORIZON });
  const gate = read<GateView>(path);
  const gatePart = gate.data ? gateRows(gate.data) : pending(gate, "the gate", path) || emptyLine("the gate could not be read");
  return card("ov-prog", html`${blockHead("progress", undefined, hashFor("progress"))}<div class="ov-rows">${gatePart}${plansRow()}</div>`);
}

/* ------------------------------------------------------------------ page */

function wire(root: HTMLElement): void {
  wireRetry(root);
  root.addEventListener("click", function (event) {
    const target = event.target as Element;
    const retry = target.closest<HTMLElement>("[data-ov-retry]");
    if (retry) {
      loadOne(retry.dataset.ovRetry as ApiUrl);
      return;
    }
    if (target.closest("a, button")) return;
    const row = target.closest<HTMLElement>(".entry[data-href]");
    if (row && root.contains(row)) location.hash = row.dataset.href!;
  });
}

export function renderOverview(body: HTMLElement): void {
  const blocks = { fac: factoriesBlock(), pow: powerBlock(), todo: doBlock(), prog: progressBlock() };
  const markup = PHONE.matches
    ? html`<div class="ov one">${blocks.todo}${blocks.fac}${blocks.pow}${blocks.prog}</div>`
    : html`<div class="ov"><div class="ov-col">${blocks.fac}${blocks.prog}</div><div class="ov-col">${blocks.pow}${blocks.todo}</div></div>`;
  const root = document.createElement("div");
  mount(root, html`<h1 class="dk-hidden">Overview</h1>${markup}`);
  wire(root);
  body.appendChild(root);
}
