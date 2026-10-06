/* Recipes > browse, `dash=recipes/<mode>?q=…`: the item list, the recipe search and the
 * unlocked recipes, with one search box kept across redraws and each list's scroll restored. */

import { appendNote, checkbox, empty, selectBox, settingsLinkNote, subTabs, table } from "../../kit/dashkit";
import { el, make } from "../../kit/dom";
import { count, formatNumber } from "../../kit/format";
import { hashFor, writeHash } from "../../map/map";
import { go, subjectQuery, withQuery } from "../../app/nav";
import { state } from "../../app/state";
import { counted, RECIPE_KIND } from "../../kit/words";
import { cachedFetch, PRIMED_PATH, redraw, waiting } from "./cache";
import { hidesLocked, itemNameWithIcon, recipeLink, recipeTable, saveNote, spoilers } from "./cells";

import type { ApiUrl } from "../../api/client";
import type { Column, SortState } from "../../kit/dashkit";
import type { ItemRow, ItemsResponse, RecipesResponse, UnlockedResponse } from "../../api/shapes";

export type Mode = "items" | "recipes" | "unlocked";

interface Browse {
  mode: Mode;
  query: string;
  kind: string;
  alternatesOnly: boolean;
  everyAutomatable: boolean;
}

const MODES: [Mode, string][] = [
  ["items", "Items"],
  ["recipes", "Recipes"],
  ["unlocked", "Unlocked"],
];

const KINDS: [string, string][] = [
  ["part", "made in a machine"],
  ["building", "build-gun costs"],
  ["manual", "crafted by hand"],
  ["all", "every kind"],
];

const DEBOUNCE_MS = 180;

const itemSort: SortState = { key: "item", desc: false };
const unlockedSort: SortState = { key: "recipe", desc: false };

let searchTimer = 0;
let lastBrowse = "recipes";
const scrollByDash: Record<string, number> = {};
let restoring: { dash: string; top: number } | null = null;

const searchInput = make("input", "dash-name rx-q");
searchInput.type = "search";
searchInput.setAttribute("data-candidate", "recipes-q");
searchInput.oninput = function () {
  window.clearTimeout(searchTimer);
  searchTimer = window.setTimeout(function () {
    state.dash = browseDash(withBrowse(parseBrowse(subjectOf(state.dash)), { query: searchInput.value.trim() }));
    writeHash();
    redraw();
  }, DEBOUNCE_MS);
};

export function subjectOf(dash: string): string {
  const cut = dash.indexOf("/");
  return cut < 0 ? "" : dash.slice(cut + 1);
}

/* `item/<cls>` or `recipe/<cls>`: a detail page rather than a list. */
export function detailOf(subject: string): { kind: string; id: string } | null {
  const cut = subject.indexOf("/");
  if (cut < 0) return null;
  const kind = subject.slice(0, cut);
  const id = subject.slice(cut + 1);
  return id && (kind === "item" || kind === "recipe") ? { kind: kind, id: id } : null;
}

function parseBrowse(subject: string): Browse {
  const parts = subjectQuery(subject);
  const params = parts.params;
  const browse: Browse = {
    mode: "items",
    query: params.q || "",
    kind: "part",
    alternatesOnly: params.alt === "1",
    everyAutomatable: params.all === "1",
  };
  MODES.forEach(function (mode) {
    if (mode[0] === parts.head) browse.mode = mode[0];
  });
  if (KINDS.some(function (kind) { return kind[0] === params.kind; })) browse.kind = params.kind!;
  return browse;
}

function browseDash(browse: Browse): string {
  return withQuery("recipes/" + browse.mode, {
    q: browse.query,
    kind: browse.mode === "recipes" && browse.kind !== "part" ? browse.kind : "",
    alt: browse.mode === "recipes" && browse.alternatesOnly ? "1" : "",
    all: browse.mode === "unlocked" && browse.everyAutomatable ? "1" : "",
  });
}

function withBrowse(browse: Browse, patch: Partial<Browse>): Browse {
  return { ...browse, ...patch };
}

