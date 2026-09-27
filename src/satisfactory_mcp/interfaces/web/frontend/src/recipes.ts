/* The Recipes codex: the dashboard's `dash=recipes…` section. See docs/frontend_vision.md §10. */

import { get, latest, missing } from "./api";
import { empty, error, link, loading, note, table, tabs2 } from "./dashkit";
import { el, make } from "./dom";
import { count, num, perMin } from "./format";
import { hashFor, writeHash } from "./map";
import { decoded, go, subjectQuery, withQuery } from "./nav";
import { registerFetch } from "./registry";
import { setting } from "./settings";
import { state } from "./state";
import { friendly } from "./toast";
import { counted, RECIPE_KIND as KIND_WORD } from "./words";

import type { ApiError, ApiUrl, StatusError } from "./api";
import type { Column, SortState } from "./dashkit";
import type {
  AlternatesResponse,
  ItemRow,
  ItemsResponse,
  MakerRow,
  Rate,
  RecipeDetail,
  RecipeRow,
  RecipesResponse,
  UnlockedResponse,
} from "./api-shapes";

type Mode = "items" | "recipes" | "unlocked";

interface Browse {
  mode: Mode;
  q: string;
  kind: string;
  alt: boolean;
  all: boolean;
}

interface Got<T> {
  data: T | null;
  failure: unknown;
  retry: () => void;
}

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

var PER: Record<string, string> = { building: "/build", manual: "/craft" };

var PRIMED_PATH: ApiUrl = "/api/gamedata/unlocked?only_alternates=true";

var DEBOUNCE_MS = 180;

var cache: Record<string, unknown> = {};
var failed: Record<string, unknown> = {};
var pending: Record<string, boolean> = {};
var shown: Record<string, unknown> = {};
var shownEpoch = -1;
var generation = 0;
var primed = -1;
var redraw: () => void = function () {};
var timer = 0;
var lastBrowse = "recipes";
var rendered = "";
var scrolled: Record<string, number> = {};
var restoring: { dash: string; top: number } | null = null;
var sorts: Record<string, SortState> = {
  items: { key: "item", desc: false },
  recipes: { key: "", desc: false },
  unlocked: { key: "recipe", desc: false },
};

var input = make("input", "dash-name rx-q");
input.type = "search";
input.setAttribute("data-candidate", "recipes-q");
input.oninput = function () {
  window.clearTimeout(timer);
  timer = window.setTimeout(function () {
    var b = parseBrowse(subjectOf(state.dash));
    b.q = input.value.trim();
    state.dash = browseDash(b);
    writeHash();
    redraw();
  }, DEBOUNCE_MS);
};

function subjectOf(dash: string): string {
  var cut = dash.indexOf("/");
  return cut < 0 ? "" : dash.slice(cut + 1);
}

function parseBrowse(subject: string): Browse {
  var parts = subjectQuery(subject);
  var p = parts.params;
  var b: Browse = { mode: "items", q: p.q || "", kind: "part", alt: p.alt === "1", all: p.all === "1" };
  MODES.forEach(function (m) {
    if (m[0] === parts.head) b.mode = m[0];
  });
  if (KINDS.some(function (k) { return k[0] === p.kind; })) b.kind = p.kind!;
  return b;
}

function browseDash(b: Browse): string {
  return withQuery("recipes/" + b.mode, {
    q: b.q,
    kind: b.mode === "recipes" && b.kind !== "part" ? b.kind : "",
    alt: b.mode === "recipes" && b.alt ? "1" : "",
    all: b.mode === "unlocked" && b.all ? "1" : "",
  });
}

function hidesLocked(): boolean {
  return !setting("spoilers");
}

function spoilers(): string {
  return hidesLocked() ? "&spoilers=0" : "";
}

function keyOf(path: ApiUrl): string {
  return state.epoch + "|" + generation + "|" + path;
}

function load<T extends ApiError>(slot: string, path: ApiUrl, keep?: boolean): Got<T> {
  if (shownEpoch !== state.epoch) {
    shownEpoch = state.epoch;
    shown = {};
  }
  var key = keyOf(path);
  var got: Got<T> = {
    data: null,
    failure: null,
    retry: function () {
      delete failed[key];
      redraw();
    },
  };
  if (key in cache) {
    shown[slot] = cache[key];
    got.data = cache[key] as T;
    return got;
  }
  if (key in failed) {
    got.failure = failed[key];
    return got;
  }
  if (keep && shown[slot]) got.data = shown[slot] as T;
  if (path === PRIMED_PATH && primed !== state.epoch) return got;
  if (!pending[key]) {
    pending[key] = true;
    var ticket = latest(slot);
    get<T>(path)
      .then(
        function (body) {
          cache[key] = body;
        },
        function (reason: unknown) {
          failed[key] = reason;
        }
      )
      .then(function () {
        delete pending[key];
        if (ticket.fresh()) redraw();
      });
  }
  return got;
}

