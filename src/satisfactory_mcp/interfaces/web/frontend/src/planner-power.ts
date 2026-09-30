/* The workbench's power row: the per-plan power-priority slider and what each step costs.
 * See docs/planner-power-priority_contract.md §5. */

import { slider } from "./dashkit";
import { make } from "./dom";
import { count, mw, num } from "./format";
import { bench, gesture } from "./planner-core";

import type { SolveResponse } from "./api-shapes";

type PowerPriority = SolveResponse["power"];
type PowerStep = PowerPriority["steps"][number];

var STOPS = ["100%", "75%", "50%", "33%", "25%"];
var COST_ITEMS = 3;

function cost(step: PowerStep): string {
  var parts = step.cost.slice(0, COST_ITEMS).map(function (c) {
    return "+" + num(c.amount, 0) + " " + c.item;
  });
  if (step.cost.length > COST_ITEMS) parts.push("…");
  return parts.join(", ");
}

export function stepWords(ladder: PowerPriority | null, i: number): string {
  var head = i ? "machines at most " + STOPS[i] : "full clock";
  var step = ladder && ladder.steps[i];
  if (!step) return head + (bench.solving ? ": solving…" : "");
  var parts = [head + ": " + count(step.machines) + " machines" + (step.extra_machines ? " (+" + count(step.extra_machines) + ")" : ""), "draw " + mw(step.mw_draw) + (step.saved_mw > 0.05 ? " (−" + mw(step.saved_mw) + ")" : "")];
  if (step.foundations) parts.push("+" + count(step.foundations) + " foundations");
  if (step.cost.length) parts.push(cost(step));
  return parts.join(" · ");
}

export function powerRow(body: HTMLElement): void {
  var current = bench.plan!.args.power_priority;
  var result = bench.result;
  var ladder = result && result.feasible && result.power.steps.length ? result.power : null;
  var still = !!ladder && !ladder.splits;
  var line = make("span", "plan-sub plan-power-line", still ? "nothing here to split: extractors, generators and somersloop rows keep their count" : stepWords(ladder, current));
  line.setAttribute("aria-live", "polite");
  body.classList.add("plan-stack");
  body.appendChild(make("span", "plan-sub plan-power-hint", "fewer machines at full clock save materials and space; more machines, underclocked, save power"));
  body.appendChild(
    slider(
      STOPS,
      current,
      function (i) {
        if (!still) line.textContent = stepWords(ladder, i);
      },
      function (i) {
        if (i !== bench.plan!.args.power_priority) gesture([{ op: "set", field: "power_priority", value: i }]);
      },
      { label: "power priority: highest machine clock", ends: ["materials", "power"], disabled: still, title: "the highest clock a production machine runs at in this plan" }
    )
  );
  body.appendChild(line);
}