export function censusLine(data: RecipesResponse, kind: string): string {
  const rows = data.recipes;
  const have = rows.filter(function (row) {
    return row.unlocked === true;
  }).length;
  const locked = rows.filter(function (row) {
    return row.unlocked === false;
  }).length;
  const census = data.census;
  const asked = kind === "all" ? census.total : census.by_kind[kind] || 0;
  const parts = [counted(rows.length, kind === "all" || kind === "part" ? "recipe" : RECIPE_KIND[kind] + " recipe")];
  if (kind === "all") {
    const split = ["part", "building", "manual"]
      .filter(function (each) {
        return rows.some(function (row) {
          return row.kind === each;
        });
      })
      .map(function (each) {
        const n = rows.filter(function (row) {
          return row.kind === each;
        }).length;
        return count(n) + " " + RECIPE_KIND[each];
      });
    if (split.length > 1) parts[0] += " (" + split.join(", ") + ")";
  }
  if (locked) parts.push(count(have) + " have" + (locked ? ", " + count(locked) + " locked" : ""));
  if (asked > rows.length) parts.push(counted(asked - rows.length, "event recipe") + " hidden");
  return parts.join(" · ");
}

/* `readable`: the save could be read, so "locked recipes hidden" is a true statement. */
export function countLine(parent: HTMLElement, text: string, readable: boolean): void {
  if (hidesLocked() && readable) settingsLinkNote(parent, text + (text ? " · " : "") + "locked recipes hidden; ", " can show them");
  else if (text) appendNote(parent, text);
}

function modeBar(card: HTMLElement, browse: Browse): void {
  const items = MODES.map(function (mode) {
    return { id: mode[0], label: mode[1], href: hashFor(browseDash(withBrowse(browse, { mode: mode[0] }))) };
  });
  card.appendChild(subTabs(items, browse.mode, undefined, "recipe book view"));
}

function itemColumns(): Column<ItemRow>[] {
  return [
    {
      key: "item",
      label: "item",
      sort: function (item) {
        return item.name;
      },
      render: function (item) {
        return itemNameWithIcon(item.cls, item.name, true);
      },
    },
    {
      key: "form",
      label: "form",
      sort: function (item) {
        return item.fluid ? 1 : 0;
      },
      render: function (item) {
        return item.fluid ? "fluid" : "solid";
      },
    },
    {
      key: "energy",
      label: "energy MJ",
      align: "right",
      sort: function (item) {
        return item.energy_mj;
      },
      render: function (item) {
        return item.energy_mj ? formatNumber(item.energy_mj) : "–";
      },
    },
    {
      key: "sink",
      label: "sink points",
      align: "right",
      sort: function (item) {
        return item.sink_points;
      },
      render: function (item) {
        return item.sink_points ? count(item.sink_points) : "–";
      },
    },
  ];
}

function renderItems(card: HTMLElement, browse: Browse): boolean {
  const fetched = cachedFetch<ItemsResponse>("recipes", `/api/gamedata/items?q=${encodeURIComponent(browse.query)}`, { keepPrevious: true });
  if (waiting(card, fetched, "the item list")) return false;
  const data = fetched.data!;
  if (!data.items.length) {
    empty(card, browse.query ? "no item matches “" + browse.query + "”" : "no items", browse.query ? "clear the filter to see every item" : undefined);
    return true;
  }
  appendNote(card, counted(data.total, "item") + (data.items.length < data.total ? ", the first " + count(data.items.length) + " shown" : ""));
  card.appendChild(table(itemColumns(), data.items, { sort: itemSort, onSort: redraw, caption: "items" }));
  return true;
}

function renderRecipeSearch(card: HTMLElement, browse: Browse): boolean {
  const controls = make("div", "rx-controls");
  controls.appendChild(
    selectBox(
      KINDS,
      browse.kind,
      function (value) {
        go(browseDash(withBrowse(browse, { kind: value })));
      },
      { label: "recipe kind", candidate: "recipes-kind" }
    )
  );
  controls.appendChild(
    checkbox(
      "alternates only",
      browse.alternatesOnly,
      function (on) {
        go(browseDash(withBrowse(browse, { alternatesOnly: on })));
      },
      { candidate: "recipes-alt", className: "rx-check" }
    )
  );
  card.appendChild(controls);
  const path: ApiUrl = `/api/gamedata/recipes?q=${encodeURIComponent(browse.query)}&recipe_kind=${browse.kind}${browse.alternatesOnly ? "&only_alternates=true" : ""}${spoilers()}`;
  const fetched = cachedFetch<RecipesResponse>("recipes", path, { keepPrevious: true });
  if (waiting(card, fetched, "the recipe list")) return false;
  const data = fetched.data!;
  saveNote(card, data.save_note);
  if (!data.recipes.length) {
    const what = browse.alternatesOnly ? "alternate recipe" : "recipe";
    empty(
      card,
      browse.query ? "no " + what + " matches “" + browse.query + "”" : "no " + what + " of this kind",
      browse.query ? "clear the filter or pick another kind" : undefined
    );
    countLine(card, "", data.save_note === null);
    return true;
  }
  countLine(card, censusLine(data, browse.kind), data.save_note === null);
  recipeTable(card, data.recipes, { kinds: browse.kind === "all", qty: "", sort: "recipes" });
  return true;
}

