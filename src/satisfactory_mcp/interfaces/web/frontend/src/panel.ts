/* The side panel: factory health and the power circuits, over the map. See
 * docs/spatial-and-map.md §21. */

import { button, chip, empty, error, issueCount, issueGroups, link, loading } from "./dashkit";
import { el, keepFocus, make, TRACE_ATTR, TRACE_DIR_ATTR } from "./dom";
import { count, mw, pct } from "./format";
import { chooseLabel, FACTORY_PICKED, flyToFactory, paddedBounds, reveal } from "./labels";
import { onLayersToggle, setLayersOpen } from "./layercontrol";
import { L } from "./leaflet";
import { loadOne } from "./load";
import { flyPadded, flyToPoint, map, NARROW } from "./map";
import { declareColours } from "./palette";
import { bar, circuitDark, circuitName, headroom, LEDGER } from "./powerview";
import { regionAt } from "./regions";
import { registerFetch } from "./registry";
import { editName, renamingIn } from "./rename";
import { state } from "./state";
import { actionTone, learnStates, statesOf, tone } from "./states";
import { counted, W } from "./words";

import type { IssueGroup } from "./dashkit";
import type {
  CircuitRow,
  CircuitsResponse,
  FactoryHealthResponse,
  FactoryHealthRow,
  Ledger,
  MachineRef,
  StarvedGenerator,
} from "./api-shapes";

type Tab = "factories" | "power";

export var HIGHLIGHT = declareColours("panel", { highlight: "#ff4fd8" }).highlight;

var MACHINE_ZOOM = 2;

var CIRCUIT_ZOOM = 1;

var DARK_SHOWN = 40;

var STORE_KEY = "panel";

var HEALTH_PATH = "/api/factories/health" as const;

var CIRCUITS_PATH = "/api/power/circuits" as const;

var view = {
  open: !NARROW.matches,
  tab: "factories" as Tab,
  factory: "",
  circuit: -1,
  pending: "",
  health: null as FactoryHealthResponse | null,
  healthError: "",
  circuits: null as CircuitsResponse | null,
  circuitsError: "",
};

var mark = L.layerGroup();

var listeners: Array<() => void> = [];

var missed = false;

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
    return;
  }
}

function recall(): void {
  try {
    var saved = JSON.parse(localStorage.getItem(STORE_KEY) || "{}");
    if (typeof saved.open === "boolean" && !NARROW.matches) view.open = saved.open;
    if (saved.tab === "factories" || saved.tab === "power") view.tab = saved.tab;
  } catch (ignored) {
    return;
  }
}

export function makeRoom(keep: "panel" | "layers" | "trace"): void {
  if (!NARROW.matches) return;
  document.body.setAttribute("data-sheet", keep);
  if (keep !== "layers" && state.panel.open) setLayersOpen(false);
  if (keep !== "panel" && view.open) {
    view.open = false;
    render();
  }
}

function setOpen(open: boolean): void {
  view.open = open;
  if (open) makeRoom("panel");
  else if (document.body.getAttribute("data-sheet") === "panel") document.body.removeAttribute("data-sheet");
  remember();
  render();
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

function pin(x_m: number, y_m: number, label?: string): void {
  mark.clearLayers();
  var ring = L.circleMarker([-y_m, x_m], {
    radius: 14,
    color: HIGHLIGHT,
    weight: 3,
    fill: false,
    interactive: false,
  });
  if (label) ring.bindTooltip(label, { permanent: true, direction: "right", offset: [14, 0], className: "pin-label" });
  ring.addTo(mark);
  if (!map.hasLayer(mark)) mark.addTo(map);
  flyToPoint([-y_m, x_m], Math.max(map.getZoom(), MACHINE_ZOOM));
}

interface Placed {
  x_m: number;
  y_m: number;
}

function located(row: { x_m: number | null; y_m: number | null }): row is Placed {
  return row.x_m !== null && row.y_m !== null;
}

function say(body: HTMLElement, text: string): void {
  body.appendChild(make("p", "panel-note", text));
}

function selectFactory(row: FactoryHealthRow, fly: boolean): void {
  view.factory = row.name;
  chooseLabel(row.name);
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
    flyPadded(bounds, CIRCUIT_ZOOM);
  } else {
    mark.clearLayers();
  }
  render();
}

function pinButton(row: { x_m: number | null; y_m: number | null }, label: string): HTMLElement | null {
  if (!located(row)) return null;
  var at = row;
  return button(
    "map",
    function () {
      reveal(["machines"]);
      pin(at.x_m, at.y_m, label);
    },
    { map: true, title: "fly the map to it", label: "show " + label + " on the map" }
  );
}

