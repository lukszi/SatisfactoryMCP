/* The pick's sheet: an item (have, made by, used by) or a container (where, fill, contents). */

import { count, formatNumber, mw, pct } from "../../kit/format";
import { cls, html, type Safe } from "../../kit/html";
import { sidePanel } from "../../kit/overlay/panels";
import { entry, list, metric, nameLine } from "../../kit/ui/entry";
import { bar, chip, tagChip } from "../../kit/ui/marks";
import { dashH, emptyLine, icon, na, note } from "../../kit/ui/text";
import { hashFor } from "../../map/map";
import { buildingIcon } from "../factories/model";
import { slug, itemsDash, type ItemsAddress } from "./address";
import { boxFill, boxName, boxRegion, findItem, placesOf, SHARD, shownRecipe, SLOOP, type Index, type Item, type Offers } from "./model";
import { itemHref } from "./grid";

import type { AlternatesResponse, MakerRow, Rate, RecipeRow, RecipesResponse, ShardsResponse, SloopsResponse, StockPlace } from "../../api/shapes";

const PLACES_SHOWN = 5;

export interface SheetCtx {
  addr: ItemsAddress;
  index: Index;
  places: StockPlace[];
  offers: Offers;
  showLocked: boolean;
  shards: ShardsResponse | null;
  sloops: SloopsResponse | null;
}

/** What the sheet's reads have given so far: null while one is loading, with its line. */
export interface Reads {
  makers: AlternatesResponse | null;
  users: RecipesResponse | null;
  pending: Safe | null;
}

function hfig(value: string, unit: string): Safe {
  return html`<span class="hfig"><b>${value}</b><span>${unit}</span></span>`;
}

function closeLink(href: string): Safe {
  return html`<a class="btn icon" href="${href}" aria-label="close the sheet" title="close (Esc)">×</a>`;
}

function pinBtn(p: StockPlace): Safe {
  if (p.x_m === null || p.y_m === null) return html`<span></span>`;
  return html`<button type="button" class="btn icon" data-pin="${p.instance_leaf}" title="show it on the map" aria-label="${"show " + boxName(p) + " on the map"}">◎</button>`;
}

function kvRows(rows: [string, number | null][], fluid: boolean): Safe {
  return html`<div class="kvs">${rows.map((r) => html`<span class="k">${r[0]}</span>${r[1] ? html`<b>${formatNumber(r[1], fluid ? 1 : 0)}</b>` : na()}`)}</div>`;
}

function boxHref(p: StockPlace): string {
  return hashFor(itemsDash({ view: "containers", box: p.instance_leaf }));
}

function placeRow(p: StockPlace, amount: number, fluid: boolean): Safe {
  const lead = icon(p.source === "crate" ? null : buildingIcon(p.cls), boxName(p));
  return entry({ href: boxHref(p) }, html`${lead}<div class="id"><a class="name" href="${boxHref(p)}">${boxName(p)}</a><div class="ctx">${boxRegion(p) || "–"}</div></div>${metric(formatNumber(amount, fluid ? 1 : 0), fluid ? "m³" : "")}<div class="acts">${pinBtn(p)}</div>`);
}

function boostRows(c: SheetCtx, it: Item): [string, number | null][] | null {
  if (it.cls === SHARD && c.shards) return [["free", c.shards.free], ["craftable", c.shards.craftable], ["in machines", c.shards.committed]];
  if (it.cls === SLOOP && c.sloops) return [["free", c.sloops.free], ["in machines", c.sloops.committed]];
  return null;
}

