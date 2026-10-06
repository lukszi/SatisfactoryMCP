/* The result panel: what the head solves to, as a build list or a graph, redrawn after every
 * new version. See docs/planner-p3_contract.md §9. */

import { button, chip, copyButton, error, idChip, loading, subTabs, table } from "../../kit/dashkit";
import { make } from "../../kit/dom";
import { count, flow, mw, pct } from "../../kit/format";
import { drawGraph, graphCardFrame, GRAPH_HINT, setPicked } from "../graph";
import { vitals } from "../../app/vitals";
import { recipesButton, renderAlternates } from "./planner-alternates";
import { askButton, openAsksAbout } from "../../chat/asks";
import { pickTab } from "./planner-reads";
import { bench, changed, pendingFocus } from "./planner-state";
import { applyOps, banOps, undoRev } from "./planner-writes";
import { rowOverclock } from "./planner-power";
import { renderSite } from "./planner-site";
import { renderTrack } from "./planner-track";
import { processPinsByRecipe, createPin } from "../../chat/pins";
import { headroom } from "../power-ledger";
import { WORDS } from "../../kit/words";

import type { Column, SortState } from "../../kit/dashkit";
import type { GraphNodeShape } from "../graph";
import type { FocusSelection, Ledger, PlanGraphNode, SolveRate, SolveResponse, SolveRow } from "../../api/shapes";
import type { ResultTab } from "./planner-state";

interface PlanNode extends GraphNodeShape {
  tip: string;
  row: string | null;
  item: string | null;
}

interface BudgetRow {
  label: string;
  now: number;
  after: number | null;
}

// The graph is drawn again only when its data, badges or flashes change; scroll and focus are kept.
var graphCache = { data: null as SolveResponse | null, key: "", frame: null as HTMLElement | null, x: 0, y: 0, focus: "" };
var flashed: Record<string, number> = {};
var order: SortState = { key: "building", desc: false };

var POWER = "MW";
var FLASH_MS = 4000;
var WIDE = window.matchMedia("(min-width: 1280px)");
var TABS: { id: ResultTab; label: string }[] = [
  { id: "build list", label: "build list" },
  { id: "graph", label: "graph" },
  { id: "track", label: WORDS.track },
  { id: "site", label: WORDS.site },
];

function clockText(row: { machines: number; clock: number; last_clock?: number | null }): string {
  if (row.last_clock === null || row.last_clock === undefined) return pct(row.clock, 1);
  if (row.machines === 1) return pct(row.last_clock, 1);
  return "100%, last " + pct(row.last_clock, 1);
}

function clockCell(row: SolveRow, live: boolean): string | HTMLElement {
  const pick = live && !bench.gone ? rowOverclock(row) : null;
  if (!pick) return clockText(row);
  const cell = make("span", "plan-clock");
  cell.appendChild(make("span", "", clockText(row)));
  cell.appendChild(pick);
  return cell;
}

function ratesText(rows: SolveRate[]): string {
  return (
    rows
      .map(function (r) {
        return r.item === POWER ? mw(r.per_min) : flow(r.item, r.per_min);
      })
      .join(", ") || "–"
  );
}

