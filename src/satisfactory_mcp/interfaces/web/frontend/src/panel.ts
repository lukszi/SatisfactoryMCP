/* The side panel: factory health and the power circuits, over the map. See
 * docs/spatial-and-map.md §21. */

import { el, make } from "./dom";
import { mw, pct } from "./format";
import { FACTORY_PICKED, flyToFactory, paddedBounds } from "./labels";
import { L } from "./leaflet";
import { hashFor, map } from "./map";
import { declareColours } from "./palette";
import { registerFetch } from "./registry";

import type {
  CircuitRow,
  CircuitsResponse,
  FactoryHealthResponse,
  FactoryHealthRow,
  Ledger,
  MachineIssue,
  MachineRef,
  StarvedGenerator,
} from "./api-shapes";

type Tab = "factories" | "power";

var HIGHLIGHT = declareColours("panel", { highlight: "#ff4fd8" }).highlight;

var MACHINE_ZOOM = 2;

var DARK_SHOWN = 40;

var STORE_KEY = "panel";

var view = {
  open: true,
  tab: "factories" as Tab,
  factory: "",
  circuit: -1,
  health: null as FactoryHealthResponse | null,
  healthError: "",
  circuits: null as CircuitsResponse | null,
  circuitsError: "",
};

var mark = L.layerGroup();

var listeners: Array<() => void> = [];

export interface Vitals {
  health: FactoryHealthResponse | null;
  healthError: string;
  circuits: CircuitsResponse | null;
  circuitsError: string;
}

export function vitals(): Vitals {
  return view;
}

export function onVitals(listener: () => void): void {
  listeners.push(listener);
}

function changed(): void {
  render();
  listeners.forEach(function (listener) {
    listener();
  });
}

function remember(): void {
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify({ open: view.open, tab: view.tab }));
  } catch (ignored) {
    /* storage is a convenience */
  }
}

function recall(): void {
  try {
    var saved = JSON.parse(localStorage.getItem(STORE_KEY) || "{}");
    if (typeof saved.open === "boolean") view.open = saved.open;
    if (saved.tab === "factories" || saved.tab === "power") view.tab = saved.tab;
  } catch (ignored) {
    /* storage is a convenience */
  }
}

function outline(bounds: L.LatLngBounds): void {
  mark.clearLayers();
  L.rectangle(bounds, {
    color: HIGHLIGHT,
    weight: 2,
    dashArray: "6 4",
    fill: false,
    interactive: false,
  }).addTo(mark);
  if (!map.hasLayer(mark)) mark.addTo(map);
}

function pin(x_m: number, y_m: number): void {
  mark.clearLayers();
  L.circleMarker([-y_m, x_m], {
    radius: 14,
    color: HIGHLIGHT,
    weight: 3,
    fill: false,
    interactive: false,
  }).addTo(mark);
  if (!map.hasLayer(mark)) mark.addTo(map);
  map.flyTo([-y_m, x_m], Math.max(map.getZoom(), MACHINE_ZOOM));
}

interface Placed {
  x_m: number;
  y_m: number;
}

function located(row: { x_m: number | null; y_m: number | null }): row is Placed {
  return row.x_m !== null && row.y_m !== null;
}

function dashLink(address: string, text: string): HTMLElement {
  var link = make("a", "panel-dash", text);
  link.setAttribute("href", hashFor(address));
  link.onclick = function (event) {
    event.stopPropagation();
    link.setAttribute("href", hashFor(address));
  };
  return link;
}

function say(body: HTMLElement, text: string): void {
  body.appendChild(make("p", "panel-note", text));
}

function selectFactory(row: FactoryHealthRow, fly: boolean): void {
  view.factory = row.name;
  var bounds = fly ? flyToFactory(row.bbox_m) : paddedBounds(row.bbox_m);
  if (bounds) outline(bounds);
  else mark.clearLayers();
  render();
}

function selectCircuit(row: CircuitRow): void {
  view.circuit = view.circuit === row.index ? -1 : row.index;
  var bounds = paddedBounds(row.bbox_m);
  if (view.circuit >= 0 && bounds) {
    outline(bounds);
    map.flyToBounds(bounds, { maxZoom: 1 });
  } else {
    mark.clearLayers();
  }
  render();
}

