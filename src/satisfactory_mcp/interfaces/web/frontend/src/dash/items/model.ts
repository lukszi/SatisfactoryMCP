/* The Items page's data, joined and grouped: one index over the catalogue and the stock, the
 * slot groups of each view, and what spoilers hide. Pure. docs/frontend_kit.md, "Items". */

import { slug } from "./address";

import type { HardDrivesResponse, ItemRow, StockPile, StockPlace } from "../../api/shapes";

export const SHARD = "Desc_CrystalShard_C";
export const SLOOP = "Desc_WAT1_C";

/** One item as the page knows it: catalogue facts, held piles when any. */
export interface Item {
  cls: string;
  name: string;
  key: string;
  fluid: boolean;
  category: string;
  locked: boolean;
  sink: number;
  pile: StockPile | null;
  held: number;
}

export interface Index {
  items: Item[];
  byCls: Record<string, Item>;
  byKey: Record<string, Item>;
}

export function heldTotal(pile: StockPile | null): number {
  return pile ? pile.carried + pile.storage + pile.depot + pile.buffers + pile.crates : 0;
}

/** The catalogue in its order, then held items the catalogue lacks. */
export function buildIndex(catalogue: ItemRow[], piles: StockPile[]): Index {
  const pileOf: Record<string, StockPile> = {};
  piles.forEach((p) => (pileOf[p.item] = p));
  const items: Item[] = catalogue.map((r) => ({
    cls: r.cls,
    name: r.name,
    key: slug(r.name),
    fluid: r.fluid,
    category: r.category,
    locked: r.locked === true,
    sink: r.sink_points,
    pile: pileOf[r.cls] || null,
    held: heldTotal(pileOf[r.cls] || null),
  }));
  const known = new Set(catalogue.map((r) => r.cls));
  piles
    .filter((p) => !known.has(p.item))
    .forEach((p) => items.push({ cls: p.item, name: p.name, key: slug(p.name), fluid: p.fluid, category: p.fluid ? "fluids" : "other", locked: false, sink: 0, pile: p, held: heldTotal(p) }));
  const byCls: Record<string, Item> = {};
  const byKey: Record<string, Item> = {};
  items.forEach((it) => {
    byCls[it.cls] = it;
    if (!byKey[it.key]) byKey[it.key] = it;
  });
  return { items, byCls, byKey };
}

/** An item by its slug or its class. */
export function findItem(index: Index, key: string): Item | null {
  return index.byKey[key] || index.byCls[key] || index.byKey[slug(key)] || null;
}

/* ------------------------------------------------------------------ groups */

export const HELD_GROUPS = ["parts", "raw", "fluids", "boosts", "other"];

export const CATALOGUE_GROUPS = ["raw", "fluids", "ingots", "minerals", "oil products", "standard parts", "industrial parts", "electronics", "space elevator", "nuclear", "boosts", "other"];

export function heldGroup(category: string): string {
  return category === "raw" || category === "fluids" || category === "boosts" || category === "other" ? category : "parts";
}

export type ItemSort = "category" | "count" | "name";

export interface SlotGroup<T> {
  key: string;
  label: string;
  rows: T[];
}

/** Category keeps the view's groups; count and name merge them into "items", fluids and boosts
 *  keep their own rows. */
export function groupItems(items: Item[], view: "held" | "all", sort: ItemSort): SlotGroup<Item>[] {
  const groupOf = (it: Item) => (view === "held" ? heldGroup(it.category) : it.category);
  const order = sort === "category" ? (view === "held" ? HELD_GROUPS : CATALOGUE_GROUPS) : ["items", "fluids", "boosts"];
  const keyOf = (it: Item) => {
    const g = groupOf(it);
    return sort === "category" || g === "fluids" || g === "boosts" ? g : "items";
  };
  const cmp =
    sort === "name"
      ? (a: Item, b: Item) => a.name.localeCompare(b.name)
      : sort === "count" || view === "held"
        ? (a: Item, b: Item) => b.held - a.held || a.name.localeCompare(b.name)
        : null;
  return order
    .map((g) => {
      const rows = items.filter((it) => keyOf(it) === g);
      if (cmp) rows.sort(cmp);
      return { key: g, label: g, rows };
    })
    .filter((g) => g.rows.length);
}

