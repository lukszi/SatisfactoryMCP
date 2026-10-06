/* What the Recipes views draw in their cells: icons, item and recipe links, the unlock badge,
 * flows, and the recipe table they all share. */

import { appendNote, link, table } from "../../kit/dashkit";
import { make } from "../../kit/dom";
import { formatNumber, perMin } from "../../kit/format";
import { settingOn } from "../../app/settings";
import { friendlyError } from "../../kit/toast";
import { RECIPE_KIND } from "../../kit/words";
import { iconsServed, redraw } from "./cache";

import type { Column, SortState } from "../../kit/dashkit";
import type { Rate, RecipeRow } from "../../api/shapes";

const QTY_SUFFIX_BY_KIND: Record<string, string> = { building: "/build", manual: "/craft" };

const tableSorts: Record<string, SortState> = {};

export function hidesLocked(): boolean {
  return !settingOn("spoilers");
}

export function spoilers(): string {
  return hidesLocked() ? "&spoilers=0" : "";
}

export function itemLink(cls: string, name: string): HTMLAnchorElement {
  return link("recipes/item/" + cls, name);
}

export function recipeLink(cls: string, name: string): HTMLAnchorElement {
  return link("recipes/recipe/" + cls, name);
}

export function icon(cls: string): Node {
  if (!iconsServed()) return document.createTextNode("");
  const img = make("img", "rx-icon");
  img.src = "/api/icons/" + encodeURIComponent(cls);
  img.alt = "";
  img.loading = "lazy";
  img.onerror = function () {
    img.remove();
  };
  return img;
}

export function itemNameWithIcon(cls: string, name: string, linked: boolean): HTMLElement {
  const box = make("span", "rx-name");
  box.appendChild(icon(cls));
  box.appendChild(linked ? itemLink(cls, name) : make("span", "rx-plain", name));
  return box;
}

export function unlockBadge(unlocked: boolean | null): HTMLElement {
  if (unlocked === null) return make("span", "dash-muted", "–");
  return make("span", unlocked ? "rx-have" : "rx-locked", unlocked ? "unlocked" : "locked");
}

function statusRank(unlocked: boolean | null): number {
  return unlocked === null ? 2 : unlocked ? 0 : 1;
}

export function statusColumn<R extends { unlocked: boolean | null }>(): Column<R> {
  return {
    key: "status",
    label: "status",
    sort: function (row) {
      return statusRank(row.unlocked);
    },
    render: function (row) {
      return unlockBadge(row.unlocked);
    },
  };
}

export function showsStatus(rows: { unlocked: boolean | null }[]): boolean {
  return !hidesLocked() && rows.some(function (row) {
    return row.unlocked !== null;
  });
}

export function flows(rates: Rate[]): HTMLElement {
  const span = make("span", "rx-flows");
  rates.forEach(function (rate, i) {
    if (i) span.appendChild(document.createTextNode(", "));
    span.appendChild(document.createTextNode(formatNumber(rate.per_min) + " "));
    span.appendChild(itemLink(rate.item, rate.name));
  });
  return span;
}

export function saveNote(parent: HTMLElement, text: string | null): void {
  if (text) appendNote(parent, friendlyError(text));
}

function sortFor(name: string): SortState {
  if (!tableSorts[name]) tableSorts[name] = { key: "", desc: false };
  return tableSorts[name]!;
}

function amount(row: RecipeRow): string {
  return row.kind === "part" ? perMin(row.qty) : formatNumber(row.qty) + (QTY_SUFFIX_BY_KIND[row.kind] || "");
}

function kindWord(kind: string): string {
  return RECIPE_KIND[kind] || kind;
}

/* A kind column appears only when asked for and the rows mix kinds. */
export function recipeTable(parent: HTMLElement, rows: RecipeRow[], options: { kinds: boolean; qty: string; sort: string }): void {
  const columns: Column<RecipeRow>[] = [
    {
      key: "recipe",
      label: "recipe",
      sort: function (row) {
        return row.name;
      },
      render: function (row) {
        return recipeLink(row.cls, row.name);
      },
    },
  ];
  const mixed = rows.some(function (row) {
    return row.kind !== rows[0]!.kind;
  });
  if (options.kinds && mixed) {
    columns.push({
      key: "kind",
      label: "kind",
      sort: function (row) {
        return kindWord(row.kind);
      },
      render: function (row) {
        return kindWord(row.kind);
      },
    });
  }
  columns.push({
    key: "machine",
    label: "machine",
    sort: function (row) {
      return row.machine || "";
    },
    render: function (row) {
      return row.machine || "–";
    },
  });
  if (options.qty) {
    columns.push({
      key: "qty",
      label: options.qty,
      align: "right",
      title: "per minute in a machine at 100%; per build or per craft otherwise",
      render: amount,
    });
  }
  if (showsStatus(rows)) columns.push(statusColumn<RecipeRow>());
  parent.appendChild(table(columns, rows, { sort: sortFor(options.sort), onSort: redraw, caption: "recipes" }));
}