function stateChips(row: FactoryHealthRow): HTMLElement {
  var chips = make("div", "panel-chips");
  row.states.forEach(function (s) {
    if (s.state === "saturated" || s.state === "unmonitored") return;
    var bad = !!view.health && view.health.actionable_states.indexOf(s.state) >= 0;
    chips.appendChild(make("span", "panel-chip" + (bad ? " bad" : ""), s.count + " " + s.state));
  });
  if (row.unwired) chips.appendChild(make("span", "panel-chip bad", row.unwired + " no wire"));
  if (row.no_generator) {
    chips.appendChild(make("span", "panel-chip bad", row.no_generator + " no generator"));
  }
  if (row.review) chips.appendChild(make("span", "panel-chip", "label " + row.review));
  return chips;
}

function issueRow(issue: MachineIssue): HTMLElement {
  var line = make("li", "panel-issue" + (located(issue) ? " go" : ""));
  line.appendChild(make("span", "panel-issue-state", issue.state));
  line.appendChild(make("span", "panel-issue-what", issue.what));
  var detail = issue.cause.length ? issue.cause.join(", ") : "";
  if (issue.uptime !== null) detail = (detail ? detail + " · " : "") + pct(issue.uptime) + " up";
  if (detail) line.appendChild(make("span", "panel-issue-cause", detail));
  line.title = issue.instance;
  if (located(issue)) {
    var at = issue;
    line.onclick = function (event) {
      event.stopPropagation();
      pin(at.x_m, at.y_m);
    };
  }
  return line;
}

function factoryRow(row: FactoryHealthRow): HTMLElement {
  var selected = row.name === view.factory;
  var item = make("li", "panel-row" + (selected ? " on" : "") + (row.bbox_m ? " go" : ""));
  item.setAttribute("data-factory", row.name);
  var head = make("div", "panel-row-head");
  head.appendChild(make("span", "panel-row-name", row.name));
  var up = make("span", "panel-badge" + (row.actionable ? " bad" : " ok"), pct(row.uptime));
  up.title = "mean uptime over each machine's last 300 s window";
  head.appendChild(up);
  item.appendChild(head);
  item.appendChild(
    make(
      "div",
      "panel-row-sub",
      row.alive +
        (row.alive === row.anchors ? "" : " of " + row.anchors) +
        " machines · " +
        mw(row.measured_mw) +
        " measured of " +
        mw(row.nameplate_mw)
    )
  );
  item.appendChild(stateChips(row));
  if (selected) item.appendChild(dashLink("factories/" + row.name, "open in dashboard ›"));
  if (selected && row.worst.length) {
    var list = make("ul", "panel-issues");
    row.worst.forEach(function (issue) {
      list.appendChild(issueRow(issue));
    });
    var rest = row.attention - row.worst.length;
    if (rest > 0) list.appendChild(make("li", "panel-more", rest + " more need attention"));
    item.appendChild(list);
  }
  item.onclick = function () {
    selectFactory(row, true);
  };
  return item;
}

function renderFactories(body: HTMLElement): void {
  if (view.healthError || !view.health) {
    say(body, view.healthError || "loading…");
    return;
  }
  var rows = view.health.factories;
  if (!rows.length) {
    say(body, "no factories named yet — name one with the name_factory tool");
    return;
  }
  var todo = rows.filter(function (r) {
    return r.actionable > 0;
  }).length;
  say(body, rows.length + " named · " + todo + " with starved, stalled, dead or unset machines");
  var list = make("ul", "panel-list");
  rows.forEach(function (row) {
    list.appendChild(factoryRow(row));
  });
  body.appendChild(list);
}

function bar(ledger: Ledger): HTMLElement {
  var track = make("div", "panel-bar");
  var cap = Math.max(ledger.generation_mw, ledger.draw_mw, 1);
  var measured = make("span", "panel-bar-measured");
  measured.style.width = Math.min(100, (ledger.measured_draw_mw / cap) * 100) + "%";
  var nameplate = make("span", "panel-bar-nameplate");
  nameplate.style.width = Math.min(100, (ledger.draw_mw / cap) * 100) + "%";
  var generation = make("span", "panel-bar-cap");
  generation.style.left = Math.min(100, (ledger.generation_mw / cap) * 100) + "%";
  track.appendChild(nameplate);
  track.appendChild(measured);
  track.appendChild(generation);
  track.title =
    mw(ledger.measured_draw_mw) +
    " measured draw, " +
    mw(ledger.draw_mw) +
    " nameplate, against " +
    mw(ledger.generation_mw) +
    " generation";
  return track;
}

