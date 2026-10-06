/* The side panel: factory health and the power circuits, over the map. See
 * docs/spatial-and-map.md §21. */

import { button, chip, empty, error, link, loading } from "../kit/dashkit";
import { issueCount, issueGroups } from "../dash/machine-health";
import { el, LASSO_ATTR, make, TRACE_ATTR, TRACE_DIR_ATTR } from "../kit/dom";
import { keepFocus } from "../kit/focus";
import { showRef } from "./tools/finder";
import { count, mw, pct } from "../kit/format";
import { chooseLabel, FACTORY_PICKED, flyToFactory, paddedBounds, reveal } from "./labels";
import { onLayersToggle, setLayersOpen } from "./layercontrol/control";
import { loadOne } from "../app/load";
import { flyPadded, NARROW } from "./map";
import {
  biomassLine,
  biomassQuery,
  circuitName,
  ledgerBar,
  onBiomass,
  ratedCircuit,
  ratedWorld,
  readGeneration,
  readHeadroomFull,
  readHeadroomNow,
  readMeasuredDraw,
  whereOf,
} from "../dash/power-ledger";
import { registerFetch } from "../app/registry";
import { onSelect, select, selected } from "../app/selection";
import { clearMark, outline, ringAt, ringedKey, selectAndRing } from "./map-highlight";
import { editName, renamingIn } from "../dash/factories/rename";
import { state } from "../app/state";
import { notifyVitals, vitals } from "../app/vitals";
import { actionTone, learnStates, stateTone, statesOf } from "../dash/machine-states";
import { counted, WORDS } from "../kit/words";

import type { IssueGroup } from "../dash/machine-health";
import type { CircuitRow, CircuitsResponse, FactoryHealthResponse, FactoryHealthRow, MachineRef, StarvedGenerator } from "../api/shapes";
import type { Rated, Reading } from "../dash/power-ledger";
import type { Selection } from "../app/selection";

type Tab = "factories" | "power";

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
};

var readings = vitals();

var missed = false;