function waiting<T>(parent: HTMLElement, got: Got<T>, thing: string): boolean {
  if (got.data) return false;
  if (got.failure) error(parent, thing, got.failure, got.retry);
  else loading(parent, thing);
  return true;
}

function itemLink(cls: string, name: string): HTMLAnchorElement {
  return link("recipes/item/" + cls, name);
}

function recipeLink(cls: string, name: string): HTMLAnchorElement {
  return link("recipes/recipe/" + cls, name);
}

var iconsHere: boolean | null = null;

function probeIcons(): void {
  if (iconsHere !== null) return;
  iconsHere = false;
  fetch("/api/icons/Desc_IronPlate_C", { method: "HEAD" })
    .then(function (r) {
      iconsHere = r.status === 200;
      if (iconsHere) redraw();
    })
    .catch(function () {});
}

function icon(cls: string): Node {
  if (!iconsHere) return document.createTextNode("");
  var img = make("img", "rx-icon");
  img.src = "/api/icons/" + encodeURIComponent(cls);
  img.alt = "";
  img.loading = "lazy";
  img.onerror = function () {
    img.remove();
  };
  return img;
}

function named(cls: string, name: string, linked: boolean): HTMLElement {
  var box = make("span", "rx-name");
  box.appendChild(icon(cls));
  box.appendChild(linked ? itemLink(cls, name) : make("span", "rx-plain", name));
  return box;
}

function status(unlocked: boolean | null): HTMLElement {
  if (unlocked === null) return make("span", "dash-muted", "–");
  return make("span", unlocked ? "rx-have" : "rx-locked", unlocked ? "unlocked" : "locked");
}

function statusRank(unlocked: boolean | null): number {
  return unlocked === null ? 2 : unlocked ? 0 : 1;
}

function statusColumn<R extends { unlocked: boolean | null }>(): Column<R> {
  return {
    key: "status",
    label: "status",
    sort: function (r) {
      return statusRank(r.unlocked);
    },
    render: function (r) {
      return status(r.unlocked);
    },
  };
}