/** Piles as fixed rows (boosts: free, craftable, in machines), then the places, biggest first. */
function haveBlock(c: SheetCtx, it: Item): Safe {
  const p = it.pile;
  const boost = boostRows(c, it);
  const slugRow = c.shards?.slugs.filter((s) => s.item === it.cls)[0];
  const rows: [string, number | null][] = boost || [
    ["storage", p?.storage || null],
    ["depot", p?.depot || null],
    ["carried", p?.carried || null],
    ["in machines", p?.buffers || null],
    ["crates", p?.crates || null],
  ];
  if (slugRow) rows.push(["makes shards", slugRow.shards]);
  const places = placesOf(c.places, it.cls);
  const head = html`<h4 class="slab">places <span class="n">· ${places.length}</span></h4>`;
  const where = places.length
    ? html`${list("place", "stacked", places.slice(0, PLACES_SHOWN).map((x) => placeRow(x.place, x.amount, it.fluid)))}${places.length > PLACES_SHOWN ? html`<p class="sub-more">+${places.length - PLACES_SHOWN} more</p>` : ""}`
    : note(p && p.depot ? "the depot holds it; no container does" : "no container holds it");
  const machines = boost ? note("in machines: a factory's machines, “boosted only”") : "";
  return html`${dashH("have", undefined, "h3")}${kvRows(rows, it.fluid)}${machines}${head}${where}`;
}

function ratePill(c: SheetCtx, r: Rate): Safe {
  const it = c.index.byCls[r.item];
  const fl = it ? it.fluid : false;
  const inner = html`${icon(r.item, r.name)}<b>${formatNumber(r.per_min)}</b>${fl ? html`<span>m³</span>` : ""}`;
  const here = it && (c.addr.item === it.key || c.addr.item === it.cls);
  return it && !here ? html`<a class="pill" href="${itemHref(c.addr, it.key)}" title="${r.name}">${inner}</a>` : html`<span class="pill" title="${r.name}">${inner}</span>`;
}

function recipeChips(c: SheetCtx, cls: string, alternate: boolean, unlocked: boolean | null): Safe {
  const drive = c.offers.recipes[cls];
  return html`${alternate ? tagChip("alt") : ""}${unlocked === false ? chip("locked", "muted") : ""}${drive ? chip("drive " + drive, undefined, "a hard drive on hand offers it") : ""}`;
}

function recMarked(c: SheetCtx, cls: string, name: string): boolean {
  return !!c.addr.rec && (c.addr.rec === cls || c.addr.rec === slug(name));
}

/** One recipe as a flow: inputs → building with its draw → outputs, the columns fixed across rows. */
function makeRow(c: SheetCtx, r: MakerRow): Safe {
  const outs = r.products.map((p, i) => html`${i ? html`<span class="plus">+</span>` : ""}${ratePill(c, p)}`);
  const machine = html`<span class="mach">${icon(r.machine_icon, r.machine || "by hand")}<span>${r.machine ? mw(r.power_mw) : "by hand"}</span></span>`;
  const name = nameLine(html`<span class="name">${r.name}</span>${recipeChips(c, r.cls, r.alternate, r.unlocked)}`);
  return html`<div class="${cls("entry mrow", recMarked(c, r.cls, r.name) && "picked")}" id="${"rec-" + slug(r.name)}">${name}<div class="mflow"><span class="pills min">${r.ingredients.map((p) => ratePill(c, p))}</span><span class="arrow">→</span>${machine}<span class="arrow">→</span><span class="pills mout">${outs}</span></div></div>`;
}

function useRow(c: SheetCtx, u: RecipeRow, fluid: boolean): Safe {
  const product = u.product ? c.index.byCls[u.product] : undefined;
  const here = product && (c.addr.item === product.key || c.addr.item === product.cls);
  const href = product && !here ? itemHref(c.addr, product.key) : undefined;
  const lead = icon(u.product, product ? product.name : u.name);
  const name = nameLine(html`<span class="name">${u.name}</span>${recipeChips(c, u.cls, u.alternate, u.unlocked)}`, u.machine || "");
  return entry({ href, extra: recMarked(c, u.cls, u.name) ? "picked" : "" }, html`${lead}<div class="id">${name}</div>${metric(formatNumber(u.qty), fluid ? "m³/min" : "/min")}`);
}

function byDefaultFirst(a: MakerRow, b: MakerRow): number {
  return Number(a.alternate) - Number(b.alternate) || a.name.localeCompare(b.name);
}

