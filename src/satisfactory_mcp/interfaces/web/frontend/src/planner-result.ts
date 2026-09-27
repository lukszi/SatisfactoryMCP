/* The result panel: what the head solves to, redrawn after every new version. */

import { button, chip, error, loading, table } from "./dashkit";
import { make } from "./dom";
import { count, flow, mw, pct, perMin } from "./format";
import { drawGraph, graphCard as graphFrame, GRAPH_HINT } from "./graph";
import { vitals } from "./panel";
import { bench, changed, gesture, undoRev } from "./planner-core";
import { headroom, LEDGER } from "./powerview";

import type { Column, SortState } from "./dashkit";
import type { GraphEdgeShape, GraphNodeShape } from "./graph";
import type { Ledger, SolveRate, SolveResponse, SolveRow } from "./api-shapes";
import type { Op, Selection } from "./planner-core";

interface PlanNode extends GraphNodeShape {
  tip: string;
}

interface BudgetRow {
  label: string;
  now: number;
  after: number | null;
}

var drawn: { data: SolveResponse | null; svg: HTMLElement | null } = { data: null, svg: null };
var order: SortState = { key: "building", desc: false };

var POWER = "MW";

function rates(rows: SolveRate[]): string {
  return (
    rows
      .map(function (r) {
        return r.item === POWER ? mw(r.per_min) : flow(r.item, r.per_min);
      })
      .join(", ") || "–"
  );
}

export function recipeName(id: string): string {
  return (bench.plan && bench.plan.names[id]) || id;
}

function listed(field: "required" | "banned", member: string): boolean {
  return !!bench.plan && bench.plan.args[field].indexOf(member) >= 0;
}

