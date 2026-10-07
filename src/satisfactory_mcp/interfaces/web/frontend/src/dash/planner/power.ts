/* The workbench's power row: the per-plan payback horizon and the overclock-last switch, and
 * each build-list row's own overclock choice. See docs/planner-payback-horizon_contract.md §7. */

import { button, checkbox, selectBox, slider } from "../../kit/dashkit";
import { make } from "../../kit/dom";
import { count, formatNumber, mw, pct } from "../../kit/format";
import { bench } from "./state";
import { applyOps } from "./writes";

import type { SolveResponse, SolveRow } from "../../api/shapes";
import type { Op } from "./state";

type Payback = SolveResponse["power"];
type Stop = Payback["stops"][number];
type OverclockOption = NonNullable<SolveRow["overclock_option"]>;

/* The recipes before a slider release, kept until the re-solve lands (F5a). */
let switched = { key: "", from: -1, before: {} as Record<string, string>, rev: 0, text: "" };

const PAYBACK_HOURS = [0, 1, 2, 5, 10, 20, 50, 100];
const PAYBACK_LABELS = PAYBACK_HOURS.map(hours);

export function hours(h: number): string {
  return formatNumber(h, 1) + " h";
}

function nearestStopIndex(h: number): number {
  let best = 0;
  PAYBACK_HOURS.forEach(function (stop, i) {
    if (Math.abs(stop - h) < Math.abs(PAYBACK_HOURS[best]! - h)) best = i;
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

function signedMachineCount(n: number): string {
  return (n > 0 ? "+" : n < 0 ? "−" : "") + count(Math.abs(n)) + (Math.abs(n) === 1 ? " machine" : " machines");
}

function stopWords(view: Payback | null, h: number): string {
  const stop = stopAt(view, h);
  if (!stop) return hours(h) + (bench.solvingRev ? ": solving…" : "");
  if (!h || !stop.extra_machines) return hours(h) + ": no extra machines · " + count(stop.machines) + " machines · " + mw(stop.mw_draw);
  const parts = ["pays back within " + hours(h), signedMachineCount(stop.extra_machines), (stop.saved_mw < 0 ? "+" : "−") + mw(Math.abs(stop.saved_mw))];
  if (stop.average_payback_h !== null) parts.push("on average " + hours(stop.average_payback_h));
  return parts.join(" · ");
}

function priceWords(view: Payback | null): string {
  if (!view) return "";
  if (view.price_source === "plan") return " at " + formatNumber(view.price, 0) + " pts/MWh set on this plan";
  if (!view.price) return "; this grid burns no fuel, so power costs nothing to run";
  return " at the grid mix, " + formatNumber(view.price, 0) + " pts/MWh";
}

type OverclockRow = Payback["overclock"]["rows"][number];

function shardWords(oc: Payback["overclock"], shards: number): string {
  const hand = oc.shards_free === null ? "" : formatNumber(oc.shards_free, 0) + " in hand + " + formatNumber(oc.shards_craftable || 0, 0) + " craftable";
  return count(shards) + (shards === 1 ? " shard" : " shards") + (hand ? " (" + hand + ")" : "");
}

function overclockTally(oc: Payback["overclock"], rows: OverclockRow[], stock: boolean): string {
  let shards = 0;
  let saved = 0;
  let extra = 0;
  rows.forEach(function (r) {
    shards += r.shards;
    saved += r.instead - r.machines;
    extra += r.extra_mw;
  });
  const bill = stock ? shardWords(oc, shards) : count(shards) + (shards === 1 ? " shard" : " shards");
  return [signedMachineCount(-saved), "+" + mw(extra), bill].join(" · ");
}

function overclockWords(view: Payback | null): string {
  if (!view) return bench.solvingRev ? "solving…" : "";
  const oc = view.overclock;
  const own = oc.pinned_last ? count(oc.pinned_last) + " row(s) overclocked by their own setting" : "";
  if (!oc.rows.length) {
    if ((oc.on || oc.pinned_last) && oc.without.length) return "no row got its shards: " + shardWords(oc, 0);
    if (oc.unused.length) return (oc.on ? "unused" : "would not pay") + ": at " + hours(view.hours) + " spreading saves more than one machine fewer";
    return "no row has a fraction for its last machine to carry";
  }
  const built = oc.rows.filter(function (r) {
    return r.applied;
  });
  const spare = oc.rows.filter(function (r) {
    return !r.applied;
  });
  const parts: string[] = [];
  if (oc.on) parts.push(overclockTally(oc, built, true));
  else {
    if (built.length) parts.push(own + ": " + overclockTally(oc, built, true));
    if (spare.length) parts.push((built.length ? "the switch would save " : "would save: ") + overclockTally(oc, spare, !built.length));
  }
  if ((oc.on || oc.pinned_last) && oc.without.length) parts.push(count(oc.without.length) + " row(s) went without");
  if (oc.pinned_spread) parts.push(count(oc.pinned_spread) + " row(s) set to one more machine");
  return parts.join(" · ");
}

function overclockRow(body: HTMLElement, view: Payback | null): void {
  const plan = bench.plan!;
  const on = view ? view.overclock.on : plan.args.overclock_last === true;
  const row = make("div", "plan-line plan-overclock");
  const box = checkbox("overclock the last machine", on, function (next) {
    applyOps([{ op: "set", field: "overclock_last", value: next }]);
  });
  box.title = "a row of 4.2 machines becomes 3 at 100% and 1 at 120%, using 1–2 Power Shards";
  row.appendChild(box);
  const line = make("span", "plan-sub", overclockWords(view) + (view?.overclock.inherited ? " · shared default" : ""));
  line.setAttribute("aria-live", "polite");
  row.appendChild(line);
  if (plan.args.overclock_last !== null) row.appendChild(followDefault("overclock_last"));
  body.appendChild(row);
}

function followDefault(field: "payback_hours" | "overclock_last"): HTMLElement {
  return button(
    "use default",
    function () {
      applyOps([{ op: "set", field: field, value: null }]);
    },
    { title: "follow the shared setting on the Settings tab again" }
  );
}

function recipeNamesById(data: SolveResponse | null): Record<string, string> {
  const out: Record<string, string> = {};
  if (data?.feasible)
    data.rows.forEach(function (r) {
      out[r.recipe_id || r.recipe] = r.recipe;
    });
  return out;
}

function namesOnlyIn(a: Record<string, string>, b: Record<string, string>): string[] {
  return Object.keys(a)
    .filter(function (id) {
      return !(id in b);
    })
    .map(function (id) {
      return a[id]!;
    })
    .sort();
}

function switchWords(before: Record<string, string>, after: Record<string, string>): string {
  const added = namesOnlyIn(after, before);
  const dropped = namesOnlyIn(before, after);
  if (!added.length) return dropped.length ? "dropped " + dropped.join(", ") : "";
  return "switched to " + added.join(", ") + (dropped.length ? ", from " + dropped.join(", ") : "");
}

function snapshotRecipes(): void {
  switched = { key: bench.key, from: bench.resultRev, before: recipeNamesById(bench.result), rev: 0, text: "" };
}

function switchLine(): string {
  if (switched.key !== bench.key) return "";
  if (switched.from >= 0 && bench.resultRev > switched.from && bench.result) {
    switched.text = bench.result.feasible ? switchWords(switched.before, recipeNamesById(bench.result)) : "";
    switched.rev = bench.resultRev;
    switched.from = -1;
  }
  return switched.rev === bench.resultRev ? switched.text : "";
}

function optionWords(o: OverclockOption): string {
  const last = count(o.machines) + (o.machines === 1 ? " machine" : " machines") + ", the last at " + pct(o.last_clock, 1) + ", " + count(o.shards) + (o.shards === 1 ? " shard" : " shards");
  const spread = count(o.spread_machines) + " at " + pct(o.spread_clock, 1);
  return "overclock last: " + last + " (+" + mw(o.extra_mw) + ") · one more underclocked: " + spread;
}

/* A build-list row's own choice: overclock its last machine, or one more underclocked machine,
 * whatever the plan's switch says; the "plan: …" option drops the choice. */
export function rowOverclock(row: SolveRow): HTMLElement | null {
  const o = row.overclock_option;
  const id = row.recipe_id;
  if (!o || !id) return null;
  const follows = o.pinned === null;
  let plan = o.applied && follows ? "plan: overclock last" : "plan: one more machine";
  if (follows && o.without) plan = "plan: no shards left";
  const pick = selectBox(
    [
      ["", plan],
      ["last", "overclock last"],
      ["spread", "one more underclocked"],
    ],
    o.pinned || "",
    function (value) {
      const op: Op = value ? { op: "put", field: "row_overclock", item: id!, value: value } : { op: "del", field: "row_overclock", item: id! };
      applyOps([op]);
    },
    { label: "last machine of " + row.recipe, title: optionWords(o) + (o.without ? " · not enough shards for this row" : "") }
  );
  pick.classList.add("plan-row-oc");
  pick.addEventListener("click", function (event) {
    event.stopPropagation();
  });
  pick.addEventListener("keydown", function (event) {
    event.stopPropagation();
  });
  return pick;
}

export function powerRow(body: HTMLElement): void {
  const plan = bench.plan!;
  const result = bench.result;
  const view = result && result.feasible && result.power.stops.length ? result.power : null;
  const current = view ? view.hours : plan.args.payback_hours === null ? 0 : plan.args.payback_hours;
  const still = !!view && !view.splits;
  const tail = view?.inherited ? " · shared default" : "";
  const line = make("span", "plan-sub plan-power-line", (still ? view!.reason : stopWords(view, current)) + tail);
  line.setAttribute("aria-live", "polite");
  body.classList.add("plan-stack");
  body.appendChild(make("span", "plan-sub plan-power-hint", "build extra, slower machines when the power they save repays their build points" + priceWords(view)));
  const row = make("div", "plan-line plan-payback");
  row.appendChild(
    slider(
      PAYBACK_LABELS,
      nearestStopIndex(current),
      function (i) {
        if (!still) line.textContent = stopWords(view, PAYBACK_HOURS[i]!);
      },
      function (i) {
        if (PAYBACK_HOURS[i] === plan.args.payback_hours) return;
        snapshotRecipes();
        applyOps([{ op: "set", field: "payback_hours", value: PAYBACK_HOURS[i]! }]);
      },
      { label: "payback horizon in hours of play", ends: ["fewer machines", "less power"], disabled: still, title: "hours of play the saved power must repay the extra machines in" }
    )
  );
  if (plan.args.payback_hours !== null) row.appendChild(followDefault("payback_hours"));
  body.appendChild(row);
  body.appendChild(line);
  const change = switchLine();
  if (change) {
    const note = make("span", "plan-sub plan-switch", change);
    note.setAttribute("role", "status");
    body.appendChild(note);
  }
  overclockRow(body, view);
}
