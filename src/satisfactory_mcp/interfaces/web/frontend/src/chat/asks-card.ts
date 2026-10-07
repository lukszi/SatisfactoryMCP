/* The asks card: every queued ask:N with what it is about and whether chat has seen it.
 * See docs/planner-p4_contract.md §2 F6. */

import { askLabel, askStore, asksFor, dropAsk, liveAsks, loadAsks, refetchAsks } from "./asks";
import { button, chip, copyButton, empty, error, link, loading, table } from "../kit/dashkit";
import { make } from "../kit/dom";
import { timeOfDay } from "../kit/format";
import { ASK_STATE, counted } from "../kit/words";

import type { Column, SortState } from "../kit/dashkit";
import type { AskRow } from "../api/shapes";

const order: SortState = { key: "ask", desc: true };

function stateChip(ask: AskRow): HTMLElement {
  if (ask.state === "answered" && ask.answered !== null) {
    return chip(ASK_STATE.answered + " " + timeOfDay(ask.answered), "muted", ask.answered_by ? "marked answered by " + ask.answered_by : "");
  }
  if (ask.state === "seen" && ask.seen !== null) {
    return chip(ASK_STATE.seen + " " + timeOfDay(ask.seen), "muted", ask.seen_by ? "read by " + ask.seen_by : "");
  }
  return chip(ASK_STATE.open || ask.state, "muted", "queued " + timeOfDay(ask.created) + "; paste " + ask.id + " into chat");
}

function questionCell(ask: AskRow): HTMLElement {
  const cell = make("span", "", ask.text);
  if (!ask.answer) return cell;
  const said = make("span", "ask-answer", "answer: " + ask.answer);
  said.title = ask.answer;
  cell.appendChild(said);
  return cell;
}

function aboutCell(ask: AskRow, planKey: string): HTMLElement {
  const plan = ask.about.plan;
  if (ask.about.kind === "plan" && plan && ask.plan_name && plan !== planKey) {
    const named = make("span", "ask-about", "plan ");
    named.appendChild(link("planner/" + plan, "“" + ask.plan_name + "”"));
    return named;
  }
  const cell = make("span", "ask-about", askLabel(ask.about));
  if (!plan || plan === planKey || ask.about.kind === "plan") return cell;
  cell.appendChild(document.createTextNode(" in "));
  if (ask.plan_name) cell.appendChild(link("planner/" + plan, "“" + ask.plan_name + "”"));
  else cell.appendChild(make("span", "dash-muted", "a forgotten plan"));
  return cell;
}

function actions(ask: AskRow): HTMLElement {
  const box = make("span", "dash-acts");
  box.appendChild(copyButton(ask.copy, "copy", { title: "copy " + ask.id + " and its question for chat", label: "copy " + ask.id }));
  box.appendChild(
    button(
      "delete",
      function () {
        box.querySelectorAll("button").forEach(function (b) {
          b.disabled = true;
        });
        dropAsk(ask);
      },
      { title: "delete " + ask.id + "; its number is not reused", label: "delete " + ask.id }
    )
  );
  return box;
}

export function renderAsks(parent: HTMLElement, redraw: () => void, planKey?: string): void {
  loadAsks();
  const got = askStore();
  const rows = planKey ? asksFor(planKey) : liveAsks();
  const card = make("section", "dash-card");
  card.appendChild(make("h2", "dash-h", got.data && rows.length ? "asks · " + counted(rows.length, "ask") : "asks"));
  if (got.error && !got.data) error(card, "the asks", got.error, refetchAsks);
  else if (!got.data) loading(card, "asks");
  else if (!rows.length) empty(card, planKey ? "no asks about this plan yet" : "no asks yet: ask chat from any row of a plan");
  else {
    const columns: Column<AskRow>[] = [
      {
        key: "ask",
        label: "ask",
        sort: function (ask) {
          return ask.n;
        },
        render: function (ask) {
          return ask.id;
        },
      },
      {
        key: "text",
        label: "question",
        className: "ask-question",
        render: questionCell,
      },
      {
        key: "about",
        label: "about",
        sort: function (ask) {
          return askLabel(ask.about);
        },
        render: function (ask) {
          return aboutCell(ask, planKey || "");
        },
      },
      {
        key: "state",
        label: "state",
        sort: function (ask) {
          return ask.state;
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
