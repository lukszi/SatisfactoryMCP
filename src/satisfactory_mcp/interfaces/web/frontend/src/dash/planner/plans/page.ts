/* The Planner page, `dash=planner`: plan cards left, the activity timeline right (sticky);
 * phones stack them. docs/frontend_kit.md, "Planner". */

import "./plans.css";
import { renderAsks } from "../../../chat/asks-card";
import { renderPins } from "../../../chat/pins-card";
import { state } from "../../../app/state";
import { html, mount } from "../../../kit/html";
import { sortSelect } from "../../../kit/ui/controls";
import { list } from "../../../kit/ui/entry";
import { dashH, emptyLine, toolrow } from "../../../kit/ui/text";
import { renderChatSolveCard } from "../chat-card";
import { loadList, planIndex } from "../plan-index";
import { changed, itemList, loadItems } from "../state";
import { adoptLocalPlanIcons, focusRename, newPlanCard, planCard, wireCards } from "./cards";
import { SORTS, sortPlans, type PlanSort } from "./model";
import { timeline, wireTimeline } from "./timeline";

let sortBy: PlanSort = "last";

function cards() {
  const data = planIndex.data;
  const head = toolrow(dashH("plans", data ? data.index.length : undefined), sortSelect("psort", SORTS, sortBy));
  if (planIndex.error) return html`${head}${list("plans", "grid", newPlanCard())}${emptyLine("the plans did not load", planIndex.error)}`;
  if (!data) return html`${head}${list("plans", "grid", newPlanCard())}<p class="note" aria-busy="true">loading plans…</p>`;
  const rows = sortPlans(data.index, planIndex.built, sortBy);
  const none = rows.length ? "" : emptyLine("no plans in this world yet", "make one here, or ask chat to plan a factory");
  return html`${head}${list("plans", "grid", html`${newPlanCard()}${rows.map(planCard)}`)}${none}`;
}

export function renderPlansPage(parent: HTMLElement): void {
  if (planIndex.world !== state.world && planIndex.data) {
    planIndex.data = null;
    loadList();
  }
  if (planIndex.world === state.world) adoptLocalPlanIcons();
  loadItems();
  const root = document.createElement("div");
  root.className = "plans";
  mount(
    root,
    html`<h1 class="dk-hidden">Planner</h1><div class="pchat"></div><div class="psplit"><section class="pcards" aria-label="plans">${cards()}<div class="pextra"></div></section><aside class="ptl" aria-label="activity">${timeline()}</aside></div>`
  );
  root.appendChild(itemList());
  renderChatSolveCard(root.querySelector<HTMLElement>(".pchat")!);
  const extra = root.querySelector<HTMLElement>(".pextra")!;
  renderPins(extra, changed);
  renderAsks(extra, changed);
  wireCards(root);
  wireTimeline(root);
  root.addEventListener("change", function (event) {
    const select = event.target as HTMLSelectElement;
    if (select.id !== "psort") return;
    sortBy = select.value as PlanSort;
    changed();
  });
  parent.appendChild(root);
  focusRename(root);
}