function stateChips(row: FactoryHealthRow): HTMLElement {
  var chips = make("div", "panel-chips");
  row.states.forEach(function (s) {
    var t = tone(s.state);
    if (t === "ok") return;
    chips.appendChild(chip(s.count + " " + s.state, t));
  });
  if (row.unwired) chips.appendChild(chip(row.unwired + " " + W.noWire, "bad", W.powerProblems));
  if (row.no_generator) chips.appendChild(chip(row.no_generator + " " + W.noGenerator, "bad", W.powerProblems));
  if (row.review) chips.appendChild(chip("label " + row.review, "muted"));
  return chips;
}

function issueRow(group: IssueGroup): HTMLElement {
  var line = make("li", "panel-issue");
  var text = make("span", "panel-issue-text");
  text.appendChild(make("span", "panel-issue-state " + tone(group.state, true), group.state));
  text.appendChild(make("span", "panel-issue-what", group.what));
  var detail = [counted(group.issues.length, "machine"), group.cause].filter(Boolean).join(" · ");
  text.appendChild(make("span", "panel-issue-cause", detail));
  line.appendChild(text);
  var go = pinButton(group.issues[0]!, group.what);
  if (go) line.appendChild(go);
  return line;
}

function nameButton(text: string, expanded: boolean, action: () => void): HTMLElement {
  var host = make("span", "panel-row-name");
  var pick = make("button", "panel-pick", text);
  pick.type = "button";
  pick.title = text;
  pick.setAttribute("aria-expanded", String(expanded));
  pick.onclick = function (event) {
    event.stopPropagation();
    action();
  };
  host.appendChild(pick);
  return host;
}

function factoryRow(row: FactoryHealthRow): HTMLElement {
  var selected = row.name === view.factory;
  var item = make("li", "panel-row" + (selected ? " on" : "") + (row.bbox_m ? " go" : ""));
  item.setAttribute("data-factory", row.name);
  var head = make("div", "panel-row-head");
  var nameEl = nameButton(row.name, selected, function () {
    selectFactory(row, true);
  });
  head.appendChild(nameEl);
  var up = make("span", "panel-badge", pct(row.uptime));
  up.title = "mean uptime over each machine's last 300 s window";
  head.appendChild(up);
  item.appendChild(head);
  item.appendChild(
    make(
      "div",
      "panel-row-sub",
      counted(row.alive, "machine") +
        (row.alive === row.anchors ? "" : " of " + row.anchors) +
        " · " +
        mw(row.measured_mw) +
        " " +
        LEDGER.measuredDraw +
        " of " +
        mw(row.nameplate_mw)
    )
  );
  item.appendChild(stateChips(row));
  if (selected) {
    var tools = make("div", "panel-row-tools");
    tools.appendChild(link("factories/" + row.name, "open in dashboard", "panel-dash"));
    tools.appendChild(
      button(
        "rename",
        function () {
          var health = view.health;
          if (!health) return;
          editName(nameEl, row.name, health.labels_version, function (reply) {
            if (reply) view.factory = reply.name;
            if (reply || missed) render();
          });
        },
        { title: "rename this factory" }
      )
    );
    var trace = button("trace supply", function () {}, { title: "draw what feeds this factory on the map" });
    trace.onclick = null;
    trace.setAttribute(TRACE_ATTR, "label:" + row.name);
    trace.setAttribute(TRACE_DIR_ATTR, "up");
    tools.appendChild(trace);
    item.appendChild(tools);
  }
  var groups = selected ? issueGroups([row]) : [];
  if (groups.length) {
    var list = make("ul", "panel-issues");
    groups.forEach(function (group) {
      list.appendChild(issueRow(group));
    });
    var rest = row.actionable - issueCount(groups);
    if (rest > 0) list.appendChild(make("li", "panel-more", counted(rest, "more machine", "more machines") + " " + W.needAction));
    item.appendChild(list);
  }
  item.onclick = function () {
    selectFactory(row, true);
  };
  return item;
}

function unavailable(body: HTMLElement, thing: string, failed: string, path: "/api/factories/health" | "/api/power/circuits"): boolean {
  if (state.noSaves) {
    empty(body, W.noSaves, "save a game, or set SATISFACTORY_SAVES if the saves live elsewhere");
    return true;
  }
  if (failed) {
    error(body, thing, "", function () {
      loadOne(path);
    });
    return true;
  }
  return false;
}

