/* The Items page's slot grids: held, all (the catalogue) and containers. */

import { count, formatNumber, pct } from "../../kit/format";
import { html, type Safe } from "../../kit/html";
import { sortSelect } from "../../kit/ui/controls";
import { slotTile } from "../../kit/ui/marks";
import { emptyLine, note } from "../../kit/ui/text";
import { hashFor } from "../../map/map";
import { buildingIcon } from "../factories/model";
import { itemsDash, type ItemsAddress } from "./address";
import { boxFill, boxMatches, boxName, boxRegion, compact, groupBoxes, groupItems, matches, shownInCatalogue, type BoxSort, type Index, type Item, type ItemSort, type Offers, type SlotGroup } from "./model";

import type { StockPlace, StockResponse } from "../../api/shapes";

export const ITEM_SORTS: [ItemSort, string][] = [
  ["category", "category"],
  ["count", "count"],
  ["name", "name"],
];

export const BOX_SORTS: [BoxSort, string][] = [
  ["category", "category"],
  ["region", "region"],
  ["fill", "fill"],
];

export interface GridCtx {
  addr: ItemsAddress;
  filter: string;
  itemSort: ItemSort;
  boxSort: BoxSort;
  spoilers: boolean;
  offers: Offers;
}

export interface Grid {
  title: string;
  n: number;
  tools: Safe;
  body: Safe;
}

const COMPUTED = {
  raw: "worked out: the item's resource flag in the game data",
  other: "worked out: equipment, ammunition, consumables and what no category takes",
  catalogue: "worked out from the game data: the AWESOME Shop's categories and the item's own data",
};

export function itemHref(addr: ItemsAddress, key: string): string {
  return hashFor(itemsDash({ ...addr, item: key, rec: "", box: "" }));
}

function groupBlock<T>(g: SlotGroup<T>, slot: (row: T) => Safe, tip?: string): Safe {
  const label = tip ? html`<span class="cmp" title="${tip}">${g.label}</span>` : g.label;
  return html`<section class="sgrp" id="${"sec-" + g.key.replace(/\s+/g, "-")}"><h3 class="slab">${label} <span class="n">· ${g.rows.length}</span></h3><div class="slots">${g.rows.map(slot)}</div></section>`;
}

function itemSlot(c: GridCtx, it: Item, locked: boolean): Safe {
  const on = c.addr.item === it.key || c.addr.item === it.cls;
  const label = it.name + (it.held ? " · " + formatNumber(it.held, it.fluid ? 1 : 0) + (it.fluid ? " m³" : "") : "") + (locked ? " · locked" : "");
  return slotTile({
    name: label,
    icon: it.cls,
    value: it.held ? compact(it.held) : "",
    unit: it.held && it.fluid ? "m³" : "",
    href: itemHref(c.addr, on ? "" : it.key),
    on,
    locked,
  });
}

function tipFor(view: "held" | "all", group: string, sort: ItemSort): string | undefined {
  if (sort !== "category") return undefined;
  if (view === "held") return group === "raw" || group === "other" ? COMPUTED[group] : undefined;
  return group === "fluids" ? undefined : COMPUTED.catalogue;
}

export function heldGrid(c: GridCtx, index: Index): Grid {
  const held = index.items.filter((it) => it.held > 0);
  const shown = held.filter((it) => matches(it.name, c.filter));
  const groups = groupItems(shown, "held", c.itemSort);
  const body = shown.length
    ? html`${groups.map((g) => groupBlock(g, (it: Item) => itemSlot(c, it, false), tipFor("held", g.key, c.itemSort)))}`
    : held.length
      ? emptyLine("no held item matches “" + c.filter + "”", "clear the filter")
      : emptyLine("nothing held", "nothing carried, stored, in the depot, in a machine or in a crate");
  const legend = c.itemSort === "category" ? note(html`<span class="cmp">dotted</span>: worked out from the game data, not sent by the save`) : "";
  return { title: "items", n: held.length, tools: sortSelect("items-sort", ITEM_SORTS, c.itemSort), body: html`${body}${legend}` };
}

export function allGrid(c: GridCtx, index: Index, total: number): Grid {
  const showLocked = c.addr.locked && c.spoilers;
  const visible = index.items.filter((it) => shownInCatalogue(it, showLocked, c.offers));
  const shown = visible.filter((it) => matches(it.name, c.filter));
  const groups = groupItems(shown, "all", c.itemSort);
  const hidden = index.items.length - visible.length;
  const body = shown.length
    ? html`${groups.map((g) => groupBlock(g, (it: Item) => itemSlot(c, it, it.locked), tipFor("all", g.key, c.itemSort)))}`
    : emptyLine("no item matches “" + c.filter + "”", "clear the filter");
  const lockTitle = c.spoilers ? "show items no unlocked recipe makes" : "spoilers are off (settings)";
  const lock = html`<label class="${c.spoilers ? "" : "off"}" title="${lockTitle}"><input type="checkbox" id="items-locked"${showLocked ? html` checked` : ""}${c.spoilers ? "" : html` disabled`}> show locked</label>`;
  const notes = [count(visible.length) + " of " + count(total) + " items", hidden ? count(hidden) + " locked hidden" : ""].filter(Boolean).join(" · ");
  return { title: "items", n: visible.length, tools: html`${lock}${sortSelect("items-sort", ITEM_SORTS, c.itemSort)}`, body: html`${body}${note(notes)}` };
}

function boxSlot(c: GridCtx, p: StockPlace): Safe {
  const on = c.addr.box === p.instance_leaf;
  const fill = boxFill(p);
  const name = boxName(p);
  const label = name + (boxRegion(p) ? " · " + boxRegion(p) : "") + (fill === null ? "" : " · " + pct(fill) + " full");
  return slotTile({
    name: label,
    icon: p.source === "crate" ? null : buildingIcon(p.cls),
    sub: boxRegion(p),
    fill: fill === null ? undefined : fill * 100,
    href: hashFor(itemsDash({ view: "containers", box: on ? "" : p.instance_leaf })),
    on,
  });
}

export function boxGrid(c: GridCtx, data: StockResponse): Grid {
  const shown = data.places.filter((p) => boxMatches(p, c.filter));
  const body = shown.length
    ? html`${groupBoxes(shown, c.boxSort).map((g) => groupBlock(g, (p: StockPlace) => boxSlot(c, p)))}`
    : data.places.length
      ? emptyLine("no container matches “" + c.filter + "”", "clear the filter")
      : emptyLine("no containers", "nothing stored and no crates on the ground");
  return { title: "containers", n: data.places.length, tools: sortSelect("box-sort", BOX_SORTS, c.boxSort), body };
}
