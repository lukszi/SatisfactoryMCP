/* The machines tab: the result band (the whole plan as one flow, net MW, the targets toggle),
 * the alternates drawer, and the build rows grouped by step; `v<n>` shows a version read-only
 * under its strip. docs/frontend_kit.md, "Plan page". */

import { openAsk, openAsksAbout } from "../../../chat/asks";
import { createPin, processPinsByRecipe } from "../../../chat/pins";
import { go } from "../../../app/nav";
import { busyButton } from "../../../kit/busy";
import { html, type Safe } from "../../../kit/html";
import { openMenu, type MenuItem } from "../../../kit/overlay/menu";
import { sortSelect } from "../../../kit/ui/controls";
import { actSlots, entry, group, list } from "../../../kit/ui/entry";
import { chip, tagChip } from "../../../kit/ui/marks";
import { loadingLine } from "../../../kit/ui/states";
import { dashH, emptyLine, fig, note, toolrow } from "../../../kit/ui/text";
import { WORDS } from "../../../kit/words";
import { hashFor } from "../../../map/map";
import { describeArgs } from "../chat-request";
import { nodeForPick, planGraphOf } from "../graph/model";
import { optionWords } from "../power";
import { showAlternates } from "../reads";
import { altDash, planDash } from "../routes";
import { bench, changed, pendingFocus } from "../state";
import { applyOps, banOps, restoreRev, undoRev, writesSettled } from "../writes";
import { drawer } from "./alternates";
import { BUILD_SORTS, clockWords, orderRows, type BuildSort } from "./model";
import { buildingIcon, lookupOf, rateFlow, ratePill, selectThing, sgn, view, type Lookup } from "./parts";
import { targetsForm } from "./targets";

import type { SolveResponse, SolveRow } from "../../../api/shapes";
import type { Op } from "../state";

function zone(cls: string, label: string, inner: Safe[]): Safe {
  return html`<div class="${cls}"><span class="k">${label}</span><div class="pills">${inner.length ? inner : html`<em class="na">–</em>`}</div></div>`;
}

function flowZones(data: SolveResponse, look: Lookup): Safe {
  const ins = data.inputs.slice().sort((a, b) => b.per_min - a.per_min).map((r) => ratePill(look, r.item, r.per_min));
  const machines = planGraphOf(data).buildings.map((b) => html`<span class="pill">${buildingIcon(look, b.building)}<b>×${b.count}</b><span class="nm">${b.building}</span></span>`);
  const outs = data.exports.map((r) => ratePill(look, r.item, r.per_min));
  return html`<div class="pres-f">${zone("pres-in", "in", ins)}<span class="arrow" aria-hidden="true">→</span>${zone("pres-m", "machines", machines)}<span class="arrow" aria-hidden="true">→</span>${zone("pres-out", "out", outs)}</div>`;
}

/** The band: "result · vN", net MW, the targets toggle; the cause when it does not solve. */
function band(data: SolveResponse, rev: number, live: boolean): Safe {
  const look = lookupOf(data);
  const solving = live && bench.solvingRev && bench.solvingRev !== rev ? html`<span class="note">solving v${bench.solvingRev}…</span>` : "";
  const open = !!view.targets[bench.key];
  const toggle = live ? html`<button type="button" class="btn" data-pm="targets" aria-pressed="${String(open)}" title="goal, exports, sources, recipe rules and power settings">targets</button>` : "";
  const net = data.mw_net === null ? "–" : sgn(data.mw_net);
  const undo = live && !data.feasible && bench.plan && bench.plan.rev > 1 && !bench.gone ? html` <button type="button" class="btn" data-pm="undo" title="undo the change that made this plan unsolvable, as a new version">undo v${bench.plan.rev}</button>` : "";
  const body = data.feasible ? flowZones(data, look) : html`<p class="tone-bad">not solvable: ${data.cause || data.headline}${undo}</p>`;
  return html`<section class="${solving ? "card pres stale" : "card pres"}" aria-label="result"><div class="bh">${dashH("result · v" + rev)}${data.feasible ? fig(net, "MW net") : ""}${solving}<span class="grow"></span>${toggle}</div>${body}</section>`;
}

