/* One plan's page, `planner/<key>[/<tab>]`: the slim header with the tabs machines · flows ·
 * site · to build · versions, the strips, the tab; and the pad editor at `/place`.
 * docs/frontend_kit.md, "Plan page". */

import "./plan.css";
import "../../factories/factories.css";
import "../../items/items.css";
import "../../progress/progress.css";
import "../../world/world.css";
import "../plans/plans.css";
import { html, toElement } from "../../../kit/html";
import { loadingLine } from "../../../kit/ui/states";
import { emptyLine, pageHead } from "../../../kit/ui/text";
import { hashFor } from "../../../map/map";
import { renderPlacing } from "../site";
import { PLAN_TABS, planDash, type PlannerAddress } from "../routes";
import { bench, changed, itemList, NAME_MAX } from "../state";
import { openPlan, renamePlan } from "../writes";
import { drawerClick } from "./alternates";
import { buildChange, buildClick, buildKey, buildTab } from "./build";
import { flowsClick, flowsTab, lightItem, mountFlows } from "./flows";
import { headClick, planHead, strips } from "./head";
import { machinesChange, machinesClick, machinesTab, pickRow } from "./machines";
import { view } from "./parts";
import { mountSite, showPad, siteTab } from "./site";
import { targetsChange, targetsClick, targetsInput } from "./targets";
import { versionsClick, versionsTab } from "./versions";

import type { Safe } from "../../../kit/html";

function tabBody(at: PlannerAddress): Safe {
  const tab = bench.tab;
  if (tab === "flows") return flowsTab();
  if (tab === "site") return siteTab();
  if (tab === "build") return buildTab(at.stage, at.pick);
  if (tab === "versions") return versionsTab();
  return machinesTab();
}

function startRename(page: HTMLElement): void {
  const h1 = page.querySelector<HTMLElement>(".phead .title h1");
  if (!h1 || !bench.plan) return;
  h1.replaceWith(toElement(html`<input type="text" id="ph-rename" data-ctl="ph-rename" class="prename" value="${bench.plan.name}" maxlength="${NAME_MAX}" aria-label="plan name">`));
}

/* The caret goes into the name field once, when ⋯ rename opened it. */
function focusRename(page: HTMLElement): void {
  const box = page.querySelector<HTMLInputElement>("#ph-rename");
  if (!box || !view.renameFocus) return;
  view.renameFocus = false;
  box.focus();
  box.select();
}

function endRename(box: HTMLInputElement, save: boolean): void {
  if (!view.renaming) return;
  view.renaming = false;
  const name = box.value.trim();
  box.defaultValue = box.value;
  if (save && name && bench.plan && name !== bench.plan.name) renamePlan(name);
  else changed();
}

function wire(page: HTMLElement, close: () => void): void {
  page.addEventListener("click", function (event) {
    const t = event.target as Element;
    if (headClick(t, showPad) || drawerClick(t, close) || targetsClick(t) || machinesClick(t) || flowsClick(t) || buildClick(t) || versionsClick(t)) return;
    if (t.closest("a, button, input, select, label, textarea")) return;
    const row = t.closest<HTMLElement>(".l-pbuild .entry[data-key]");
    if (row && !bench.viewedRev) pickRow(row.dataset.key || "");
  });
  page.addEventListener("change", function (event) {
    const el = event.target as HTMLInputElement & HTMLSelectElement;
    if (el.id !== "ph-rename") targetsChange(el) || machinesChange(el) || buildChange(el);
  });
  page.addEventListener("input", (event) => targetsInput(event.target as HTMLInputElement));
  page.addEventListener("keydown", function (event) {
    const t = event.target as HTMLElement;
    if (t.id === "ph-rename") {
      if (event.key === "Enter" || event.key === "Escape") {
        event.preventDefault();
        endRename(t as HTMLInputElement, event.key === "Enter");
      }
      return;
    }
    if (event.key === "Enter" && t.matches(".l-pbuild .entry[data-key]")) pickRow(t.dataset.key || "");
    else buildKey(event);
  });
  page.addEventListener("focusout", function (event) {
    const t = event.target as HTMLInputElement;
    if (t.id === "ph-rename" && t.isConnected) endRename(t, true);
  });
  if (bench.tab !== "flows") return;
  page.addEventListener("mouseover", function (event) {
    const hit = (event.target as Element).closest("[data-item]");
    lightItem(page, hit ? hit.getAttribute("data-item") : null);
  });
}

function placeView(parent: HTMLElement): void {
  const box = toElement(html`<div class="ppage pplace"><p class="note"><a href="${hashFor(planDash(bench.key, "site"))}" data-nav="close">‹ back to the site</a> · ${bench.plan!.name}</p></div>`);
  renderPlacing(box);
  parent.appendChild(box);
}

/** Draws the open plan into `parent`; `close` shuts the alternates drawer. */
export function renderPlanPage(parent: HTMLElement, at: PlannerAddress, close: () => void): void {
  const crumbs: [string, string][] = [["Planner", hashFor("planner")]];
  if (bench.missing || bench.error) {
    const how = bench.missing ? "it may belong to another world; pick one from the list" : html`<button type="button" class="btn" data-retry="1">try again</button>`;
    const box = toElement(html`<div class="ppage">${pageHead({ crumbs, name: at.key })}${emptyLine(bench.missing ? bench.error : "the plan could not be read · " + bench.error, how)}</div>`);
    box.querySelector("[data-retry]")?.addEventListener("click", () => openPlan(bench.key));
    parent.appendChild(box);
    return;
  }
  if (!bench.plan || (!at.tab && !at.place)) {
    parent.appendChild(toElement(html`<div class="ppage">${loadingLine("the plan")}</div>`));
    return;
  }
  if (bench.place) {
    placeView(parent);
    return;
  }
  const label = PLAN_TABS.filter((t) => t[0] === bench.tab)[0]![1];
  const page = toElement(html`<div class="ppage">${planHead(bench.viewedRev ? "versions" : bench.tab)}${strips()}<div class="${"pgx pg-" + bench.tab}"><section class="fside" aria-label="${label}">${tabBody(at)}</section></div></div>`);
  page.appendChild(itemList());
  if (view.renaming) startRename(page);
  wire(page, close);
  parent.appendChild(page);
  if (bench.tab === "flows") mountFlows(page);
  if (bench.tab === "site") mountSite(page);
  focusRename(page);
}