function ledgerBlock(ledger: Ledger): HTMLElement {
  var box = make("div", "panel-ledger");
  var grid = make("div", "panel-kv");
  var pairs: [string, string, boolean][] = [
    ["generation", mw(ledger.generation_mw), false],
    ["draw, measured", mw(ledger.measured_draw_mw), false],
    ["draw, nameplate", mw(ledger.draw_mw), false],
    ["headroom, measured", mw(ledger.measured_headroom_mw), ledger.measured_headroom_mw < 0],
    ["headroom, nameplate", mw(ledger.headroom_mw), ledger.headroom_mw < 0],
  ];
  if (ledger.starved_generation_mw) {
    pairs.splice(1, 0, ["of it starved", mw(ledger.starved_generation_mw), true]);
  }
  pairs.forEach(function (p) {
    grid.appendChild(make("span", "panel-k", p[0]));
    grid.appendChild(make("span", "panel-v" + (p[2] ? " bad" : ""), p[1]));
  });
  box.appendChild(bar(ledger));
  box.appendChild(grid);
  return box;
}

type Ref = MachineRef | StarvedGenerator;

var STARVED = "starved generators";
var STARVED_HINT = "input ran dry and produced nothing in its window";

function refList(title: string, rows: Ref[], hint: string): HTMLElement {
  var fold = make("details", "panel-fold-list");
  var summary = make("summary", "", title + " (" + rows.length + ")");
  summary.title = hint;
  fold.appendChild(summary);
  var list = make("ul", "panel-issues");
  rows.slice(0, DARK_SHOWN).forEach(function (r) {
    var line = make("li", "panel-issue" + (located(r) ? " go" : ""));
    line.appendChild(make("span", "panel-issue-what", r.name));
    if ("missing" in r) {
      var why = mw(r.mw) + " · out of " + r.missing.join(", ");
      line.appendChild(make("span", "panel-issue-cause", why));
    }
    line.title = r.instance;
    if (located(r)) {
      var at = r;
      line.onclick = function () {
        pin(at.x_m, at.y_m);
      };
    }
    list.appendChild(line);
  });
  var rest = rows.length - DARK_SHOWN;
  if (rest > 0) list.appendChild(make("li", "panel-more", rest + " more"));
  fold.appendChild(list);
  return fold;
}

function circuitRow(row: CircuitRow): HTMLElement {
  var selected = row.index === view.circuit;
  var led = row.ledger;
  var item = make("li", "panel-row" + (selected ? " on" : "") + (row.bbox_m ? " go" : ""));
  var head = make("div", "panel-row-head");
  var name = row.factories.length ? row.factories.join(", ") : "circuit " + (row.index + 1);
  head.appendChild(make("span", "panel-row-name", name));
  var dark = led.generation_mw <= 0 && row.consumers > 0;
  var short = led.measured_headroom_mw < 0 || led.starved_generation_mw > 0;
  head.appendChild(
    make(
      "span",
      "panel-badge" + (dark || short ? " bad" : " ok"),
      dark ? "no generator" : mw(led.measured_headroom_mw) + " free"
    )
  );
  item.appendChild(head);
  item.appendChild(
    make(
      "div",
      "panel-row-sub",
      mw(led.measured_draw_mw) +
        " of " +
        mw(led.generation_mw) +
        " · " +
        row.consumers +
        " consumers · " +
        row.poles +
        " poles"
    )
  );
  if (led.generation_mw > 0 || led.draw_mw > 0) item.appendChild(bar(led));
  if (selected) {
    item.appendChild(dashLink("power/" + (row.index + 1), "open in dashboard ›"));
    item.appendChild(ledgerBlock(led));
    if (row.generators.length) {
      item.appendChild(
        make(
          "div",
          "panel-row-sub",
          row.generators
            .map(function (g) {
              return g.count + "× " + g.name;
            })
            .join(", ")
        )
      );
    }
    if (row.starved.length) item.appendChild(refList(STARVED, row.starved, STARVED_HINT));
  }
  item.onclick = function (event) {
    if ((event.target as HTMLElement).closest("details")) return;
    selectCircuit(row);
  };
  return item;
}

