/* The dashboard's Inventory section: stock per item, containers and crates, filtered by item,
 * addressed as `dash=inventory[/<item>]`. See docs/frontend_vision.md §10. */

import { crateLabel } from "./crates";
import { button, checkbox, choice, empty, error, heading, link, loading, note, showAll, table, tile } from "./dashkit";
import { make } from "./dom";
import { amount, count, num, pct, regionLine } from "./format";
import { loadOne } from "./load";
import { writeHash } from "./map";
import { dashParts, go } from "./nav";
import { showPoint } from "./panel";
import { registerFetch } from "./registry";
import { state } from "./state";
import { counted } from "./words";

import type { Column, SortState } from "./dashkit";
import type { StockPile, StockPlace, StockResponse } from "./api-shapes";

export interface InventoryHost {
  toMap: (action: () => void) => void;
  render: () => void;
}

var stock = {
  data: null as StockResponse | null,
  failed: false,
};

var host: InventoryHost | null = null;

var results: HTMLElement | null = null;

var alsoLine: HTMLElement | null = null;

var sorts: Record<string, SortState> = {
  stock: { key: "spendable", desc: true },
  containers: { key: "held", desc: true },
  crates: { key: "distance", desc: false },
};

var view = { empty: false, kind: "all", allStock: false };

var CONTENTS_SHOWN = 3;

var STOCK_SHOWN: 25 = 25;

var UPLOADER = "Build_CentralStorage_C";