export function requireOps(id: string): Op[] {
  var ops: Op[] = [{ op: "add", field: "required", member: id }];
  if (listed("banned", id)) ops.push({ op: "remove", field: "banned", member: id });
  return ops;
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
    var head = make("p", "plan-headline bad", "not solvable: " + data.cause);
    body.appendChild(head);
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

function pick(row: SolveRow, select: (s: Selection) => void): void {
  select({ kind: "process", label: row.building + " · " + row.recipe, ref: row.recipe_id || row.recipe });
}

function actions(row: SolveRow): HTMLElement {
  var box = make("span", "dash-acts");
  var id = row.recipe_id;
  if (id && !row.required) {
    box.appendChild(
      button(
        "require",
        function () {
          gesture(requireOps(id!));
        },
        { title: "make every " + (row.item || "output") + " with this recipe", label: "require " + row.recipe }
      )
    );
  }
  box.appendChild(
    button(
      "ban",
      function () {
        gesture(banOps(id || row.recipe));
      },
      { title: "exclude this recipe from the plan", label: "ban " + row.recipe }
    )
  );
  return box;
}

function recipeCell(row: SolveRow): HTMLElement {
  var cell = make("span", "", row.recipe);
  if (row.required) cell.appendChild(chip("required", "muted"));
  return cell;
}

function buildList(parent: HTMLElement, data: SolveResponse, select: (s: Selection) => void, live: boolean): void {
  var card = make("section", "dash-card");
  card.appendChild(make("h2", "dash-h", "build list · " + count(data.rows.length) + " processes"));
  var picked = bench.selection ? bench.selection.ref : "";
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
      render: recipeCell,
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
        return pct(r.clock, 1);
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
  if (live && !bench.gone) columns.push({ key: "acts", label: "", render: actions });
  card.appendChild(
    table(columns, data.rows, {
      sort: order,
      onSort: changed,
      onRow: function (row) {
        pick(row, select);
      },
      rowClass: function (row) {
        return picked && picked === (row.recipe_id || row.recipe) ? "plan-picked" : "";
      },
      caption: "build list",
    })
  );
  parent.appendChild(card);
}


function items(rows: SolveRate[]): SolveRate[] {
  return rows.filter(function (r) {
    return r.item !== POWER;
  });
}

function graphOf(data: SolveResponse): { nodes: PlanNode[]; edges: GraphEdgeShape[] } {
  var nodes: PlanNode[] = [];
  var edges: GraphEdgeShape[] = [];
  var made: Record<string, { id: string; per_min: number }[]> = {};
  var total: Record<string, number> = {};
  data.rows.forEach(function (row, i) {
    var id = "p" + i;
    var power = row.mw ? " · " + mw(row.mw, { signed: true }) : "";
    var at = " · " + pct(row.clock, 1);
    nodes.push({
      id: id,
      kind: "process",
      label: row.recipe,
      detail: row.building + " ×" + count(row.machines) + power + at,
      machines: 0,
      running: 0,
      blocked: 0,
      stopped: 0,
      tip: row.building + " · " + row.recipe + at + power + "\nin: " + rates(items(row.inputs)) + "\nout: " + rates(items(row.outputs)),
    });
    var out = items(row.outputs);
    if (row.mw > 0) out.push({ item: POWER, per_min: row.mw });
    out.forEach(function (o) {
      (made[o.item] = made[o.item] || []).push({ id: id, per_min: o.per_min });
      total[o.item] = (total[o.item] || 0) + o.per_min;
    });
  });
  var inputs: Record<string, boolean> = {};
  var feed = function (item: string, target: string, need: number) {
    var from = made[item];
    if (!from || !total[item]) {
      if (item === POWER) return;
      if (!inputs[item]) {
        inputs[item] = true;
        nodes.push({ id: "in:" + item, kind: "input", label: item, detail: "from outside the plan", machines: 0, running: 0, blocked: 0, stopped: 0, tip: item });
      }
      edges.push({ source: "in:" + item, target: target, item: item, per_min: need });
      return;
    }
    from.forEach(function (f) {
      var share = (need * f.per_min) / total[item]!;
      edges.push({ source: f.id, target: target, item: item, per_min: share, text: item === POWER ? mw(share) : undefined });
    });
  };
  data.rows.forEach(function (row, i) {
    items(row.inputs).forEach(function (input) {
      feed(input.item, "p" + i, input.per_min);
    });
  });
  data.exports.forEach(function (e) {
    var id = "ex:" + e.item;
    var amount = e.item === POWER ? mw(e.per_min) : perMin(e.per_min);
    nodes.push({ id: id, kind: "export", label: e.item === POWER ? "power" : e.item, detail: "exported " + amount, machines: 0, running: 0, blocked: 0, stopped: 0, tip: "exported " + amount });
    feed(e.item, id, e.per_min);
  });
  return { nodes: nodes, edges: edges };
}

function graphCard(parent: HTMLElement, data: SolveResponse): void {
  var card = graphFrame("production graph", bench.graph, function () {
    bench.graph = !bench.graph;
    changed();
  });
  if (bench.graph && data.rows.length) {
    if (drawn.data !== data) {
      drawn.data = data;
      drawn.svg = drawGraph(graphOf(data), function (n: PlanNode) {
        return n.tip;
      });
    }
    card.appendChild(drawn.svg!);
    card.appendChild(make("p", "dash-note", "rates split over each item's producers by share · " + GRAPH_HINT));
  }
  parent.appendChild(card);
}

export function renderVersionResult(parent: HTMLElement, data: SolveResponse, rev: number, select: (s: Selection) => void): void {
  summary(parent, data, rev, false);
  if (data.feasible) buildList(parent, data, select, false);
}

export function renderResult(parent: HTMLElement, select: (s: Selection) => void): void {
  if (bench.solveError) error(parent, "the result", bench.solveError);
  var data = bench.result;
  if (!data) {
    if (!bench.solveError) loading(parent, "the result");
    return;
  }
  summary(parent, data, bench.resultRev, true);
  if (!data.feasible) {
    var last = bench.feasible;
    if (!last) return;
    var grey = make("div", "plan-stale");
    grey.appendChild(make("p", "dash-note", "last solvable version, v" + last.rev + ":"));
    summary(grey, last.data, last.rev, false);
    buildList(grey, last.data, select, false);
    parent.appendChild(grey);
    return;
  }
  var rest = make("div", bench.solving && bench.solving !== bench.resultRev ? "plan-stale" : "");
  graphCard(rest, data);
  buildList(rest, data, select, true);
  parent.appendChild(rest);
}
