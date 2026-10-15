/* The result panel: what the head solves to, as a build list or a graph, redrawn after every
 * new version. See docs/planner-p3_contract.md §9. */

import { chip, error, idChip, loading, subTabs, table } from "../../kit/dashkit";
import { make } from "../../kit/dom";
import { count, mw } from "../../kit/format";
import { recipesButton, renderAlternates } from "./alternates";
import { openAsksAbout } from "../../chat/asks";
import { pickTab } from "./reads";
import { bench, changed } from "./state";
import { rowOverclock } from "./power";
import { banButton, clockText, pinProcessButton, processAsk } from "./row-actions";
import { planGraphTab } from "./graph/view";
import { nodeForPick, planGraphOf } from "./graph/model";
import { renderSite } from "./site";
import { renderTrack } from "./track/track";
import { ratesText, resultSummaryCard } from "./result-summary";
import { processPinsByRecipe } from "../../chat/pins";
import { WORDS } from "../../kit/words";

import type { Column, SortState } from "../../kit/dashkit";
import type { FocusSelection, SolveResponse, SolveRow } from "../../api/shapes";
import type { ResultTab } from "./state";

const order: SortState = { key: "building", desc: false };

const WIDE = window.matchMedia("(min-width: 1280px)");
const TABS: { id: ResultTab; label: string }[] = [
  { id: "build list", label: "build list" },
  { id: "graph", label: "graph" },
  { id: "track", label: WORDS.track },
  { id: "site", label: WORDS.site },
];

function clockCell(row: SolveRow, live: boolean): string | HTMLElement {
  const pick = live && !bench.gone ? rowOverclock(row) : null;
  if (!pick) return clockText(row);
  const cell = make("span", "plan-clock");
  cell.appendChild(make("span", "", clockText(row)));
  cell.appendChild(pick);
  return cell;
}

function mainItem(data: SolveResponse, row: SolveRow): string | null {
  const node = nodeForPick(planGraphOf(data), row.id);
  return node ? node.itemId : null;
}

WIDE.addEventListener("change", function () {
  if (bench.alternates) changed();
});

function pickRow(row: SolveRow, select: (s: FocusSelection) => void): void {
  bench.picked = row.id;
  select({ kind: "process", label: row.building + " · " + row.recipe, ref: row.recipe_id || row.recipe });
}

function rowActions(data: SolveResponse, row: SolveRow): HTMLElement {
  const box = make("span", "dash-acts");
  const item = mainItem(data, row);
  if (item && row.recipe_id) box.appendChild(recipesButton(item, row.item || row.recipe, "row"));
  box.appendChild(banButton(row));
  const pin = pinProcessButton(row);
  if (pin) box.appendChild(pin);
  box.appendChild(processAsk(row, "row"));
  return box;
}

function recipeCell(row: SolveRow, live: boolean): HTMLElement {
  const cell = make("span", "plan-recipe", row.recipe);
  if (row.required) cell.appendChild(chip("required", "muted"));
  if (!live) return cell;
  if (bench.chatChangedRows[row.id]) cell.appendChild(chip(WORDS.actorChat, "muted", "chat changed this process since you opened the plan"));
  const pinned = row.recipe_id ? processPinsByRecipe(bench.key)[row.recipe_id] : undefined;
  if (pinned) cell.appendChild(idChip(pinned.id, pinned.text));
  openAsksAbout(bench.key, "process", row.recipe_id || row.recipe).forEach(function (a) {
    cell.appendChild(idChip(a.id, a.text));
  });
  return cell;
}

