/* The dashboard's Inventory section: stock per item, containers and crates, filtered by item,
 * addressed as `dash=inventory[/<item>]`. See docs/frontend_vision.md §10. */

import { count, make } from "./dom";
import { pct, regionLine } from "./format";
import { hashFor, writeHash } from "./map";
import { showPoint } from "./panel";
import { registerFetch } from "./registry";
import { state } from "./state";

import type { StockPile, StockPlace, StockResponse } from "./api-shapes";

export interface InventoryHost {
  toMap: (action: () => void) => void;
  render: () => void;
}

interface Sort {
  key: string;
  desc: boolean;
}

type Column = [string, string];

var stock = {
  data: null as StockResponse | null,
  error: "",
};

var host: InventoryHost | null = null;

var results: HTMLElement | null = null;

var sorts: Record<string, Sort> = {
  stock: { key: "spendable", desc: true },
  containers: { key: "held", desc: true },
  crates: { key: "distance", desc: false },
};

var view = { empty: false, kind: "all" };

var CONTENTS_SHOWN = 3;

function query(): string {
  var cut = state.dash.indexOf("/");
  return cut < 0 ? "" : state.dash.slice(cut + 1);
}

function address(q: string): string {
  return "inventory" + (q ? "/" + q : "");
}

interface Matcher {
  active: boolean;
  exact: string;
  also: string[];
  test: (name: string) => boolean;
}

function matcher(data: StockResponse): Matcher {
  var q = query().trim().toLowerCase();
  if (!q) {
    return {
      active: false,
      exact: "",
      also: [],
      test: function () {
        return true;
      },
    };
  }
  var names: Record<string, string> = {};
  data.items.forEach(function (row) {
    names[row.name.toLowerCase()] = row.name;
  });
  data.places.forEach(function (p) {
    p.items.forEach(function (i) {
      names[i.name.toLowerCase()] = i.name;
    });
  });
  var exact = names[q] || "";
  var also = Object.keys(names)
    .filter(function (n) {
      return n !== q && n.indexOf(q) >= 0;
    })
    .map(function (n) {
      return names[n]!;
    })
    .sort();
  return {
    active: true,
    exact: exact,
    also: exact ? also : [],
    test: function (name) {
      var n = name.toLowerCase();
      return exact ? n === q : n.indexOf(q) >= 0;
    },
  };
}

function amount(value: number, fluid: boolean): string {
  if (fluid) return count(Math.round(value * 10) / 10) + " m³";
  return count(Math.round(value));
}

function pile(value: number, fluid: boolean): string {
  return value ? amount(value, fluid) : "";
}

function toMapButton(place: StockPlace): HTMLElement {
  if (place.x_m === null || place.y_m === null) return make("span", "dash-muted", "–");
  var button = make("button", "dash-map", "map");
  button.type = "button";
  button.title = "fly the map to it";
  button.onclick = function (event) {
    event.stopPropagation();
    fly(place);
  };
  return button;
}

function fly(place: StockPlace): void {
  if (place.x_m === null || place.y_m === null || !host) return;
  var at = { x: place.x_m, y: place.y_m };
  host.toMap(function () {
    showPoint(at.x, at.y);
  });
}

function cell(tr: HTMLElement, content: string | number | HTMLElement, className?: string): void {
  var td = make("td", className);
  if (content instanceof HTMLElement) td.appendChild(content);
  else td.textContent = String(content);
  tr.appendChild(td);
}

function table(name: string, headers: Column[]): HTMLTableElement {
  var sort = sorts[name]!;
  var t = make("table", "dash-table");
  var head = make("thead");
  var tr = make("tr");
  headers.forEach(function (h) {
    var th = make("th", h[1] === "name" || h[1] === "region" || h[1] === "kind" ? "" : "num", h[0]);
    if (h[1]) {
      var on = sort.key === h[1];
      th.className += " sort" + (on ? (sort.desc ? " desc" : " asc") : "");
      th.setAttribute("aria-sort", on ? (sort.desc ? "descending" : "ascending") : "none");
      th.tabIndex = 0;
      var pick = function () {
        if (sort.key === h[1]) sort.desc = !sort.desc;
        else {
          sort.key = h[1];
          sort.desc = th.className.indexOf("num") >= 0;
        }
        redraw();
      };
      th.onclick = pick;
      th.onkeydown = function (event) {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          pick();
        }
      };
    }
    tr.appendChild(th);
  });
  head.appendChild(tr);
  t.appendChild(head);
  t.appendChild(make("tbody"));
  return t;
}

