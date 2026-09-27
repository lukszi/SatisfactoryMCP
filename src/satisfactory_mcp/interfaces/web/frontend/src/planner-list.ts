/* The plans list, and the item-at-rate form that starts a new plan. */

import { get } from "./api";
import { empty, error, link, loading, table } from "./dashkit";
import { make } from "./dom";
import { perMin } from "./format";
import { go } from "./nav";
import { renderCard } from "./planner-bench";
import { actorWord, age, changed, commitWords, createPlan, itemList, knownItem, loadItems, OBJECTIVES } from "./planner-core";
import { state } from "./state";
import { friendly } from "./toast";

import type { Column, SortState } from "./dashkit";
import type { PlanIndexRow, PlansResponse } from "./api-shapes";

var list = {
  world: "",
  data: null as PlansResponse | null,
  error: "",
  creating: false,
  draft: ["", ""],
  problem: "",
};

var order: SortState = { key: "plan", desc: false };
var seq = 0;

export function loadList(): void {
  var mine = ++seq;
  var world = state.world;
  get<PlansResponse>("/api/plans")
    .then(function (data) {
      if (mine !== seq) return;
      list.world = world;
      list.data = data;
      list.error = "";
      changed();
    })
    .catch(function (reason) {
      if (mine !== seq) return;
      list.error = friendly(reason);
      changed();
    });
}

export function planTitle(key: string): string {
  var row = list.data
    ? list.data.index.filter(function (r) {
        return r.key === key;
      })[0]
    : undefined;
  return row ? row.name : "";
}

function target(row: PlanIndexRow): string {
  var parts = Object.keys(row.rates).map(function (item) {
    return item + " " + perMin(row.rates[item]!);
  });
  row.exports.forEach(function (e) {
    if (!(e in row.rates)) parts.push(e);
  });
  if (row.target_item && !parts.length) parts.push(row.target_item);
  return parts.join(", ") || "MW";
}

function lastChange(row: PlanIndexRow): string {
  return commitWords(row.last.text) + " · " + actorWord(row.last.actor) + ", " + age(row.last.ts) + " ago";
}

function plans(parent: HTMLElement, rows: PlanIndexRow[]): void {
  var columns: Column<PlanIndexRow>[] = [
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
      render: target,
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

function form(parent: HTMLElement): void {
  var card = make("section", "dash-card");
  card.appendChild(make("h2", "dash-h", "new plan: an item at a rate"));
  var row = make("form", "plan-controls");
  var item = make("input", "dash-name plan-text");
  item.placeholder = "item, e.g. Heavy Modular Frame";
  item.setAttribute("data-ctl", "new-item");
  item.setAttribute("aria-label", "item");
  item.setAttribute("list", "plan-items");
  item.value = item.defaultValue = list.draft[0]!;
  if (list.problem) item.setAttribute("aria-invalid", "true");
  var rate = make("input", "dash-number");
  rate.type = "number";
  rate.step = "any";
  rate.min = "0";
  rate.placeholder = "rate";
  rate.setAttribute("data-ctl", "new-rate");
  rate.setAttribute("aria-label", "rate per minute");
  rate.value = rate.defaultValue = list.draft[1]!;
  var submit = make("button", "btn", list.creating ? "creating…" : "create");
  submit.type = "submit";
  submit.disabled = list.creating;
  row.onsubmit = function (event) {
    event.preventDefault();
    list.draft = [item.value, rate.value];
    item.defaultValue = item.value;
    rate.defaultValue = rate.value;
    var n = Number(rate.value);
    var name = knownItem(item.value);
    if (!item.value.trim()) list.problem = "name the item to make";
    else if (!name) list.problem = "no item is called “" + item.value.trim() + "”; pick one from the list";
    else if (!(n > 0)) list.problem = "the rate is a positive number per minute";
    else list.problem = "";
    if (list.problem || !name) {
      changed();
      return;
    }
    list.creating = true;
    changed();
    var minimums: Record<string, number> = {};
    minimums[name] = n;
    createPlan(name + " " + perMin(n), { objective: "min_machines", exports: [name], export_minimums: minimums }, "")
      .then(function (key) {
        list.draft = ["", ""];
        go("planner/" + key);
      })
      .catch(function (reason) {
        list.problem = friendly(reason);
      })
      .then(function () {
        list.creating = false;
        changed();
      });
  };
  row.appendChild(item);
  row.appendChild(rate);
  row.appendChild(make("span", "dash-muted", "/min"));
  row.appendChild(submit);
  card.appendChild(row);
  if (list.problem) {
    var bad = make("p", "plan-invalid", list.problem);
    bad.setAttribute("role", "alert");
    card.appendChild(bad);
  }
  card.appendChild(make("p", "dash-note", "makes a min-machines plan named “<item> <rate>/min” and opens it"));
  parent.appendChild(card);
}

export function renderList(root: HTMLElement): void {
  if (list.world !== state.world && list.data) {
    list.data = null;
    loadList();
  }
  loadItems();
  root.appendChild(itemList());
  renderCard(root);
  var card = make("section", "dash-card");
  card.appendChild(make("h2", "dash-h", "plans"));
  if (list.error) error(card, "the plans", list.error, loadList);
  else if (!list.data) loading(card, "plans");
  else if (!list.data.index.length) empty(card, "no plans in this world yet", "make one below, or ask chat to plan a factory");
  else plans(card, list.data.index);
  root.appendChild(card);
  form(root);
}
