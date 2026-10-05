/* The workbench's power row: the per-plan payback horizon and the overclock-last switch.
 * See docs/planner-payback-horizon_contract.md §7. */

import { button, checkbox, slider } from "./dashkit";
import { count, make } from "./dom";
import { mw, num } from "./format";
import { bench, gesture } from "./planner-core";

import type { SolveResponse } from "./api-shapes";

type Payback = SolveResponse["power"];
type Stop = Payback["stops"][number];

var HOURS = [0, 1, 2, 5, 10, 20, 50, 100];
var STOPS = HOURS.map(hours);

export function hours(h: number): string {
  return num(h, 1) + " h";
}

function nearest(h: number): number {
  var best = 0;
  HOURS.forEach(function (stop, i) {
    if (Math.abs(stop - h) < Math.abs(HOURS[best]! - h)) best = i;
  });
  return best;
}

function stopAt(view: Payback | null, h: number): Stop | undefined {
  return view
    ? view.stops.filter(function (s) {
        return s.hours === h;
      })[0]
    : undefined;
}

function machines(n: number): string {
  return (n > 0 ? "+" : n < 0 ? "−" : "") + count(Math.abs(n)) + (Math.abs(n) === 1 ? " machine" : " machines");
}

export function stopWords(view: Payback | null, h: number): string {
  var stop = stopAt(view, h);
  if (!stop) return hours(h) + (bench.solving ? ": solving…" : "");
  if (!h || !stop.extra_machines) return hours(h) + ": no extra machines · " + count(stop.machines) + " machines · " + mw(stop.mw_draw);
  var parts = ["pays back within " + hours(h), machines(stop.extra_machines), "−" + mw(stop.saved_mw)];
  if (stop.average_payback_h !== null) parts.push("on average " + hours(stop.average_payback_h));
  return parts.join(" · ");
}

function priceWords(view: Payback | null): string {
  if (!view) return "";
  if (view.price_source === "plan") return " at " + num(view.price, 0) + " pts/MWh set on this plan";
  if (!view.price) return "; this grid burns no fuel, so power costs nothing to run";
  return " at the grid mix, " + num(view.price, 0) + " pts/MWh";
}

function shardWords(oc: Payback["overclock"]): string {
  var hand = oc.shards_free === null ? "" : num(oc.shards_free, 0) + " in hand";
  if (oc.shards_free !== null && oc.shards_free < oc.shards && oc.shards_craftable) hand += ", " + num(oc.shards_craftable, 0) + " more from slugs";
  return count(oc.shards) + (oc.shards === 1 ? " shard" : " shards") + (hand ? " (" + hand + ")" : "");
}

export function overclockWords(view: Payback | null): string {
  if (!view) return bench.solving ? "solving…" : "";
  var oc = view.overclock;
  if (!oc.rows.length) {
    if (oc.on && oc.without.length) return "no row got its shards: " + shardWords(oc);
    if (oc.unused.length) return (oc.on ? "unused" : "would not pay") + ": at " + hours(view.hours) + " spreading saves more than one machine fewer";
    return "no row has a fraction for its last machine to carry";
  }
  var parts = [machines(-oc.machines_saved), "+" + mw(oc.extra_mw), shardWords(oc)];
  if (oc.on && oc.without.length) parts.push(count(oc.without.length) + " row(s) went without");
  return (oc.on ? "" : "would save: ") + parts.join(" · ");
}

function overclockRow(body: HTMLElement, view: Payback | null): void {
  var plan = bench.plan!;
  var on = view ? view.overclock.on : plan.args.overclock_last === true;
  var row = make("div", "plan-line plan-overclock");
  var box = checkbox("overclock the last machine", on, function (next) {
    gesture([{ op: "set", field: "overclock_last", value: next }]);
  });
  box.title = "a row of 4.2 machines becomes 3 at 100% and 1 at 120%, using 1–2 Power Shards";
  row.appendChild(box);
  var line = make("span", "plan-sub", overclockWords(view) + (view && view.overclock.inherited ? " · shared default" : ""));
  line.setAttribute("aria-live", "polite");
  row.appendChild(line);
  if (plan.args.overclock_last !== null) row.appendChild(followDefault("overclock_last"));
  body.appendChild(row);
}

function followDefault(field: "payback_hours" | "overclock_last"): HTMLElement {
  return button(
    "use default",
    function () {
      gesture([{ op: "set", field: field, value: null }]);
    },
    { title: "follow the shared setting on the Settings tab again" }
  );
}

export function powerRow(body: HTMLElement): void {
  var plan = bench.plan!;
  var result = bench.result;
  var view = result && result.feasible && result.power.stops.length ? result.power : null;
  var current = view ? view.hours : plan.args.payback_hours === null ? 0 : plan.args.payback_hours;
  var still = !!view && !view.splits;
  var tail = view && view.inherited ? " · shared default" : "";
  var line = make("span", "plan-sub plan-power-line", (still ? view!.reason : stopWords(view, current)) + tail);
  line.setAttribute("aria-live", "polite");
  body.classList.add("plan-stack");
  body.appendChild(make("span", "plan-sub plan-power-hint", "build extra, slower machines when the power they save repays their build points" + priceWords(view)));
  var row = make("div", "plan-line plan-payback");
  row.appendChild(
    slider(
      STOPS,
      nearest(current),
      function (i) {
        if (!still) line.textContent = stopWords(view, HOURS[i]!);
      },
      function (i) {
        if (HOURS[i] !== plan.args.payback_hours) gesture([{ op: "set", field: "payback_hours", value: HOURS[i]! }]);
      },
      { label: "payback horizon in hours of play", ends: ["fewer machines", "less power"], disabled: still, title: "hours of play the saved power must repay the extra machines in" }
    )
  );
  if (plan.args.payback_hours !== null) row.appendChild(followDefault("payback_hours"));
  body.appendChild(row);
  body.appendChild(line);
  overclockRow(body, view);
}