function ordered<T>(rows: T[], name: string, value: (row: T, key: string) => number | string | null): T[] {
  var sort = sorts[name]!;
  return rows.slice().sort(function (a, b) {
    var va = value(a, sort.key);
    var vb = value(b, sort.key);
    if (va === null && vb === null) return 0;
    if (va === null) return 1;
    if (vb === null) return -1;
    var d = typeof va === "string" ? va.localeCompare(String(vb)) : va - (vb as number);
    return sort.desc ? -d : d;
  });
}

function heading(parent: HTMLElement, text: string): void {
  parent.appendChild(make("h2", "dash-h", text));
}

function note(parent: HTMLElement, text: string): void {
  parent.appendChild(make("p", "dash-note", text));
}

function tile(label: string, value: string, sub: string): HTMLElement {
  var box = make("div", "dash-tile");
  box.appendChild(make("span", "dash-tile-k", label));
  box.appendChild(make("span", "dash-tile-v", value));
  if (sub) box.appendChild(make("span", "dash-tile-sub", sub));
  return box;
}

function held(place: StockPlace, m: Matcher): number {
  if (!m.active) return place.total;
  var sum = 0;
  place.items.forEach(function (i) {
    if (m.test(i.name)) sum += i.amount;
  });
  return sum;
}

function contents(place: StockPlace, m: Matcher): string {
  var fluid = place.kind === "fluid";
  var shown = place.items.filter(function (i) {
    return m.test(i.name);
  });
  var head = m.active ? shown : shown.slice(0, CONTENTS_SHOWN);
  var words = head.map(function (i) {
    return amount(i.amount, fluid) + " " + i.name;
  });
  var rest = place.items.length - head.length;
  if (rest > 0) words.push("+" + rest + (m.active ? " other" : " more"));
  if (!place.items.length) return fluid && place.total ? amount(place.total, true) + " unnamed fluid" : "empty";
  return words.join(", ");
}

function fillCell(place: StockPlace): HTMLElement {
  var box = make("span", "dash-sub");
  if (place.fill === null) {
    box.textContent = "–";
    return box;
  }
  var wrap = make("div", "inv-fill");
  var bar = make("div", "dash-hbar");
  var part = make("span", "dash-mix-mid");
  part.style.width = Math.min(100, place.fill * 100) + "%";
  bar.appendChild(part);
  wrap.appendChild(bar);
  wrap.appendChild(make("span", "inv-pct", pct(place.fill)));
  wrap.title = used(place);
  return wrap;
}

function used(place: StockPlace): string {
  if (place.kind === "fluid") {
    return amount(place.total, true) + " of " + (place.capacity_m3 === null ? "?" : amount(place.capacity_m3, true));
  }
  if (place.slots_used === null || !place.slots) return "–";
  return place.slots_used + " of " + place.slots + " slots";
}

function search(parent: HTMLElement, data: StockResponse | null): void {
  var card = make("section", "dash-card");
  var row = make("div", "inv-search");
  var input = make("input", "dash-name");
  input.type = "search";
  input.placeholder = "filter by item, e.g. Quartz";
  input.setAttribute("aria-label", "filter by item");
  input.setAttribute("data-candidate", "inventory-search");
  input.setAttribute("list", "inv-items");
  input.value = query();
  input.oninput = function () {
    state.dash = address(input.value);
    writeHash();
    redraw();
  };
  input.onfocus = function () {
    var end = input.value.length;
    input.setSelectionRange(end, end);
  };
  row.appendChild(input);
  if (query()) {
    var clear = make("button", "dash-map", "clear");
    clear.type = "button";
    clear.onclick = function () {
      location.hash = hashFor(address(""));
    };
    row.appendChild(clear);
  }
  card.appendChild(row);
  if (data) {
    var list = make("datalist");
    list.id = "inv-items";
    data.items.forEach(function (r) {
      var option = make("option");
      option.value = r.name;
      list.appendChild(option);
    });
    card.appendChild(list);
  }
  parent.appendChild(card);
}

function renderTiles(parent: HTMLElement, data: StockResponse): void {
  var c = data.census;
  var spendable = data.items.filter(function (r) {
    return r.spendable > 0;
  }).length;
  var crateItems = 0;
  data.places.forEach(function (p) {
    if (p.source === "crate") crateItems += p.total;
  });
  var tiles = make("div", "dash-tiles");
  tiles.appendChild(tile("item kinds held", count(data.items.length), spendable + " of them spendable"));
  tiles.appendChild(
    tile("containers", count(c.containers), c.filled + " of " + c.solid + " with something in · " + c.fluid + " fluid buffers")
  );
  tiles.appendChild(tile("crates on the ground", count(c.crates), c.deaths + " from a death · " + count(crateItems) + " items"));
  parent.appendChild(tiles);
}

