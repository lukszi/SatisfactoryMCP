/* The dashboard's Inventory section: stock per item, containers and crates, filtered by item,
 * addressed as `dash=inventory[/<item>]`. See docs/frontend_vision.md §10. */

import { crateLabel } from "../map/drawn/crates";
import { appendNote, button, capRows, checkbox, empty, heading, link, pendingNotice, selectBox, table, tile } from "../kit/dashkit";
import { make } from "../kit/dom";
import { amount, count, formatNumber, pct, regionLine } from "../kit/format";
import { loadOne } from "../app/load";
import { writeHash } from "../map/map";
import { dashParts, go } from "../app/nav";
import { showPoint } from "../map/map-highlight";
import { registerFetch } from "../app/registry";
import { state } from "../app/state";
import { counted } from "../kit/words";
import { leaveDashThen, requestRender } from "./actions";

import type { Column, SortState } from "../kit/dashkit";
import type { StockPile, StockPlace, StockResponse } from "../api/shapes";

const stock = {
  data: null as StockResponse | null,
  failed: false,
};

let results: HTMLElement | null = null;

let alsoLine: HTMLElement | null = null;

const sorts: Record<string, SortState> = {
  stock: { key: "spendable", desc: true },
  containers: { key: "held", desc: true },
  crates: { key: "distance", desc: false },
};

const view = { showEmpty: false, kind: "all", allStock: false };

const CONTENTS_SHOWN = 3;

const STOCK_SHOWN: 25 = 25;

const UPLOADER = "Build_CentralStorage_C";

function filterText(): string {
  return dashParts().subject;
}

function inventoryDash(filter: string): string {
  return "inventory" + (filter ? "/" + filter : "");
}

interface Matcher {
  active: boolean;
  exact: string;
  also: string[];
  test: (name: string) => boolean;
}

/* An exact item name matches only itself, and the names that merely contain it are offered
 * beside it; anything else matches every name containing it. */
function buildMatcher(data: StockResponse): Matcher {
  const filter = filterText().trim().toLowerCase();
  if (!filter) {
    return {
      active: false,
      exact: "",
      also: [],
      test: function () {
        return true;
      },
    };
  }
  const names: Record<string, string> = {};
  data.items.forEach(function (row) {
    names[row.name.toLowerCase()] = row.name;
  });
  data.places.forEach(function (place) {
    place.items.forEach(function (item) {
      names[item.name.toLowerCase()] = item.name;
    });
  });
  const exact = names[filter] || "";
  const also = Object.keys(names)
    .filter(function (name) {
      return name !== filter && name.indexOf(filter) >= 0;
    })
    .map(function (name) {
      return names[name]!;
    })
    .sort();
  return {
    active: true,
    exact: exact,
    also: exact ? also : [],
    test: function (name) {
      const lower = name.toLowerCase();
      return exact ? lower === filter : lower.indexOf(filter) >= 0;
    },
  };
}

function amountOrBlank(value: number, fluid: boolean): string {
  return value ? amount(value, fluid) : "";
}

function placeMapButton(place: StockPlace): HTMLElement {
  if (place.x_m === null || place.y_m === null) return make("span", "dash-muted", "–");
  return button(
    "map",
    function () {
      flyToPlace(place);
    },
    { title: "fly the map to it", map: true }
  );
}

function flyToPlace(place: StockPlace): void {
  if (place.x_m === null || place.y_m === null) return;
  const at = { x: place.x_m, y: place.y_m };
  const shown = { label: place.name, layers: place.source === "storage" ? ["storage"] : undefined };
  leaveDashThen(function () {
    showPoint(at.x, at.y, shown);
  });
}

function matchingAmount(place: StockPlace, matcher: Matcher): number {
  if (!matcher.active) return place.total;
  let sum = 0;
  place.items.forEach(function (item) {
    if (matcher.test(item.name)) sum += item.amount;
  });
  return sum;
}

function contentsText(place: StockPlace, matcher: Matcher): string {
  const fluid = place.kind === "fluid";
  if (!place.items.length) return fluid && place.total ? amount(place.total, true) + " unnamed fluid" : "empty";
  const shown = place.items.filter(function (item) {
    return matcher.test(item.name);
  });
  const head = matcher.active ? shown : shown.slice(0, CONTENTS_SHOWN);
  const words = head.map(function (item) {
    return amount(item.amount, fluid) + " " + item.name;
  });
  const rest = place.items.length - head.length;
  if (rest > 0) words.push("+" + rest + (matcher.active ? " other" : " more"));
  return words.join(", ");
}

