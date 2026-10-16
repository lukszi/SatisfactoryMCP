/* The Items page, `dash=items?view=held|all|containers&item=…&box=…&rec=…&locked=1`: one slot
 * grid for what is held, the whole catalogue or the containers, and the pick's sheet beside
 * it (a bottom sheet on phones). docs/frontend_kit.md, "Items". */

import "./items.css";
import { replaceDash } from "../../app/nav";
import { settingOn } from "../../app/settings";
import { state } from "../../app/state";
import { failedInto, loadingInto } from "../../kit/ui/states";
import { html, mount, type Safe } from "../../kit/html";
import { filterField, seg } from "../../kit/ui/controls";
import { dashH, emptyLine, toolrow } from "../../kit/ui/text";
import { loadOne } from "../../app/load";
import { hashFor } from "../../map/map";
import { showPoint } from "../../map/map-highlight";
import { WORDS } from "../../kit/words";
import { leaveDashThen, requestRender } from "../actions";
import { pending, read, wireRetry } from "../factories/data";
import { drives, shards, sloops } from "../progress/feeds";
import { itemsAddress, itemsDash, VIEWS, type ItemsAddress, type View } from "./address";
import { allGrid, boxGrid, heldGrid, type Grid, type GridCtx } from "./grid";
import { buildIndex, driveOffers, findItem, type BoxSort, type Index, type ItemSort } from "./model";
import { boxSheet, itemSheet, type Reads, type SheetCtx } from "./sheet";
import { stock } from "./stock";

import type { AlternatesResponse, ItemsResponse, RecipeDetail, RecipesResponse, StockPlace } from "../../api/shapes";

const CATALOGUE = "/api/gamedata/items?limit=200";

const filters: Record<View, string> = { held: "", all: "", containers: "" };
const sorts = { item: "category" as ItemSort, box: "category" as BoxSort };
let scrolledTo = "";
let lastIndex: Index | null = null;

function retryStock(): void {
  loadOne("/api/stock");
}

/** The picked item's or container's name, for the window title. */
export function itemsTitle(subject: string): string {
  const a = itemsAddress(subject);
  if (a.box) {
    const p = stock.data?.places.filter((x) => x.instance_leaf === a.box)[0];
    return p ? p.name : "";
  }
  const it = a.item && lastIndex ? findItem(lastIndex, a.item) : null;
  return it ? it.name : "";
}

function viewSeg(a: ItemsAddress): Safe {
  const items = VIEWS.map((v) => ({
    value: v,
    label: v,
    href: hashFor(v === "containers" ? itemsDash({ view: v }) : itemsDash({ ...a, view: v, box: "" })),
  }));
  return seg("items-view", items, a.view, "show held items, all items or containers");
}

function filterBox(a: ItemsAddress): Safe {
  return a.view === "containers"
    ? filterField("items-filter", "filter by kind, region or item", filters.containers, "filter containers by kind, region or item held")
    : filterField("items-filter", "filter items, e.g. Rotor", filters[a.view], "filter items by name");
}

function gridCtx(a: ItemsAddress): GridCtx {
  return { addr: a, filter: filters[a.view], itemSort: sorts.item, boxSort: sorts.box, spoilers: settingOn("spoilers"), offers: driveOffers(drives.data) };
}

function gridOf(a: ItemsAddress, index: Index, total: number): Grid | null {
  const c = gridCtx(a);
  if (a.view === "all") return allGrid(c, index, total);
  if (!stock.data) return null;
  return a.view === "containers" ? boxGrid(c, stock.data) : heldGrid(c, index);
}

function stockLine(): Safe {
  if (state.noSaves) return emptyLine(WORDS.noSaves, "the catalogue still works: all");
  return html``;
}

/* ------------------------------------------------------------------ sheet */

function sheetReads(cls: string): Reads {
  const makers = read<AlternatesResponse>("/api/gamedata/alternates?item=" + encodeURIComponent(cls));
  const usersPath = "/api/gamedata/recipes?recipe_kind=part&consumes=" + encodeURIComponent(cls);
  const users = read<RecipesResponse>(usersPath);
  const none = { recipes: [] } as unknown;
  return {
    makers: makers.data || (makers.missing ? (none as AlternatesResponse) : null),
    users: users.data || (users.missing ? (none as RecipesResponse) : null),
    pending: pending(makers, "the recipes that make it", "/api/gamedata/alternates?item=" + encodeURIComponent(cls)) || pending(users, "the recipes that use it", usersPath),
  };
}

function sheetOf(a: ItemsAddress, index: Index): Safe | null {
  const c: SheetCtx = {
    addr: a,
    index,
    places: stock.data?.places || [],
    offers: driveOffers(drives.data),
    showLocked: a.locked && settingOn("spoilers"),
    shards: shards.data,
    sloops: sloops.data,
  };
  if (a.box) {
    const p = c.places.filter((x) => x.instance_leaf === a.box)[0];
    return p ? boxSheet(c, p) : null;
  }
  const it = a.item ? findItem(index, a.item) : null;
  return it ? itemSheet(c, it, sheetReads(it.cls)) : null;
}

/* A recipe asked for without its item (an old recipe address, the jump search) opens its main
 * product with that row marked. */