function rowChips(row: SolveRow, live: boolean): Safe {
  const ref = row.recipe_id || row.recipe;
  const pin = live && row.recipe_id ? processPinsByRecipe(bench.key)[row.recipe_id] : undefined;
  const asks = live ? openAsksAbout(bench.key, "process", ref) : [];
  return html`${row.required ? tagChip("required") : ""}${live && bench.chatChangedRows[row.id] ? chip(WORDS.actorChat, "muted", "chat changed this process since the plan was opened") : ""}${pin ? chip(pin.id, "id", pin.text) : ""}${asks.map((a) => chip(a.id, "id", a.text))}`;
}

function mainItem(data: SolveResponse, row: SolveRow): string | null {
  const node = nodeForPick(planGraphOf(data), row.id);
  return node ? node.itemId : null;
}

function buildRow(data: SolveResponse, row: SolveRow, live: boolean): Safe {
  const look = lookupOf(data);
  const [clock, last] = clockWords(row);
  const item = mainItem(data, row);
  const alt = live && !bench.gone && item && row.recipe_id ? html`<button type="button" class="btn hide-phone" data-pm="alt" data-item="${item}" title="${"every recipe for " + (row.item || row.recipe) + " and what requiring each would change"}">alternates</button>` : "";
  const more = live && !bench.gone ? html`<button class="btn icon more" type="button" aria-haspopup="menu" aria-expanded="false" aria-label="${"more for " + row.recipe}" data-rowmenu="${row.id}">⋯</button>` : "";
  return entry(
    { key: row.id, picked: live && bench.picked === row.id, focusable: live, extra: "proc", title: row.building + " · " + row.recipe },
    html`${buildingIcon(look, row.building)}<div class="id"><span class="name">${row.recipe}</span><div class="ctx">${rowChips(row, live)} ${row.building}</div></div><div class="pills">${rateFlow(look, row.inputs, row.outputs)}</div><div class="metric clk"><b>${clock}</b>${last ? html`<span class="line2">${last}</span>` : ""}</div><div class="metric pmw"><b>${sgn(row.mw)}</b><span>MW</span></div>${actSlots(alt, { more })}`
  );
}

function buildList(data: SolveResponse, live: boolean): Safe {
  const groups = orderRows(data.rows, view.sort);
  const inner = groups.map((g) => html`${g.step === null ? "" : group("step " + g.step, g.rows.length)}${g.rows.map((r) => buildRow(data, r, live))}`);
  const tools = live ? sortSelect("pm-sort", BUILD_SORTS, view.sort) : "";
  return html`<section>${toolrow(dashH("build list", data.rows.length), tools)}${list("pbuild", "stacked", inner)}</section>`;
}

/** The read-only strip over a viewed version: what it asked for, its delta, restore. */
function versionStrip(rev: number): Safe {
  const plan = bench.plan!;
  const shown = bench.viewedPlan;
  const restore = rev !== plan.rev && !bench.gone ? html`<button type="button" class="btn" data-pm="restore" data-rev="${String(rev)}" title="${"a new version equal to v" + rev + "; nothing after it is lost"}"><span class="spinner" aria-hidden="true"></span>restore v${rev}</button>` : "";
  const what = shown ? (shown.name !== plan.name ? "named “" + shown.name + "” · " : "") + describeArgs(shown.args as unknown as Record<string, unknown>) : "";
  const d = bench.viewedDelta?.from_rev === rev ? note("from v" + rev + " to v" + bench.viewedDelta.to_rev + ": " + bench.viewedDelta.text) : "";
  return html`<section class="card verstrip">${toolrow(dashH("v" + rev + " · read-only"), restore)}<p class="note"><a href="${hashFor(planDash(bench.key, "versions"))}" data-nav="close">back to versions</a> · the plan is at v${plan.rev}</p>${what ? note(what) : ""}${d}</section>`;
}

function viewedBody(): Safe {
  const rev = bench.viewedRev;
  const strip = versionStrip(rev);
  if (bench.viewedError) return html`${strip}${emptyLine("v" + rev + " could not be read", bench.viewedError)}`;
  const data = bench.viewedResult;
  if (!data) return html`${strip}${loadingLine("v" + rev)}`;
  return html`${strip}<div class="greyed">${band(data, rev, false)}${data.feasible ? buildList(data, false) : ""}</div>`;
}

