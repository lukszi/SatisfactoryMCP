/* What is held: /api/stock in the live wave, and the address other pages link an item by
 * (held when it is held, else the catalogue). */

import { registerFetch } from "../../app/registry";
import { state } from "../../app/state";
import { requestRender } from "../actions";
import { itemsDash, slug } from "./address";
import { heldTotal } from "./model";

import type { StockResponse } from "../../api/shapes";

export const stock = {
  data: null as StockResponse | null,
  failed: false,
};

function itemsOpen(): boolean {
  return state.dash === "items" || state.dash.indexOf("items?") === 0;
}

/** An item's address on Items: held when any is held, else the catalogue. */
export function itemDash(cls: string, name: string): string {
  const pile = stock.data?.items.filter((p) => p.item === cls)[0] || null;
  return itemsDash({ view: heldTotal(pile) > 0 ? "held" : "all", item: slug(name) });
}

registerFetch<StockResponse>({
  wave: "live",
  rank: 70,
  path: "/api/stock",
  label: "stock",
  clears: [],
  refilters: false,
  draw: function (data) {
    stock.data = data;
    stock.failed = false;
    if (itemsOpen()) requestRender();
  },
  failed: function () {
    stock.data = null;
    stock.failed = true;
    if (itemsOpen()) requestRender();
  },
});
