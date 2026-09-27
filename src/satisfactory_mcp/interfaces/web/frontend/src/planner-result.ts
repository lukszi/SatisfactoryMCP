/* The result panel: what the head solves to, redrawn after every new version. */

import { count, make } from "./dom";
import { mw } from "./format";
import { drawGraph } from "./graph";
import { vitals } from "./panel";
import { bench, changed, gesture, undoRev } from "./planner-core";

import type { GraphEdgeShape, GraphNodeShape } from "./graph";
import type { Ledger, SolveRate, SolveResponse, SolveRow } from "./api-shapes";
import type { Op, Selection } from "./planner-core";

interface PlanNode extends GraphNodeShape {
  tip: string;
}

var drawn: { data: SolveResponse | null; svg: SVGSVGElement | null } = { data: null, svg: null };

export function perMin(value: number): string {
  return count(Math.round(value * 10) / 10) + "/min";
}

function exact(value: number | null | undefined): string {
  return value === null || value === undefined ? "–" : count(Math.round(value * 10) / 10) + " MW";
}

function rates(rows: SolveRate[]): string {
  return (
    rows
      .map(function (r) {
        return r.item + " " + perMin(r.per_min);
      })
      .join(", ") || "–"
  );
}

export function button(text: string, title: string, action: () => void, className?: string): HTMLButtonElement {
  var b = make("button", "dash-map" + (className ? " " + className : ""), text);
  b.type = "button";
  b.title = title;
  b.onclick = function (event) {
    event.stopPropagation();
    action();
  };
  return b;
}

function tile(label: string, value: string, sub: string, bad?: boolean): HTMLElement {
  var box = make("div", "dash-tile" + (bad ? " bad" : ""));
  box.appendChild(make("span", "dash-tile-k", label));
  box.appendChild(make("span", "dash-tile-v", value));
  if (sub) box.appendChild(make("span", "dash-tile-sub", sub));
  return box;
}

