/* The plan page's slim header (breadcrumb, icon and name, one status chip, ◎ and ⋯, the note
 * line, the tabs), its ⋯ actions, and the strips above every tab: forgotten, conflicts,
 * chat's solve, the versions others made since the page opened the plan. */

import { go } from "../../../app/nav";
import { copyText } from "../../../kit/copy";
import { html, type Safe } from "../../../kit/html";
import { openMenu } from "../../../kit/overlay/menu";
import { fail, friendlyError, notify } from "../../../kit/toast";
import { acts, entry, list, nameLine } from "../../../kit/ui/entry";
import { chip } from "../../../kit/ui/marks";
import { dashH, note, pageHead, toolrow } from "../../../kit/ui/text";
import { OBJECTIVES, objectiveText } from "../../../kit/words";
import { hashFor } from "../../../map/map";
import { factoryDash } from "../../factories/address";
import { openIconPicker } from "../../factories/factory-actions";
import { choiceOf, iconLead, type IconStore } from "../../factories/icon-choice";
import { chatName, describeParts } from "../chat-request";
import { loadList, planIndex, planTitle } from "../plan-index";
import { factoryOf, planIcon, statusOf } from "../plans/model";
import { PLAN_TABS, planDash, type PlanTab } from "../routes";
import { bench, changed, inbox, itemClass } from "../state";
import { applyArgs, applyOps, createPlan, dismissStrip, dropChip, duplicatePlan, forgetPlan, restorePlan, syncStatus, undoRev } from "../writes";
import { view } from "./parts";

import type { PlanIndexRow } from "../../../api/shapes";

const MENU = ["rename", "change icon", "duplicate", "copy id", "delete"];

function indexRow(): PlanIndexRow | undefined {
  return planIndex.data?.index.filter((r) => r.key === bench.key)[0];
}

function lead(row: PlanIndexRow | undefined): Safe {
  if (row) return iconLead(planIcon(row, itemClass), row.icon !== null ? "chosen icon" : "", "lead");
  const first = bench.plan!.args.exports.filter((x) => x !== "MW")[0];
  return iconLead(first ? itemClass(first) : null, "", "lead");
}

/** Machines left to place: the stages' count when read, else the built figure's gap. */
function leftToBuild(row: PlanIndexRow | undefined): number {
  const d = bench.track.data;
  if (d && d.key === bench.key && d.rev === bench.plan!.rev) return d.to_build;
  const b = row ? planIndex.built[row.key] : undefined;
  return b && b.built !== null && b.rev === bench.plan!.rev ? Math.max(0, b.total - b.built) : 0;
}

function noteLine(row: PlanIndexRow | undefined): Safe {
  const plan = bench.plan!;
  const fac = row ? factoryOf(row) : plan.factory && plan.factory[0] !== "/" && plan.factory.indexOf("cluster:") !== 0 ? plan.factory : "";
  const sync = syncStatus();
  const state = sync === "saved" ? "" : html` · <span class="${sync === "conflict" ? "tone-bad" : ""}">${sync}</span>`;
  return html`${OBJECTIVES[plan.args.objective] || plan.args.objective} · v${plan.rev}${fac ? html` · <a href="${hashFor(factoryDash(fac, ""))}">${fac}</a>` : ""}${state}<span class="plan-held" role="status"></span>`;
}

export function planHead(tab: PlanTab): Safe {
  const plan = bench.plan!;
  const row = indexRow();
  const status = row ? statusOf(row, planIndex.built[row.key]) : null;
  const left = leftToBuild(row);
  const tabs = PLAN_TABS.map((t) => ({
    label: t[0] === "build" && left ? html`${t[1]} <span class="n">· ${left}</span>` : t[1],
    href: hashFor(planDash(bench.key, t[0])),
    on: t[0] === tab,
  }));
  const pad = plan.siting ? html`<button type="button" class="btn icon" data-ph="pad" aria-label="show the pad on the map" title="show the pad on the map">◎</button>` : "";
  const more = html`<button class="btn icon more" type="button" aria-haspopup="menu" aria-expanded="false" aria-label="${"more for " + plan.name}" data-ph="more">⋯</button>`;
  return pageHead({
    crumbs: [["Planner", hashFor("planner")]],
    name: plan.name,
    lead: lead(row),
    chips: status ? chip(status.text, status.tone, status.title) : "",
    acts: html`${pad}${more}`,
    note: noteLine(row),
    tabs,
    tabsLabel: "plan views",
  });
}

/* ------------------------------------------------------------------ strips */

