/* The asks card: every queued ask:N with what it is about and whether chat has seen it.
 * See docs/planner-p4_contract.md §2 F6. */

import { askLabel, askStore, asksFor, dropAsk, liveAsks, loadAsks, refetchAsks } from "./asks";
import { button, chip, copyButton, empty, error, link, loading, table } from "./dashkit";
import { make } from "./dom";
import { ASK_STATE, counted } from "./words";

import type { Column, SortState } from "./dashkit";
import type { AskRow } from "./api-shapes";

var order: SortState = { key: "ask", desc: true };

function clock(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
}

function stateChip(a: AskRow): HTMLElement {
  if (a.state === "answered" && a.answered !== null) {
    return chip(ASK_STATE.answered + " " + clock(a.answered), "muted", a.answered_by ? "marked answered by " + a.answered_by : "");
  }
  if (a.state === "seen" && a.seen !== null) {
    return chip(ASK_STATE.seen + " " + clock(a.seen), "muted", a.seen_by ? "read by " + a.seen_by : "");
  }
  return chip(ASK_STATE.open || a.state, "muted", "queued " + clock(a.created) + "; paste " + a.id + " into chat");
}

function aboutCell(a: AskRow, planKey: string): HTMLElement {
  var plan = a.about.plan;
  if (a.about.kind === "plan" && plan && a.plan_name && plan !== planKey) {
    var named = make("span", "ask-about", "plan ");
    named.appendChild(link("planner/" + plan, "“" + a.plan_name + "”"));
    return named;
  }
  var cell = make("span", "ask-about", askLabel(a.about));
  if (!plan || plan === planKey || a.about.kind === "plan") return cell;
  cell.appendChild(document.createTextNode(" in "));
  if (a.plan_name) cell.appendChild(link("planner/" + plan, "“" + a.plan_name + "”"));
  else cell.appendChild(make("span", "dash-muted", "a forgotten plan"));
  return cell;
}

function actions(a: AskRow): HTMLElement {
  var box = make("span", "dash-acts");
  box.appendChild(copyButton(a.copy, "copy", { title: "copy " + a.id + " and its question for chat", label: "copy " + a.id }));
  box.appendChild(
    button(
      "delete",
      function () {
        box.querySelectorAll("button").forEach(function (b) {
          b.disabled = true;
        });
        dropAsk(a);
      },
      { title: "delete " + a.id + "; its number is not reused", label: "delete " + a.id }
    )
  );
  return box;
}

export function renderAsks(parent: HTMLElement, redraw: () => void, planKey?: string): void {
  loadAsks();
  var got = askStore();
  var rows = planKey ? asksFor(planKey) : liveAsks();
  var card = make("section", "dash-card");
  card.appendChild(make("h2", "dash-h", got.data && rows.length ? "asks · " + counted(rows.length, "ask") : "asks"));
  if (got.error && !got.data) error(card, "the asks", got.error, refetchAsks);
  else if (!got.data) loading(card, "asks");
  else if (!rows.length) empty(card, planKey ? "no asks about this plan yet" : "no asks yet: ask chat from any row of a plan");
  else {
    var columns: Column<AskRow>[] = [
      {
        key: "ask",
        label: "ask",
        sort: function (a) {
          return a.n;
        },
        render: function (a) {
          return a.id;
        },
      },
      {
        key: "text",
        label: "question",
        className: "ask-question",
        render: function (a) {
          return a.text;
        },
      },
      {
        key: "about",
        label: "about",
        sort: function (a) {
          return askLabel(a.about);
        },
        render: function (a) {
          return aboutCell(a, planKey || "");
        },
      },
      {
        key: "state",
        label: "state",
        sort: function (a) {
          return a.state;
        },
        render: stateChip,
      },
      {
        key: "acts",
        label: "",
        render: actions,
      },
    ];
    card.appendChild(table(columns, rows, { sort: order, onSort: redraw, caption: "asks" }));
  }
  parent.appendChild(card);
}
