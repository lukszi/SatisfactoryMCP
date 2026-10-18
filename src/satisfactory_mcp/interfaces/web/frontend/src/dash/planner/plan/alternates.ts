/* The alternates drawer on the machines tab (`planner/<key>/alt/<item>`): every recipe for one
 * item, what requiring it would change, and require / ban / let the solver choose, one version
 * each. See docs/planner-p3_contract.md F2. */

import { html, type Safe } from "../../../kit/html";
import { openMenu } from "../../../kit/overlay/menu";
import { actSlots, entry, list } from "../../../kit/ui/entry";
import { chip, tagChip } from "../../../kit/ui/marks";
import { loadingLine } from "../../../kit/ui/states";
import { dashH, emptyLine, note } from "../../../kit/ui/text";
import { WORDS } from "../../../kit/words";
import { loadAlternates } from "../reads";
import { bench } from "../state";
import { applyOps, hasPushInFlight } from "../writes";
import { buildingIcon, itemIcon, lookupOf, sgn } from "./parts";

import type { PlanOpBody, SwapOption } from "../../../api/shapes";
import type { Op } from "../state";

/** The drawer has re-solved the current head and no push is on its way. */
function current(): boolean {
  const drawer = bench.alternates;
  return !!drawer && !!drawer.data && !drawer.asked && !hasPushInFlight() && !!bench.plan && drawer.data.rev === bench.plan.rev;
}

function named(ops: PlanOpBody[]): Op[] {
  return ops.filter((o): o is Op => typeof o.op === "string");
}

type AltOp = "require" | "ban" | "free";

/** What can be done to an option, in button order: the first is the button, the rest go into ⋯. */
function opsOf(o: SwapOption): AltOp[] {
  const out: AltOp[] = [];
  if (!o.required && named(o.require_ops).length) out.push("require");
  if (named(o.free_ops).length) out.push("free");
  if (!o.banned && named(o.ban_ops).length) out.push("ban");
  return out;
}

const OP_LABEL: Record<AltOp, string> = { require: "require", ban: "ban", free: WORDS.letSolverChoose };

function opTitle(what: AltOp, o: SwapOption): string {
  if (what === "require") return "make every unit in this plan with " + o.name;
  if (what === "ban") return "keep " + o.name + " out of this plan";
  return "drop " + o.name + " from the required and banned lists";
}

function optionActs(o: SwapOption, i: number): Safe {
  if (o.status === "locked" || bench.gone || (o.banned_by && !o.solved)) return actSlots("", { more: "" });
  const ops = opsOf(o);
  const off = current() ? "" : html` aria-disabled="true"`;
  const main = ops[0] ? html`<button type="button" class="btn" data-alt-op="${ops[0]}" data-alt-i="${String(i)}" data-ctl="${"alt:" + o.recipe_id + ":" + ops[0]}" title="${opTitle(ops[0], o)}"${off}>${OP_LABEL[ops[0]]}</button>` : "";
  const more = ops.length > 1 ? html`<button class="btn icon more" type="button" aria-haspopup="menu" aria-expanded="false" aria-label="${"more for " + o.name}" data-alt-more="${String(i)}" data-ctl="${"alt:" + o.recipe_id + ":more"}"${off}>⋯</button>` : "";
  return actSlots(main, { more });
}

function rawPills(o: SwapOption): Safe {
  const d = o.delta;
  if (!o.solved || !d || !d.comparable) return html``;
  const moved = d.inputs.filter((r) => Math.abs(r.delta) >= 0.05);
  if (!moved.length) return html``;
  const look = lookupOf(bench.result);
  return html`<div class="pills row2"><span class="ctx">Δ raw</span>${moved.map((r) => html`<span class="pill">${itemIcon(look, r.name)}<b>${sgn(r.delta, 1)} /min</b><span class="nm">${r.name}</span></span>`)}</div>`;
}

