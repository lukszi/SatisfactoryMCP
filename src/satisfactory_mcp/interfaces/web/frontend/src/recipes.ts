/* The Recipes codex: the dashboard's `dash=recipes…` section. See docs/frontend_vision.md §10. */

import { get } from "./api";
import { count, make } from "./dom";
import { hashFor } from "./map";
import { registerFetch } from "./registry";
import { setting } from "./settings";
import { state } from "./state";

import type { ApiError, ApiUrl } from "./api";
import type {
  AlternatesResponse,
  ItemsResponse,
  MakerRow,
  Rate,
  RecipeDetail,
  RecipeRow,
  RecipesResponse,
  UnlockedResponse,
} from "./api-shapes";

type Mode = "items" | "recipes" | "unlocked";

var MODES: [Mode, string][] = [
  ["items", "Items"],
  ["recipes", "Recipes"],
  ["unlocked", "Unlocked"],
];

var KINDS: [string, string][] = [
  ["part", "made in a machine"],
  ["building", "build-gun costs"],
  ["manual", "crafted by hand"],
  ["all", "every kind"],
];

var DEBOUNCE_MS = 180;

var browse = {
  mode: "items" as Mode,
  query: "",
  kind: "part",
  alternatesOnly: false,
  unlockedAll: false,
};

var cache: Record<string, unknown> = {};
var last: Record<string, unknown> = {};
var pending: Record<string, boolean> = {};
var failures: Record<string, string> = {};
var generation = 0;
var redraw: () => void = function () {};
var timer = 0;

var input = make("input", "dash-name rx-q");
input.type = "search";
input.setAttribute("data-candidate", "recipes-q");
input.oninput = function () {
  browse.query = input.value;
  window.clearTimeout(timer);
  timer = window.setTimeout(redraw, DEBOUNCE_MS);
};

function keyOf(path: ApiUrl): string {
  return generation + "|" + state.world + "|" + state.save + "|" + path;
}

function load<T extends ApiError>(path: ApiUrl, slot?: string): { data: T | null; error: string } {
  var key = keyOf(path);
  if (key in cache) {
    if (slot) last[slot] = cache[key];
    return { data: cache[key] as T, error: "" };
  }
  if (key in failures) return { data: null, error: failures[key]! };
  if (!pending[key]) {
    pending[key] = true;
    get<T>(path)
      .then(function (body) {
        cache[key] = body;
      })
      .catch(function (error: unknown) {
        failures[key] = error instanceof Error ? error.message : String(error);
      })
      .then(function () {
        delete pending[key];
        if (key.indexOf(generation + "|") === 0) redraw();
      });
  }
  return { data: slot && last[slot] ? (last[slot] as T) : null, error: "" };
}

function hidesLocked(): boolean {
  return !setting("spoilers");
}

function go(dash: string): string {
  return hashFor(dash);
}

function link(dash: string, text: string): HTMLAnchorElement {
  var a = make("a", "", text);
  a.setAttribute("href", go(dash));
  return a;
}

function itemLink(cls: string, name: string): HTMLAnchorElement {
  return link("recipes/item/" + cls, name);
}

function recipeLink(cls: string, name: string): HTMLAnchorElement {
  return link("recipes/recipe/" + cls, name);
}

function icon(cls: string): HTMLImageElement {
  var img = make("img", "rx-icon");
  img.src = "/api/icons/" + encodeURIComponent(cls);
  img.alt = "";
  img.loading = "lazy";
  img.onerror = function () {
    img.remove();
  };
  return img;
}

function num(value: number): string {
  return (Math.round(value * 100) / 100).toLocaleString("en-GB");
}

function note(parent: HTMLElement, text: string): void {
  parent.appendChild(make("p", "dash-note", text));
}

function hiddenNote(parent: HTMLElement, hidden: number): void {
  if (!hidden) return;
  var p = make("p", "dash-note", count(hidden) + " locked recipe" + (hidden === 1 ? " is" : "s are") + " hidden. ");
  p.appendChild(link("settings", "Settings"));
  p.appendChild(document.createTextNode(" can show them."));
  parent.appendChild(p);
}

function waiting(parent: HTMLElement, error: string): void {
  note(parent, error || "loading…");
}

function status(unlocked: boolean | null): HTMLElement {
  if (unlocked === null) return make("span", "dash-muted", "–");
  return make("span", unlocked ? "rx-have" : "rx-locked", unlocked ? "HAVE" : "LOCKED");
}

