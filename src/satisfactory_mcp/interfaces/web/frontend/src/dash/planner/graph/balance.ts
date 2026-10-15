/* Each item's flows by where they start and end, and the plan's power, read off the graph so
 * the lists agree with the lines. */

import type { PlanGraphView, Unit } from "./model";

export interface ItemBalance {
  key: string;
  name: string;
  unit: Unit;
  supplied: number;
  produced: number;
  consumed: number;
  exported: number;
  surplus: number;
}

/** Power is not an item and stays out. */
export function itemBalances(view: PlanGraphView): ItemBalance[] {
  const kind: Record<string, string> = {};
  view.nodes.forEach(function (n) {
    kind[n.id] = n.kind;
  });
  const by: Record<string, ItemBalance> = {};
  view.edges.forEach(function (e) {
    if (e.group === "power" || e.unit === "MW") return;
    const key = e.itemId || e.itemName;
    const b = (by[key] = by[key] || { key: key, name: e.itemName, unit: e.unit, supplied: 0, produced: 0, consumed: 0, exported: 0, surplus: 0 });
    const from = kind[e.source];
    const to = kind[e.target];
    if (from === "input") b.supplied += e.perMin;
    if (from === "process") b.produced += e.perMin;
    if (to === "process") b.consumed += e.perMin;
    if (to === "export") b.exported += e.perMin;
    if (to === "leftover" || to === "sink") b.surplus += e.perMin;
  });
  return Object.keys(by)
    .sort()
    .map(function (k) {
      return by[k]!;
    });
}

/** Signed node MW summed: what the plan's machines and sinks draw, and what it generates. */
export function powerTotals(view: PlanGraphView): { draw: number; made: number } {
  let draw = 0;
  let made = 0;
  view.nodes.forEach(function (n) {
    if (n.mw < 0) draw -= n.mw;
    else made += n.mw;
  });
  return { draw: draw, made: made };
}