function placeLine(place: StockPlace): string {
  if (place.region) return regionLine(place.region);
  return place.x_m === null ? "" : "off the map";
}

function capacityText(place: StockPlace): string {
  if (place.kind === "fluid") return place.capacity_m3 === null ? "" : amount(place.capacity_m3, true);
  return place.slots ? counted(place.slots, "slot") : "";
}

function usageText(place: StockPlace): string {
  if (place.kind === "fluid") {
    return amount(place.total, true) + " of " + (place.capacity_m3 === null ? "?" : amount(place.capacity_m3, true));
  }
  if (place.slots_used === null || !place.slots) return "";
  return count(place.slots_used) + " of " + counted(place.slots, "slot");
}

function stacked(lead: string, lines: string[]): HTMLElement {
  const box = make("span", "dash-makes");
  box.appendChild(make("span", "", lead));
  lines.forEach(function (line) {
    if (line) box.appendChild(make("span", "dash-sub", line));
  });
  return box;
}

function fillCell(place: StockPlace): HTMLElement {
  if (place.fill === null) return make("span", "dash-muted", "–");
  const wrap = make("div", "inv-fill");
  const bar = make("div", "dash-hbar");
  const part = make("span", "dash-mix-mid");
  part.style.width = Math.min(100, place.fill * 100) + "%";
  bar.appendChild(part);
  wrap.appendChild(bar);
  wrap.appendChild(make("span", "inv-pct", pct(place.fill)));
  wrap.title = usageText(place);
  return wrap;
}

function renderAlsoMatches(parent: HTMLElement, matcher: Matcher): void {
  if (!matcher.active) return;
  const line = make("p", "dash-note inv-also");
  if (matcher.exact && matcher.also.length) {
    line.appendChild(document.createTextNode("exactly " + matcher.exact + " · also containing “" + filterText() + "”: "));
    matcher.also.forEach(function (name, i) {
      if (i) line.appendChild(document.createTextNode(", "));
      line.appendChild(link(inventoryDash(name), name));
    });
    line.appendChild(document.createTextNode(" · "));
  }
  line.appendChild(document.createTextNode("the tiles count the whole world"));
  parent.appendChild(line);
}

/* The box writes the address as it is typed and redraws only the results under it. */
function renderFilterBox(parent: HTMLElement): void {
  const card = make("section", "dash-card");
  const row = make("div", "inv-search");
  const input = make("input", "dash-name");
  input.type = "search";
  input.placeholder = "filter by item, e.g. Quartz";
  input.setAttribute("aria-label", "filter by item");
  input.setAttribute("data-candidate", "inventory-search");
  input.setAttribute("list", "inv-items");
  input.value = filterText();
  input.oninput = function () {
    state.dash = inventoryDash(input.value);
    writeHash();
    redraw();
  };
  input.onfocus = function () {
    const end = input.value.length;
    input.setSelectionRange(end, end);
  };
  row.appendChild(input);
  card.appendChild(row);
  if (stock.data) {
    const list = make("datalist");
    list.id = "inv-items";
    stock.data.items.forEach(function (pile) {
      const option = make("option");
      option.value = pile.name;
      list.appendChild(option);
    });
    card.appendChild(list);
  }
  const line = make("div");
  card.appendChild(line);
  alsoLine = line;
  parent.appendChild(card);
}

function renderTiles(parent: HTMLElement, data: StockResponse): void {
  const census = data.census;
  const spendable = data.items.filter(function (pile) {
    return pile.spendable > 0;
  }).length;
  let crateItems = 0;
  let uploaders = 0;
  let boxes = 0;
  let filled = 0;
  data.places.forEach(function (place) {
    if (place.source === "crate") crateItems += place.total;
    else if (place.cls === UPLOADER) uploaders += 1;
    else if (place.kind === "solid") {
      boxes += 1;
      if (place.total) filled += 1;
    }
  });
  const tiles = make("div", "dash-tiles");
  tiles.appendChild(tile("item kinds held", count(data.items.length), count(spendable) + " spendable"));
  const rest = [count(filled) + " hold something", counted(census.fluid, "fluid buffer")];
  if (uploaders) rest.push(counted(uploaders, "depot uploader"));
  tiles.appendChild(tile("containers", count(boxes), rest.join(" · ")));
  tiles.appendChild(
    tile("crates on the ground", count(census.crates), counted(census.deaths, "death crate") + " · " + counted(crateItems, "item"))
  );
  parent.appendChild(tiles);
}