export function machinesTab(): Safe {
  if (bench.viewedRev) return viewedBody();
  const data = bench.result;
  const failed = bench.solveError ? emptyLine("the result could not be read", bench.solveError) : "";
  if (!data) return html`${failed}${failed ? "" : loadingLine("the result")}`;
  const targets = view.targets[bench.key] ? targetsForm() : "";
  const warnings = data.warnings.map((w) => html`<p class="note tone-blocked">${w}</p>`);
  const last = bench.lastFeasible;
  let rows: Safe | string = "";
  if (data.feasible) rows = html`<div class="${bench.solvingRev && bench.solvingRev !== bench.resultRev ? "stale" : ""}">${buildList(data, true)}</div>`;
  else if (last) rows = html`${note("last solvable version, v" + last.rev)}<div class="greyed">${buildList(last.data, false)}</div>`;
  return html`${failed}${band(data, bench.resultRev, true)}${warnings}${targets}${drawer()}${rows}`;
}

/* ------------------------------------------------------------------ actions */

function rowOf(id: string): SolveRow | undefined {
  return bench.result?.rows.filter((r) => r.id === id)[0];
}

function overclockItems(row: SolveRow): MenuItem[] {
  const o = row.overclock_option;
  const id = row.recipe_id;
  if (!o || !id) return [];
  const put = (value: string | null): Op => (value ? { op: "put", field: "row_overclock", item: id, value: value } : { op: "del", field: "row_overclock", item: id });
  const out: MenuItem[] = [];
  const last = o.pinned === "last" || (o.pinned === null && o.applied);
  if (!last) out.push({ label: "overclock the last machine", act: () => applyOps([put("last")]) });
  if (o.pinned !== "spread" && (o.pinned !== null || o.applied)) out.push({ label: "one more underclocked", act: () => applyOps([put("spread")]) });
  if (o.pinned !== null) out.push({ label: "follow the plan's switch", act: () => applyOps([put(null)]) });
  return out;
}

function rowMenu(row: SolveRow, button: HTMLElement): void {
  const member = row.recipe_id || row.recipe;
  const items: MenuItem[] = [
    {
      label: "ban",
      act: function () {
        pendingFocus.ctl = "pt-ban-add";
        pendingFocus.until = Date.now() + 5000;
        applyOps(banOps(member));
      },
    },
  ];
  if (row.recipe_id) items.push({ label: WORDS.pin, act: () => createPin("process", { plan: bench.key, recipe: row.recipe_id! }) });
  items.push({ label: WORDS.askChat, act: () => openAsk({ kind: "process", label: row.building + " · " + row.recipe, ref: member, plan: bench.key, rev: bench.plan ? bench.plan.rev : null }, "row:" + row.id) });
  button.title = row.overclock_option ? optionWords(row.overclock_option) : "";
  openMenu(button, items.concat(overclockItems(row)));
}

/** Picks a build row (again clears it): the row chat sees and the graph card it lights. */
export function pickRow(id: string): void {
  const row = rowOf(id);
  if (!row || bench.picked === id) {
    bench.picked = "";
    selectThing(null);
    return;
  }
  bench.picked = id;
  selectThing({ kind: "process", label: row.building + " · " + row.recipe, ref: row.recipe_id || row.recipe });
}

/** The tab's clicks; false when `target` is none of its controls. */
export function machinesClick(target: Element): boolean {
  const b = target.closest<HTMLElement>("[data-pm]");
  const what = b?.dataset.pm;
  if (what === "targets") {
    view.targets[bench.key] = !view.targets[bench.key];
    changed();
  } else if (what === "undo" && bench.plan) undoRev(bench.plan.rev);
  else if (what === "alt") {
    const item = b!.dataset.item || "";
    showAlternates(item, "");
    go(altDash(bench.key, item));
  } else if (what === "restore") {
    const rev = Number(b!.dataset.rev);
    busyButton(b as HTMLButtonElement, function () {
      restoreRev(rev, () => go(planDash(bench.key, "versions")));
      return writesSettled();
    }).catch(() => undefined);
  } else {
    const more = target.closest<HTMLElement>("[data-rowmenu]");
    const row = more ? rowOf(more.dataset.rowmenu || "") : undefined;
    if (row) rowMenu(row, more!);
    else return false;
  }
  return true;
}

export function machinesChange(el: HTMLSelectElement): boolean {
  if (el.id !== "pm-sort") return false;
  view.sort = el.value as BuildSort;
  changed();
  return true;
}