export function recipeName(id: string): string {
  var data = bench.result || (bench.feasible && bench.feasible.data);
  var found = data
    ? data.rows.filter(function (r) {
        return r.recipe_id === id;
      })[0]
    : undefined;
  return found ? found.recipe : id;
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

function summary(parent: HTMLElement, data: SolveResponse, rev: number): void {
  var card = make("section", "dash-card");
  var title = make("div", "dash-title");
  title.appendChild(make("h2", "dash-h", "result · v" + rev));
  card.appendChild(title);
  card.appendChild(make("p", "plan-headline" + (data.feasible ? "" : " bad"), data.headline));
  data.warnings.forEach(function (w) {
    card.appendChild(make("p", "plan-warning", "! " + w));
  });
  if (data.blockers.length) {
    card.appendChild(make("p", "plan-warning", "blocked by: " + data.blockers.join(" · ")));
  }
  if (data.feasible) {
    var tiles = make("div", "dash-tiles");
    tiles.appendChild(tile("machines", count(data.machines), data.processes + " processes"));
    tiles.appendChild(tile("draw", exact(data.mw_draw), "generation " + exact(data.mw_generated)));
    tiles.appendChild(tile("net", exact(data.mw_net), data.grid_import ? "draws from the grid" : "self-powered", data.mw_net !== null && data.mw_net < 0));
    tiles.appendChild(tile("exports", String(data.exports.length), rates(data.exports)));
    card.appendChild(tiles);
    var facts: string[] = [];
    if (data.shards !== null) facts.push(data.shards + " power shards");
    if (data.sloops_used) facts.push(data.sloops_used + " somersloops");
    if (data.plan_id) facts.push("plan " + data.plan_id);
    if (facts.length) card.appendChild(make("p", "dash-note", facts.join(" · ")));
  } else {
    data.notes.forEach(function (n) {
      card.appendChild(make("p", "dash-note", n));
    });
  }
  parent.appendChild(card);
}

function signed(value: number | null): string {
  return value === null ? "–" : (value > 0 ? "+" : "") + mw(value);
}

function budgetRow(t: HTMLTableElement, label: string, before: number, net: number | null): void {
  var tr = make("tr");
  var after = net === null ? null : before + net;
  tr.appendChild(make("td", "", label));
  tr.appendChild(make("td", "num" + (before < 0 ? " bad" : ""), signed(before)));
  tr.appendChild(make("td", "num" + (after !== null && after < 0 ? " bad" : ""), signed(after)));
  t.tBodies[0]!.appendChild(tr);
}

function budget(parent: HTMLElement, data: SolveResponse): void {
  var card = make("section", "dash-card");
  card.appendChild(make("h2", "dash-h", "power budget"));
  var ledger: Ledger | null = vitals().circuits ? vitals().circuits!.world : null;
  card.appendChild(make("p", "dash-note", "plan: draw " + exact(data.mw_draw) + " · generation " + exact(data.mw_generated) + " · net " + exact(data.mw_net)));
  if (!ledger) {
    card.appendChild(make("p", "dash-note", vitals().circuitsError || "grid figures loading…"));
  } else {
    var t = make("table", "dash-table");
    var head = make("tr");
    ["grid", "headroom now", "after this plan"].forEach(function (h, i) {
      head.appendChild(make("th", i ? "num" : "", h));
    });
    t.appendChild(make("thead")).appendChild(head);
    t.appendChild(make("tbody"));
    budgetRow(t, "measured", ledger.measured_headroom_mw, data.mw_net);
    budgetRow(t, "nameplate", ledger.headroom_mw, data.mw_net);
    if (data.mw_net === null) card.appendChild(make("p", "dash-note", "the plan's net power is unknown, so the after column is left blank"));
    card.appendChild(t);
    card.appendChild(make("p", "dash-note", "the dashboard's two headroom figures; neither is the answer on its own"));
  }
  parent.appendChild(card);
}

function pick(row: SolveRow, select: (s: Selection) => void): void {
  select({ kind: "process", label: row.building + " · " + row.recipe, ref: row.recipe_id || row.recipe });
}

function buildList(parent: HTMLElement, data: SolveResponse, select: (s: Selection) => void): void {
  var card = make("section", "dash-card");
  card.appendChild(make("h2", "dash-h", "build list · " + data.rows.length + " processes"));
  var t = make("table", "dash-table");
  var head = make("tr");
  ["building", "recipe", "machines", "clock", "MW", "in", "out", ""].forEach(function (h, i) {
    head.appendChild(make("th", i >= 2 && i <= 4 ? "num" : "", h));
  });
  t.appendChild(make("thead")).appendChild(head);
  var tb = t.appendChild(make("tbody"));
  var picked = bench.selection ? bench.selection.ref : "";
  data.rows.forEach(function (row) {
    var tr = make("tr", "go" + (picked && picked === (row.recipe_id || row.recipe) ? " plan-picked" : ""));
    tr.onclick = function () {
      pick(row, select);
    };
    tr.appendChild(make("td", "", row.building));
    var recipe = make("td", "", row.recipe);
    if (row.required) recipe.appendChild(make("span", "plan-tag", "required"));
    tr.appendChild(recipe);
    tr.appendChild(make("td", "num", count(row.machines)));
    tr.appendChild(make("td", "num", Math.round(row.clock * 1000) / 10 + "%"));
    tr.appendChild(make("td", "num", exact(row.mw)));
    tr.appendChild(make("td", "dash-sub", rates(row.inputs)));
    tr.appendChild(make("td", "dash-sub", rates(row.outputs)));
    var acts = make("td");
    var box = make("span", "dash-acts");
    var id = row.recipe_id;
    if (id && !row.required) {
      box.appendChild(
        button("require", "this recipe makes its item; every other recipe for it is excluded", function () {
          gesture(requireOps(id!));
        })
      );
    }
    box.appendChild(
      button("ban", "exclude this recipe from the plan", function () {
        gesture(banOps(id || row.recipe));
      })
    );
    acts.appendChild(box);
    tr.appendChild(acts);
    tb.appendChild(tr);
  });
  var wrap = make("div", "dash-scroll");
  wrap.appendChild(t);
  card.appendChild(wrap);
  parent.appendChild(card);
}

function graphOf(data: SolveResponse): { nodes: PlanNode[]; edges: GraphEdgeShape[] } {
  var nodes: PlanNode[] = [];
  var edges: GraphEdgeShape[] = [];
  var made: Record<string, { id: string; per_min: number }[]> = {};
  var total: Record<string, number> = {};
  data.rows.forEach(function (row, i) {
    var id = "p" + i;
    nodes.push({
      id: id,
      kind: "process",
      label: row.recipe,
      detail: row.building + " ×" + row.machines + " · " + Math.round(row.clock * 100) + "%",
      machines: 0,
      running: 0,
      blocked: 0,
      stopped: 0,
      tip: row.building + " · " + row.recipe + "\nin: " + rates(row.inputs) + "\nout: " + rates(row.outputs),
    });
    row.outputs.forEach(function (o) {
      (made[o.item] = made[o.item] || []).push({ id: id, per_min: o.per_min });
      total[o.item] = (total[o.item] || 0) + o.per_min;
    });
  });
  var inputs: Record<string, boolean> = {};
  var feed = function (item: string, target: string, need: number) {
    var from = made[item];
    if (!from || !total[item]) {
      if (!inputs[item]) {
        inputs[item] = true;
        nodes.push({ id: "in:" + item, kind: "input", label: item, detail: "from outside the plan", machines: 0, running: 0, blocked: 0, stopped: 0, tip: item });
      }
      edges.push({ source: "in:" + item, target: target, item: item, per_min: need });
      return;
    }
    from.forEach(function (f) {
      edges.push({ source: f.id, target: target, item: item, per_min: (need * f.per_min) / total[item]! });
    });
  };
  data.rows.forEach(function (row, i) {
    row.inputs.forEach(function (input) {
      feed(input.item, "p" + i, input.per_min);
    });
  });
  data.exports.forEach(function (e) {
    var id = "ex:" + e.item;
    nodes.push({ id: id, kind: "export", label: e.item, detail: "export " + perMin(e.per_min), machines: 0, running: 0, blocked: 0, stopped: 0, tip: "exported " + perMin(e.per_min) });
    feed(e.item, id, e.per_min);
  });
  return { nodes: nodes, edges: edges };
}

function graphCard(parent: HTMLElement, data: SolveResponse): void {
  var card = make("section", "dash-card dash-graph");
  var title = make("div", "dash-title");
  title.appendChild(make("h2", "dash-h", "production graph"));
  title.appendChild(
    button(bench.graph ? "hide graph" : "graph", "draw this plan's production graph", function () {
      bench.graph = !bench.graph;
      changed();
    })
  );
  card.appendChild(title);
  if (bench.graph && data.rows.length) {
    if (drawn.data !== data) {
      drawn.data = data;
      drawn.svg = drawGraph(
        graphOf(data),
        function (n: PlanNode) {
          return n.tip;
        },
        function () {}
      );
    }
    card.appendChild(drawn.svg!);
    card.appendChild(make("p", "dash-note", "each consumer's need is split over the processes making that item, by their share. Scroll to zoom, drag to pan, double-click to reset."));
  }
  parent.appendChild(card);
}

export function renderResult(parent: HTMLElement, select: (s: Selection) => void): void {
  if (bench.solveError) parent.appendChild(make("p", "dash-note", "the plan could not be solved: " + bench.solveError));
  var data = bench.result;
  if (!data) {
    parent.appendChild(make("p", "dash-note", bench.solving || bench.plan ? "solving…" : ""));
    return;
  }
  if (!data.feasible) {
    summary(parent, data, bench.resultRev);
    var plan = bench.plan;
    if (plan && plan.rev > 1) {
      var head = plan.rev;
      parent.appendChild(
        button("undo last change", "undo v" + head + ", the change that made this plan infeasible", function () {
          undoRev(head);
        })
      );
    }
    var last = bench.feasible;
    if (!last) return;
    var grey = make("div", "plan-stale");
    grey.appendChild(make("p", "dash-note", "last feasible result, v" + last.rev + ":"));
    summary(grey, last.data, last.rev);
    buildList(grey, last.data, select);
    parent.appendChild(grey);
    return;
  }
  var split = make("div", "dash-split");
  summary(split, data, bench.resultRev);
  budget(split, data);
  parent.appendChild(split);
  graphCard(parent, data);
  buildList(parent, data, select);
}
