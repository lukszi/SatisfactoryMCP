/* Recipes > one item or one recipe, `dash=recipes/item/<cls>` and `dash=recipes/recipe/<cls>`. */

import { isNotFound } from "../../api/client";
import { appendNote, empty, error, link, loading, table } from "../../kit/dashkit";
import { make } from "../../kit/dom";
import { count, formatNumber, perMin } from "../../kit/format";
import { decodeOrKeep } from "../../app/nav";
import { friendlyError } from "../../kit/toast";
import { RECIPE_KIND } from "../../kit/words";
import { backTo, censusLine, countLine } from "./browse";
import { cachedFetch, waiting } from "./cache";
import {
  flows,
  hidesLocked,
  icon,
  itemNameWithIcon,
  recipeLink,
  recipeTable,
  saveNote,
  showsStatus,
  spoilers,
  statusColumn,
  unlockBadge,
} from "./cells";

import type { StatusError } from "../../api/client";
import type { Column } from "../../kit/dashkit";
import type { AlternatesResponse, MakerRow, Rate, RecipeDetail, RecipesResponse } from "../../api/shapes";
import type { CachedFetch } from "./cache";
import type { Mode } from "./browse";

function backLink(body: HTMLElement, back: Mode): void {
  body.appendChild(link(backTo(back), "‹ all " + back, "dash-back"));
}

function detailHeader(body: HTMLElement, back: Mode, text: string, cls: string, tag: string): void {
  backLink(body, back);
  const bar = make("div", "dash-title");
  const title = make("h1", "rx-title");
  if (cls) title.appendChild(icon(cls));
  title.appendChild(document.createTextNode(text));
  bar.appendChild(title);
  if (tag) bar.appendChild(make("span", "rx-tag", tag));
  body.appendChild(bar);
}

function ambiguous(reason: unknown): boolean {
  return (reason as StatusError | null)?.status === 409;
}

/* Not found and ambiguous are answers, drawn as empty; anything else is an error to retry. */
function renderDetailUnavailable<T>(body: HTMLElement, fetched: CachedFetch<T>, back: Mode, thing: string, how: string): void {
  backLink(body, back);
  if (!fetched.failure) loading(body, thing);
  else if (isNotFound(fetched.failure) || ambiguous(fetched.failure)) empty(body, friendlyError(fetched.failure), how);
  else error(body, thing, fetched.failure, fetched.retry);
}

function settingsLinkHint(text: string): HTMLElement {
  const hint = make("span");
  hint.appendChild(link("settings", "Settings"));
  hint.appendChild(document.createTextNode(" " + text));
  return hint;
}

function candidates(body: HTMLElement, name: string): void {
  const fetched = cachedFetch<RecipesResponse>("recipes:candidates", `/api/gamedata/recipes?q=${encodeURIComponent(name)}&recipe_kind=all${spoilers()}`);
  if (!fetched.data?.recipes.length) return;
  const card = make("section", "dash-card");
  card.appendChild(make("h2", "dash-h", "recipes with “" + name + "” in the name"));
  recipeTable(card, fetched.data.recipes, { kinds: true, qty: "", sort: "candidates" });
  body.appendChild(card);
}

function makerTable(card: HTMLElement, rows: MakerRow[]): void {
  const columns: Column<MakerRow>[] = [
    {
      key: "recipe",
      label: "recipe",
      render: function (maker) {
        return recipeLink(maker.cls, maker.name);
      },
    },
    {
      key: "machine",
      label: "machine",
      render: function (maker) {
        return maker.machine ? maker.machine + " · " + formatNumber(maker.power_mw) + " MW" : "–";
      },
    },
    {
      key: "in",
      label: "in /min",
      render: function (maker) {
        return flows(maker.ingredients);
      },
    },
    {
      key: "out",
      label: "out /min",
      render: function (maker) {
        return flows(maker.products);
      },
    },
  ];
  if (showsStatus(rows)) columns.push(statusColumn<MakerRow>());
  columns.push({
    key: "granted",
    label: "granted by",
    render: function (maker) {
      return maker.granted_by.join("; ") || "–";
    },
  });
  card.appendChild(table(columns, rows, { caption: "recipes that make this item" }));
}

function usedByCard(body: HTMLElement, cls: string): void {
  const used = make("section", "dash-card");
  used.appendChild(make("h2", "dash-h", "used by"));
  const uses = cachedFetch<RecipesResponse>("recipes:used", `/api/gamedata/recipes?consumes=${encodeURIComponent(cls)}&recipe_kind=all${spoilers()}`);
  if (!waiting(used, uses, "the recipes that use it")) {
    const rows = uses.data!.recipes;
    if (!rows.length) empty(used, hidesLocked() ? "no unlocked recipe uses this" : "no recipe uses this");
    else {
      appendNote(used, censusLine(uses.data!, "all"));
      recipeTable(used, rows, { kinds: true, qty: "uses", sort: "used" });
    }
  }
  body.appendChild(used);
}

