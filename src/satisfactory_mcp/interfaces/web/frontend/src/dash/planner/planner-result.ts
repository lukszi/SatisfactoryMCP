/* The result panel: what the head solves to, as a build list or a graph, redrawn after every
 * new version. See docs/planner-p3_contract.md §9. */

import { button, chip, copyButton, error, idChip, loading, subTabs, table } from "../../kit/dashkit";
import { make } from "../../kit/dom";
import { count, flow, mw, pct } from "../../kit/format";
import { drawGraph, graphCard as graphFrame, GRAPH_HINT, setPicked } from "../graph";
import { dashParts, go } from "../../app/nav";
import { vitals } from "../../app/vitals";
import { renderAlternates } from "./planner-alternates";
import { askButton, askMarks } from "../../chat/asks";
import { bench, changed, gesture, pendingFocus, pickTab, showAlternates, undoRev } from "./planner-core";
import { rowOverclock } from "./planner-power";
import { renderSite } from "./planner-site";
import { renderTrack } from "./planner-track";
import { pinsFor, pinThis } from "../../chat/pins";
import { headroom, LEDGER } from "../power-ledger";
import { WORDS } from "../../kit/words";

import type { Column, SortState } from "../../kit/dashkit";
import type { GraphNodeShape } from "../graph";
import type { Ledger, PlanGraphNode, SolveRate, SolveResponse, SolveRow } from "../../api/shapes";
import type { Op, ResultTab, Selection } from "./planner-core";

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

var drawn = { data: null as SolveResponse | null, key: "", frame: null as HTMLElement | null, x: 0, y: 0, focus: "" };
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
  var pick = live && !bench.gone ? rowOverclock(row) : null;
  if (!pick) return clockText(row);
  var cell = make("span", "plan-clock");
  cell.appendChild(make("span", "", clockText(row)));
  cell.appendChild(pick);
  return cell;
}

function rates(rows: SolveRate[]): string {
  return (
    rows
      .map(function (r) {
        return r.item === POWER ? mw(r.per_min) : flow(r.item, r.per_min);
      })
      .join(", ") || "–"
  );
}

function items(rows: SolveRate[]): SolveRate[] {
  return rows.filter(function (r) {
    return r.item !== POWER;
  });
}

export function recipeName(id: string): string {
  return (bench.plan && bench.plan.names[id]) || id;
}

function listed(field: "required" | "banned", member: string): boolean {
  return !!bench.plan && bench.plan.args[field].indexOf(member) >= 0;
}

export function banOps(member: string): Op[] {
  var ops: Op[] = [{ op: "add", field: "banned", member: member }];
  if (listed("required", member)) ops.push({ op: "remove", field: "required", member: member });
  return ops;
}

function facts(data: SolveResponse): string {
  var net = data.mw_net;
  var parts = [
    count(data.machines) + " machines in " + count(data.processes) + " processes",
    "draw " + (data.mw_draw === null ? "–" : mw(data.mw_draw)),
    "generation " + (data.mw_generated === null ? "–" : mw(data.mw_generated)),
    "net " + (net === null ? "–" : headroom(net)) + (data.grid_import ? ", from the grid" : ", self-powered"),
    "exports " + rates(data.exports),
  ];
  if (data.shards) parts.push(count(data.shards) + " power shards");
  if (data.sloops_used) parts.push(count(data.sloops_used) + " somersloops");
  return parts.join(" · ");
}