function table(headers: string[], numeric: number[]): HTMLTableElement {
  var t = make("table", "dash-table");
  var tr = make("tr");
  headers.forEach(function (h, i) {
    tr.appendChild(make("th", numeric.indexOf(i) >= 0 ? "num" : "", h));
  });
  var head = make("thead");
  head.appendChild(tr);
  t.appendChild(head);
  t.appendChild(make("tbody"));
  return t;
}

function row(t: HTMLTableElement, cells: (string | HTMLElement)[], numeric: number[]): void {
  var tr = make("tr");
  cells.forEach(function (c, i) {
    var td = make("td", numeric.indexOf(i) >= 0 ? "num" : "");
    if (typeof c === "string") td.textContent = c;
    else td.appendChild(c);
    tr.appendChild(td);
  });
  t.tBodies[0]!.appendChild(tr);
}

function scroll(parent: HTMLElement, t: HTMLTableElement): void {
  var wrap = make("div", "dash-scroll");
  wrap.appendChild(t);
  parent.appendChild(wrap);
}

function flows(rates: Rate[]): HTMLElement {
  var span = make("span", "rx-flows");
  rates.forEach(function (r, i) {
    if (i) span.appendChild(document.createTextNode(", "));
    span.appendChild(document.createTextNode(num(r.per_min) + " "));
    span.appendChild(itemLink(r.item, r.name));
  });
  return span;
}

function recipeName(r: { cls: string; name: string; alternate: boolean }): HTMLElement {
  var box = make("span", "rx-name");
  box.appendChild(recipeLink(r.cls, r.name));
  if (r.alternate) box.appendChild(make("span", "rx-tag", "alt"));
  return box;
}

function query(params: [string, string | boolean][]): string {
  return params
    .filter(function (p) {
      return p[1] !== "" && p[1] !== false;
    })
    .map(function (p) {
      return p[0] + "=" + encodeURIComponent(String(p[1]));
    })
    .join("&");
}

function modeBar(card: HTMLElement): void {
  var bar = make("div", "dash-title rx-bar");
  MODES.forEach(function (m) {
    var b = make("button", "rx-mode" + (browse.mode === m[0] ? " on" : ""), m[1]);
    b.type = "button";
    b.setAttribute("aria-pressed", String(browse.mode === m[0]));
    b.onclick = function () {
      browse.mode = m[0];
      redraw();
    };
    bar.appendChild(b);
  });
  card.appendChild(bar);
}

function checkbox(label: string, on: boolean, change: (on: boolean) => void): HTMLElement {
  var wrap = make("label", "dash-toggle rx-check");
  var box = make("input");
  box.type = "checkbox";
  box.checked = on;
  box.onchange = function () {
    change(box.checked);
    redraw();
  };
  wrap.appendChild(box);
  wrap.appendChild(document.createTextNode(" " + label));
  return wrap;
}

function renderItems(card: HTMLElement): void {
  var got = load<ItemsResponse>(`/api/gamedata/items?${query([["q", browse.query.trim()]])}`, "items");
  if (!got.data) return waiting(card, got.error);
  var data = got.data;
  note(card, count(data.total) + " item" + (data.total === 1 ? "" : "s") + (data.items.length < data.total ? ", first " + data.items.length + " shown" : ""));
  if (!data.items.length) return;
  var t = table(["item", "form", "energy MJ", "sink points"], [2, 3]);
  data.items.forEach(function (i) {
    var name = make("span", "rx-name");
    name.appendChild(icon(i.cls));
    name.appendChild(itemLink(i.cls, i.name));
    row(t, [name, i.fluid ? "fluid" : "solid", i.energy_mj ? num(i.energy_mj) : "–", i.sink_points ? count(i.sink_points) : "–"], [2, 3]);
  });
  scroll(card, t);
}

function censusLine(data: RecipesResponse): string {
  var c = data.census;
  var parts = ["part", "building", "manual"]
    .filter(function (k) {
      return c.by_kind[k];
    })
    .map(function (k) {
      var gate = c.have[k] || c.locked[k] ? " (" + (c.have[k] || 0) + " have" + (hidesLocked() ? "" : ", " + (c.locked[k] || 0) + " locked") + ")" : "";
      return c.by_kind[k] + " " + k + gate;
    });
  return c.total ? count(c.total) + " recipes match: " + parts.join(", ") : "no recipe matches";
}