function savedFrom(data: UnlockedResponse): string {
  return "from the " + data.save_kind + (data.written_ago ? ", written " + data.written_ago : "");
}

function unlockedColumns(): Column<UnlockedResponse["recipes"][number]>[] {
  return [
    {
      key: "recipe",
      label: "recipe",
      sort: function (recipe) {
        return recipe.name;
      },
      render: function (recipe) {
        return recipeLink(recipe.cls, recipe.name);
      },
    },
    {
      key: "machine",
      label: "machine",
      sort: function (recipe) {
        return recipe.machine || "";
      },
      render: function (recipe) {
        return recipe.machine || "–";
      },
    },
  ];
}

function renderUnlocked(card: HTMLElement, browse: Browse): boolean {
  card.appendChild(
    checkbox(
      "every automatable recipe, not only alternates",
      browse.everyAutomatable,
      function (on) {
        go(browseDash(withBrowse(browse, { everyAutomatable: on })));
      },
      { candidate: "recipes-all", className: "rx-check" }
    )
  );
  const fetched = cachedFetch<UnlockedResponse>("recipes", browse.everyAutomatable ? "/api/gamedata/unlocked?only_alternates=false" : PRIMED_PATH, { keepPrevious: true });
  if (waiting(card, fetched, "the unlocked recipes")) return false;
  const data = fetched.data!;
  const alternates = count(data.alternates_unlocked) + (hidesLocked() ? "" : " of " + count(data.alternates_total)) + " alternates unlocked";
  appendNote(card, [alternates, counted(data.automatable_total, "automatable recipe") + " in all", savedFrom(data)].join(" · "));
  const query = browse.query.toLowerCase();
  const rows = data.recipes.filter(function (recipe) {
    return !query || recipe.name.toLowerCase().indexOf(query) >= 0;
  });
  if (!rows.length) {
    const what = browse.everyAutomatable ? "unlocked recipe" : "unlocked alternate";
    empty(
      card,
      query ? "no " + what + " matches “" + browse.query + "”" : "no " + what + " yet",
      query ? "clear the filter to see all " + count(data.recipes.length) : undefined
    );
    return true;
  }
  card.appendChild(table(unlockedColumns(), rows, { sort: unlockedSort, onSort: redraw, caption: "unlocked recipes" }));
  return true;
}

function trackBrowseScroll(): void {
  const dash = el("dash");
  dash.addEventListener("scroll", function () {
    if (state.dash.indexOf("recipes") === 0 && !detailOf(subjectOf(state.dash))) scrollByDash[state.dash] = dash.scrollTop;
  });
}

function restoreBrowseScroll(): void {
  if (!restoring || restoring.dash !== state.dash) return;
  const top = restoring.top;
  restoring = null;
  if (!top) return;
  window.requestAnimationFrame(function () {
    el("dash").scrollTop = top;
  });
}

/* `arrived`: the address changed since the last draw, so the list's old scroll comes back. */
export function renderBrowse(body: HTMLElement, subject: string, arrived: boolean): void {
  const browse = parseBrowse(subject);
  if (arrived) restoring = { dash: state.dash, top: scrollByDash[state.dash] || 0 };
  lastBrowse = state.dash;
  const card = make("section", "dash-card");
  modeBar(card, browse);
  searchInput.placeholder = browse.mode === "items" ? "filter items by name" : browse.mode === "recipes" ? "filter recipes by name" : "filter unlocked recipes";
  searchInput.setAttribute("aria-label", searchInput.placeholder);
  if (document.activeElement !== searchInput && searchInput.value !== browse.query) searchInput.value = browse.query;
  card.appendChild(searchInput);
  body.appendChild(card);
  const drawn =
    browse.mode === "items" ? renderItems(card, browse) : browse.mode === "recipes" ? renderRecipeSearch(card, browse) : renderUnlocked(card, browse);
  if (drawn) restoreBrowseScroll();
}

/* The way back from a detail page: the last list of that mode, filters and all. */
export function backTo(mode: Mode): string {
  const browse = parseBrowse(subjectOf(lastBrowse));
  return browse.mode === mode ? lastBrowse : browseDash({ mode: mode, query: "", kind: "part", alternatesOnly: false, everyAutomatable: false });
}

trackBrowseScroll();