function budget(parent: HTMLElement, data: SolveResponse): void {
  var ledger: Ledger | null = vitals().circuits ? vitals().circuits!.world : null;
  if (!ledger) {
    if (vitals().circuitsError) error(parent, "the grid figures", vitals().circuitsError);
    else loading(parent, "grid figures");
    return;
  }
  var net = data.mw_net;
  var rows: BudgetRow[] = [
    { label: LEDGER.headroomNow, now: ledger.measured_headroom_mw, after: net === null ? null : ledger.measured_headroom_mw + net },
    { label: LEDGER.headroomFull, now: ledger.headroom_mw, after: net === null ? null : ledger.headroom_mw + net },
  ];
  var columns: Column<BudgetRow>[] = [
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
  var box = make("div", "plan-budget");
  box.appendChild(table(columns, rows, { caption: "power budget" }));
  parent.appendChild(box);
}

function undoButton(parent: HTMLElement): void {
  var plan = bench.plan;
  if (!plan || plan.rev <= 1 || bench.gone) return;
  var head = plan.rev;
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

function summary(parent: HTMLElement, data: SolveResponse, rev: number, live: boolean): void {
  var card = make("section", "dash-card");
  var title = make("div", "dash-title");
  title.appendChild(make("h2", "dash-h", "result · v" + rev));
  var waiting = live && bench.solving && bench.solving !== rev;
  if (waiting) title.appendChild(make("span", "plan-status", "solving v" + bench.solving + "…"));
  card.appendChild(title);
  var body = make("div", waiting ? "plan-stale" : "");
  card.appendChild(body);
  if (!data.feasible) {
    body.appendChild(make("p", "plan-headline bad", "not solvable: " + data.cause));
    if (live) undoButton(body);
  } else {
    body.appendChild(make("p", "plan-facts", facts(data)));
  }
  data.warnings.forEach(function (w) {
    body.appendChild(make("p", "plan-warning", w));
  });
  if (data.feasible && live) budget(body, data);
  parent.appendChild(card);
}

function mainItem(data: SolveResponse, row: SolveRow): string | null {
  var node = data.graph.nodes.filter(function (n) {
    return n.row === row.id;
  })[0];
  return node ? node.item : null;
}

WIDE.addEventListener("change", function () {
  if (bench.alt) changed();
});

function pickRow(row: SolveRow, select: (s: Selection) => void): void {
  bench.picked = row.id;
  select({ kind: "process", label: row.building + " · " + row.recipe, ref: row.recipe_id || row.recipe });
}

export function recipesButton(item: string, name: string, where: string): HTMLButtonElement {
  var ctl = "alt:" + where + ":" + item;
  var open = !!bench.alt && bench.alt.item === item;
  var b = button(
    WORDS.recipes,
    function () {
      var switching = dashParts().rest[1] === "alt";
      showAlternates(item, ctl);
      if (!switching) bench.altBack = true;
      go("planner/" + bench.key + "/alt/" + item, switching);
    },
    { title: "every recipe for " + name + " and what requiring each would change", label: "recipes for " + name }
  );
  b.setAttribute("data-ctl", ctl);
  b.setAttribute("aria-expanded", String(open));
  return b;
}

function banButton(row: SolveRow): HTMLButtonElement {
  return button(
    "ban",
    function () {
      var member = row.recipe_id || row.recipe;
      pendingFocus.ctl = "banned:" + member;
      pendingFocus.until = Date.now() + 5000;
      gesture(banOps(member));
    },
    { title: "keep this recipe out of the plan", label: "ban " + row.recipe }
  );
}

function pinButton(row: SolveRow): HTMLButtonElement | null {
  var id = row.recipe_id;
  if (!id) return null;
  return button(
    WORDS.pin,
    function () {
      pinThis("process", { plan: bench.key, recipe: id! });
    },
    { title: "pin this process and copy its pin:N for chat", label: "pin " + row.recipe }
  );
}

function copyFor(text: string, what: string): HTMLButtonElement {
  return copyButton(text, "copy", { title: "copy " + text + " for a tool call", label: "copy " + what });
}

function rowActions(data: SolveResponse, row: SolveRow): HTMLElement {
  var box = make("span", "dash-acts");
  var item = mainItem(data, row);
  if (item && row.recipe_id) box.appendChild(recipesButton(item, row.item || row.recipe, "row"));
  box.appendChild(banButton(row));
  var pin = pinButton(row);
  if (pin) box.appendChild(pin);
  box.appendChild(processAsk(row, "row"));
  return box;
}

function processAsk(row: SolveRow, where: string): HTMLButtonElement {
  var ref = row.recipe_id || row.recipe;
  return askButton({ kind: "process", label: row.building + " · " + row.recipe, ref: ref, plan: bench.key, rev: bench.plan ? bench.plan.rev : null }, where + ":" + row.id);
}

function recipeCell(row: SolveRow, live: boolean): HTMLElement {
  var cell = make("span", "plan-recipe", row.recipe);
  if (row.required) cell.appendChild(chip("required", "muted"));
  if (!live) return cell;
  if (bench.chatRows[row.id]) cell.appendChild(chip(WORDS.actorChat, "muted", "chat changed this process since you opened the plan"));
  var pinned = row.recipe_id ? pinsFor(bench.key)[row.recipe_id] : undefined;
  if (pinned) cell.appendChild(idChip(pinned.id, pinned.text));
  askMarks(bench.key, "process", row.recipe_id || row.recipe).forEach(function (a) {
    cell.appendChild(idChip(a.id, a.text));
  });
  return cell;
}

function buildList(parent: HTMLElement, data: SolveResponse, select: (s: Selection) => void, live: boolean): void {
  var card = make("section", "dash-card");
  card.appendChild(make("h2", "dash-h", "build list · " + count(data.rows.length) + " processes"));
  var columns: Column<SolveRow>[] = [
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
        return rates(r.inputs);
      },
    },
    {
      key: "out",
      label: "out",
      className: "dash-sub",
      render: function (r) {
        return rates(r.outputs);
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

function flashing(): string[] {
  var now = Date.now();
  Object.keys(flashed).forEach(function (id) {
    if (!bench.chatRows[id]) delete flashed[id];
  });
  Object.keys(bench.chatRows).forEach(function (id) {
    if (!flashed[id]) flashed[id] = now;
  });
  return Object.keys(flashed).filter(function (id) {
    return now - flashed[id]! < FLASH_MS;
  });
}

function planNodes(data: SolveResponse): PlanNode[] {
  var byId: Record<string, SolveRow> = {};
  data.rows.forEach(function (r) {
    byId[r.id] = r;
  });
  var pins = pinsFor(bench.key);
  return data.graph.nodes.map(function (n: PlanGraphNode): PlanNode {
    var row = n.row ? byId[n.row] : undefined;
    var badges: string[] = [];
    if (row && bench.chatRows[row.id]) badges.push(WORDS.actorChat);
    if (row && row.recipe_id && pins[row.recipe_id]) badges.push(pins[row.recipe_id]!.id);
    var tip = row
      ? row.building + " · " + row.recipe + "\nin: " + rates(items(row.inputs)) + "\nout: " + rates(items(row.outputs))
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

function pickNode(data: SolveResponse, node: PlanNode, select: (s: Selection) => void): void {
  var row = data.rows.filter(function (r) {
    return r.id === node.row;
  })[0];
  if (row) {
    pickRow(row, select);
    return;
  }
  bench.picked = node.id;
  select({ kind: "item", label: node.label, ref: node.item || "" });
}

function graphFrameFor(data: SolveResponse, select: (s: Selection) => void): HTMLElement {
  var nodes = planNodes(data);
  var flash = flashing();
  var key =
    nodes
      .map(function (n) {
        return n.id + "=" + (n.badges || []).join("+");
      })
      .join("|") +
    "#" +
    flash.join("|");
  if (drawn.data !== data || drawn.key !== key || !drawn.frame) {
    var frame = drawGraph(
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
    if (drawn.data !== data) {
      drawn.x = 0;
      drawn.y = 0;
    }
    frame.addEventListener("scroll", function () {
      if (!frame.isConnected) return;
      drawn.x = frame.scrollLeft;
      drawn.y = frame.scrollTop;
    });
    frame.addEventListener("focusin", function (event) {
      var node = (event.target as Element).closest(".graph-node");
      drawn.focus = node ? node.getAttribute("data-node") || "" : "";
    });
    frame.addEventListener("focusout", function () {
      setTimeout(function () {
        if (frame.isConnected && !frame.contains(document.activeElement)) drawn.focus = "";
      }, 0);
    });
    drawn.data = data;
    drawn.key = key;
    drawn.frame = frame;
    if (flash.length) setTimeout(changed, FLASH_MS);
  } else setPicked(drawn.frame, bench.picked);
  return drawn.frame;
}

function nodeCard(parent: HTMLElement, data: SolveResponse): void {
  var picked = bench.picked;
  var node = picked
    ? data.graph.nodes.filter(function (n) {
        return n.id === picked;
      })[0]
    : undefined;
  if (!node) return;
  var card = make("section", "dash-card plan-node");
  card.setAttribute("aria-label", "picked: " + node.label);
  var row = data.rows.filter(function (r) {
    return r.id === node!.row;
  })[0];
  var title = make("div", "dash-title");
  title.appendChild(make("h2", "dash-h", row ? row.recipe : node.label));
  var acts = make("div", "dash-acts");
  if (row) {
    var power = row.mw ? " · " + mw(row.mw, { signed: true }) : "";
    card.appendChild(title);
    card.appendChild(make("p", "plan-facts", row.building + " ×" + count(row.machines) + " · " + clockText(row) + power));
    card.appendChild(make("p", "dash-sub", "in: " + rates(items(row.inputs))));
    card.appendChild(make("p", "dash-sub", "out: " + rates(items(row.outputs))));
    if (!bench.gone) {
      if (node.item && row.recipe_id) acts.appendChild(recipesButton(node.item, row.item || row.recipe, "node"));
      acts.appendChild(banButton(row));
      var pin = pinButton(row);
      if (pin) acts.appendChild(pin);
    }
    if (row.recipe_id) acts.appendChild(copyFor(row.recipe_id, row.recipe));
    if (!bench.gone) acts.appendChild(processAsk(row, "node"));
  } else {
    card.appendChild(title);
    card.appendChild(make("p", "plan-facts", node.detail));
    if (node.item && !bench.gone) acts.appendChild(recipesButton(node.item, node.label, "node"));
    if (!bench.gone) {
      var about = { kind: "item", label: node.label, ref: node.item || node.id, plan: bench.key, rev: bench.plan ? bench.plan.rev : null };
      acts.appendChild(askButton(about, "node:" + node.id));
    }
  }
  card.appendChild(acts);
  parent.appendChild(card);
}

function graphTab(parent: HTMLElement, data: SolveResponse, select: (s: Selection) => void): HTMLElement | null {
  var card = graphFrame("production graph", true);
  parent.appendChild(card);
  if (!data.graph.nodes.length) {
    card.appendChild(make("p", "dash-note", "nothing to draw"));
    return null;
  }
  var frame = graphFrameFor(data, select);
  card.appendChild(frame);
  card.appendChild(make("p", "dash-note", "click a process for its recipes · rates split over each item's producers by share · " + GRAPH_HINT));
  nodeCard(parent, data);
  return frame;
}

export function renderVersionResult(parent: HTMLElement, data: SolveResponse, rev: number, select: (s: Selection) => void): void {
  summary(parent, data, rev, false);
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

export function renderResult(parent: HTMLElement, select: (s: Selection) => void, close: () => void): void {
  if (bench.tab === "site") {
    resultTabs(parent);
    renderSite(parent);
    return;
  }
  if (bench.solveError) error(parent, "the result", bench.solveError);
  var data = bench.result;
  if (!data) {
    if (!bench.solveError) loading(parent, "the result");
    return;
  }
  summary(parent, data, bench.resultRev, true);
  if (!data.feasible) {
    var last = bench.feasible;
    if (bench.alt) renderAlternates(parent, close);
    if (!last) return;
    var grey = make("div", "plan-stale");
    if (bench.tab === "track") grey.appendChild(make("p", "dash-note", "track needs a solvable version"));
    grey.appendChild(make("p", "dash-note", "last solvable version, v" + last.rev + ":"));
    summary(grey, last.data, last.rev, false);
    buildList(grey, last.data, select, false);
    parent.appendChild(grey);
    return;
  }
  resultTabs(parent);
  var beside = !!bench.alt && bench.tab === "graph" && WIDE.matches;
  var split = make("div", "plan-result" + (beside ? " beside" : ""));
  var dim = bench.tab !== "track" && bench.solving && bench.solving !== bench.resultRev;
  var main = make("div", "plan-main" + (dim ? " plan-stale" : ""));
  if (bench.alt && !beside) renderAlternates(split, close);
  split.appendChild(main);
  if (bench.alt && beside) renderAlternates(split, close);
  var frame = bench.tab === "graph" ? graphTab(main, data, select) : null;
  if (bench.tab === "track") renderTrack(main, select);
  else if (bench.tab !== "graph") buildList(main, data, select, true);
  parent.appendChild(split);
  if (!frame) return;
  frame.scrollLeft = drawn.x;
  frame.scrollTop = drawn.y;
  var lost = !document.activeElement || document.activeElement === document.body;
  var again = drawn.focus && lost ? frame.querySelector<SVGGElement>('[data-node="' + CSS.escape(drawn.focus) + '"]') : null;
  if (again) again.focus({ preventScroll: true });
}