function option(o: SwapOption, i: number): Safe {
  const look = lookupOf(bench.result);
  const d = o.solved && o.delta ? o.delta : null;
  const chips = html`${o.in_use ? chip("in use", "ok") : ""}${o.banned ? chip("banned", "mid") : ""}${o.required ? tagChip("required") : ""}${o.alternate ? tagChip("alt") : ""}${o.status === "locked" ? chip("locked", "muted", o.granted_by.length ? "granted by " + o.granted_by.join(", ") : "") : ""}`;
  const ctx = o.banned_by && !o.solved ? "banned by “" + o.banned_by + "”: edit the banned list to change it" : o.machine || "";
  const machines = !d ? html`<div class="metric none"><b>${o.in_use ? "no change" : "–"}</b></div>` : d.comparable ? html`<div class="metric"><b>${sgn(d.machines)}</b><span>machines</span></div>` : html`<div class="metric none" title="${d.text}"><b>–</b></div>`;
  const draw = d && d.comparable ? html`<div class="facts"><span><em>${sgn(d.mw_draw)}</em> MW draw</span></div>` : html`<div class="facts"></div>`;
  return entry(
    { tone: o.banned ? "mid" : "", title: o.name },
    html`${buildingIcon(look, o.machine || "")}<div class="id"><span class="name">${o.name}</span><div class="ctx">${chips} ${ctx}</div></div>${machines}${draw}${optionActs(o, i)}${rawPills(o)}`
  );
}

/** The drawer, above the build list while the address names an item. */
export function drawer(): Safe {
  const d = bench.alternates;
  if (!d) return html``;
  const data = d.data;
  const heading = "recipes for " + (data ? data.name : d.item);
  const close = html`<button type="button" class="btn icon" data-alt-close="1" data-ctl="alt-close" aria-label="${"close the " + heading}" title="close the recipes (Escape)">×</button>`;
  const head = html`<div class="drawer-head">${dashH(heading + (data ? " · v" + data.rev : ""))}${d.asked ? html`<span class="note">solving v${d.asked}…</span>` : ""}${close}</div>`;
  let body: Safe;
  if (d.error) body = html`${emptyLine("the recipes could not be read", d.error)} <button type="button" class="btn" data-alt-retry="1">try again</button>`;
  else if (!data) body = loadingLine("the recipes");
  else {
    const rows = data.options.length ? list("pswap", "stacked", data.options.map(option)) : emptyLine("no recipe makes " + data.name);
    const head2 = !data.head_feasible ? note("v" + data.rev + " is not solvable") : "";
    const hidden = data.hidden ? note(WORDS.lockedHidden(data.hidden) + " (settings, spoilers)") : "";
    body = html`<div class="${current() ? "" : "stale"}">${head2}${rows}${hidden}</div>`;
  }
  return html`<aside class="card drawer" aria-label="${heading}">${head}${body}</aside>`;
}

/** The drawer's buttons; `close` shuts it. False when `target` is not one of them. */
export function drawerClick(target: Element, close: () => void): boolean {
  if (target.closest("[data-alt-close]")) {
    close();
    return true;
  }
  if (target.closest("[data-alt-retry]")) {
    loadAlternates();
    return true;
  }
  const more = target.closest<HTMLElement>("[data-alt-more]");
  if (more) {
    const o = bench.alternates?.data?.options[Number(more.dataset.altMore)];
    if (o && current()) openMenu(more, opsOf(o).slice(1).map((what) => ({ label: OP_LABEL[what], act: () => runOp(o, what) })));
    return true;
  }
  const b = target.closest<HTMLElement>("[data-alt-op]");
  if (!b) return false;
  const o = bench.alternates?.data?.options[Number(b.dataset.altI)];
  if (o && current()) runOp(o, b.dataset.altOp as AltOp);
  return true;
}

function runOp(o: SwapOption, what: AltOp): void {
  if (what === "require") applyOps(named(o.require_ops), bench.alternates!.data!.item);
  else if (what === "ban") applyOps(named(o.ban_ops));
  else applyOps(named(o.free_ops));
}