function pileColumn(key: string, label: string, pick: (pile: StockPile) => number, title?: string): Column<StockPile> {
  return {
    key: key,
    label: label,
    align: "right",
    title: title,
    sort: pick,
    render: function (pile) {
      return amountOrBlank(pick(pile), pile.fluid);
    },
  };
}

function stockColumns(placesHolding: Record<string, number>): Column<StockPile>[] {
  return [
    {
      key: "name",
      label: "item",
      sort: function (pile) {
        return pile.name;
      },
      render: function (pile) {
        return link(inventoryDash(pile.name), pile.name);
      },
    },
    {
      key: "spendable",
      label: "spendable",
      align: "right",
      title: "carried + storage + depot: what an affordability check spends; fluids in m³",
      sort: function (pile) {
        return pile.spendable;
      },
      render: function (pile) {
        return amount(pile.spendable, pile.fluid);
      },
    },
    pileColumn("carried", "carried", function (pile) {
      return pile.carried;
    }),
    pileColumn("storage", "storage", function (pile) {
      return pile.storage;
    }),
    pileColumn("depot", "depot", function (pile) {
      return pile.depot;
    }),
    pileColumn(
      "buffers",
      "in machines",
      function (pile) {
        return pile.buffers;
      },
      "machine buffers: listed, never spendable"
    ),
    pileColumn(
      "crates",
      "in crates",
      function (pile) {
        return pile.crates;
      },
      "crates on the ground: recoverable, never spendable"
    ),
    {
      key: "places",
      label: "held in",
      align: "right",
      title: "how many containers, fluid buffers and crates hold it",
      sort: function (pile) {
        return placesHolding[pile.item] || 0;
      },
      render: function (pile) {
        return placesHolding[pile.item] ? count(placesHolding[pile.item]!) : "";
      },
    },
  ];
}

function renderStock(parent: HTMLElement, data: StockResponse, rows: StockPile[]): void {
  const card = make("section", "dash-card");
  heading(card, "stock");
  const placesHolding: Record<string, number> = {};
  data.places.forEach(function (place) {
    place.items.forEach(function (item) {
      placesHolding[item.item] = (placesHolding[item.item] || 0) + 1;
    });
  });
  const grid = table(stockColumns(placesHolding), rows, {
    sort: sorts.stock,
    caption: "stock per item",
    onRow: function (pile) {
      go(inventoryDash(pile.name));
    },
  });
  card.appendChild(grid);
  capRows(card, grid, rows.length, STOCK_SHOWN, "show all " + counted(rows.length, "item"), view.allStock, function () {
    view.allStock = true;
  });
  parent.appendChild(card);
}