function recipeRows(card: HTMLElement, rows: RecipeRow[], qty: string): void {
  var shown = hidesLocked()
    ? rows.filter(function (r) {
        return r.unlocked !== false;
      })
    : rows;
  var numeric = qty ? [3] : [];
  var t = table(qty ? ["recipe", "kind", "machine", qty, "status"] : ["recipe", "kind", "machine", "status"], numeric);
  shown.forEach(function (r) {
    var cells: (string | HTMLElement)[] = [recipeName(r), r.kind, r.machine || "–"];
    if (qty) cells.push(num(r.qty) + (r.kind === "part" ? "/min" : r.kind === "building" ? "/build" : "/craft"));
    cells.push(status(r.unlocked));
    row(t, cells, numeric);
  });
  if (shown.length) scroll(card, t);
  hiddenNote(card, rows.length - shown.length);
}

function renderRecipeSearch(card: HTMLElement): void {
  var controls = make("div", "rx-controls");
  var kind = make("select", "dash-select");
  KINDS.forEach(function (k) {
    var option = make("option", "", k[1]);
    option.value = k[0];
    kind.appendChild(option);
  });
  kind.value = browse.kind;
  kind.onchange = function () {
    browse.kind = kind.value;
    redraw();
  };
  controls.appendChild(kind);
  controls.appendChild(
    checkbox("alternates only", browse.alternatesOnly, function (on) {
      browse.alternatesOnly = on;
    })
  );
  card.appendChild(controls);
  var got = load<RecipesResponse>(
    `/api/gamedata/recipes?${query([
      ["q", browse.query.trim()],
      ["recipe_kind", browse.kind],
      ["only_alternates", browse.alternatesOnly],
    ])}`,
    "recipes"
  );
  if (!got.data) return waiting(card, got.error);
  note(card, censusLine(got.data));
  if (got.data.save_note) note(card, got.data.save_note);
  recipeRows(card, got.data.recipes, "");
}

function renderUnlocked(card: HTMLElement): void {
  card.appendChild(
    checkbox("every automatable recipe, not only alternates", browse.unlockedAll, function (on) {
      browse.unlockedAll = on;
    })
  );
  var got = load<UnlockedResponse>(`/api/gamedata/unlocked?only_alternates=${!browse.unlockedAll}`);
  if (!got.data) return waiting(card, got.error);
  var data = got.data;
  note(
    card,
    data.alternates_unlocked + " of " + data.alternates_total + " alternates unlocked; " + count(data.automatable_total) + " automatable recipes in all. " + data.age_note
  );
  var q = browse.query.trim().toLowerCase();
  var rows = data.recipes.filter(function (r) {
    return !q || r.name.toLowerCase().indexOf(q) >= 0;
  });
  if (!rows.length) return note(card, q ? "none of them match" : "nothing unlocked yet");
  var t = table(["recipe", "machine"], []);
  rows.forEach(function (r) {
    row(t, [recipeLink(r.cls, r.name), r.machine || "–"], []);
  });
  scroll(card, t);
}

function renderBrowse(body: HTMLElement): void {
  var card = make("section", "dash-card");
  modeBar(card);
  input.placeholder =
    browse.mode === "items" ? "filter items by name" : browse.mode === "recipes" ? "filter recipes by name" : "filter unlocked recipes";
  if (input.value !== browse.query) input.value = browse.query;
  card.appendChild(input);
  if (browse.mode === "items") renderItems(card);
  else if (browse.mode === "recipes") renderRecipeSearch(card);
  else renderUnlocked(card);
  body.appendChild(card);
}

function title(body: HTMLElement, text: string, cls: string, tag: string): void {
  var bar = make("div", "dash-title");
  bar.appendChild(link("recipes", "‹ codex"));
  bar.lastElementChild!.className = "dash-back";
  var h = make("h1", "rx-title");
  if (cls) h.appendChild(icon(cls));
  h.appendChild(document.createTextNode(text));
  bar.appendChild(h);
  if (tag) bar.appendChild(make("span", "rx-tag", tag));
  body.appendChild(bar);
}

function makerTable(card: HTMLElement, rows: MakerRow[]): void {
  var shown = hidesLocked()
    ? rows.filter(function (r) {
        return r.unlocked !== false;
      })
    : rows;
  if (shown.length) {
    var t = table(["recipe", "machine", "in /min", "out /min", "status", "granted by"], []);
    shown.forEach(function (r) {
      row(t, [recipeName(r), r.machine ? r.machine + " · " + num(r.power_mw) + " MW" : "–", flows(r.ingredients), flows(r.products), status(r.unlocked), r.granted_by.join("; ")], []);
    });
    scroll(card, t);
  } else if (!rows.length) note(card, "nothing makes this in a machine");
  hiddenNote(card, rows.length - shown.length);
}