function renderPower(body: HTMLElement): void {
  var data = view.circuits;
  if (view.circuitsError || !data) {
    say(body, view.circuitsError || "loading…");
    return;
  }
  body.appendChild(make("h3", "panel-h", "whole world"));
  body.appendChild(ledgerBlock(data.world));
  if (data.generators.length) {
    var kinds = data.generators.map(function (g) {
      return g.count + "× " + g.name + " " + mw(g.mw);
    });
    say(body, kinds.join(" · "));
  }
  if (data.paused) say(body, data.paused + " paused buildings are left out of both sides");
  if (data.unmodellable.length) {
    say(body, "not in game data, left out: " + data.unmodellable.join(", "));
  }
  if (data.starved.length) body.appendChild(refList(STARVED, data.starved, STARVED_HINT));
  if (data.unwired.length) {
    body.appendChild(refList("no power connection", data.unwired, "machines with no wire at all"));
  }
  if (data.no_generator.length) {
    var hint = "wired, but to a circuit no generator stands on";
    body.appendChild(refList("no generator on its circuit", data.no_generator, hint));
  }
  body.appendChild(make("h3", "panel-h", data.circuits.length + " circuits"));
  var list = make("ul", "panel-list");
  data.circuits.forEach(function (row) {
    list.appendChild(circuitRow(row));
  });
  body.appendChild(list);
  say(body, "a circuit is what the wires join; switches read as closed, batteries are not counted");
}

function render(): void {
  var panel = el("panel");
  panel.className = view.open ? "" : "shut";
  var tabs = panel.querySelectorAll<HTMLButtonElement>("[data-tab]");
  Array.prototype.forEach.call(tabs, function (button: HTMLButtonElement) {
    var on = view.open && button.getAttribute("data-tab") === view.tab;
    button.className = "panel-tab" + (on ? " on" : "");
  });
  var fold = el("panel-fold");
  fold.textContent = view.open ? "–" : "+";
  fold.title = view.open ? "fold the panel away" : "open the panel";
  var body = el("panel-body");
  var scroll = body.scrollTop;
  body.textContent = "";
  if (!view.open) return;
  if (view.tab === "factories") renderFactories(body);
  else renderPower(body);
  body.scrollTop = scroll;
}

export function showFactory(name: string): void {
  var row = view.health
    ? view.health.factories.filter(function (r) {
        return r.name === name;
      })[0]
    : undefined;
  if (!row) return;
  view.open = true;
  view.tab = "factories";
  selectFactory(row, true);
  scrollToFactory();
}

export function showCircuit(index: number): void {
  var row = view.circuits ? view.circuits.circuits[index] : undefined;
  if (!row) return;
  view.open = true;
  view.tab = "power";
  view.circuit = -1;
  selectCircuit(row);
}

export function showPoint(x_m: number, y_m: number): void {
  pin(x_m, y_m);
}

function scrollToFactory(): void {
  var body = el("panel-body");
  var rows = body.querySelectorAll<HTMLElement>("[data-factory]");
  Array.prototype.forEach.call(rows, function (row: HTMLElement) {
    if (row.getAttribute("data-factory") === view.factory) row.scrollIntoView({ block: "nearest" });
  });
}

function wire(): void {
  var buttons = el("panel").querySelectorAll("[data-tab]");
  Array.prototype.forEach.call(buttons, function (button: HTMLElement) {
    button.onclick = function () {
      var tab = button.getAttribute("data-tab") as Tab;
      view.open = true;
      if (view.tab !== tab) el("panel-body").scrollTop = 0;
      view.tab = tab;
      remember();
      render();
    };
  });
  el("panel-fold").onclick = function () {
    view.open = !view.open;
    remember();
    render();
  };
  document.addEventListener(FACTORY_PICKED, function (event) {
    var name = (event as CustomEvent<string>).detail;
    var row = view.health
      ? view.health.factories.filter(function (r) {
          return r.name === name;
        })[0]
      : undefined;
    if (!row) return;
    view.tab = "factories";
    selectFactory(row, false);
    if (view.open) scrollToFactory();
  });
}

recall();
wire();
render();

registerFetch<FactoryHealthResponse>({
  wave: "live",
  rank: 40,
  path: "/api/factories/health",
  label: "factory health",
  clears: [],
  refilters: false,
  draw: function (data) {
    view.health = data;
    view.healthError = "";
    if (
      view.factory &&
      !data.factories.some(function (r) {
        return r.name === view.factory;
      })
    ) {
      view.factory = "";
      mark.clearLayers();
    }
    changed();
  },
  failed: function () {
    view.health = null;
    view.healthError = "factory health could not be read for this save";
    changed();
  },
});

registerFetch<CircuitsResponse>({
  wave: "live",
  rank: 50,
  path: "/api/power/circuits",
  label: "power circuits",
  clears: [],
  refilters: false,
  draw: function (data) {
    view.circuits = data;
    view.circuitsError = "";
    if (view.circuit >= data.circuits.length) view.circuit = -1;
    changed();
  },
  failed: function () {
    view.circuits = null;
    view.circuitsError = "power circuits could not be read for this save";
    changed();
  },
});