function kindPicker(): HTMLElement {
  const kind = selectBox(
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

function emptyToggle(matcher: Matcher): HTMLElement {
  const toggle = checkbox("show empty", view.showEmpty, function (on) {
    view.showEmpty = on;
    redraw();
  });
  if (matcher.active) {
    toggle.querySelector("input")!.disabled = true;
    toggle.classList.add("off");
    toggle.title = "a filter shows only containers holding a match";
  }
  return toggle;
}

function containerColumns(matcher: Matcher): Column<StockPlace>[] {
  return [
    {
      key: "name",
      label: "container",
      sort: function (place) {
        return place.name;
      },
      render: function (place) {
        return stacked(place.name, [[placeLine(place), capacityText(place)].filter(Boolean).join(" · ")]);
      },
    },
    {
      key: "held",
      label: matcher.active ? "matching" : "holds",
      title: "solids and fluids sort apart",
      sort: function (place) {
        return (place.kind === "fluid" ? 0 : 1e9) + matchingAmount(place, matcher);
      },
      render: function (place) {
        return contentsText(place, matcher);
      },
      className: "inv-contents",
    },
    {
      key: "fill",
      label: "fill",
      title: "used slots over slots for a container, m³ over capacity for a fluid buffer",
      sort: function (place) {
        return place.fill === null ? -1 : place.fill;
      },
      render: fillCell,
      className: "bar",
    },
    {
      key: "map",
      label: "",
      align: "right",
      render: placeMapButton,
    },
  ];
}

function renderContainers(parent: HTMLElement, data: StockResponse, matcher: Matcher): void {
  const card = make("section", "dash-card");
  const bar = make("div", "dash-title");
  heading(bar, "containers");
  bar.appendChild(kindPicker());
  bar.appendChild(emptyToggle(matcher));
  card.appendChild(bar);
  const all = data.places.filter(function (place) {
    return place.source === "storage";
  });
  const rows = all.filter(function (place) {
    if (view.kind !== "all" && place.kind !== view.kind) return false;
    if (matcher.active) return matchingAmount(place, matcher) > 0;
    return view.showEmpty || place.total > 0;
  });
  if (!rows.length) {
    empty(card, matcher.active ? "no container holds a matching item" : "no container matches these filters");
    parent.appendChild(card);
    return;
  }
  card.appendChild(table(containerColumns(matcher), rows, { sort: sorts.containers, caption: "containers", onRow: flyToPlace }));
  if (rows.length < all.length) appendNote(card, count(rows.length) + " of " + count(all.length) + " shown");
  parent.appendChild(card);
}

function crateColumns(matcher: Matcher, playerKnown: boolean): Column<StockPlace>[] {
  return [
    {
      key: "kind",
      label: "crate",
      title: "a crate deletes itself once emptied; the save records no owner and no time",
      sort: function (place) {
        return crateLabel(place.crate_kind || "");
      },
      render: function (place) {
        const box = stacked(crateLabel(place.crate_kind || ""), [place.crate_kind_text || "", placeLine(place)]);
        box.appendChild(make("span", "inv-narrow", contentsText(place, matcher)));
        return box;
      },
    },
    {
      key: "distance",
      label: playerKnown ? "from the player" : "distance",
      align: "right",
      title: "straight line from where the player last stood",
      sort: function (place) {
        return place.distance_m === null ? Infinity : place.distance_m;
      },
      render: function (place) {
        return place.distance_m === null ? "–" : formatNumber(place.distance_m, 0) + " m";
      },
    },
    {
      key: "held",
      label: matcher.active ? "matching" : "items",
      align: "right",
      sort: function (place) {
        return matchingAmount(place, matcher);
      },
      render: function (place) {
        return count(matchingAmount(place, matcher));
      },
    },
    {
      key: "contents",
      label: "contents",
      render: function (place) {
        return contentsText(place, matcher);
      },
      className: "inv-contents",
    },
    {
      key: "map",
      label: "",
      align: "right",
      render: placeMapButton,
    },
  ];
}

function renderCrates(parent: HTMLElement, data: StockResponse, matcher: Matcher): void {
  const rows = data.places.filter(function (place) {
    return place.source === "crate" && (!matcher.active || matchingAmount(place, matcher) > 0);
  });
  if (!rows.length) return;
  const card = make("section", "dash-card inv-crates");
  heading(card, "crates");
  const playerKnown = data.player.x_m !== null;
  card.appendChild(table(crateColumns(matcher, playerKnown), rows, { sort: sorts.crates, caption: "crates", onRow: flyToPlace }));
  parent.appendChild(card);
}

function retry(): void {
  loadOne("/api/stock");
}

function renderResults(parent: HTMLElement): void {
  if (alsoLine) alsoLine.textContent = "";
  const data = stock.data;
  if (!data) {
    pendingNotice(parent, "stock", null, stock.failed, retry);
    return;
  }
  const matcher = buildMatcher(data);
  if (alsoLine) renderAlsoMatches(alsoLine, matcher);
  renderTiles(parent, data);
  const piles = data.items.filter(function (pile) {
    return matcher.test(pile.name);
  });
  const anywhere = data.places.some(function (place) {
    return matchingAmount(place, matcher) > 0;
  });
  if (matcher.active && !piles.length && !anywhere) {
    empty(parent, "nothing held matches “" + filterText() + "”", "not carried, stored, in the depot, in a machine or in a crate");
    return;
  }
  renderCrates(parent, data, matcher);
  if (piles.length) renderStock(parent, data, piles);
  renderContainers(parent, data, matcher);
}

function redraw(): void {
  if (!results) return;
  results.textContent = "";
  renderResults(results);
}

export function renderInventory(body: HTMLElement): void {
  renderFilterBox(body);
  results = make("div", "inv-results");
  body.appendChild(results);
  renderResults(results);
}

function inventoryTabOpen(): boolean {
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
    if (inventoryTabOpen()) requestRender();
  },
  failed: function () {
    stock.data = null;
    stock.failed = true;
    if (inventoryTabOpen()) requestRender();
  },
});