export function matches(text: string, filter: string): boolean {
  const f = filter.trim().toLowerCase();
  return !f || text.toLowerCase().indexOf(f) >= 0;
}

/* --------------------------------------------------------------- spoilers */

export interface Offers {
  /** recipe class -> the drive that offers it */
  recipes: Record<string, string>;
  /** item class -> the drive whose offer makes it */
  items: Record<string, string>;
}

/** What the hard drives on hand offer, by recipe and by the items those recipes make. */
export function driveOffers(drives: HardDrivesResponse | null): Offers {
  const out: Offers = { recipes: {}, items: {} };
  (drives?.drives || []).forEach((d, i) => {
    const label = String(d.hard_drive_id ?? i + 1);
    d.options.forEach((o) =>
      o.recipes.forEach((r) => {
        out.recipes[r.cls] = label;
        r.products.forEach((p) => (out.items[p.item] = label));
      })
    );
  });
  return out;
}

/** In the catalogue a locked item shows with "show locked" (spoilers on) or a drive's offer. */
export function shownInCatalogue(it: Item, showLocked: boolean, offers: Offers): boolean {
  return !it.locked || showLocked || !!offers.items[it.cls];
}

/** A recipe row shows when unlocked or unknown, with "show locked", or on a drive's offer. */
export function shownRecipe(cls: string, unlocked: boolean | null, showLocked: boolean, offers: Offers): boolean {
  return unlocked !== false || showLocked || !!offers.recipes[cls];
}

/* ---------------------------------------------------------------- numbers */

/** The compact slot count: 129, 3.9k, 70k, 1.2M; the exact figure is in the title and the sheet. */
export function compact(n: number): string {
  if (n < 999.5) return String(Math.round(n));
  if (n < 9950) return (n / 1000).toFixed(1).replace(/\.0$/, "") + "k";
  if (n < 999500) return Math.round(n / 1000) + "k";
  return (n / 1e6).toFixed(1).replace(/\.0$/, "") + "M";
}

/* ------------------------------------------------------------- containers */

export type BoxSort = "category" | "region" | "fill";

export function boxKind(p: StockPlace): string {
  if (p.source === "crate") return "crates";
  return p.kind === "fluid" ? "fluid buffers" : "storage";
}

export function boxName(p: StockPlace): string {
  if (p.source !== "crate") return p.name;
  return p.crate_kind === "death" ? "death crate" : p.crate_kind === "dismantle" ? "dismantle crate" : "crate";
}

export function boxRegion(p: StockPlace): string {
  return p.region ? p.region.name : p.x_m === null ? "" : "off the map";
}

/** A crate's slots are its items, so it has no fill. */
export function boxFill(p: StockPlace): number | null {
  return p.source === "crate" ? null : p.fill;
}

export function boxMatches(p: StockPlace, filter: string): boolean {
  return matches(boxName(p) + " " + boxRegion(p), filter) || p.items.some((i) => matches(i.name, filter));
}

/** Storage, fluid buffers, crates; or by region; or one group by fill. Fullest first. */
export function groupBoxes(places: StockPlace[], sort: BoxSort): SlotGroup<StockPlace>[] {
  const keyOf = sort === "region" ? (p: StockPlace) => boxRegion(p) || "unknown place" : sort === "fill" ? () => "by fill" : boxKind;
  const order: string[] = sort === "category" ? ["storage", "fluid buffers", "crates"] : [];
  places.forEach((p) => order.indexOf(keyOf(p)) < 0 && order.push(keyOf(p)));
  if (sort === "region") order.sort();
  const byFill = (a: StockPlace, b: StockPlace) => (boxFill(b) ?? 0) - (boxFill(a) ?? 0) || (a.distance_m ?? Infinity) - (b.distance_m ?? Infinity);
  return order
    .map((g) => ({ key: g, label: g.toLowerCase(), rows: places.filter((p) => keyOf(p) === g).sort(byFill) }))
    .filter((g) => g.rows.length);
}

/** The places holding an item, biggest first. */
export function placesOf(places: StockPlace[], cls: string): { place: StockPlace; amount: number }[] {
  const out: { place: StockPlace; amount: number }[] = [];
  places.forEach((place) => {
    const amount = place.items.filter((i) => i.item === cls).reduce((s, i) => s + i.amount, 0);
    if (amount > 0) out.push({ place, amount });
  });
  return out.sort((a, b) => b.amount - a.amount);
}
