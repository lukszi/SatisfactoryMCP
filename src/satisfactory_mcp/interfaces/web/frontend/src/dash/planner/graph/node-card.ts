/* The card under the graph for the picked node: a process's facts and row actions, an item's
 * recipes, and for an export its minimum rate, edited in place. */

import { make } from "../../../kit/dom";
import { count, mw, pct } from "../../../kit/format";
import { askButton } from "../../../chat/asks";
import { recipesButton } from "../alternates";
import { banButton, clockText, copyIdButton, pinProcessButton, processAsk } from "../row-actions";
import { bench } from "../state";
import { applyOps } from "../writes";
import { tipLines } from "./interact";

import type { SolveResponse, SolveRow } from "../../../api/shapes";
import type { Op } from "../state";
import type { PlanGraphView, PlanNode } from "./model";

/** What an export rate box commits: blank drops the minimum, a positive number sets it. */
export function exportRateOps(item: string, raw: string, had: boolean): Op[] | null {
  const text = raw.trim();
  if (!text) return had ? [{ op: "del", field: "export_minimums", item: item }] : [];
  const n = Number(text);
  if (!(n > 0) || !isFinite(n)) return null;
  return [{ op: "put", field: "export_minimums", item: item, value: n }];
}

function exportItem(node: PlanNode): string | null {
  if (node.itemId === "power" || (!node.itemId && node.label === "power")) return "MW";
  return node.itemId;
}

function rateEditor(node: PlanNode): HTMLElement | null {
  const item = exportItem(node);
  if (!item || !bench.plan || bench.gone) return null;
  const minimums = bench.plan.args.export_minimums;
  const had = item in minimums;
  const line = make("label", "plan-export pg-rate");
  line.appendChild(make("span", "dash-muted", "at least"));
  const box = make("input", "dash-number");
  box.type = "number";
  box.min = "0";
  box.step = "any";
  box.placeholder = "any";
  box.value = had ? String(minimums[item]) : "";
  box.setAttribute("aria-label", "minimum " + node.label + " per minute");
  box.addEventListener("change", function () {
    const ops = exportRateOps(item, box.value, had);
    if (ops === null) {
      box.setCustomValidity("a rate is a positive number per minute, or blank for any");
      box.reportValidity();
      return;
    }
    box.setCustomValidity("");
    if (ops.length) applyOps(ops);
  });
  line.appendChild(box);
  line.appendChild(make("span", "dash-muted", item === "MW" ? "MW" : "/min"));
  return line;
}

function rowFor(data: SolveResponse, node: PlanNode): SolveRow | undefined {
  return data.rows.filter(function (r) {
    return r.id === node.row;
  })[0];
}

function processCard(card: HTMLElement, acts: HTMLElement, node: PlanNode, row: SolveRow, view: PlanGraphView): void {
  const power = node.mw ? " · " + mw(node.mw, { signed: true }) : "";
  const clock = node.rows.length > 1 ? pct(node.clock, 1) + " mean" : clockText(row);
  card.appendChild(make("p", "plan-facts", (node.building || row.building) + " ×" + count(node.built) + " · " + clock + power));
  tipLines(view, node)
    .slice(2)
    .forEach(function (line) {
      card.appendChild(make("p", "dash-sub", line));
    });
  if (!bench.gone) {
    if (node.itemId && row.recipe_id) acts.appendChild(recipesButton(node.itemId, row.item || row.recipe, "node"));
    acts.appendChild(banButton(row));
    const pin = pinProcessButton(row);
    if (pin) acts.appendChild(pin);
  }
  if (row.recipe_id) acts.appendChild(copyIdButton(row.recipe_id, row.recipe));
  if (!bench.gone) acts.appendChild(processAsk(row, "node"));
}

export function nodeCard(parent: HTMLElement, data: SolveResponse, view: PlanGraphView, node: PlanNode): void {
  const card = make("section", "dash-card plan-node");
  card.setAttribute("aria-label", "picked: " + node.label);
  const row = rowFor(data, node);
  const title = make("div", "dash-title");
  title.appendChild(make("h2", "dash-h", node.kind === "process" ? node.recipeName || node.label : node.itemName || node.label));
  card.appendChild(title);
  const acts = make("div", "dash-acts");
  if (row) processCard(card, acts, node, row, view);
  else {
    card.appendChild(make("p", "plan-facts", tipLines(view, node).slice(1).join(" · ") || node.detail));
    if (node.kind === "export") {
      const edit = rateEditor(node);
      if (edit) card.appendChild(edit);
    }
    if (node.itemId && !bench.gone) acts.appendChild(recipesButton(node.itemId, node.itemName || node.label, "node"));
    if (!bench.gone) {
      const about = { kind: "item", label: node.label, ref: node.itemId || node.id, plan: bench.key, rev: bench.plan ? bench.plan.rev : null };
      acts.appendChild(askButton(about, "node:" + node.id));
    }
  }
  card.appendChild(acts);
  parent.appendChild(card);
}
