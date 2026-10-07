/* The plans list, and the item-at-rate form that starts a new plan. */

import { renderAsks } from "../../chat/asks-card";
import { empty, error, link, loading, table } from "../../kit/dashkit";
import { make } from "../../kit/dom";
import { ageShort, perMin } from "../../kit/format";
import { go } from "../../app/nav";
import { renderChatSolveCard } from "./chat-card";
import { progressText, toggleProgress } from "./built";
import { trackDash } from "./address";
import { loadList, planIndex } from "./plan-index";
import { actorWord, changed, commitWords, itemList, knownItem, loadItems } from "./state";
import { createPlan } from "./writes";
import { renderActivity } from "./history";
import { renderPins } from "../../chat/pins-card";
import { state } from "../../app/state";
import { friendlyError } from "../../kit/toast";
import { OBJECTIVES } from "../../kit/words";

import type { Column, SortState } from "../../kit/dashkit";
import type { PlanIndexRow } from "../../api/shapes";

const newPlan = {
  creating: false,
  draft: { item: "", rate: "" },
  problem: "",
};

const order: SortState = { key: "plan", desc: false };

function builtCell(row: PlanIndexRow): HTMLElement | string {
  const built = planIndex.built[row.key];
  if (built?.rev !== row.rev) return "…";
  if (built.figure === "?") {
    const ask = link(trackDash(row.key, 0), "?");
    ask.title = built.text || "open Track to say which factory this plan is";
    return ask;
  }
  const cell = make("button", "built-figure", progressText(built));
  cell.type = "button";
  cell.title = built.text + (built.built === null ? "" : " · click to switch machines and percent");
  cell.disabled = built.built === null;
  cell.onclick = function (event) {
    event.stopPropagation();
    toggleProgress();
  };
  return cell;
}

/** What the plan exports, with the rates it asks for. */
function exportsText(row: PlanIndexRow): string {
  const parts = Object.keys(row.rates).map(function (item) {
    return item + " " + perMin(row.rates[item]!);
  });
  row.exports.forEach(function (item) {
    if (!(item in row.rates)) parts.push(item);
  });
  if (row.target_item && !parts.length) parts.push(row.target_item);
  return parts.join(", ") || "MW";
}

function lastChange(row: PlanIndexRow): string {
  return commitWords(row.last.text) + " · " + actorWord(row.last.actor) + ", " + ageShort(row.last.ts) + " ago";
}

function statusWords(row: PlanIndexRow): string {
  if (row.status.length) return row.status.join("; ");
  return row.recorded ? "" : "field not recorded";
}

function plansTable(parent: HTMLElement, rows: PlanIndexRow[]): void {
  const columns: Column<PlanIndexRow>[] = [
    {
      key: "plan",
      label: "plan",
      sort: function (r) {
        return r.name;
      },
      render: function (r) {
        return link("planner/" + r.key, r.name);
      },
    },
    {
      key: "goal",
      label: "goal",
      sort: function (r) {
        return OBJECTIVES[r.objective] || r.objective;
      },
      render: function (r) {
        return OBJECTIVES[r.objective] || r.objective;
      },
    },
    {
      key: "target",
      label: "exports",
      render: exportsText,
    },
    {
      key: "built",
      label: "built",
      align: "right",
      className: "dash-nowrap",
      title: "what stands at the plan's site; ? means Track asks which factory it is, – that the plan has no site",
      sort: function (r) {
        const built = planIndex.built[r.key];
        return built?.total ? (built.built || 0) / built.total : -1;
      },
      render: builtCell,
    },
    {
      key: "version",
      label: "version",
      align: "right",
      sort: function (r) {
        return r.rev;
      },
      render: function (r) {
        return "v" + r.rev;
      },
    },
    {
      key: "status",
      label: "status",
      className: "dash-sub",
      title: "what moved under the plan since it was saved: an unlock, a freed node, a new building, or a field re-cut",
      sort: function (r) {
        return statusWords(r);
      },
      render: statusWords,
    },
    {
      key: "last",
      label: "last change",
      className: "dash-sub",
      sort: function (r) {
        return r.last.ts;
      },
      render: lastChange,
    },
  ];
  parent.appendChild(
    table(columns, rows, {
      sort: order,
      onSort: changed,
      onRow: function (row) {
        go("planner/" + row.key);
      },
      caption: "plans",
    })
  );
}

function newPlanForm(parent: HTMLElement): void {
  const card = make("section", "dash-card");
  card.appendChild(make("h2", "dash-h", "new plan: an item at a rate"));
  const row = make("form", "plan-controls");
  const item = make("input", "dash-name plan-text");
  item.placeholder = "item, e.g. Heavy Modular Frame";
  item.setAttribute("data-ctl", "new-item");
  item.setAttribute("aria-label", "item");
  item.setAttribute("list", "plan-items");
  item.value = item.defaultValue = newPlan.draft.item;
  if (newPlan.problem) item.setAttribute("aria-invalid", "true");
  const rate = make("input", "dash-number");
  rate.type = "number";
  rate.step = "any";
  rate.min = "0";
  rate.placeholder = "rate";
  rate.setAttribute("data-ctl", "new-rate");
  rate.setAttribute("aria-label", "rate per minute");
  rate.value = rate.defaultValue = newPlan.draft.rate;
  const submit = make("button", "btn", newPlan.creating ? "creating…" : "create");
  submit.type = "submit";
  submit.disabled = newPlan.creating;
  row.onsubmit = function (event) {
    event.preventDefault();
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
    createPlan(name + " " + perMin(perMinute), { objective: "min_machines", exports: [name], export_minimums: minimums }, "")
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
  };
  row.appendChild(item);
  row.appendChild(rate);
  row.appendChild(make("span", "dash-muted", "/min"));
  row.appendChild(submit);
  card.appendChild(row);
  if (newPlan.problem) {
    const bad = make("p", "plan-invalid", newPlan.problem);
    bad.setAttribute("role", "alert");
    card.appendChild(bad);
  }
  card.appendChild(make("p", "dash-note", "makes a min-machines plan named “<item> <rate>/min” and opens it"));
  parent.appendChild(card);
}

export function renderList(root: HTMLElement): void {
  if (planIndex.world !== state.world && planIndex.data) {
    planIndex.data = null;
    loadList();
  }
  loadItems();
  root.appendChild(make("h1", "dk-hidden", "Planner"));
  root.appendChild(itemList());
  renderChatSolveCard(root);
  const card = make("section", "dash-card");
  card.appendChild(make("h2", "dash-h", "plans"));
  if (planIndex.error) error(card, "the plans", planIndex.error, loadList);
  else if (!planIndex.data) loading(card, "plans");
  else if (!planIndex.data.index.length) empty(card, "no plans in this world yet", "make one below, or ask chat to plan a factory");
  else plansTable(card, planIndex.data.index);
  root.appendChild(card);
  newPlanForm(root);
  renderActivity(root);
  renderPins(root, changed);
  renderAsks(root, changed);
}