function withoutPower(rows: SolveRate[]): SolveRate[] {
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

function resultSummaryCard(parent: HTMLElement, data: SolveResponse, rev: number, live: boolean): void {
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

function mainItem(data: SolveResponse, row: SolveRow): string | null {
  const node = data.graph.nodes.filter(function (n) {
    return n.row === row.id;
  })[0];
  return node ? node.item : null;
}

WIDE.addEventListener("change", function () {
  if (bench.alternates) changed();
});

function pickRow(row: SolveRow, select: (s: FocusSelection) => void): void {
  bench.picked = row.id;
  select({ kind: "process", label: row.building + " · " + row.recipe, ref: row.recipe_id || row.recipe });
}

function banButton(row: SolveRow): HTMLButtonElement {
  return button(
    "ban",
    function () {
      const member = row.recipe_id || row.recipe;
      pendingFocus.ctl = "banned:" + member;
      pendingFocus.until = Date.now() + 5000;
      applyOps(banOps(member));
    },
    { title: "keep this recipe out of the plan", label: "ban " + row.recipe }
  );
}

function pinProcessButton(row: SolveRow): HTMLButtonElement | null {
  const id = row.recipe_id;
  if (!id) return null;
  return button(
    WORDS.pin,
    function () {
      createPin("process", { plan: bench.key, recipe: id! });
    },
    { title: "pin this process and copy its pin:N for chat", label: "pin " + row.recipe }
  );
}

function copyIdButton(text: string, what: string): HTMLButtonElement {
  return copyButton(text, "copy", { title: "copy " + text + " for a tool call", label: "copy " + what });
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

function processAsk(row: SolveRow, where: string): HTMLButtonElement {
  const ref = row.recipe_id || row.recipe;
  return askButton({ kind: "process", label: row.building + " · " + row.recipe, ref: ref, plan: bench.key, rev: bench.plan ? bench.plan.rev : null }, where + ":" + row.id);
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

/** The chat-changed rows still inside their flash window, counted from when each was first seen. */
function updateFlashingRows(): string[] {
  const now = Date.now();
  Object.keys(flashed).forEach(function (id) {
    if (!bench.chatChangedRows[id]) delete flashed[id];
  });
  Object.keys(bench.chatChangedRows).forEach(function (id) {
    if (!flashed[id]) flashed[id] = now;
  });
  return Object.keys(flashed).filter(function (id) {
    return now - flashed[id]! < FLASH_MS;
  });
}

function planNodes(data: SolveResponse): PlanNode[] {
  const byId: Record<string, SolveRow> = {};
  data.rows.forEach(function (r) {
    byId[r.id] = r;
  });
  const pins = processPinsByRecipe(bench.key);
  return data.graph.nodes.map(function (n: PlanGraphNode): PlanNode {
    const row = n.row ? byId[n.row] : undefined;
    const badges: string[] = [];
    if (row && bench.chatChangedRows[row.id]) badges.push(WORDS.actorChat);
    if (row && row.recipe_id && pins[row.recipe_id]) badges.push(pins[row.recipe_id]!.id);
    const tip = row
      ? row.building + " · " + row.recipe + "\nin: " + ratesText(withoutPower(row.inputs)) + "\nout: " + ratesText(withoutPower(row.outputs))
      : n.label + (n.detail ? " · " + n.detail : "");
    return {
      id: n.id,
      kind: n.kind,
      label: n.label,
      detail: n.detail,
      rank: n.rank,
      row: n.row,
      item: n.item,
      badges: badges,
      tip: tip,
      machines: 0,
      running: 0,
      blocked: 0,
      stopped: 0,
    };
  });
}

function pickNode(data: SolveResponse, node: PlanNode, select: (s: FocusSelection) => void): void {
  const row = data.rows.filter(function (r) {
    return r.id === node.row;
  })[0];
  if (row) {
    pickRow(row, select);
    return;
  }
  bench.picked = node.id;
  select({ kind: "item", label: node.label, ref: node.item || "" });
}

function cachedGraphFrame(data: SolveResponse, select: (s: FocusSelection) => void): HTMLElement {
  const nodes = planNodes(data);
  const flash = updateFlashingRows();
  const key =
    nodes
      .map(function (n) {
        return n.id + "=" + (n.badges || []).join("+");
      })
      .join("|") +
    "#" +
    flash.join("|");
  if (graphCache.data !== data || graphCache.key !== key || !graphCache.frame) {
    const frame = drawGraph(
      { nodes: nodes, edges: data.graph.edges.map(function (e) {
        return { source: e.source, target: e.target, item: e.item, per_min: e.per_min, text: e.text || undefined };
      }) },
      function (n: PlanNode) {
        return n.tip;
      },
      function (n: PlanNode) {
        pickNode(data, n, select);
      },
      {
        pickable: function (n: PlanNode) {
          return n.kind === "process" || (n.kind === "export" && !!n.item);
        },
        picked: bench.picked,
        flash: flash,
      }
    );
    if (graphCache.data !== data) {
      graphCache.x = 0;
      graphCache.y = 0;
    }
    frame.addEventListener("scroll", function () {
      if (!frame.isConnected) return;
      graphCache.x = frame.scrollLeft;
      graphCache.y = frame.scrollTop;
    });
    frame.addEventListener("focusin", function (event) {
      const node = (event.target as Element).closest(".graph-node");
      graphCache.focus = node ? node.getAttribute("data-node") || "" : "";
    });
    frame.addEventListener("focusout", function () {
      setTimeout(function () {
        if (frame.isConnected && !frame.contains(document.activeElement)) graphCache.focus = "";
      }, 0);
    });
    graphCache.data = data;
    graphCache.key = key;
    graphCache.frame = frame;
    if (flash.length) setTimeout(changed, FLASH_MS);
  } else setPicked(graphCache.frame, bench.picked);
  return graphCache.frame;
}

function nodeCard(parent: HTMLElement, data: SolveResponse): void {
  const picked = bench.picked;
  const node = picked
    ? data.graph.nodes.filter(function (n) {
        return n.id === picked;
      })[0]
    : undefined;
  if (!node) return;
  const card = make("section", "dash-card plan-node");
  card.setAttribute("aria-label", "picked: " + node.label);
  const row = data.rows.filter(function (r) {
    return r.id === node!.row;
  })[0];
  const title = make("div", "dash-title");
  title.appendChild(make("h2", "dash-h", row ? row.recipe : node.label));
  const acts = make("div", "dash-acts");
  if (row) {
    const power = row.mw ? " · " + mw(row.mw, { signed: true }) : "";
    card.appendChild(title);
    card.appendChild(make("p", "plan-facts", row.building + " ×" + count(row.machines) + " · " + clockText(row) + power));
    card.appendChild(make("p", "dash-sub", "in: " + ratesText(withoutPower(row.inputs))));
    card.appendChild(make("p", "dash-sub", "out: " + ratesText(withoutPower(row.outputs))));
    if (!bench.gone) {
      if (node.item && row.recipe_id) acts.appendChild(recipesButton(node.item, row.item || row.recipe, "node"));
      acts.appendChild(banButton(row));
      const pin = pinProcessButton(row);
      if (pin) acts.appendChild(pin);
    }
    if (row.recipe_id) acts.appendChild(copyIdButton(row.recipe_id, row.recipe));
    if (!bench.gone) acts.appendChild(processAsk(row, "node"));
  } else {
    card.appendChild(title);
    card.appendChild(make("p", "plan-facts", node.detail));
    if (node.item && !bench.gone) acts.appendChild(recipesButton(node.item, node.label, "node"));
    if (!bench.gone) {
      const about = { kind: "item", label: node.label, ref: node.item || node.id, plan: bench.key, rev: bench.plan ? bench.plan.rev : null };
      acts.appendChild(askButton(about, "node:" + node.id));
    }
  }
  card.appendChild(acts);
  parent.appendChild(card);
}

function graphTab(parent: HTMLElement, data: SolveResponse, select: (s: FocusSelection) => void): HTMLElement | null {
  const card = graphCardFrame("production graph", true);
  parent.appendChild(card);
  if (!data.graph.nodes.length) {
    card.appendChild(make("p", "dash-note", "nothing to draw"));
    return null;
  }
  const frame = cachedGraphFrame(data, select);
  card.appendChild(frame);
  card.appendChild(make("p", "dash-note", "click a process for its recipes · rates split over each item's producers by share · " + GRAPH_HINT));
  nodeCard(parent, data);
  return frame;
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

/** Puts the graph back where it was scrolled to, and its focused node, after a redraw. */
function restoreGraphView(frame: HTMLElement): void {
  frame.scrollLeft = graphCache.x;
  frame.scrollTop = graphCache.y;
  const lost = !document.activeElement || document.activeElement === document.body;
  const again = graphCache.focus && lost ? frame.querySelector<SVGGElement>('[data-node="' + CSS.escape(graphCache.focus) + '"]') : null;
  if (again) again.focus({ preventScroll: true });
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
  const frame = bench.tab === "graph" ? graphTab(main, data, select) : null;
  if (bench.tab === "track") renderTrack(main, select);
  else if (bench.tab !== "graph") buildList(main, data, select, true);
  parent.appendChild(split);
  if (frame) restoreGraphView(frame);
}
