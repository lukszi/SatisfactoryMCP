/* The Planner page's plan cards and the "+ new plan" card, and what their ⋯ menu does. */

import { createPin } from "../../../chat/pins";
import { go } from "../../../app/nav";
import { settingChoice } from "../../../app/settings";
import { isRebuilding } from "../../../kit/focus";
import { perMin } from "../../../kit/format";
import { html, type Safe } from "../../../kit/html";
import { openMenu } from "../../../kit/overlay/menu";
import { fail, friendlyError, notify } from "../../../kit/toast";
import { moreBtn } from "../../../kit/ui/controls";
import { acts, entry } from "../../../kit/ui/entry";
import { bar, chip, pill } from "../../../kit/ui/marks";
import { frow } from "../../../kit/ui/forms";
import { OBJECTIVES } from "../../../kit/words";
import { hashFor } from "../../../map/map";
import { factoryDash } from "../../factories/address";
import { openIconPicker } from "../../factories/factory-actions";
import { choiceOf, iconLead, type IconStore } from "../../factories/icon-choice";
import { browserStore, takeLocalChoices } from "../../factories/local-icons";
import { trackDash } from "../address";
import { toggleProgress } from "../built";
import { loadList, planIndex } from "../plan-index";
import { actorWord, changed, commitWords, itemClass, knownItem, NAME_MAX } from "../state";
import { createPlan, duplicatePlan, pushOpsTo } from "../writes";
import { builtOf, exportItems, factoryOf, planIcon, productItem, statusOf } from "./model";
import { loadActivity } from "./timeline";

import type { PlanIndexRow } from "../../../api/shapes";

const MENU = ["rename", "change icon", "duplicate", "pin", "forget"];

const newPlan = { creating: false, draft: { item: "", rate: "" }, problem: "" };

/* The plan whose name is an input right now. */
let renaming = "";

function planLead(row: PlanIndexRow): Safe {
  const item = productItem(row);
  return iconLead(planIcon(row, itemClass), row.icon !== null ? "chosen icon" : item ? "exports " + item : "");
}

function exportPills(row: PlanIndexRow): Safe {
  const items = exportItems(row).map((x) => pill({ name: x.name, icon: itemClass(x.name), rate: x.rate === null ? null : perMin(x.rate, false), noName: true }));
  const mw = !items.length && row.objective === "max_mw" ? html`<span class="pill" title="power for the grid"><b>MW</b></span>` : "";
  return html`<div class="pills full">${items}${mw}</div>`;
}

function builtLine(row: PlanIndexRow): Safe {
  const b = builtOf(row, planIndex.built[row.key]);
  if (b.kind === "pending") return html`<div class="pbuilt"><span class="dim" aria-busy="true">…</span></div>`;
  if (b.kind === "nosite") return html`<div class="pbuilt"><span title="the plan has no site yet">no site</span></div>`;
  if (b.kind === "nosolve") return html`<div class="pbuilt"><span>${b.text}</span></div>`;
  if (b.kind === "ask") return html`<div class="pbuilt"><a href="${hashFor(trackDash(row.key, 0))}" title="${b.text || "open Track to say which factory this plan is"}">which factory?</a></div>`;
  const percent = settingChoice("progress") === "percent" && b.percent !== null;
  const fig = percent ? html`<b>${Math.round(b.percent!)}%</b><span> of planned rate</span>` : html`<b>${b.figure}</b><span> built</span>`;
  return html`<div class="pbuilt"><button type="button" class="pb-fig" data-builtmode="1" title="${b.text + " · click to switch machines and percent"}">${fig}</button>${bar(b.pct, "progress", Math.round(b.pct) + "% built")}</div>`;
}

function nameCell(row: PlanIndexRow, href: string): Safe {
  if (renaming === row.key) return html`<input type="text" class="prename" id="prename" data-ctl="rename" value="${row.name}" maxlength="${NAME_MAX}" aria-label="plan name" data-key="${row.key}">`;
  return html`<a class="name" href="${href}" title="${row.name}">${row.name}</a>`;
}