function renderItem(body: HTMLElement, cls: string): void {
  var got = load<AlternatesResponse>(`/api/gamedata/alternates?item=${encodeURIComponent(cls)}`);
  if (!got.data) return waiting(body, got.error);
  var data = got.data;
  title(body, data.name, data.item, data.fluid ? "fluid" : "solid");
  var facts = [data.energy_mj ? num(data.energy_mj) + " MJ" + (data.fluid ? " per m³" : " each") : "", data.sink_points ? count(data.sink_points) + " sink points" : ""].filter(Boolean);
  if (facts.length) note(body, facts.join(" · "));
  if (data.save_note) note(body, data.save_note);

  var made = make("section", "dash-card");
  made.appendChild(make("h2", "dash-h", "made by"));
  makerTable(made, data.recipes);
  body.appendChild(made);

  var used = make("section", "dash-card");
  used.appendChild(make("h2", "dash-h", "used by"));
  var uses = load<RecipesResponse>(`/api/gamedata/recipes?consumes=${encodeURIComponent(cls)}&recipe_kind=all`);
  if (!uses.data) waiting(used, uses.error);
  else {
    note(used, censusLine(uses.data));
    if (uses.data.recipes.length) recipeRows(used, uses.data.recipes, "uses");
  }
  body.appendChild(used);
}

function renderRecipe(body: HTMLElement, cls: string): void {
  var got = load<RecipeDetail>(`/api/gamedata/recipe?recipe=${encodeURIComponent(cls)}`);
  if (!got.data) return waiting(body, got.error);
  var r = got.data;
  if (r.unlocked === false && hidesLocked()) {
    title(body, "Locked recipe", "", "");
    var p = make("p", "dash-note", "This recipe is not unlocked on this save. ");
    p.appendChild(link("settings", "Settings"));
    p.appendChild(document.createTextNode(" can show locked recipes."));
    body.appendChild(p);
    return;
  }
  title(body, r.name, "", r.alternate ? "alternate" : r.kind);
  var head = make("p", "rx-status");
  head.appendChild(status(r.unlocked));
  if (r.save_note) head.appendChild(make("span", "dash-note", " " + r.save_note));
  body.appendChild(head);

  var card = make("section", "dash-card");
  var facts: [string, string][] = [
    ["machine", r.machine || "–"],
    ["cycle", num(r.duration_s) + " s"],
    ["power", r.power_range_mw ? num(r.power_range_mw[0]) + "–" + num(r.power_range_mw[1]) + " MW, " + num(r.power_mw) + " MW average" : num(r.power_mw) + " MW"],
    ["granted by", r.granted_by.join("; ") || "no known unlock"],
  ];
  var list = make("dl", "rx-facts");
  facts.forEach(function (f) {
    list.appendChild(make("dt", "", f[0]));
    list.appendChild(make("dd", "", f[1]));
  });
  card.appendChild(list);
  body.appendChild(card);

  var unit = r.kind === "part" ? "per min, one machine at 100%" : r.kind === "building" ? "per build" : "per craft";
  [
    ["in", r.ingredients],
    ["out", r.products],
  ].forEach(function (side) {
    var rates = side[1] as Rate[];
    var box = make("section", "dash-card");
    box.appendChild(make("h2", "dash-h", side[0] + " (" + unit + ")"));
    var t = table(["item", "rate"], [1]);
    rates.forEach(function (x) {
      var name = make("span", "rx-name");
      name.appendChild(icon(x.item));
      name.appendChild(itemLink(x.item, x.name));
      row(t, [name, num(x.per_min)], [1]);
    });
    scroll(box, t);
    body.appendChild(box);
  });
}

export function renderRecipes(body: HTMLElement, subject: string, rerender: () => void): void {
  redraw = rerender;
  var cut = subject.indexOf("/");
  var kind = cut < 0 ? subject : subject.slice(0, cut);
  var id = cut < 0 ? "" : subject.slice(cut + 1);
  if (kind === "item" && id) renderItem(body, id);
  else if (kind === "recipe" && id) renderRecipe(body, id);
  else renderBrowse(body);
}

registerFetch<UnlockedResponse>({
  wave: "live",
  rank: 70,
  path: "/api/gamedata/unlocked?only_alternates=true",
  label: "unlocked recipes",
  clears: [],
  refilters: false,
  draw: function (data) {
    generation += 1;
    cache = {};
    failures = {};
    cache[keyOf("/api/gamedata/unlocked?only_alternates=true")] = data;
    redraw();
  },
  failed: function () {
    generation += 1;
    cache = {};
    failures = {};
    redraw();
  },
});
