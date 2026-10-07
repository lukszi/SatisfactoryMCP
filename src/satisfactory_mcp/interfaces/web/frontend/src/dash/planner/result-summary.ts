/* The result's summary card: the facts of one solved version, its warnings, and the power
 * budget it leaves the grid. See docs/planner-p3_contract.md §9. */

import { button, error, loading, table } from "../../kit/dashkit";
import { make } from "../../kit/dom";
import { count, flow, mw } from "../../kit/format";
import { vitals } from "../../app/vitals";
import { bench } from "./state";
import { undoRev } from "./writes";
import { headroom } from "../power-ledger";
import { WORDS } from "../../kit/words";

import type { Column } from "../../kit/dashkit";
import type { Ledger, SolveRate, SolveResponse } from "../../api/shapes";

interface BudgetRow {
  label: string;
  now: number;
  after: number | null;
}

const POWER = "MW";

export function ratesText(rows: SolveRate[]): string {
  return (
    rows
      .map(function (r) {
        return r.item === POWER ? mw(r.per_min) : flow(r.item, r.per_min);
      })
      .join(", ") || "–"
  );
}

export function withoutPower(rows: SolveRate[]): SolveRate[] {
  return rows.filter(function (r) {
    return r.item !== POWER;
  });
}

function resultFactsText(data: SolveResponse): string {
  const net = data.mw_net;
  const parts = [
    count(data.machines) + " machines in " + count(data.processes) + " processes",
    "draw " + (data.mw_draw === null ? "–" : mw(data.mw_draw)),
    "generation " + (data.mw_generated === null ? "–" : mw(data.mw_generated)),
    "net " + (net === null ? "–" : headroom(net)) + (data.grid_import ? ", from the grid" : ", self-powered"),
    "exports " + ratesText(data.exports),
  ];
  if (data.shards) parts.push(count(data.shards) + " power shards");
  if (data.sloops_used) parts.push(count(data.sloops_used) + " somersloops");
  return parts.join(" · ");
}

function powerBudgetTable(parent: HTMLElement, data: SolveResponse): void {
  const ledger: Ledger | null = vitals().circuits ? vitals().circuits!.world : null;
  if (!ledger) {
    if (vitals().circuitsError) error(parent, "the grid figures", vitals().circuitsError);
    else loading(parent, "grid figures");
    return;
  }
  const net = data.mw_net;
  const rows: BudgetRow[] = [
    { label: WORDS.headroomNow, now: ledger.measured_headroom_mw, after: net === null ? null : ledger.measured_headroom_mw + net },
    { label: WORDS.headroomFull, now: ledger.headroom_mw, after: net === null ? null : ledger.headroom_mw + net },
  ];
  const columns: Column<BudgetRow>[] = [
    {
      key: "grid",
      label: "grid",
      render: function (r) {
        return r.label;
      },
    },
    {
      key: "now",
      label: "today",
      align: "right",
      render: function (r) {
        return headroom(r.now);
      },
      tone: function (r) {
        return r.now < 0 ? "bad" : "";
      },
    },
    {
      key: "after",
      label: "with this plan",
      align: "right",
      render: function (r) {
        return r.after === null ? "–" : headroom(r.after);
      },
      tone: function (r) {
        return r.after !== null && r.after < 0 ? "bad" : "";
      },
    },
  ];
  const box = make("div", "plan-budget");
  box.appendChild(table(columns, rows, { caption: "power budget" }));
  parent.appendChild(box);
}

function undoButton(parent: HTMLElement): void {
  const plan = bench.plan;
  if (!plan || plan.rev <= 1 || bench.gone) return;
  const head = plan.rev;
  parent.appendChild(
    button(
      "undo v" + head,
      function () {
        undoRev(head);
      },
      { title: "undo the change that made this plan unsolvable, as a new version" }
    )
  );
}

export function resultSummaryCard(parent: HTMLElement, data: SolveResponse, rev: number, live: boolean): void {
  const card = make("section", "dash-card");
  const title = make("div", "dash-title");
  title.appendChild(make("h2", "dash-h", "result · v" + rev));
  const waiting = live && bench.solvingRev && bench.solvingRev !== rev;
  if (waiting) title.appendChild(make("span", "plan-status", "solving v" + bench.solvingRev + "…"));
  card.appendChild(title);
  const body = make("div", waiting ? "plan-stale" : "");
  card.appendChild(body);
  if (!data.feasible) {
    body.appendChild(make("p", "plan-headline bad", "not solvable: " + data.cause));
    if (live) undoButton(body);
  } else {
    body.appendChild(make("p", "plan-facts", resultFactsText(data)));
  }
  data.warnings.forEach(function (w) {
    body.appendChild(make("p", "plan-warning", w));
  });
  if (data.feasible && live) powerBudgetTable(body, data);
  parent.appendChild(card);
}