export function planCard(row: PlanIndexRow): Safe {
  const href = hashFor("planner/" + row.key);
  const fac = factoryOf(row);
  const status = statusOf(row, planIndex.built[row.key]);
  const ctx = html`${chip(OBJECTIVES[row.objective] || row.objective, "muted")} <span class="pv">v${row.rev}</span>${fac ? html` · <a href="${hashFor(factoryDash(fac, ""))}">${fac}</a>` : ""}`;
  const last = actorWord(row.last.actor) + ": " + commitWords(row.last.text);
  return entry(
    { href, key: row.key, title: last, extra: "pcard" },
    html`${planLead(row)}<div class="id">${nameCell(row, href)}<div class="ctx">${ctx}</div></div>${acts(html`${status && chip(status.text, status.tone, status.title)}<span data-plan="${row.key}">${moreBtn(row.name, MENU)}</span>`)}${exportPills(row)}${builtLine(row)}`
  );
}

export function prefillNewPlan(draft: { item: string; rate: string }): void {
  newPlan.draft = draft;
  newPlan.problem = "";
}

export function newPlanCard(): Safe {
  const bad = newPlan.problem ? html` aria-invalid="true"` : "";
  return html`<div class="pnew"><form id="newplan" aria-label="new plan: an item at a rate"><span class="name">+ new plan</span>
    ${frow("item", html`<input type="text" id="np-item" data-ctl="new-item" list="plan-items" placeholder="item, e.g. Heavy Modular Frame" value="${newPlan.draft.item}"${bad}>`, { forId: "np-item" })}
    ${frow("rate", html`<span class="fnum"><input type="number" id="np-rate" data-ctl="new-rate" min="0" step="any" placeholder="rate" value="${newPlan.draft.rate}"><span class="u">/min</span></span>`, { forId: "np-rate", sub: true })}
    ${frow("", html`<button class="${newPlan.creating ? "btn is-busy" : "btn"}" type="submit" title="a plan named “item rate/min”, opened at once"${newPlan.creating ? html` disabled aria-busy="true"` : ""}><span class="spinner" aria-hidden="true"></span>create</button>${newPlan.problem ? html`<p class="invalid" role="alert">${newPlan.problem}</p>` : ""}`, { sub: true })}
  </form></div>`;
}

function submitNewPlan(form: HTMLFormElement): void {
  const item = form.querySelector<HTMLInputElement>("#np-item")!;
  const rate = form.querySelector<HTMLInputElement>("#np-rate")!;
  newPlan.draft = { item: item.value, rate: rate.value };
  item.defaultValue = item.value;
  rate.defaultValue = rate.value;
  const perMinute = Number(rate.value);
  const name = knownItem(item.value);
  if (!item.value.trim()) newPlan.problem = "name the item to make";
  else if (!name) newPlan.problem = "no item is called “" + item.value.trim() + "”; pick one from the list";
  else if (!(perMinute > 0)) newPlan.problem = "the rate is a positive number per minute";
  else newPlan.problem = "";
  if (newPlan.problem || !name) {
    changed();
    return;
  }
  newPlan.creating = true;
  changed();
  const minimums: Record<string, number> = {};
  minimums[name] = perMinute;
  createPlan(name + " " + perMin(perMinute), { objective: "min_raw_power", exports: [name], export_minimums: minimums }, "")
    .then(function (key) {
      newPlan.draft = { item: "", rate: "" };
      go("planner/" + key);
    })
    .catch(function (reason) {
      newPlan.problem = friendlyError(reason);
    })
    .then(function () {
      newPlan.creating = false;
      changed();
    });
}

function rowOf(key: string): PlanIndexRow | undefined {
  return planIndex.data?.index.filter((r) => r.key === key)[0];
}

function reread(): void {
  loadList();
  loadActivity();
}

function commitRename(input: HTMLInputElement): void {
  const row = rowOf(input.dataset.key || "");
  const name = input.value.trim();
  renaming = "";
  input.defaultValue = input.value;
  if (!row || !name || name === row.name) {
    if (!name) fail("a plan name cannot be blank");
    changed();
    return;
  }
  pushOpsTo(row.key, row.rev, [{ op: "rename", name }])
    .then(reread)
    .catch((reason) => fail(friendlyError(reason)))
    .then(changed);
}

