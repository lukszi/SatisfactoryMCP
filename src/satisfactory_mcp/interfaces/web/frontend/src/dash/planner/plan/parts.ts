/* Small parts the plan page's tabs share: icons and units read off the solve's graph, item
 * pills, signed numbers, the page-session view state and the pick chat sees. */

import { count, formatNumber } from "../../../kit/format";
import { html, type Safe } from "../../../kit/html";
import { icon } from "../../../kit/ui/text";
import { scheduleFocus } from "../focus";
import { planGraphOf } from "../graph/model";
import { bench, changed, itemClass } from "../state";

import type { FocusSelection, SolveResponse } from "../../../api/shapes";
import type { BuildSort } from "./model";

/** What the page remembers for the session: open folds, sorts, filters. */
export const view = {
  targets: {} as Record<string, boolean>,
  sort: "chain" as BuildSort,
  versionsWho: "all",
  renaming: false,
  renameFocus: false,
};

export interface Lookup {
  buildingIcon: Record<string, string>;
  itemIcon: Record<string, string>;
  fluid: Record<string, boolean>;
}

const lookups = new WeakMap<SolveResponse, Lookup>();

/** Building and item icons and which items are fluids, from the solve's graph. */
export function lookupOf(data: SolveResponse | null): Lookup {
  if (!data) return { buildingIcon: {}, itemIcon: {}, fluid: {} };
  let found = lookups.get(data);
  if (found) return found;
  const graph = planGraphOf(data);
  found = { buildingIcon: {}, itemIcon: {}, fluid: {} };
  const out = found;
  graph.nodes.forEach(function (n) {
    if (n.building && n.buildingIcon) out.buildingIcon[n.building] = n.buildingIcon;
    if (n.itemName && n.itemId) out.itemIcon[n.itemName] = n.itemId;
  });
  graph.edges.forEach(function (e) {
    if (e.itemId) out.itemIcon[e.itemName] = e.itemId;
    if (e.unit === "m3") out.fluid[e.itemName] = true;
  });
  lookups.set(data, found);
  return found;
}

/** "−22" / "+150": signed for nets and deltas, with a true minus. */
export function sgn(value: number, decimals?: number): string {
  const text = formatNumber(Math.abs(value), decimals === undefined ? 0 : decimals);
  if (text === formatNumber(0, decimals === undefined ? 0 : decimals)) return text;
  return (value < 0 ? "−" : "+") + text;
}

export function rateUnit(look: Lookup, item: string): string {
  return look.fluid[item] ? "m³/min" : "/min";
}

export function itemIcon(look: Lookup, name: string, extra?: string): Safe {
  return icon(look.itemIcon[name] || itemClass(name), name, extra);
}

export function buildingIcon(look: Lookup, building: string, extra?: string): Safe {
  return icon(look.buildingIcon[building] || null, building, extra);
}

/** An item pill at a rate; "MW" is power, a signed pill with no icon. */
export function ratePill(look: Lookup, item: string, perMin: number, noName?: boolean): Safe {
  if (item === "MW") return html`<span class="pill"><b>${sgn(perMin)}</b><span class="nm">MW</span></span>`;
  const rate = formatNumber(perMin, 1) + " " + rateUnit(look, item);
  if (noName) return html`<span class="pill" title="${item}">${itemIcon(look, item)}<b>${rate}</b></span>`;
  return html`<span class="pill">${itemIcon(look, item)}<b>${rate}</b><span class="nm">${item}</span></span>`;
}

/** Inputs → outputs as icon-and-rate pills, names in the titles. */
export function rateFlow(look: Lookup, inputs: { item: string; per_min: number }[], outputs: { item: string; per_min: number }[]): Safe {
  const outs = outputs.map((r, i) => html`${i ? html`<span class="plus">+</span>` : ""}${ratePill(look, r.item, r.per_min, true)}`);
  return html`<span class="pills">${inputs.map((r) => ratePill(look, r.item, r.per_min, true))}${inputs.length ? html`<span class="arrow">→</span>` : ""}${outs}</span>`;
}

export function machinesText(n: number): string {
  return count(n) + (n === 1 ? " machine" : " machines");
}

/** What chat sees picked on the page. */
export function selectThing(selection: FocusSelection | null): void {
  bench.selection = selection;
  changed();
  scheduleFocus();
}