function recipeBlocks(c: SheetCtx, it: Item, reads: Reads): Safe {
  if (!reads.makers || !reads.users) return reads.pending || html``;
  const made = reads.makers.recipes.filter((r) => shownRecipe(r.cls, r.unlocked, c.showLocked, c.offers)).sort(byDefaultFirst);
  const used = reads.users.recipes.filter((u) => shownRecipe(u.cls, u.unlocked, c.showLocked, c.offers));
  const raw = it.category === "raw" || (it.category === "fluids" && !reads.makers.recipes.length);
  const why = raw ? "extracted" : reads.makers.recipes.length ? "every recipe is locked" : "no recipe makes it in a machine";
  const madeBlock = made.length ? html`${list("mk", "stacked", made.map((r) => makeRow(c, r)))}${note("per minute, one machine at 100%")}` : emptyLine(why);
  const usedBlock = used.length ? list("use", "stacked", used.map((u) => useRow(c, u, it.fluid))) : emptyLine(reads.users.recipes.length ? "every recipe that uses it is locked" : "no recipe uses it");
  return html`${dashH("made by", made.length, "h3")}${madeBlock}${dashH("used by", used.length, "h3")}${usedBlock}`;
}

/** The item sheet: header, have (only when any is held), made by, used by. */
export function itemSheet(c: SheetCtx, it: Item, reads: Reads): Safe {
  const figs = html`${it.held ? hfig(formatNumber(it.held, it.fluid ? 1 : 0), it.fluid ? "m³ held" : "held") : ""}${it.sink ? hfig(count(it.sink), "sink pts") : ""}${chip(it.fluid ? "fluid" : "solid", "muted")}`;
  const head = html`${icon(it.cls, it.name)}<h2 class="dash-h">${it.name}</h2>${figs}${closeLink(itemHref(c.addr, ""))}`;
  const owned = it.held > 0 || boostRows(c, it) !== null;
  return sidePanel(head, html`${owned ? haveBlock(c, it) : ""}${recipeBlocks(c, it, reads)}`, "items-sheet");
}

function crateWhy(p: StockPlace): string {
  if (p.source !== "crate") return "";
  return p.crate_kind_text || (p.crate_kind === "dismantle" ? "dismantled with a full inventory" : p.crate_kind === "death" ? "dropped on death" : "");
}

function distance(m: number | null): string {
  if (m === null) return "";
  return m >= 1000 ? formatNumber(m / 1000, 1) + " km" : count(Math.round(m)) + " m";
}

/** The container sheet: where, how full, what it holds. */
export function boxSheet(c: SheetCtx, p: StockPlace): Safe {
  const name = boxName(p);
  const head = html`${icon(p.source === "crate" ? null : buildingIcon(p.cls), name)}<h2 class="dash-h">${name}</h2>${closeLink(hashFor(itemsDash({ view: "containers" })))}`;
  const where = [boxRegion(p), p.distance_m === null ? "" : distance(p.distance_m) + " from the player", crateWhy(p)].filter(Boolean).join(" · ");
  const fluid = p.kind === "fluid";
  const fill = boxFill(p);
  const used = fluid ? formatNumber(p.total, 1) + " of " + (p.capacity_m3 === null ? "?" : formatNumber(p.capacity_m3, 0)) + " m³" : p.slots_used === null || !p.slots ? "" : count(p.slots_used) + " of " + count(p.slots) + " slots";
  const fillRow = fill === null ? "" : html`<div class="side-fill">${bar(fill * 100, "util", pct(fill) + " full")}<span class="metric"><b>${used}</b></span></div>`;
  const rows = p.items.map((i) => {
    const it = findItem(c.index, i.item);
    const href = it ? hashFor(itemsDash({ view: it.held > 0 ? "held" : "all", item: it.key })) : undefined;
    return entry({ href }, html`${icon(i.item, i.name)}<div class="id">${href ? html`<a class="name" href="${href}">${i.name}</a>` : html`<span class="name">${i.name}</span>`}</div>${metric(formatNumber(i.amount, fluid ? 1 : 0), fluid ? "m³" : "")}`);
  });
  const contents = rows.length ? list("cont", "stacked", rows) : emptyLine(fluid && p.total ? formatNumber(p.total, 1) + " m³ of an unnamed fluid" : "empty");
  const body = html`<div class="side-where"><span class="ctx">${where || "–"}</span>${pinBtn(p)}</div>${fillRow}${dashH("contents", p.items.length, "h3")}${contents}`;
  return sidePanel(head, body, "items-sheet");
}