function renderStock(parent: HTMLElement, data: StockResponse, m: Matcher): void {
  var card = make("section", "dash-card");
  heading(card, "stock");
  var places: Record<string, number> = {};
  data.places.forEach(function (p) {
    p.items.forEach(function (i) {
      places[i.item] = (places[i.item] || 0) + 1;
    });
  });
  var rows = data.items.filter(function (r) {
    return m.test(r.name);
  });
  if (!rows.length) {
    note(card, "nothing held matches “" + query() + "” — not carried, stored, in the Depot, in a machine or in a crate");
    parent.appendChild(card);
    return;
  }
  var t = table("stock", [
    ["item", "name"],
    ["spendable", "spendable"],
    ["carried", "carried"],
    ["storage", "storage"],
    ["depot", "depot"],
    ["in machines", "buffers"],
    ["in crates", "crates"],
    ["places", "places"],
  ]);
  var tb = t.tBodies[0]!;
  ordered(rows, "stock", function (r: StockPile, key) {
    if (key === "name") return r.name;
    if (key === "places") return places[r.item] || 0;
    return r[key as "spendable"];
  }).forEach(function (r) {
    var tr = make("tr", "go");
    var a = make("a", "", r.name);
    a.setAttribute("href", hashFor(address(r.name)));
    cell(tr, a);
    cell(tr, amount(r.spendable, r.fluid), "num");
    cell(tr, pile(r.carried, r.fluid), "num");
    cell(tr, pile(r.storage, r.fluid), "num");
    cell(tr, pile(r.depot, r.fluid), "num");
    cell(tr, pile(r.buffers, r.fluid), "num");
    cell(tr, pile(r.crates, r.fluid), "num");
    cell(tr, places[r.item] || "", "num");
    tr.onclick = function () {
      location.hash = hashFor(address(r.name));
    };
    tb.appendChild(tr);
  });
  var wrap = make("div", "dash-scroll");
  wrap.appendChild(t);
  card.appendChild(wrap);
  note(
    card,
    "spendable is carried + storage + Dimensional Depot, the pool every affordability check spends. " +
      "Machine buffers and crates are listed beside it and never added in. Fluids are m³."
  );
  parent.appendChild(card);
}

function containerControls(card: HTMLElement): void {
  var bar = make("div", "dash-title");
  heading(bar, "containers");
  var kind = make("select", "dash-select inv-kind");
  [
    ["all", "solid and fluid"],
    ["solid", "solid only"],
    ["fluid", "fluid only"],
  ].forEach(function (o) {
    var option = make("option", "", o[1]);
    option.value = o[0]!;
    kind.appendChild(option);
  });
  kind.value = view.kind;
  kind.setAttribute("aria-label", "container kind");
  kind.onchange = function () {
    view.kind = kind.value;
    redraw();
  };
  bar.appendChild(kind);
  var toggle = make("label", "dash-toggle");
  var box = make("input");
  box.type = "checkbox";
  box.checked = view.empty;
  box.onchange = function () {
    view.empty = box.checked;
    redraw();
  };
  toggle.appendChild(box);
  toggle.appendChild(document.createTextNode(" show empty"));
  bar.appendChild(toggle);
  card.appendChild(bar);
}