function renderFactories(body: HTMLElement): void {
  if (unavailable(body, "factory health", view.healthError, HEALTH_PATH)) return;
  if (!view.health) {
    loading(body, "factory health");
    return;
  }
  var rows = view.health.factories;
  if (!rows.length) {
    empty(body, "no " + W.factories + " named yet", link("factories", "find and name them on the Factories tab"));
    return;
  }
  var todo = rows.filter(function (r) {
    return r.actionable > 0;
  }).length;
  var line = make("p", "panel-note", counted(rows.length, W.factory, W.factories) + " · ");
  line.appendChild(make("span", actionTone(statesOf(rows)), count(todo)));
  line.appendChild(document.createTextNode(" " + W.needAction));
  body.appendChild(line);
  var list = make("ul", "panel-list");
  rows.forEach(function (row) {
    list.appendChild(factoryRow(row));
  });
  body.appendChild(list);
}

function ledgerBlock(ledger: Ledger): HTMLElement {
  var box = make("div", "panel-ledger");
  var grid = make("div", "panel-kv");
  var pairs: [string, string, boolean][] = [
    [LEDGER.generation, mw(ledger.generation_mw), false],
    [LEDGER.measuredDraw, mw(ledger.measured_draw_mw), false],
    [LEDGER.nameplateDraw, mw(ledger.draw_mw), false],
    [LEDGER.headroomNow, headroom(ledger.measured_headroom_mw), ledger.measured_headroom_mw < 0],
    [LEDGER.headroomFull, headroom(ledger.headroom_mw), ledger.headroom_mw < 0],
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

var STARVED_HINT = "input ran dry and produced nothing in its window";

function refList(title: string, rows: Ref[], hint: string): HTMLElement {
  var fold = make("details", "panel-fold-list");
  var summary = make("summary", "", title + " (" + rows.length + ")");
  summary.title = hint;
  fold.appendChild(summary);
  var list = make("ul", "panel-issues");
  rows.slice(0, DARK_SHOWN).forEach(function (r) {
    var line = make("li", "panel-issue");
    var text = make("span", "panel-issue-text");
    text.appendChild(make("span", "panel-issue-what", r.name));
    var where = located(r) ? regionAt(r.x_m, r.y_m) : null;
    var why = "missing" in r ? mw(r.mw) + " · " + r.cause : "";
    var sub = [where, why].filter(Boolean).join(" · ");
    if (sub) text.appendChild(make("span", "panel-issue-cause", sub));
    line.appendChild(text);
    var go = pinButton(r, r.name);
    if (go) line.appendChild(go);
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
  head.appendChild(
    nameButton(circuitName(row), selected, function () {
      selectCircuit(row);
    })
  );
  var dark = circuitDark(row);
  var short = led.measured_headroom_mw < 0 || led.starved_generation_mw > 0;
  head.appendChild(
    make("span", "panel-badge" + (dark || short ? " bad" : " ok"), dark ? W.noGenerator : headroom(led.measured_headroom_mw))
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
        counted(row.consumers, "consumer") +
        " · " +
        counted(row.poles, "pole or tower", LEDGER.poles)
    )
  );
  if (led.generation_mw > 0 || led.draw_mw > 0) item.appendChild(bar(led));
  if (selected) {
    item.appendChild(link("power/" + (row.index + 1), "open in dashboard", "panel-dash"));
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
    if (row.starved.length) item.appendChild(refList(W.starvedGenerator + "s", row.starved, STARVED_HINT));
  }
  item.onclick = function (event) {
    if ((event.target as HTMLElement).closest("details, a, button")) return;
    selectCircuit(row);
  };
  return item;
}

function renderPower(body: HTMLElement): void {
  if (unavailable(body, "power circuits", view.circuitsError, CIRCUITS_PATH)) return;
  var data = view.circuits;
  if (!data) {
    loading(body, "power circuits");
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
  if (data.paused) say(body, counted(data.paused, "paused building") + ", left out of both sides");
  if (data.unmodellable.length) {
    say(body, "not in game data, left out: " + data.unmodellable.join(", "));
  }
  if (data.unwired_generators.length) body.appendChild(refList("generators on no wire", data.unwired_generators, "generation no circuit can use"));
  if (data.starved.length || data.unwired.length || data.no_generator.length) body.appendChild(make("h3", "panel-h", W.powerProblems));
  if (data.starved.length) body.appendChild(refList(W.starvedGenerator + "s", data.starved, STARVED_HINT));
  if (data.unwired.length) body.appendChild(refList(W.noWire, data.unwired, "machines on no power line"));
  if (data.no_generator.length) body.appendChild(refList(W.noGenerator, data.no_generator, "wired to a circuit no generator stands on"));
  body.appendChild(make("h3", "panel-h", counted(data.circuits.length, "circuit")));
  var list = make("ul", "panel-list");
  data.circuits.forEach(function (row) {
    list.appendChild(circuitRow(row));
  });
  body.appendChild(list);
}

export function render(): void {
  var panel = el("panel");
  if (renamingIn(panel)) {
    missed = true;
    return;
  }
  missed = false;
  panel.className = view.open ? "" : "shut";
  var tabs = panel.querySelectorAll<HTMLButtonElement>("[data-tab]");
  Array.prototype.forEach.call(tabs, function (tab: HTMLButtonElement) {
    var on = view.open && tab.getAttribute("data-tab") === view.tab;
    tab.className = "panel-tab";
    tab.setAttribute("aria-selected", String(on));
    tab.tabIndex = on || (!view.open && tab.getAttribute("data-tab") === view.tab) ? 0 : -1;
  });
  var fold = el("panel-fold");
  fold.setAttribute("aria-expanded", String(view.open));
  fold.setAttribute("aria-label", view.open ? "fold the panel away" : "open the panel");
  fold.title = fold.getAttribute("aria-label")!;
  var body = el("panel-body");
  var scroll = body.scrollTop;
  keepFocus(body, function () {
    body.textContent = "";
    if (!view.open) return;
    if (view.tab === "factories") renderFactories(body);
    else renderPower(body);
  });
  body.scrollTop = scroll;
}

function factoryNamed(name: string): FactoryHealthRow | undefined {
  return view.health
    ? view.health.factories.filter(function (r) {
        return r.name === name;
      })[0]
    : undefined;
}

export function showFactory(name: string): void {
  var row = factoryNamed(name);
  if (!row) {
    view.pending = name;
    return;
  }
  view.pending = "";
  view.tab = "factories";
  setOpen(true);
  selectFactory(row, true);
  scrollToFactory();
}

export function showSelector(selector: string): void {
  if (selector.indexOf("label:") === 0) showFactory(selector.slice("label:".length));
}

export function showCircuit(index: number): void {
  var row = view.circuits ? view.circuits.circuits[index] : undefined;
  if (!row) return;
  view.tab = "power";
  view.circuit = -1;
  setOpen(true);
  selectCircuit(row);
}

export function showPoint(x_m: number, y_m: number, options?: { label?: string; layers?: string[] }): void {
  if (options && options.layers) reveal(options.layers);
  pin(x_m, y_m, options && options.label);
}

export function showBox(bbox_m: [number, number, number, number]): void {
  var bounds = flyToFactory(bbox_m);
  if (bounds) outline(bounds);
}

function scrollToFactory(): void {
  var body = el("panel-body");
  var rows = body.querySelectorAll<HTMLElement>("[data-factory]");
  Array.prototype.forEach.call(rows, function (row: HTMLElement) {
    if (row.getAttribute("data-factory") === view.factory) row.scrollIntoView({ block: "nearest" });
  });
}

function pickTab(tab: Tab): void {
  if (view.tab !== tab) el("panel-body").scrollTop = 0;
  view.tab = tab;
  setOpen(true);
}

function wire(): void {
  var tabs: HTMLElement[] = Array.prototype.slice.call(el("panel").querySelectorAll("[data-tab]"));
  tabs.forEach(function (tab, i) {
    tab.onclick = function () {
      pickTab(tab.getAttribute("data-tab") as Tab);
    };
    tab.onkeydown = function (event) {
      if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
      var next = tabs[(i + (event.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length]!;
      pickTab(next.getAttribute("data-tab") as Tab);
      next.focus();
    };
  });
  el("panel-fold").onclick = function () {
    setOpen(!view.open);
  };
  onLayersToggle(function (open) {
    if (open) makeRoom("layers");
    else if (document.body.getAttribute("data-sheet") === "layers") document.body.removeAttribute("data-sheet");
  });
  document.addEventListener(FACTORY_PICKED, function (event) {
    var row = factoryNamed((event as CustomEvent<string>).detail);
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
  path: HEALTH_PATH,
  label: "factory health",
  clears: [],
  refilters: false,
  draw: function (data) {
    learnStates(data);
    view.health = data;
    view.healthError = "";
    if (view.factory && !factoryNamed(view.factory)) {
      view.factory = "";
      mark.clearLayers();
    }
    changed();
    if (view.pending) showFactory(view.pending);
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
  path: CIRCUITS_PATH,
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