function query(): string {
  return dashParts().subject;
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

function pile(value: number, fluid: boolean): string {
  return value ? amount(value, fluid) : "";
}

function mapButton(place: StockPlace): HTMLElement {
  if (place.x_m === null || place.y_m === null) return make("span", "dash-muted", "–");
  return button(
    "map",
    function () {
      fly(place);
    },
    { title: "fly the map to it", map: true }
  );
}

function fly(place: StockPlace): void {
  if (place.x_m === null || place.y_m === null || !host) return;
  var at = { x: place.x_m, y: place.y_m };
  var shown = { label: place.name, layers: place.source === "storage" ? ["storage"] : undefined };
  host.toMap(function () {
    showPoint(at.x, at.y, shown);
  });
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
  if (!place.items.length) return fluid && place.total ? amount(place.total, true) + " unnamed fluid" : "empty";
  var shown = place.items.filter(function (i) {
    return m.test(i.name);
  });
  var head = m.active ? shown : shown.slice(0, CONTENTS_SHOWN);
  var words = head.map(function (i) {
    return amount(i.amount, fluid) + " " + i.name;
  });
  var rest = place.items.length - head.length;
  if (rest > 0) words.push("+" + rest + (m.active ? " other" : " more"));
  return words.join(", ");
}

function where(place: StockPlace): string {
  if (place.region) return regionLine(place.region);
  return place.x_m === null ? "" : "off the map";
}

function size(place: StockPlace): string {
  if (place.kind === "fluid") return place.capacity_m3 === null ? "" : amount(place.capacity_m3, true);
  return place.slots ? counted(place.slots, "slot") : "";
}

function used(place: StockPlace): string {
  if (place.kind === "fluid") {
    return amount(place.total, true) + " of " + (place.capacity_m3 === null ? "?" : amount(place.capacity_m3, true));
  }
  if (place.slots_used === null || !place.slots) return "";
  return count(place.slots_used) + " of " + counted(place.slots, "slot");
}

function stacked(lead: string, lines: string[]): HTMLElement {
  var box = make("span", "dash-makes");
  box.appendChild(make("span", "", lead));
  lines.forEach(function (line) {
    if (line) box.appendChild(make("span", "dash-sub", line));
  });
  return box;
}

function fillCell(place: StockPlace): HTMLElement {
  if (place.fill === null) return make("span", "dash-muted", "–");
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

function also(parent: HTMLElement, m: Matcher): void {
  if (!m.active) return;
  var line = make("p", "dash-note inv-also");
  if (m.exact && m.also.length) {
    line.appendChild(document.createTextNode("exactly " + m.exact + " · also containing “" + query() + "”: "));
    m.also.forEach(function (name, i) {
      if (i) line.appendChild(document.createTextNode(", "));
      line.appendChild(link(address(name), name));
    });
    line.appendChild(document.createTextNode(" · "));
  }
  line.appendChild(document.createTextNode("the tiles count the whole world"));
  parent.appendChild(line);
}

function search(parent: HTMLElement): void {
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
  card.appendChild(row);
  if (stock.data) {
    var list = make("datalist");
    list.id = "inv-items";
    stock.data.items.forEach(function (r) {
      var option = make("option");
      option.value = r.name;
      list.appendChild(option);
    });
    card.appendChild(list);
  }
  var line = make("div");
  card.appendChild(line);
  alsoLine = line;
  parent.appendChild(card);
}

function renderTiles(parent: HTMLElement, data: StockResponse): void {
  var c = data.census;
  var spendable = data.items.filter(function (r) {
    return r.spendable > 0;
  }).length;
  var crateItems = 0;
  var uploaders = 0;
  var boxes = 0;
  var filled = 0;
  data.places.forEach(function (p) {
    if (p.source === "crate") crateItems += p.total;
    else if (p.cls === UPLOADER) uploaders += 1;
    else if (p.kind === "solid") {
      boxes += 1;
      if (p.total) filled += 1;
    }
  });
  var tiles = make("div", "dash-tiles");
  tiles.appendChild(tile("item kinds held", count(data.items.length), count(spendable) + " spendable"));
  var rest = [count(filled) + " hold something", counted(c.fluid, "fluid buffer")];
  if (uploaders) rest.push(counted(uploaders, "depot uploader"));
  tiles.appendChild(tile("containers", count(boxes), rest.join(" · ")));
  tiles.appendChild(
    tile("crates on the ground", count(c.crates), counted(c.deaths, "death crate") + " · " + counted(crateItems, "item"))
  );
  parent.appendChild(tiles);
}

function renderStock(parent: HTMLElement, data: StockResponse, rows: StockPile[]): void {
  var card = make("section", "dash-card");
  heading(card, "stock");
  var places: Record<string, number> = {};
  data.places.forEach(function (p) {
    p.items.forEach(function (i) {
      places[i.item] = (places[i.item] || 0) + 1;
    });
  });
  function piled(key: string, label: string, pick: (r: StockPile) => number, title?: string): Column<StockPile> {
    return {
      key: key,
      label: label,
      align: "right",
      title: title,
      sort: pick,
      render: function (r) {
        return pile(pick(r), r.fluid);
      },
    };
  }
  var columns: Column<StockPile>[] = [
    {
      key: "name",
      label: "item",
      sort: function (r) {
        return r.name;
      },
      render: function (r) {
        return link(address(r.name), r.name);
      },
    },
    {
      key: "spendable",
      label: "spendable",
      align: "right",
      title: "carried + storage + depot: what an affordability check spends; fluids in m³",
      sort: function (r) {
        return r.spendable;
      },
      render: function (r) {
        return amount(r.spendable, r.fluid);
      },
    },
    piled("carried", "carried", function (r) {
      return r.carried;
    }),
    piled("storage", "storage", function (r) {
      return r.storage;
    }),
    piled("depot", "depot", function (r) {
      return r.depot;
    }),
    piled(
      "buffers",
      "in machines",
      function (r) {
        return r.buffers;
      },
      "machine buffers: listed, never spendable"
    ),
    piled(
      "crates",
      "in crates",
      function (r) {
        return r.crates;
      },
      "crates on the ground: recoverable, never spendable"
    ),
    {
      key: "places",
      label: "held in",
      align: "right",
      title: "how many containers, fluid buffers and crates hold it",
      sort: function (r) {
        return places[r.item] || 0;
      },
      render: function (r) {
        return places[r.item] ? count(places[r.item]!) : "";
      },
    },
  ];
  var grid = table(columns, rows, {
    sort: sorts.stock,
    caption: "stock per item",
    onRow: function (r) {
      go(address(r.name));
    },
  });
  card.appendChild(grid);
  showAll(card, grid, rows.length, STOCK_SHOWN, "show all " + counted(rows.length, "item"), view.allStock, function () {
    view.allStock = true;
  });
  parent.appendChild(card);
}

function kindPicker(): HTMLElement {
  var kind = choice(
    [
      ["all", "solid and fluid"],
      ["solid", "solid only"],
      ["fluid", "fluid only"],
    ],
    view.kind,
    function (value) {
      view.kind = value;
      redraw();
    },
    { label: "container kind" }
  );
  kind.classList.add("inv-kind");
  return kind;
}

function emptyToggle(m: Matcher): HTMLElement {
  var toggle = checkbox("show empty", view.empty, function (on) {
    view.empty = on;
    redraw();
  });
  if (m.active) {
    toggle.querySelector("input")!.disabled = true;
    toggle.classList.add("off");
    toggle.title = "a filter shows only containers holding a match";
  }
  return toggle;
}

function renderContainers(parent: HTMLElement, data: StockResponse, m: Matcher): void {
  var card = make("section", "dash-card");
  var bar = make("div", "dash-title");
  heading(bar, "containers");
  bar.appendChild(kindPicker());
  bar.appendChild(emptyToggle(m));
  card.appendChild(bar);
  var all = data.places.filter(function (p) {
    return p.source === "storage";
  });
  var rows = all.filter(function (p) {
    if (view.kind !== "all" && p.kind !== view.kind) return false;
    if (m.active) return held(p, m) > 0;
    return view.empty || p.total > 0;
  });
  if (!rows.length) {
    empty(card, m.active ? "no container holds a matching item" : "no container matches these filters");
    parent.appendChild(card);
    return;
  }
  var columns: Column<StockPlace>[] = [
    {
      key: "name",
      label: "container",
      sort: function (p) {
        return p.name;
      },
      render: function (p) {
        return stacked(p.name, [[where(p), size(p)].filter(Boolean).join(" · ")]);
      },
    },
    {
      key: "held",
      label: m.active ? "matching" : "holds",
      title: "solids and fluids sort apart",
      sort: function (p) {
        return (p.kind === "fluid" ? 0 : 1e9) + held(p, m);
      },
      render: function (p) {
        return contents(p, m);
      },
      className: "inv-contents",
    },
    {
      key: "fill",
      label: "fill",
      title: "used slots over slots for a container, m³ over capacity for a fluid buffer",
      sort: function (p) {
        return p.fill === null ? -1 : p.fill;
      },
      render: fillCell,
      className: "bar",
    },
    {
      key: "map",
      label: "",
      align: "right",
      render: mapButton,
    },
  ];
  card.appendChild(table(columns, rows, { sort: sorts.containers, caption: "containers", onRow: fly }));
  if (rows.length < all.length) note(card, count(rows.length) + " of " + count(all.length) + " shown");
  parent.appendChild(card);
}

function renderCrates(parent: HTMLElement, data: StockResponse, m: Matcher): void {
  var rows = data.places.filter(function (p) {
    return p.source === "crate" && (!m.active || held(p, m) > 0);
  });
  if (!rows.length) return;
  var card = make("section", "dash-card inv-crates");
  heading(card, "crates");
  var me = data.player.x_m !== null;
  var columns: Column<StockPlace>[] = [
    {
      key: "kind",
      label: "crate",
      title: "a crate deletes itself once emptied; the save records no owner and no time",
      sort: function (p) {
        return crateLabel(p.crate_kind || "");
      },
      render: function (p) {
        var box = stacked(crateLabel(p.crate_kind || ""), [p.crate_kind_text || "", where(p)]);
        box.appendChild(make("span", "inv-narrow", contents(p, m)));
        return box;
      },
    },
    {
      key: "distance",
      label: me ? "from the player" : "distance",
      align: "right",
      title: "straight line from where the player last stood",
      sort: function (p) {
        return p.distance_m === null ? Infinity : p.distance_m;
      },
      render: function (p) {
        return p.distance_m === null ? "–" : num(p.distance_m, 0) + " m";
      },
    },
    {
      key: "held",
      label: m.active ? "matching" : "items",
      align: "right",
      sort: function (p) {
        return held(p, m);
      },
      render: function (p) {
        return count(held(p, m));
      },
    },
    {
      key: "contents",
      label: "contents",
      render: function (p) {
        return contents(p, m);
      },
      className: "inv-contents",
    },
    {
      key: "map",
      label: "",
      align: "right",
      render: mapButton,
    },
  ];
  card.appendChild(table(columns, rows, { sort: sorts.crates, caption: "crates", onRow: fly }));
  parent.appendChild(card);
}

function retry(): void {
  loadOne("/api/stock");
}

function renderResults(parent: HTMLElement): void {
  if (alsoLine) alsoLine.textContent = "";
  var data = stock.data;
  if (!data) {
    if (stock.failed) error(parent, "stock", null, retry);
    else loading(parent, "stock");
    return;
  }
  var m = matcher(data);
  if (alsoLine) also(alsoLine, m);
  renderTiles(parent, data);
  var piles = data.items.filter(function (r) {
    return m.test(r.name);
  });
  var anywhere = data.places.some(function (p) {
    return held(p, m) > 0;
  });
  if (m.active && !piles.length && !anywhere) {
    empty(parent, "nothing held matches “" + query() + "”", "not carried, stored, in the depot, in a machine or in a crate");
    return;
  }
  renderCrates(parent, data, m);
  if (piles.length) renderStock(parent, data, piles);
  renderContainers(parent, data, m);
}

function redraw(): void {
  if (!results) return;
  results.textContent = "";
  renderResults(results);
}

export function renderInventory(body: HTMLElement, into: InventoryHost): void {
  host = into;
  search(body);
  results = make("div", "inv-results");
  body.appendChild(results);
  renderResults(results);
}

function shown(): boolean {
  return dashParts().tab === "inventory";
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
    if (host && shown()) host.render();
  },
  failed: function () {
    stock.data = null;
    stock.failed = true;
    if (host && shown()) host.render();
  },
});
