/* The plans list, and the item-at-rate form that starts a new plan. */

import { get } from "./api";
import { make } from "./dom";
import { hashFor } from "./map";
import { go, renderCard } from "./planner-bench";
import { age, changed, createPlan, rateName } from "./planner-core";
import { perMin } from "./planner-result";
import { state } from "./state";
import { fail, friendly } from "./toast";

import type { PlanIndexRow, PlansResponse } from "./api-shapes";

var list = {
  world: "",
  data: null as PlansResponse | null,
  error: "",
  creating: false,
  draft: ["", ""],
};

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
    .catch(function (error) {
      if (mine !== seq) return;
      list.error = friendly(error);
      changed();
    });
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

function table(parent: HTMLElement, rows: PlanIndexRow[]): void {
  var t = make("table", "dash-table");
  var head = make("tr");
  ["plan", "objective", "target", "version", "last change", ""].forEach(function (h) {
    head.appendChild(make("th", "", h));
  });
  t.appendChild(make("thead")).appendChild(head);
  var tb = t.appendChild(make("tbody"));
  rows.forEach(function (row) {
    var tr = make("tr", "go");
    tr.onclick = function () {
      go("planner/" + row.key);
    };
    var name = make("td");
    var a = make("a", "", row.name);
    a.setAttribute("href", hashFor("planner/" + row.key));
    name.appendChild(a);
    tr.appendChild(name);
    tr.appendChild(make("td", "", row.objective));
    tr.appendChild(make("td", "", target(row)));
    tr.appendChild(make("td", "num", "v" + row.rev));
    tr.appendChild(make("td", "dash-sub", row.last.actor.display + " · " + age(row.last.ts) + " · " + row.last.text));
    var open = make("td");
    open.appendChild(make("span", "btn", "open"));
    tr.appendChild(open);
    tb.appendChild(tr);
  });
  var wrap = make("div", "dash-scroll");
  wrap.appendChild(t);
  parent.appendChild(wrap);
}

function form(parent: HTMLElement): void {
  var card = make("section", "dash-card");
  card.appendChild(make("h2", "dash-h", "new plan: an item at a rate"));
  var row = make("form", "plan-controls");
  var item = make("input", "dash-name plan-text");
  item.placeholder = "item, e.g. Heavy Modular Frame";
  item.setAttribute("data-ctl", "new-item");
  item.value = item.defaultValue = list.draft[0]!;
  var rate = make("input", "dash-number");
  rate.type = "number";
  rate.step = "any";
  rate.min = "0";
  rate.placeholder = "rate";
  rate.setAttribute("data-ctl", "new-rate");
  rate.value = rate.defaultValue = list.draft[1]!;
  var submit = make("button", "btn", list.creating ? "creating…" : "create");
  submit.type = "submit";
  submit.disabled = list.creating;
  row.onsubmit = function (event) {
    event.preventDefault();
    var name = item.value.trim();
    var n = Number(rate.value);
    if (!name || !(n > 0)) {
      fail("a new plan needs an item and a positive rate per minute");
      return;
    }
    list.creating = true;
    list.draft = [item.value, rate.value];
    item.defaultValue = item.value;
    rate.defaultValue = rate.value;
    changed();
    var minimums: Record<string, number> = {};
    minimums[name] = n;
    createPlan(rateName(name, n), { objective: "min_machines", exports: [name], export_minimums: minimums }, "")
      .then(function (key) {
        list.draft = ["", ""];
        go("planner/" + key);
      })
      .catch(function (error) {
        fail(friendly(error));
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
  card.appendChild(make("p", "dash-note", "creates a min-machines plan exporting that item, named “<item> <rate>/min”, and opens it"));
  parent.appendChild(card);
}

export function renderList(root: HTMLElement): void {
  if (list.world !== state.world && list.data) {
    list.data = null;
    loadList();
  }
  renderCard(root);
  var card = make("section", "dash-card");
  card.appendChild(make("h2", "dash-h", "plans"));
  if (!list.data) card.appendChild(make("p", "dash-note", list.error || "loading…"));
  else if (!list.data.index.length) card.appendChild(make("p", "dash-note", "no plans in this world yet"));
  else table(card, list.data.index);
  root.appendChild(card);
  form(root);
}