function runAction(label: string, row: PlanIndexRow, button: HTMLElement): void {
  if (label === "rename") {
    renaming = row.key;
    changed();
  } else if (label === "change icon") openIconPicker(row.name, button, keyedStore(row.key));
  else if (label === "duplicate") {
    duplicatePlan(undefined, row.key)
      .then(function (key) {
        notify("copied “" + row.name + "” to a new plan");
        go("planner/" + key);
      })
      .catch((reason) => fail(friendlyError(reason)));
  } else if (label === "pin") createPin("plan", { plan: row.key });
  else if (label === "forget") {
    pushOpsTo(row.key, row.rev, [{ op: "forget" }])
      .then(function () {
        notify("forgot “" + row.name + "”; its history is kept, and the plan's page can restore it");
        reread();
      })
      .catch((reason) => fail(friendlyError(reason)));
  }
}

function setIcon(row: PlanIndexRow, icon: string | null): Promise<void> {
  row.icon = icon;
  return pushOpsTo(row.key, row.rev, [{ op: "set", field: "icon", value: icon }]);
}

/* The picker titles the dialog by name and stores by key, as plan state. */
function keyedStore(key: string): IconStore {
  return {
    chosen: () => choiceOf(rowOf(key)?.icon),
    choose(_name, icon) {
      const row = rowOf(key);
      if (!row) return;
      setIcon(row, icon)
        .catch((reason) => fail("the icon for “" + row.name + "” was not saved: " + friendlyError(reason)))
        .then(reread);
    },
  };
}

const adopted = new Set<string>();

/** A plan icon an older page kept in this browser moves to the plan once, unless it has one. */
export function adoptLocalPlanIcons(): void {
  const data = planIndex.data;
  if (!data || !planIndex.world || adopted.has(planIndex.world)) return;
  adopted.add(planIndex.world);
  const local = takeLocalChoices(browserStore(), "satisfactory.planIcons", planIndex.world);
  const rows = Object.keys(local)
    .map((key) => data.index.filter((r) => r.key === key && r.icon === null)[0])
    .filter((row): row is PlanIndexRow => !!row);
  if (!rows.length) return;
  rows
    .reduce((done: Promise<void>, row) => done.then(() => setIcon(row, local[row.key]!)).catch(() => undefined), Promise.resolve())
    .then(reread);
}

/** Cards' clicks, menu, rename field, built toggle and the new-plan form inside `root`. */
export function wireCards(root: HTMLElement): void {
  root.addEventListener("submit", function (event) {
    const form = event.target as HTMLFormElement;
    if (form.id !== "newplan") return;
    event.preventDefault();
    if (!newPlan.creating) submitNewPlan(form);
  });
  root.addEventListener("click", function (event) {
    const target = event.target as Element;
    const more = target.closest<HTMLElement>("[data-plan] .more[data-menu]");
    if (more) {
      const row = rowOf(more.closest<HTMLElement>("[data-plan]")!.dataset.plan!);
      if (row) openMenu(more, MENU.map((label) => ({ label, act: () => runAction(label, row, more) })));
      return;
    }
    if (target.closest("[data-builtmode]")) {
      toggleProgress();
      return;
    }
    if (target.closest("a, button, input, select, label, form")) return;
    const card = target.closest<HTMLElement>(".l-plans .entry[data-href]");
    if (card && root.contains(card)) location.hash = card.dataset.href!;
  });
  root.addEventListener("keydown", function (event) {
    const input = event.target as HTMLInputElement;
    if (input.id !== "prename") return;
    if (event.key === "Enter") {
      event.preventDefault();
      commitRename(input);
    } else if (event.key === "Escape") {
      event.preventDefault();
      renaming = "";
      input.value = input.defaultValue;
      changed();
    }
  });
  root.addEventListener("focusout", function (event) {
    const input = event.target as HTMLInputElement;
    if (input.id === "prename" && renaming && !isRebuilding()) commitRename(input);
  });
}

/** Puts the caret in the rename field once it is drawn. */
export function focusRename(root: HTMLElement): void {
  const input = renaming ? root.querySelector<HTMLInputElement>("#prename") : null;
  if (input && document.activeElement !== input) {
    input.focus();
    input.select();
  }
}