function changed(): void {
  render();
  notifyVitals();
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

interface Placed {
  x_m: number;
  y_m: number;
}

export function located(row: { x_m: number | null; y_m: number | null }): row is Placed {
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
  else clearMark();
  render();
  select({ kind: "factory", key: row.name, label: row.name });
}

function selectCircuit(row: CircuitRow): void {
  view.circuit = view.circuit === row.index ? -1 : row.index;
  var bounds = paddedBounds(row.bbox_m);
  if (view.circuit >= 0 && bounds) {
    outline(bounds);
    flyPadded(bounds, CIRCUIT_ZOOM);
  } else {
    clearMark();
  }
  render();
  select(view.circuit >= 0 ? { kind: "circuit", key: String(row.index), label: circuitName(row) } : null);
}

function spot(s: Selection | null): boolean {
  return !!s && (s.kind === "point" || s.kind === "machine");
}

function follow(): void {
  var s = selected();
  if (!s) clearMark();
  var factory = s && s.kind === "factory" && factoryNamed(s.key) ? s.key : "";
  var circuitRow = s && s.kind === "circuit" && readings.circuits ? readings.circuits.circuits[+s.key] : undefined;
  var circuit = circuitRow ? circuitRow.index : -1;
  if (s && spot(s) && ringedKey !== s.kind + ":" + s.key && s.x_m !== undefined && s.y_m !== undefined) {
    ringAt(s.x_m, s.y_m, s.label, s.kind + ":" + s.key);
  }
  if (s && !factory && !circuitRow && !spot(s)) return;
  if (factory === view.factory && circuit === view.circuit) return;
  view.factory = factory;
  view.circuit = circuit;
  var box = factory ? factoryNamed(factory)!.bbox_m : circuitRow ? circuitRow.bbox_m : null;
  var bounds = box ? paddedBounds(box) : null;
  if (bounds) outline(bounds);
  else if (!spot(s)) clearMark();
  render();
}

function pinButton(row: { x_m: number | null; y_m: number | null }, label: string): HTMLElement | null {
  if (!located(row)) return null;
  var at = row;
  return button(
    "map",
    function () {
      reveal(["machines"]);
      selectAndRing(at.x_m, at.y_m, label);
    },
    { map: true, title: "fly the map to it", label: "show " + label + " on the map" }
  );
}

function stateChips(row: FactoryHealthRow): HTMLElement {
  var chips = make("div", "panel-chips");
  row.states.forEach(function (s) {
    var t = stateTone(s.state);
    if (t === "ok") return;
    chips.appendChild(chip(s.count + " " + s.state, t));
  });
  if (row.unwired) chips.appendChild(chip(row.unwired + " " + WORDS.noWire, "bad", WORDS.powerProblems));
  if (row.no_generator) chips.appendChild(chip(row.no_generator + " " + WORDS.noGenerator, "bad", WORDS.powerProblems));
  if (row.review) chips.appendChild(chip("label " + row.review, "muted"));
  return chips;
}

function issueRow(group: IssueGroup): HTMLElement {
  var line = make("li", "panel-issue");
  var text = make("span", "panel-issue-text");
  text.appendChild(make("span", "panel-issue-state " + stateTone(group.state, true), group.state));
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
        WORDS.measuredDraw +
        " of " +
        mw(row.nameplate_mw)
    )
  );
  item.appendChild(stateChips(row));
  if (selected) {
    item.appendChild(link("factories/" + row.name, "open in dashboard", "panel-dash"));
    var tools = make("div", "panel-row-tools");
    tools.appendChild(
      button(
        "rename",
        function () {
          var health = readings.health;
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
    var amend = button("amend", function () {}, { title: "draw around machines on the map to add them or remove them" });
    amend.onclick = null;
    amend.setAttribute(LASSO_ATTR, row.name);
    tools.appendChild(amend);
    item.appendChild(tools);
  }
  var groups = selected ? issueGroups([row]) : [];
  if (groups.length) {
    var list = make("ul", "panel-issues");
    groups.forEach(function (group) {
      list.appendChild(issueRow(group));
    });
    var rest = row.actionable - issueCount(groups);
    if (rest > 0) list.appendChild(make("li", "panel-more", counted(rest, "more machine", "more machines") + " " + WORDS.needAction));
    item.appendChild(list);
  }
  item.onclick = function () {
    selectFactory(row, true);
  };
  return item;
}

function unavailable(body: HTMLElement, thing: string, failed: string, path: "/api/factories/health" | "/api/power/circuits"): boolean {
  if (state.noSaves) {
    empty(body, WORDS.noSaves, "save a game, or set SATISFACTORY_SAVES if the saves live elsewhere");
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
  if (unavailable(body, "factory health", readings.healthError, HEALTH_PATH)) return;
  if (!readings.health) {
    loading(body, "factory health");
    return;
  }
  var rows = readings.health.factories;
  if (!rows.length) {
    empty(body, "no " + WORDS.factories + " named yet", link("factories", "find and name them on the Factories tab"));
    return;
  }
  var todo = rows.filter(function (r) {
    return r.actionable > 0;
  }).length;
  var line = make("p", "panel-note", counted(rows.length, WORDS.factory, WORDS.factories) + " · ");
  line.appendChild(make("span", actionTone(statesOf(rows)), count(todo)));
  line.appendChild(document.createTextNode(" " + WORDS.needAction));
  body.appendChild(line);
  var list = make("ul", "panel-list");
  rows.forEach(function (row) {
    list.appendChild(factoryRow(row));
  });
  body.appendChild(list);
}

function ledgerBlock(r: Rated, starved: number): HTMLElement {
  var box = make("div", "panel-ledger");
  var grid = make("div", "panel-kv");
  var pairs: [string, Reading][] = [
    [WORDS.generation, readGeneration(r)],
    [WORDS.measuredDraw, readMeasuredDraw(r)],
    [WORDS.nameplateDraw, { value: mw(r.ledger.draw_mw), bad: false, why: "" }],
    [WORDS.headroomNow, readHeadroomNow(r)],
    [WORDS.headroomFull, readHeadroomFull(r)],
  ];
  if (starved) pairs.splice(1, 0, ["of it starved", { value: mw(starved), bad: true, why: "" }]);
  pairs.forEach(function (p) {
    grid.appendChild(make("span", "panel-k", p[0]));
    var v = make("span", "panel-v" + (p[1].bad ? " bad" : ""), p[1].value);
    if (p[1].why) v.title = p[1].why;
    grid.appendChild(v);
  });
  box.appendChild(ledgerBar(r.ledger));
  box.appendChild(grid);
  var extra = biomassLine(r.ledger);
  if (extra) box.appendChild(make("p", "panel-note", extra));
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
    var sub = "missing" in r ? mw(r.mw) + " · " + r.cause : whereOf(r).where;
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
  var r = ratedCircuit(row);
  var item = make("li", "panel-row" + (selected ? " on" : "") + (row.bbox_m ? " go" : ""));
  var head = make("div", "panel-row-head");
  head.appendChild(
    nameButton(circuitName(row), selected, function () {
      selectCircuit(row);
    })
  );
  var now = r.dark ? readGeneration(r) : readHeadroomNow(r);
  var tone = now.bad || led.starved_generation_mw > 0 ? " bad" : now.why ? "" : " ok";
  var badge = make("span", "panel-badge" + tone, now.value);
  badge.title = now.why || WORDS.headroomNow;
  head.appendChild(badge);
  item.appendChild(head);
  item.appendChild(
    make(
      "div",
      "panel-row-sub",
      readMeasuredDraw(r).value +
        " of " +
        (r.dark ? mw(led.generation_mw) : readGeneration(r).value) +
        " · " +
        counted(row.consumers, "consumer") +
        " · " +
        counted(row.poles, "pole or tower", WORDS.polesAndTowers)
    )
  );
  if (led.generation_mw > 0 || led.draw_mw > 0) item.appendChild(ledgerBar(led));
  if (selected) {
    item.appendChild(link("power/" + (row.index + 1), "open in dashboard", "panel-dash"));
    item.appendChild(ledgerBlock(r, led.starved_generation_mw));
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
    if (row.starved.length) item.appendChild(refList(WORDS.starvedGenerator + "s", row.starved, STARVED_HINT));
  }
  item.onclick = function (event) {
    if ((event.target as HTMLElement).closest("details, a, button")) return;
    selectCircuit(row);
  };
  return item;
}

function renderPower(body: HTMLElement): void {
  if (unavailable(body, "power circuits", readings.circuitsError, CIRCUITS_PATH)) return;
  var data = readings.circuits;
  if (!data) {
    loading(body, "power circuits");
    return;
  }
  body.appendChild(make("h3", "panel-h", "whole world"));
  body.appendChild(ledgerBlock(ratedWorld(data), data.world.starved_generation_mw));
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
  if (data.starved.length || data.unwired.length || data.no_generator.length) body.appendChild(make("h3", "panel-h", WORDS.powerProblems));
  if (data.starved.length) body.appendChild(refList(WORDS.starvedGenerator + "s", data.starved, STARVED_HINT));
  if (data.unwired.length) body.appendChild(refList(WORDS.noWire, data.unwired, "machines on no power line"));
  if (data.no_generator.length) body.appendChild(refList(WORDS.noGenerator, data.no_generator, "wired to a circuit no generator stands on"));
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
  return readings.health
    ? readings.health.factories.filter(function (r) {
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
  else if (/^(node|chain|pipe):/.test(selector)) showRef(selector);
}

export function showCircuit(index: number): void {
  var row = readings.circuits ? readings.circuits.circuits[index] : undefined;
  if (!row) return;
  view.tab = "power";
  view.circuit = -1;
  setOpen(true);
  selectCircuit(row);
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
onSelect(follow);

registerFetch<FactoryHealthResponse>({
  wave: "live",
  rank: 40,
  path: HEALTH_PATH,
  label: "factory health",
  clears: [],
  refilters: false,
  draw: function (data) {
    learnStates(data);
    readings.health = data;
    readings.healthError = "";
    if (view.factory && !factoryNamed(view.factory)) {
      view.factory = "";
      clearMark();
      select(null);
    }
    var s = selected();
    if (s && s.kind === "factory" && !factoryNamed(s.key)) select(null);
    follow();
    changed();
    if (view.pending) showFactory(view.pending);
  },
  failed: function () {
    readings.health = null;
    readings.healthError = "factory health could not be read for this save";
    changed();
  },
});

onBiomass(function () {
  loadOne(CIRCUITS_PATH);
});

registerFetch<CircuitsResponse>({
  wave: "live",
  rank: 50,
  path: CIRCUITS_PATH,
  query: biomassQuery,
  label: "power circuits",
  clears: [],
  refilters: false,
  draw: function (data) {
    readings.circuits = data;
    readings.circuitsError = "";
    if (view.circuit >= data.circuits.length) view.circuit = -1;
    follow();
    changed();
  },
  failed: function () {
    readings.circuits = null;
    readings.circuitsError = "power circuits could not be read for this save";
    changed();
  },
});