function renderContainers(parent: HTMLElement, data: StockResponse, m: Matcher): void {
  var card = make("section", "dash-card");
  containerControls(card);
  var all = data.places.filter(function (p) {
    return p.source === "storage";
  });
  var rows = all.filter(function (p) {
    if (view.kind !== "all" && p.kind !== view.kind) return false;
    if (m.active) return held(p, m) > 0;
    return view.empty || p.total > 0;
  });
  if (!rows.length) {
    note(card, m.active ? "no container holds a matching item" : "no container matches these filters");
    parent.appendChild(card);
    return;
  }
  var t = table("containers", [
    ["container", "name"],
    ["region", "region"],
    [m.active ? "matching" : "holds", "held"],
    ["fill", "fill"],
    ["", ""],
  ]);
  var tb = t.tBodies[0]!;
  ordered(rows, "containers", function (p: StockPlace, key) {
    if (key === "name") return p.name;
    if (key === "region") return p.region ? p.region.name : null;
    if (key === "fill") return p.fill;
    return held(p, m);
  }).forEach(function (p) {
    var tr = make("tr", p.x_m === null ? "" : "go");
    var name = make("span", "dash-makes");
    name.appendChild(make("span", "", p.name));
    name.appendChild(make("span", "dash-sub", used(p)));
    cell(tr, name);
    cell(tr, p.region ? regionLine(p.region) : p.x_m === null ? "–" : "off the map", "dash-sub");
    cell(tr, contents(p, m));
    cell(tr, fillCell(p), "bar");
    cell(tr, toMapButton(p), "num");
    tr.onclick = function () {
      fly(p);
    };
    tb.appendChild(tr);
  });
  var wrap = make("div", "dash-scroll");
  wrap.appendChild(t);
  card.appendChild(wrap);
  note(
    card,
    rows.length +
      " of " +
      all.length +
      " shown. Fill is used slots over slots (each item at its own stack size) for a container, " +
      "and m³ over what the class holds for a fluid buffer; – means one of the two is unknown."
  );
  parent.appendChild(card);
}

function renderCrates(parent: HTMLElement, data: StockResponse, m: Matcher): void {
  var card = make("section", "dash-card");
  heading(card, "crates");
  var all = data.places.filter(function (p) {
    return p.source === "crate";
  });
  var rows = all.filter(function (p) {
    return !m.active || held(p, m) > 0;
  });
  if (!rows.length) {
    note(card, all.length ? "no crate holds a matching item" : "no crates on the ground");
    parent.appendChild(card);
    return;
  }
  var me = data.player.x_m !== null;
  var t = table("crates", [
    ["kind", "kind"],
    ["region", "region"],
    [me ? "from the player" : "distance", "distance"],
    ["items", "held"],
    ["contents", ""],
    ["", ""],
  ]);
  var tb = t.tBodies[0]!;
  ordered(rows, "crates", function (p: StockPlace, key) {
    if (key === "kind") return p.crate_kind || "";
    if (key === "region") return p.region ? p.region.name : null;
    if (key === "distance") return p.distance_m;
    return held(p, m);
  }).forEach(function (p) {
    var tr = make("tr", p.x_m === null ? "" : "go");
    var kind = make("span", "", p.crate_kind || "–");
    if (p.crate_kind_text) kind.title = p.crate_kind_text;
    cell(tr, kind);
    cell(tr, p.region ? regionLine(p.region) : p.x_m === null ? "–" : "off the map", "dash-sub");
    cell(tr, p.distance_m === null ? "–" : count(Math.round(p.distance_m)) + " m", "num");
    cell(tr, count(held(p, m)), "num");
    cell(tr, contents(p, m));
    cell(tr, toMapButton(p), "num");
    tr.onclick = function () {
      fly(p);
    };
    tb.appendChild(tr);
  });
  var wrap = make("div", "dash-scroll");
  wrap.appendChild(t);
  card.appendChild(wrap);
  note(
    card,
    "A crate is recoverable but never spendable: it deletes itself once emptied. " +
      "The save records no owner and no time for a crate." +
      (me ? " Distance is across the ground from where the player last stood." : "")
  );
  parent.appendChild(card);
}

function renderResults(parent: HTMLElement): void {
  var data = stock.data;
  if (!data) {
    note(parent, stock.error || "loading…");
    return;
  }
  var m = matcher(data);
  if (m.exact && m.also.length) {
    note(parent, "Showing exactly " + m.exact + ". Also containing “" + query() + "”: " + m.also.join(", ") + ".");
  }
  renderTiles(parent, data);
  renderStock(parent, data, m);
  var split = make("div", "dash-split");
  renderContainers(split, data, m);
  renderCrates(split, data, m);
  parent.appendChild(split);
  if (m.active) note(parent, "The tiles count the whole world; the filter changes rows only.");
}

function redraw(): void {
  if (!results) return;
  results.textContent = "";
  renderResults(results);
}

export function renderInventory(body: HTMLElement, into: InventoryHost): void {
  host = into;
  search(body, stock.data);
  results = make("div");
  body.appendChild(results);
  renderResults(results);
}

function shown(): boolean {
  return state.dash === "inventory" || state.dash.indexOf("inventory/") === 0;
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
    stock.error = "";
    if (host && shown()) host.render();
  },
  failed: function () {
    stock.data = null;
    stock.error = "stock could not be read for this save";
    if (host && shown()) host.render();
  },
});