function energyFact(item: AlternatesResponse): string {
  if (!item.energy_mj) return "";
  return formatNumber(item.energy_mj) + " MJ" + (item.fluid ? " per m³" : " each");
}

export function renderItem(body: HTMLElement, cls: string): void {
  const fetched = cachedFetch<AlternatesResponse>("recipes", `/api/gamedata/alternates?item=${encodeURIComponent(cls)}${spoilers()}`);
  if (!fetched.data) {
    renderDetailUnavailable(body, fetched, "items", "the item", "search for it by name in the header");
    return;
  }
  const data = fetched.data;
  if (data.build_recipe && !data.recipes.length) {
    detailHeader(body, "items", data.name, data.item, "building");
    const built = make("p", "dash-note", "placed with the build gun, not made in a machine: see ");
    built.appendChild(recipeLink(data.build_recipe, "its build cost"));
    body.appendChild(built);
    return;
  }
  detailHeader(body, "items", data.name, data.item, data.fluid ? "fluid" : "solid");
  const facts = [
    energyFact(data),
    data.sink_points ? count(data.sink_points) + " sink points" : "",
  ].filter(Boolean);
  if (facts.length) appendNote(body, facts.join(" · "));
  saveNote(body, data.save_note);
  countLine(body, "", data.save_note === null);

  const made = make("section", "dash-card");
  made.appendChild(make("h2", "dash-h", "made by"));
  if (data.recipes.length) makerTable(made, data.recipes);
  else empty(made, hidesLocked() ? "no unlocked recipe makes this in a machine" : "nothing makes this in a machine");
  body.appendChild(made);
  usedByCard(body, cls);
}

function rateTable(box: HTMLElement, rates: Rate[], part: boolean, linked: boolean): void {
  const columns: Column<Rate>[] = [
    {
      key: "item",
      label: "item",
      render: function (rate) {
        return itemNameWithIcon(rate.item, rate.name, linked);
      },
    },
    {
      key: "rate",
      label: part ? "per min" : "amount",
      align: "right",
      render: function (rate) {
        return part ? perMin(rate.per_min, false) : formatNumber(rate.amount);
      },
    },
  ];
  box.appendChild(table(columns, rates));
}

function powerText(recipe: RecipeDetail): string {
  const range = recipe.power_range_mw;
  if (!range) return formatNumber(recipe.power_mw) + " MW";
  return formatNumber(range[0]) + "–" + formatNumber(range[1]) + " MW, " + formatNumber(recipe.power_mw) + " MW average";
}

function recipeFacts(recipe: RecipeDetail, part: boolean): HTMLElement {
  const facts: [string, string][] = part
    ? [
        ["machine", recipe.machine || "–"],
        ["cycle", formatNumber(recipe.duration_s, 2) + " s"],
        ["power", powerText(recipe)],
      ]
    : [];
  facts.push(["granted by", recipe.granted_by.join("; ") || "no known unlock"]);
  const list = make("dl", "rx-facts");
  facts.forEach(function (fact) {
    list.appendChild(make("dt", "", fact[0]));
    list.appendChild(make("dd", "", fact[1]));
  });
  return list;
}

// What one row of the in and out tables counts.
function rateUnit(recipe: RecipeDetail): string {
  if (recipe.kind === "part") return "per min, one machine at 100%";
  return recipe.kind === "building" ? "per build" : "per craft";
}

export function renderRecipe(body: HTMLElement, cls: string): void {
  const fetched = cachedFetch<RecipeDetail>("recipes", `/api/gamedata/recipe?recipe=${encodeURIComponent(cls)}${spoilers()}`);
  if (!fetched.data) {
    renderDetailUnavailable(body, fetched, "recipes", "the recipe", ambiguous(fetched.failure) ? "pick one below" : "search for it by name in the header");
    if (fetched.failure) candidates(body, decodeOrKeep(cls));
    return;
  }
  const recipe = fetched.data;
  if (recipe.unlocked === false && hidesLocked()) {
    detailHeader(body, "recipes", "Locked recipe", "", "");
    empty(body, "this recipe is not unlocked in this world", settingsLinkHint("can show locked recipes"));
    return;
  }
  const part = recipe.kind === "part";
  detailHeader(body, "recipes", recipe.name, "", part ? "" : RECIPE_KIND[recipe.kind] || recipe.kind);
  if (recipe.unlocked !== null && !hidesLocked()) {
    const head = make("p", "rx-status");
    head.appendChild(unlockBadge(recipe.unlocked));
    body.appendChild(head);
  }
  saveNote(body, recipe.save_note);

  const card = make("section", "dash-card");
  card.appendChild(recipeFacts(recipe, part));
  body.appendChild(card);

  const unit = rateUnit(recipe);
  [
    { label: "in", rates: recipe.ingredients, linked: true },
    { label: "out", rates: recipe.products, linked: recipe.kind !== "building" },
  ].forEach(function (side) {
    const box = make("section", "dash-card");
    box.appendChild(make("h2", "dash-h", side.label + " (" + unit + ")"));
    rateTable(box, side.rates, part, side.linked);
    body.appendChild(box);
  });
}