function showsStatus(rows: { unlocked: boolean | null }[]): boolean {
  return !hidesLocked() && rows.some(function (r) {
    return r.unlocked !== null;
  });
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

function settingsLink(text: string): HTMLElement {
  var p = make("span");
  p.appendChild(link("settings", "Settings"));
  p.appendChild(document.createTextNode(" " + text));
  return p;
}

function countLine(parent: HTMLElement, text: string, readable: boolean): void {
  var p = make("p", "dash-note", text);
  if (hidesLocked() && readable) {
    p.appendChild(document.createTextNode((text ? " · " : "") + "locked recipes hidden; "));
    p.appendChild(link("settings", "Settings"));
    p.appendChild(document.createTextNode(" can show them"));
  }
  if (p.childNodes.length) parent.appendChild(p);
}

function saveNote(parent: HTMLElement, text: string | null): void {
  if (text) note(parent, friendly(text));
}

function sortFor(name: string): SortState {
  if (!sorts[name]) sorts[name] = { key: "", desc: false };
  return sorts[name]!;
}

function amount(r: RecipeRow): string {
  return r.kind === "part" ? perMin(r.qty) : num(r.qty) + (PER[r.kind] || "");
}

function recipeTable(parent: HTMLElement, rows: RecipeRow[], options: { kinds: boolean; qty: string; sort: string }): void {
  var columns: Column<RecipeRow>[] = [
    {
      key: "recipe",
      label: "recipe",
      sort: function (r) {
        return r.name;
      },
      render: function (r) {
        return recipeLink(r.cls, r.name);
      },
    },
  ];
  var mixed = rows.some(function (r) {
    return r.kind !== rows[0]!.kind;
  });
  if (options.kinds && mixed) {
    columns.push({
      key: "kind",
      label: "kind",
      sort: function (r) {
        return KIND_WORD[r.kind] || r.kind;
      },
      render: function (r) {
        return KIND_WORD[r.kind] || r.kind;
      },
    });
  }
  columns.push({
    key: "machine",
    label: "machine",
    sort: function (r) {
      return r.machine || "";
    },
    render: function (r) {
      return r.machine || "–";
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

function censusLine(data: RecipesResponse, kind: string): string {
  var rows = data.recipes;
  var have = rows.filter(function (r) {
    return r.unlocked === true;
  }).length;
  var locked = rows.filter(function (r) {
    return r.unlocked === false;
  }).length;
  var c = data.census;
  var asked = kind === "all" ? c.total : c.by_kind[kind] || 0;
  var parts = [counted(rows.length, kind === "all" || kind === "part" ? "recipe" : KIND_WORD[kind] + " recipe")];
  if (kind === "all") {
    var split = ["part", "building", "manual"]
      .filter(function (k) {
        return rows.some(function (r) {
          return r.kind === k;
        });
      })
      .map(function (k) {
        var n = rows.filter(function (r) {
          return r.kind === k;
        }).length;
        return count(n) + " " + KIND_WORD[k];
      });
    if (split.length > 1) parts[0] += " (" + split.join(", ") + ")";
  }
  if (locked) parts.push(count(have) + " have" + (locked ? ", " + count(locked) + " locked" : ""));
  if (asked > rows.length) parts.push(counted(asked - rows.length, "event recipe") + " hidden");
  return parts.join(" · ");
}

function modeBar(card: HTMLElement, b: Browse): void {
  var items = MODES.map(function (m) {
    var to: Browse = { mode: m[0], q: b.q, kind: b.kind, alt: b.alt, all: b.all };
    return { id: m[0], label: m[1], href: hashFor(browseDash(to)) };
  });
  card.appendChild(tabs2(items, b.mode, undefined, "recipe book view"));
}

function checkbox(label: string, on: boolean, candidate: string, change: (on: boolean) => void): HTMLElement {
  var wrap = make("label", "dash-toggle rx-check");
  var box = make("input");
  box.type = "checkbox";
  box.checked = on;
  box.setAttribute("data-candidate", candidate);
  box.onchange = function () {
    change(box.checked);
  };
  wrap.appendChild(box);
  wrap.appendChild(document.createTextNode(" " + label));
  return wrap;
}

function itemColumns(): Column<ItemRow>[] {
  return [
    {
      key: "item",
      label: "item",
      sort: function (i) {
        return i.name;
      },
      render: function (i) {
        return named(i.cls, i.name, true);
      },
    },
    {
      key: "form",
      label: "form",
      sort: function (i) {
        return i.fluid ? 1 : 0;
      },
      render: function (i) {
        return i.fluid ? "fluid" : "solid";
      },
    },
    {
      key: "energy",
      label: "energy MJ",
      align: "right",
      sort: function (i) {
        return i.energy_mj;
      },
      render: function (i) {
        return i.energy_mj ? num(i.energy_mj) : "–";
      },
    },
    {
      key: "sink",
      label: "sink points",
      align: "right",
      sort: function (i) {
        return i.sink_points;
      },
      render: function (i) {
        return i.sink_points ? count(i.sink_points) : "–";
      },
    },
  ];
}

function renderItems(card: HTMLElement, b: Browse): boolean {
  var got = load<ItemsResponse>("recipes", `/api/gamedata/items?q=${encodeURIComponent(b.q)}`, true);
  if (waiting(card, got, "the item list")) return false;
  var data = got.data!;
  if (!data.items.length) {
    empty(card, b.q ? "no item matches “" + b.q + "”" : "no items", b.q ? "clear the filter to see every item" : undefined);
    return true;
  }
  note(card, counted(data.total, "item") + (data.items.length < data.total ? ", the first " + count(data.items.length) + " shown" : ""));
  card.appendChild(table(itemColumns(), data.items, { sort: sorts.items, onSort: redraw, caption: "items" }));
  return true;
}

function renderRecipeSearch(card: HTMLElement, b: Browse): boolean {
  var controls = make("div", "rx-controls");
  var kind = make("select", "dash-select");
  kind.setAttribute("aria-label", "recipe kind");
  kind.setAttribute("data-candidate", "recipes-kind");
  KINDS.forEach(function (k) {
    var option = make("option", "", k[1]);
    option.value = k[0];
    kind.appendChild(option);
  });
  kind.value = b.kind;
  kind.onchange = function () {
    go(browseDash({ mode: b.mode, q: b.q, kind: kind.value, alt: b.alt, all: b.all }));
  };
  controls.appendChild(kind);
  controls.appendChild(
    checkbox("alternates only", b.alt, "recipes-alt", function (on) {
      go(browseDash({ mode: b.mode, q: b.q, kind: b.kind, alt: on, all: b.all }));
    })
  );
  card.appendChild(controls);
  var path: ApiUrl = `/api/gamedata/recipes?q=${encodeURIComponent(b.q)}&recipe_kind=${b.kind}${b.alt ? "&only_alternates=true" : ""}${spoilers()}`;
  var got = load<RecipesResponse>("recipes", path, true);
  if (waiting(card, got, "the recipe list")) return false;
  var data = got.data!;
  saveNote(card, data.save_note);
  if (!data.recipes.length) {
    var what = b.alt ? "alternate recipe" : "recipe";
    empty(card, b.q ? "no " + what + " matches “" + b.q + "”" : "no " + what + " of this kind", b.q ? "clear the filter or pick another kind" : undefined);
    countLine(card, "", data.save_note === null);
    return true;
  }
  countLine(card, censusLine(data, b.kind), data.save_note === null);
  recipeTable(card, data.recipes, { kinds: b.kind === "all", qty: "", sort: "recipes" });
  return true;
}

function savedFrom(data: UnlockedResponse): string {
  return "from the " + data.save_kind + (data.written_ago ? ", written " + data.written_ago : "");
}

function renderUnlocked(card: HTMLElement, b: Browse): boolean {
  card.appendChild(
    checkbox("every automatable recipe, not only alternates", b.all, "recipes-all", function (on) {
      go(browseDash({ mode: b.mode, q: b.q, kind: b.kind, alt: b.alt, all: on }));
    })
  );
  var got = load<UnlockedResponse>("recipes", b.all ? "/api/gamedata/unlocked?only_alternates=false" : PRIMED_PATH, true);
  if (waiting(card, got, "the unlocked recipes")) return false;
  var data = got.data!;
  var alternates = count(data.alternates_unlocked) + (hidesLocked() ? "" : " of " + count(data.alternates_total)) + " alternates unlocked";
  note(card, [alternates, counted(data.automatable_total, "automatable recipe") + " in all", savedFrom(data)].join(" · "));
  var q = b.q.toLowerCase();
  var rows = data.recipes.filter(function (r) {
    return !q || r.name.toLowerCase().indexOf(q) >= 0;
  });
  if (!rows.length) {
    var what = b.all ? "unlocked recipe" : "unlocked alternate";
    empty(card, q ? "no " + what + " matches “" + b.q + "”" : "no " + what + " yet", q ? "clear the filter to see all " + count(data.recipes.length) : undefined);
    return true;
  }
  var columns: Column<UnlockedResponse["recipes"][number]>[] = [
    {
      key: "recipe",
      label: "recipe",
      sort: function (r) {
        return r.name;
      },
      render: function (r) {
        return recipeLink(r.cls, r.name);
      },
    },
    {
      key: "machine",
      label: "machine",
      sort: function (r) {
        return r.machine || "";
      },
      render: function (r) {
        return r.machine || "–";
      },
    },
  ];
  card.appendChild(table(columns, rows, { sort: sorts.unlocked, onSort: redraw, caption: "unlocked recipes" }));
  return true;
}

function remember(): void {
  var dash = el("dash");
  dash.addEventListener("scroll", function () {
    if (state.dash.indexOf("recipes") === 0 && !detailOf(subjectOf(state.dash))) scrolled[state.dash] = dash.scrollTop;
  });
}

function restore(): void {
  if (!restoring || restoring.dash !== state.dash) return;
  var top = restoring.top;
  restoring = null;
  if (!top) return;
  window.requestAnimationFrame(function () {
    el("dash").scrollTop = top;
  });
}

function renderBrowse(body: HTMLElement, subject: string, arrived: boolean): void {
  var b = parseBrowse(subject);
  if (arrived) restoring = { dash: state.dash, top: scrolled[state.dash] || 0 };
  lastBrowse = state.dash;
  var card = make("section", "dash-card");
  modeBar(card, b);
  input.placeholder = b.mode === "items" ? "filter items by name" : b.mode === "recipes" ? "filter recipes by name" : "filter unlocked recipes";
  input.setAttribute("aria-label", input.placeholder);
  if (document.activeElement !== input && input.value !== b.q) input.value = b.q;
  card.appendChild(input);
  body.appendChild(card);
  var drawn = b.mode === "items" ? renderItems(card, b) : b.mode === "recipes" ? renderRecipeSearch(card, b) : renderUnlocked(card, b);
  if (drawn) restore();
}

function backTo(mode: Mode): string {
  var b = parseBrowse(subjectOf(lastBrowse));
  return b.mode === mode ? lastBrowse : browseDash({ mode: mode, q: "", kind: "part", alt: false, all: false });
}

function title(body: HTMLElement, back: Mode, text: string, cls: string, tag: string): void {
  body.appendChild(link(backTo(back), "‹ all " + back, "dash-back"));
  var bar = make("div", "dash-title");
  var h = make("h1", "rx-title");
  if (cls) h.appendChild(icon(cls));
  h.appendChild(document.createTextNode(text));
  bar.appendChild(h);
  if (tag) bar.appendChild(make("span", "rx-tag", tag));
  body.appendChild(bar);
}

function unread<T>(body: HTMLElement, got: Got<T>, back: Mode, thing: string, how: string): void {
  body.appendChild(link(backTo(back), "‹ all " + back, "dash-back"));
  if (!got.failure) loading(body, thing);
  else if (missing(got.failure) || ambiguous(got.failure)) empty(body, friendly(got.failure), how);
  else error(body, thing, got.failure, got.retry);
}

function ambiguous(reason: unknown): boolean {
  return (reason as StatusError | null)?.status === 409;
}

function candidates(body: HTMLElement, name: string): void {
  var got = load<RecipesResponse>("recipes:candidates", `/api/gamedata/recipes?q=${encodeURIComponent(name)}&recipe_kind=all${spoilers()}`);
  if (!got.data || !got.data.recipes.length) return;
  var card = make("section", "dash-card");
  card.appendChild(make("h2", "dash-h", "recipes with “" + name + "” in the name"));
  recipeTable(card, got.data.recipes, { kinds: true, qty: "", sort: "candidates" });
  body.appendChild(card);
}

function makerTable(card: HTMLElement, rows: MakerRow[]): void {
  var columns: Column<MakerRow>[] = [
    {
      key: "recipe",
      label: "recipe",
      render: function (r) {
        return recipeLink(r.cls, r.name);
      },
    },
    {
      key: "machine",
      label: "machine",
      render: function (r) {
        return r.machine ? r.machine + " · " + num(r.power_mw) + " MW" : "–";
      },
    },
    {
      key: "in",
      label: "in /min",
      render: function (r) {
        return flows(r.ingredients);
      },
    },
    {
      key: "out",
      label: "out /min",
      render: function (r) {
        return flows(r.products);
      },
    },
  ];
  if (showsStatus(rows)) columns.push(statusColumn<MakerRow>());
  columns.push({
    key: "granted",
    label: "granted by",
    render: function (r) {
      return r.granted_by.join("; ") || "–";
    },
  });
  card.appendChild(table(columns, rows, { caption: "recipes that make this item" }));
}

function renderItem(body: HTMLElement, cls: string): void {
  var got = load<AlternatesResponse>("recipes", `/api/gamedata/alternates?item=${encodeURIComponent(cls)}${spoilers()}`);
  if (!got.data) {
    unread(body, got, "items", "the item", "search for it by name in the header");
    return;
  }
  var data = got.data;
  if (data.build_recipe && !data.recipes.length) {
    title(body, "items", data.name, data.item, "building");
    var built = make("p", "dash-note", "placed with the build gun, not made in a machine: see ");
    built.appendChild(recipeLink(data.build_recipe, "its build cost"));
    body.appendChild(built);
    return;
  }
  title(body, "items", data.name, data.item, data.fluid ? "fluid" : "solid");
  var facts = [data.energy_mj ? num(data.energy_mj) + " MJ" + (data.fluid ? " per m³" : " each") : "", data.sink_points ? count(data.sink_points) + " sink points" : ""].filter(Boolean);
  if (facts.length) note(body, facts.join(" · "));
  saveNote(body, data.save_note);
  countLine(body, "", data.save_note === null);

  var made = make("section", "dash-card");
  made.appendChild(make("h2", "dash-h", "made by"));
  if (data.recipes.length) makerTable(made, data.recipes);
  else empty(made, hidesLocked() ? "no unlocked recipe makes this in a machine" : "nothing makes this in a machine");
  body.appendChild(made);

  var used = make("section", "dash-card");
  used.appendChild(make("h2", "dash-h", "used by"));
  var uses = load<RecipesResponse>("recipes:used", `/api/gamedata/recipes?consumes=${encodeURIComponent(cls)}&recipe_kind=all${spoilers()}`);
  if (!waiting(used, uses, "the recipes that use it")) {
    var rows = uses.data!.recipes;
    if (!rows.length) empty(used, hidesLocked() ? "no unlocked recipe uses this" : "no recipe uses this");
    else {
      note(used, censusLine(uses.data!, "all"));
      recipeTable(used, rows, { kinds: true, qty: "uses", sort: "used" });
    }
  }
  body.appendChild(used);
}

function rateTable(box: HTMLElement, rates: Rate[], part: boolean, linked: boolean): void {
  var columns: Column<Rate>[] = [
    {
      key: "item",
      label: "item",
      render: function (x) {
        return named(x.item, x.name, linked);
      },
    },
    {
      key: "rate",
      label: part ? "per min" : "amount",
      align: "right",
      render: function (x) {
        return part ? perMin(x.per_min, false) : num(x.amount);
      },
    },
  ];
  box.appendChild(table(columns, rates));
}

function renderRecipe(body: HTMLElement, cls: string): void {
  var got = load<RecipeDetail>("recipes", `/api/gamedata/recipe?recipe=${encodeURIComponent(cls)}${spoilers()}`);
  if (!got.data) {
    unread(body, got, "recipes", "the recipe", ambiguous(got.failure) ? "pick one below" : "search for it by name in the header");
    if (got.failure) candidates(body, decoded(cls));
    return;
  }
  var r = got.data;
  if (r.unlocked === false && hidesLocked()) {
    title(body, "recipes", "Locked recipe", "", "");
    empty(body, "this recipe is not unlocked in this world", settingsLink("can show locked recipes"));
    return;
  }
  var part = r.kind === "part";
  title(body, "recipes", r.name, "", part ? "" : KIND_WORD[r.kind] || r.kind);
  if (r.unlocked !== null && !hidesLocked()) {
    var head = make("p", "rx-status");
    head.appendChild(status(r.unlocked));
    body.appendChild(head);
  }
  saveNote(body, r.save_note);

  var card = make("section", "dash-card");
  var facts: [string, string][] = part
    ? [
        ["machine", r.machine || "–"],
        ["cycle", num(r.duration_s, 2) + " s"],
        ["power", r.power_range_mw ? num(r.power_range_mw[0]) + "–" + num(r.power_range_mw[1]) + " MW, " + num(r.power_mw) + " MW average" : num(r.power_mw) + " MW"],
      ]
    : [];
  facts.push(["granted by", r.granted_by.join("; ") || "no known unlock"]);
  var list = make("dl", "rx-facts");
  facts.forEach(function (f) {
    list.appendChild(make("dt", "", f[0]));
    list.appendChild(make("dd", "", f[1]));
  });
  card.appendChild(list);
  body.appendChild(card);

  var unit = part ? "per min, one machine at 100%" : r.kind === "building" ? "per build" : "per craft";
  [
    { label: "in", rates: r.ingredients, linked: true },
    { label: "out", rates: r.products, linked: r.kind !== "building" },
  ].forEach(function (side) {
    var box = make("section", "dash-card");
    box.appendChild(make("h2", "dash-h", side.label + " (" + unit + ")"));
    rateTable(box, side.rates, part, side.linked);
    body.appendChild(box);
  });
}

function detailOf(subject: string): { kind: string; id: string } | null {
  var cut = subject.indexOf("/");
  if (cut < 0) return null;
  var kind = subject.slice(0, cut);
  var id = subject.slice(cut + 1);
  return id && (kind === "item" || kind === "recipe") ? { kind: kind, id: id } : null;
}

export function renderRecipes(body: HTMLElement, subject: string, rerender: () => void): void {
  redraw = rerender;
  probeIcons();
  var arrived = rendered !== state.dash;
  rendered = state.dash;
  var detail = detailOf(subject);
  if (detail && detail.kind === "item") renderItem(body, detail.id);
  else if (detail) renderRecipe(body, detail.id);
  else renderBrowse(body, subject, arrived);
}

registerFetch<UnlockedResponse>({
  wave: "live",
  rank: 70,
  path: PRIMED_PATH,
  label: "unlocked recipes",
  clears: [],
  refilters: false,
  draw: function (data) {
    if (primed === state.epoch) generation += 1;
    primed = state.epoch;
    cache[keyOf(PRIMED_PATH)] = data;
    redraw();
  },
  failed: function () {
    if (primed === state.epoch) generation += 1;
    primed = state.epoch;
    redraw();
  },
});

remember();