function buildList(parent: HTMLElement, data: SolveResponse, select: (s: FocusSelection) => void, live: boolean): void {
  const card = make("section", "dash-card");
  card.appendChild(make("h2", "dash-h", "build list · " + count(data.rows.length) + " processes"));
  const columns: Column<SolveRow>[] = [
    {
      key: "building",
      label: "building",
      sort: function (r) {
        return r.building + " " + r.recipe;
      },
      render: function (r) {
        return r.building;
      },
    },
    {
      key: "recipe",
      label: "recipe",
      sort: function (r) {
        return r.recipe;
      },
      render: function (r) {
        return recipeCell(r, live);
      },
    },
    {
      key: "machines",
      label: "machines",
      align: "right",
      sort: function (r) {
        return r.machines;
      },
      render: function (r) {
        return count(r.machines);
      },
    },
    {
      key: "clock",
      label: "clock",
      align: "right",
      sort: function (r) {
        return r.clock;
      },
      render: function (r) {
        return clockCell(r, live);
      },
    },
    {
      key: "mw",
      label: "power",
      align: "right",
      sort: function (r) {
        return r.mw;
      },
      render: function (r) {
        return mw(r.mw, { signed: true });
      },
    },
    {
      key: "in",
      label: "in",
      className: "dash-sub",
      render: function (r) {
        return ratesText(r.inputs);
      },
    },
    {
      key: "out",
      label: "out",
      className: "dash-sub",
      render: function (r) {
        return ratesText(r.outputs);
      },
    },
  ];
  if (live && !bench.gone) {
    columns.push({
      key: "acts",
      label: "",
      render: function (r) {
        return rowActions(data, r);
      },
    });
  }
  card.appendChild(
    table(columns, data.rows, {
      sort: order,
      onSort: changed,
      onRow: function (row) {
        pickRow(row, select);
      },
      rowClass: function (row) {
        return live && bench.picked === row.id ? "plan-picked" : "";
      },
      caption: "build list",
    })
  );
  parent.appendChild(card);
}

export function renderVersionResult(parent: HTMLElement, data: SolveResponse, rev: number, select: (s: FocusSelection) => void): void {
  resultSummaryCard(parent, data, rev, false);
  if (data.feasible) buildList(parent, data, select, false);
}

export function clearPick(): boolean {
  if (!bench.picked) return false;
  bench.picked = "";
  changed();
  return true;
}

function resultTabs(parent: HTMLElement): void {
  parent.appendChild(
    subTabs(
      TABS,
      bench.tab,
      function (id) {
        pickTab(id as ResultTab);
      },
      "result view"
    )
  );
}

/** An unsolvable head: the drawer if open, then the last solvable version greyed out. */
function renderInfeasibleFallback(parent: HTMLElement, select: (s: FocusSelection) => void, close: () => void): void {
  const last = bench.lastFeasible;
  if (bench.alternates) renderAlternates(parent, close);
  if (!last) return;
  const grey = make("div", "plan-stale");
  if (bench.tab === "track") grey.appendChild(make("p", "dash-note", "track needs a solvable version"));
  grey.appendChild(make("p", "dash-note", "last solvable version, v" + last.rev + ":"));
  resultSummaryCard(grey, last.data, last.rev, false);
  buildList(grey, last.data, select, false);
  parent.appendChild(grey);
}

export function renderResult(parent: HTMLElement, select: (s: FocusSelection) => void, close: () => void): void {
  if (bench.tab === "site") {
    resultTabs(parent);
    renderSite(parent);
    return;
  }
  if (bench.solveError) error(parent, "the result", bench.solveError);
  const data = bench.result;
  if (!data) {
    if (!bench.solveError) loading(parent, "the result");
    return;
  }
  resultSummaryCard(parent, data, bench.resultRev, true);
  if (!data.feasible) {
    renderInfeasibleFallback(parent, select, close);
    return;
  }
  resultTabs(parent);
  const beside = !!bench.alternates && bench.tab === "graph" && WIDE.matches;
  const split = make("div", "plan-result" + (beside ? " beside" : ""));
  const dim = bench.tab !== "track" && bench.solvingRev && bench.solvingRev !== bench.resultRev;
  const main = make("div", "plan-main" + (dim ? " plan-stale" : ""));
  if (bench.alternates && !beside) renderAlternates(split, close);
  split.appendChild(main);
  if (bench.alternates && beside) renderAlternates(split, close);
  if (bench.tab === "graph") planGraphTab(main, data, select);
  else if (bench.tab === "track") renderTrack(main, select);
  else buildList(main, data, select, true);
  parent.appendChild(split);
}