function followRecipe(a: ItemsAddress, index: Index): Safe | null {
  if (!a.rec || a.item) return null;
  const path = "/api/gamedata/recipe?recipe=" + encodeURIComponent(a.rec.indexOf("_C") > 0 ? a.rec : a.rec.replace(/-/g, " "));
  const slot = read<RecipeDetail>(path);
  if (slot.data) {
    const main = slot.data.products[0] ? findItem(index, slot.data.products[0].item) : null;
    if (main) {
      replaceDash(itemsDash({ ...a, item: main.key, rec: slot.data.cls }));
      return null;
    }
    return emptyLine("“" + slot.data.name + "” makes no item", "a build-gun recipe or a hand craft");
  }
  if (slot.missing || slot.failure) return emptyLine("no recipe “" + a.rec + "”", "search for it with /");
  return pending(slot, "the recipe", path);
}

/* --------------------------------------------------------------- wiring */

function flyTo(p: StockPlace): void {
  if (p.x_m === null || p.y_m === null) return;
  const x = p.x_m;
  const y = p.y_m;
  leaveDashThen(() => showPoint(x, y, { label: p.name, layers: p.source === "storage" ? ["storage"] : undefined }));
}

function wire(root: HTMLElement, a: ItemsAddress, regrid: () => void): void {
  wireRetry(root);
  root.addEventListener("input", function (event) {
    const input = event.target as HTMLInputElement;
    if (input.id !== "items-filter") return;
    filters[a.view] = input.value;
    regrid();
  });
  root.addEventListener("change", function (event) {
    const t = event.target as HTMLInputElement;
    if (t.id === "items-sort") sorts.item = t.value as ItemSort;
    else if (t.id === "box-sort") sorts.box = t.value as BoxSort;
    else if (t.id === "items-locked") {
      location.hash = hashFor(itemsDash({ ...a, locked: t.checked }));
      return;
    } else return;
    requestRender();
  });
  root.addEventListener("click", function (event) {
    const target = event.target as Element;
    const pin = target.closest<HTMLElement>("[data-pin]");
    if (pin) {
      const p = stock.data?.places.filter((x) => x.instance_leaf === pin.dataset.pin)[0];
      if (p) flyTo(p);
      return;
    }
    if (target.closest("a, button, input, select, label")) return;
    const row = target.closest<HTMLElement>(".entry[data-href]");
    if (row && root.contains(row)) location.hash = row.dataset.href!;
  });
}

function scrollToRecipe(root: HTMLElement, a: ItemsAddress): void {
  const key = a.item + "|" + a.rec;
  if (!a.rec || scrolledTo === key) return;
  const row = root.querySelector<HTMLElement>(".items-side .entry.picked");
  if (!row) return;
  scrolledTo = key;
  row.scrollIntoView({ block: "nearest" });
}

export function renderItems(body: HTMLElement, subject: string): void {
  const a = itemsAddress(subject);
  const cat = read<ItemsResponse>(CATALOGUE);
  const root = document.createElement("div");
  root.className = "items-page";
  body.appendChild(root);
  if (!cat.data) {
    mount(root, pending(cat, "the item catalogue", CATALOGUE) || emptyLine("no item catalogue"));
    wireRetry(root);
    return;
  }
  const index = buildIndex(cat.data.items, stock.data?.items || []);
  lastIndex = index;
  const followed = followRecipe(a, index);
  if (followed === null && a.rec && !a.item) {
    window.setTimeout(requestRender, 0);
    return;
  }
  const grid = gridOf(a, index, cat.data.total);
  const sheet = sheetOf(a, index);
  const head = toolrow(html`<span class="items-h">${dashH(grid ? grid.title : a.view === "containers" ? "containers" : "items", grid ? grid.n : undefined)}${viewSeg(a)}</span>`, html`${filterBox(a)}${grid ? grid.tools : ""}`);
  const gridBody = grid ? grid.body : html`${stockLine()}`;
  mount(root, html`${head}${followed || ""}<div class="${sheet ? "items has-pick" : "items"}"><div class="items-grid" id="items-grid">${gridBody}</div>${sheet || ""}</div>`);
  if (!grid && !state.noSaves) {
    const holder = root.querySelector<HTMLElement>("#items-grid")!;
    if (stock.failed) failedInto(holder, "stock", "", retryStock);
    else loadingInto(holder, "stock");
  }
  wire(root, a, function () {
    const again = gridOf(a, index, cat.data!.total);
    if (again) mount(root.querySelector("#items-grid")!, again.body);
  });
  scrollToRecipe(root, a);
}

function itemsShown(): boolean {
  return state.dash === "items" || state.dash.indexOf("items?") === 0;
}

/* Esc closes the sheet, unless a field or a dialog has the key. */
document.addEventListener("keydown", function (event) {
  if (event.key !== "Escape" || !itemsShown() || document.querySelector("dialog[open]")) return;
  const t = event.target as Element;
  if (t.closest && t.closest("input, select, textarea")) return;
  const a = itemsAddress(state.dash.slice(5));
  if (!a.item && !a.box) return;
  location.hash = hashFor(itemsDash({ ...a, item: "", box: "", rec: "" }));
});
