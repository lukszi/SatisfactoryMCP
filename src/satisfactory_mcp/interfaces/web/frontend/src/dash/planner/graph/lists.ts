/* The lists beside the graph: buildings with counts and power, every item's balance, and the
 * plan's total power. */

import { table } from "../../../kit/dashkit";
import { make } from "../../../kit/dom";
import { count, mw } from "../../../kit/format";
import { itemBalances, powerTotals } from "./balance";
import { rateText } from "./model";

import type { Column, SortState } from "../../../kit/dashkit";
import type { SolveResponse } from "../../../api/shapes";
import type { ItemBalance } from "./balance";
import type { BuildingCount, PlanGraphView, Unit } from "./model";

const buildingOrder: SortState = { key: "count", desc: true };
const itemOrder: SortState = { key: "name", desc: false };

function rate(value: number, unit: Unit): string {
  return value ? rateText(value, unit) : "–";
}

function buildingsCard(parent: HTMLElement, rows: BuildingCount[], onSort: () => void): void {
  const card = make("section", "dash-card pg-list");
  card.appendChild(make("h2", "dash-h", "buildings"));
  const columns: Column<BuildingCount>[] = [
    { key: "building", label: "building", sort: (r) => r.building, render: (r) => r.building },
    { key: "count", label: "count", align: "right", sort: (r) => r.count, render: (r) => count(r.count) },
    { key: "mw", label: "power", align: "right", sort: (r) => r.mw, render: (r) => (r.mw ? mw(r.mw, { signed: true }) : "–") },
  ];
  card.appendChild(table(columns, rows, { sort: buildingOrder, onSort: onSort, caption: "buildings" }));
  parent.appendChild(card);
}

function itemsCard(parent: HTMLElement, rows: ItemBalance[], onSort: () => void): void {
  const card = make("section", "dash-card pg-list");
  card.appendChild(make("h2", "dash-h", "items per minute"));
  const columns: Column<ItemBalance>[] = [
    { key: "name", label: "item", sort: (r) => r.name, render: (r) => r.name },
    { key: "need", label: "needed", align: "right", sort: (r) => r.consumed + r.exported, render: (r) => rate(r.consumed + r.exported, r.unit) },
    { key: "made", label: "produced", align: "right", sort: (r) => r.produced, render: (r) => rate(r.produced, r.unit) },
    { key: "in", label: "supplied", align: "right", sort: (r) => r.supplied, render: (r) => rate(r.supplied, r.unit) },
    { key: "out", label: "consumed", align: "right", sort: (r) => r.consumed, render: (r) => rate(r.consumed, r.unit) },
    { key: "surplus", label: "surplus", align: "right", sort: (r) => r.surplus, render: (r) => rate(r.surplus, r.unit) },
  ];
  card.appendChild(table(columns, rows, { sort: itemOrder, onSort: onSort, caption: "items per minute" }));
  parent.appendChild(card);
}

function powerLine(data: SolveResponse, view: PlanGraphView): string {
  const own = powerTotals(view);
  const draw = data.mw_draw ?? own.draw;
  const made = data.mw_generated ?? own.made;
  const net = data.mw_net ?? made - draw;
  return "total power: draws " + mw(draw) + " · generates " + mw(made) + " · net " + mw(net, { signed: true });
}

export function renderLists(parent: HTMLElement, data: SolveResponse, view: PlanGraphView, onSort: () => void): void {
  const box = make("div", "pg-lists");
  buildingsCard(box, view.buildings, onSort);
  itemsCard(box, itemBalances(view), onSort);
  parent.appendChild(box);
  parent.appendChild(make("p", "dash-note pg-power", powerLine(data, view)));
}
