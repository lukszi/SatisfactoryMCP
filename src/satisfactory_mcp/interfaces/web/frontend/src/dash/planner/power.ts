/* The words of the plan's power settings: the payback horizon, the overclock-last switch and a
 * build row's own overclock choice. See docs/planner-payback-horizon_contract.md §7. */

import { byCodeUnit, count, formatNumber, mw, pct } from "../../kit/format";
import { counted } from "../../kit/words";
import { bench } from "./state";

import type { SolveResponse, SolveRow } from "../../api/shapes";

type Payback = SolveResponse["power"];
type Stop = Payback["stops"][number];
type OverclockOption = NonNullable<SolveRow["overclock_option"]>;

/* The recipes before a slider release, kept until the re-solve lands (F5a). */
let switched = { key: "", from: -1, before: {} as Record<string, string>, rev: 0, text: "" };

export const PAYBACK_HOURS = [0, 1, 2, 5, 10, 20, 50, 100];

export function hours(h: number): string {
  return formatNumber(h, 1) + " h";
}

export function nearestStopIndex(h: number): number {
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

function signOf(n: number): string {
  if (n > 0) return "+";
  return n < 0 ? "−" : "";
}

function signedMachineCount(n: number): string {
  return signOf(n) + count(Math.abs(n)) + (Math.abs(n) === 1 ? " machine" : " machines");
}

export function stopWords(view: Payback | null, h: number): string {
  const stop = stopAt(view, h);
  if (!stop) return hours(h) + (bench.solvingRev ? ": solving…" : "");
  if (!h || !stop.extra_machines) return hours(h) + ": no extra machines · " + count(stop.machines) + " machines · " + mw(stop.mw_draw);
  const parts = ["pays back within " + hours(h), signedMachineCount(stop.extra_machines), (stop.saved_mw < 0 ? "+" : "−") + mw(Math.abs(stop.saved_mw))];
  if (stop.average_payback_h !== null) parts.push("on average " + hours(stop.average_payback_h));
  return parts.join(" · ");
}

export function priceWords(view: Payback | null): string {
  if (!view) return "";
  if (view.price_source === "plan") return " at " + formatNumber(view.price, 0) + " pts/MWh set on this plan";
  if (!view.price) return "; this grid burns no fuel, so power costs nothing to run";
  return " at the grid mix, " + formatNumber(view.price, 0) + " pts/MWh";
}

type OverclockRow = Payback["overclock"]["rows"][number];

function shardWords(oc: Payback["overclock"], shards: number): string {
  const hand = oc.shards_free === null ? "" : formatNumber(oc.shards_free, 0) + " in hand + " + formatNumber(oc.shards_craftable || 0, 0) + " craftable";
  return counted(shards, "shard") + (hand ? " (" + hand + ")" : "");
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
  const bill = stock ? shardWords(oc, shards) : counted(shards, "shard");
  return [signedMachineCount(-saved), "+" + mw(extra), bill].join(" · ");
}

export function overclockWords(view: Payback | null): string {
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
    .sort(byCodeUnit);
}

function switchWords(before: Record<string, string>, after: Record<string, string>): string {
  const added = namesOnlyIn(after, before);
  const dropped = namesOnlyIn(before, after);
  if (!added.length) return dropped.length ? "dropped " + dropped.join(", ") : "";
  return "switched to " + added.join(", ") + (dropped.length ? ", from " + dropped.join(", ") : "");
}

export function snapshotRecipes(): void {
  switched = { key: bench.key, from: bench.resultRev, before: recipeNamesById(bench.result), rev: 0, text: "" };
}

export function switchLine(): string {
  if (switched.key !== bench.key) return "";
  if (switched.from >= 0 && bench.resultRev > switched.from && bench.result) {
    switched.text = bench.result.feasible ? switchWords(switched.before, recipeNamesById(bench.result)) : "";
    switched.rev = bench.resultRev;
    switched.from = -1;
  }
  return switched.rev === bench.resultRev ? switched.text : "";
}

export function optionWords(o: OverclockOption): string {
  const last = count(o.machines) + (o.machines === 1 ? " machine" : " machines") + ", the last at " + pct(o.last_clock, 1) + ", " + count(o.shards) + (o.shards === 1 ? " shard" : " shards");
  const spread = count(o.spread_machines) + " at " + pct(o.spread_clock, 1);
  return "overclock last: " + last + " (+" + mw(o.extra_mw) + ") · one more underclocked: " + spread;
}

/** The payback view of the head's result, when it has stops to offer. */
export function paybackView(): Payback | null {
  const result = bench.result;
  return result && result.feasible && result.power.stops.length ? result.power : null;
}