function conflicts(): Safe {
  return html`${bench.conflictChips
    .filter((c, i, all) => all.findIndex((x) => x.gesture === c.gesture && x.field === c.field) === i)
    .map(
      (c) =>
        html`<p class="pstrip">${chip("conflict", "bad")} ${c.text} <button type="button" class="btn" data-ph="keep" data-chip="${String(c.id)}" title="leave the plan as it is now">keep ${c.who}'s</button> <button type="button" class="btn" data-ph="mine" data-chip="${String(c.id)}" title="push the change again on top of the new version">use mine</button></p>`
    )}`;
}

function chatSolve(): Safe {
  const card = inbox.card;
  if (!card) return html``;
  const elsewhere = card.plan && card.plan !== bench.key ? card.plan : "";
  if (elsewhere && !planTitle(elsewhere)) loadList();
  const called = elsewhere ? planTitle(elsewhere) || card.name || "its plan" : "";
  const from = card.plan === bench.key && typeof card.rev === "number" ? card.rev : undefined;
  const apply = card.args && !bench.gone ? html`<button type="button" class="btn" data-ph="apply" title="${from === undefined ? "replace this plan's whole request with chat's, as one new version" : "apply what chat changed from v" + from + ", merged with edits made since"}">${from === undefined ? "replace this plan's request" : "apply to this plan"}</button>` : "";
  const open = elsewhere ? html`<button type="button" class="btn" data-ph="open-solve">open “${called}”</button>` : "";
  const save = card.args ? html`<button type="button" class="btn" data-ph="new-solve" title="save chat's request as a new plan and open it">new plan</button>` : "";
  const parts = card.args ? describeParts(card.args) : [];
  return list(
    "csolve",
    "stacked",
    entry(
      { title: parts.join(" · ") },
      html`<div class="id">${nameLine(html`<span class="name">chat solved this</span>`, objectiveText(card.text))}${parts.length ? html`<div class="chips">${parts.map((p) => chip(p, "muted"))}</div>` : ""}</div>${chip("not saved", "mid")}${acts(html`${apply}${open}${save}<button type="button" class="btn icon" data-ph="dismiss-solve" aria-label="dismiss" title="dismiss">×</button>`)}`
    ),
    "nolead"
  );
}

function others(): Safe {
  const rows = bench.othersCommits;
  if (!rows.length) return html``;
  const lines = rows.map((r) =>
    entry(
      { title: "v" + r.rev + " " + r.text },
      html`<div class="id">${nameLine(html`${chip(r.who, "muted")} <span class="name">v${r.rev} ${r.text}</span>`, r.undone ? "undone" : "")}</div>${acts(!r.undone && !bench.gone ? html`<button type="button" class="btn" data-ph="undo" data-rev="${String(r.rev)}" title="${"undo v" + r.rev + " as a new version"}">undo</button>` : "")}`
    )
  );
  const d = bench.othersDelta;
  return html`${toolrow(dashH("changed since opened", rows.length, "h3"), html`<button type="button" class="btn icon" data-ph="dismiss-others" aria-label="dismiss" title="clear this list">×</button>`)}${list("pothers", "stacked", lines, "nolead")}${d ? note("since v" + d.from_rev + ": " + d.text) : ""}`;
}

export function strips(): Safe {
  const gone = bench.gone ? html`<p class="pstrip">${chip("forgotten", "mid")} restore it to edit it <button type="button" class="btn" data-ph="restore">restore</button></p>` : "";
  return html`${gone}${conflicts()}${chatSolve()}${others()}`;
}

/* ------------------------------------------------------------------ actions */

function iconStore(): IconStore {
  return {
    chosen: () => choiceOf(indexRow()?.icon),
    choose(_name, icon) {
      const row = indexRow();
      if (row) row.icon = icon;
      applyOps([{ op: "set", field: "icon", value: icon }]);
      setTimeout(loadList, 500);
    },
  };
}

function runMenu(label: string, button: HTMLElement): void {
  const plan = bench.plan!;
  if (label === "rename") {
    view.renaming = true;
    view.renameFocus = true;
    changed();
  } else if (label === "change icon") openIconPicker(plan.name, button, iconStore());
  else if (label === "duplicate")
    duplicatePlan()
      .then((key) => {
        notify("copied to a new plan");
        go(planDash(key));
      })
      .catch((reason) => fail(friendlyError(reason)));
  else if (label === "copy id")
    copyText("plan:" + plan.name).then(
      () => notify("copied plan:" + plan.name),
      () => fail("the browser refused to copy the id")
    );
  else if (label === "delete") forgetPlan();
  else if (label === "restore") restorePlan();
}

function solveAction(what: string): void {
  const card = inbox.card;
  if (!card) return;
  if (what === "dismiss-solve") {
    inbox.card = null;
    changed();
  } else if (what === "open-solve" && card.plan) go(planDash(card.plan));
  else if (what === "apply" && card.args) {
    const from = card.plan === bench.key && typeof card.rev === "number" ? card.rev : undefined;
    inbox.card = null;
    applyArgs(card.args, card.id, from);
  } else if (what === "new-solve" && card.args) {
    const args = card.args;
    inbox.card = null;
    changed();
    createPlan(chatName(args, card.text), args, card.id)
      .then((key) => go(planDash(key)))
      .catch((reason) => fail(friendlyError(reason)));
  }
}

/** The header's and the strips' clicks; false when `target` is none of their controls. */
export function headClick(target: Element, showPad: () => void): boolean {
  const b = target.closest<HTMLElement>("[data-ph]");
  if (!b || !bench.plan) return false;
  const what = b.dataset.ph || "";
  if (what === "more") {
    const items = bench.gone ? MENU.filter((m) => m !== "delete").concat(["restore"]) : MENU;
    openMenu(b, items.map((label) => ({ label, act: () => runMenu(label, b) })));
  } else if (what === "pad") showPad();
  else if (what === "restore") restorePlan();
  else if (what === "keep" || what === "mine") dropChip(Number(b.dataset.chip), what === "mine");
  else if (what === "undo") undoRev(Number(b.dataset.rev));
  else if (what === "dismiss-others") dismissStrip(null);
  else solveAction(what);
  return true;
}

