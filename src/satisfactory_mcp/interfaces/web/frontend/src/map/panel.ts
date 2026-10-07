/* The side panel: factory health and the power circuits, over the map. See
 * docs/spatial-and-map.md §21. */

import { button, chip, delegatedButton, empty, error, link, loading } from "../kit/dashkit";
import { issueCount, issueGroups } from "../dash/machine-health";
import { el, LASSO_ATTR, make, TRACE_ATTR, TRACE_DIR_ATTR } from "../kit/dom";
import { keepFocus } from "../kit/focus";
import { showRef } from "./tools/finder";
import { buildingCounts, count, mw, pct } from "../kit/format";
import { FACTORY_PICKED, flyToBuiltArea, paddedBounds, prioritiseLabel } from "./labels";
import { onLayersToggle, setLayersOpen } from "./layercontrol/control";
import { loadOne } from "../app/load";
import { flyPadded, NARROW } from "./map";
import {
  biomassQuery,
  circuitName,
  ledgerBar,
  onBiomass,
  ratedCircuit,
  ratedWorld,
  readGeneration,
  readHeadroomNow,
  readMeasuredDraw,
} from "../dash/power-ledger";
import { badgeTone, ledgerBlock, nameButton, panelNote, refList, showOnMapButton, STARVED_HINT } from "./panel-rows";
import { registerFetch } from "../app/registry";
import { onSelect, select, selected } from "../app/selection";
import { clearMark, isRinged, outline, ringAt } from "./map-highlight";
import { editName, renamingIn } from "../dash/factories/rename";
import { state } from "../app/state";
import { notifyVitals, vitals } from "../app/vitals";
import { actionTone, learnStates, stateTone, statesOf } from "../dash/machine-states";
import { counted, WORDS } from "../kit/words";

import type { IssueGroup } from "../dash/machine-health";
import type { CircuitRow, CircuitsResponse, FactoryHealthResponse, FactoryHealthRow } from "../api/shapes";
import type { Selection } from "../app/selection";

type Tab = "factories" | "power";

const CIRCUIT_ZOOM = 1;

const STORE_KEY = "panel";

const HEALTH_PATH = "/api/factories/health" as const;

const CIRCUITS_PATH = "/api/power/circuits" as const;

const view = {
  open: !NARROW.matches,
  tab: "factories" as Tab,
  factory: "",
  circuit: -1,
  pending: "",
};

const readings = vitals();

/** Set when a render was skipped because a rename was being typed; the rename renders after. */
let renderDeferred = false;

function changed(): void {
  renderPanel();
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
    const saved = JSON.parse(localStorage.getItem(STORE_KEY) || "{}");
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
    renderPanel();
  }
}

function setOpen(open: boolean): void {
  view.open = open;
  if (open) makeRoom("panel");
  else if (document.body.getAttribute("data-sheet") === "panel") document.body.removeAttribute("data-sheet");
  remember();
  renderPanel();
}

function selectFactory(row: FactoryHealthRow, fly: boolean): void {
  view.factory = row.name;
  prioritiseLabel(row.name);
  const bounds = fly ? flyToBuiltArea(row.bbox_m) : paddedBounds(row.bbox_m);
  if (bounds) outline(bounds);
  else clearMark();
  renderPanel();
  select({ kind: "factory", key: row.name, label: row.name });
}

function selectCircuit(row: CircuitRow): void {
  view.circuit = view.circuit === row.index ? -1 : row.index;
  const bounds = paddedBounds(row.bbox_m);
  if (view.circuit >= 0 && bounds) {
    outline(bounds);
    flyPadded(bounds, CIRCUIT_ZOOM);
  } else {
    clearMark();
  }
  renderPanel();
  select(view.circuit >= 0 ? { kind: "circuit", key: String(row.index), label: circuitName(row) } : null);
}

function isPointSelection(s: Selection | null): boolean {
  return !!s && (s.kind === "point" || s.kind === "machine");
}

function selectionBounds(factory: string, circuitRow: CircuitRow | undefined): L.LatLngBounds | null {
  if (factory) return paddedBounds(factoryNamed(factory)!.bbox_m);
  return circuitRow ? paddedBounds(circuitRow.bbox_m) : null;
}

function followSelection(): void {
  const s = selected();
  if (!s) clearMark();
  const factory = s?.kind === "factory" && factoryNamed(s.key) ? s.key : "";
  const circuitRow = s?.kind === "circuit" && readings.circuits ? readings.circuits.circuits[+s.key] : undefined;
  const circuit = circuitRow ? circuitRow.index : -1;
  if (s && isPointSelection(s) && !isRinged(s.kind + ":" + s.key) && s.x_m !== undefined && s.y_m !== undefined) {
    ringAt(s.x_m, s.y_m, s.label, s.kind + ":" + s.key);
  }
  if (s && !factory && !circuitRow && !isPointSelection(s)) return;
  if (factory === view.factory && circuit === view.circuit) return;
  view.factory = factory;
  view.circuit = circuit;
  const bounds = selectionBounds(factory, circuitRow);
  if (bounds) outline(bounds);
  else if (!isPointSelection(s)) clearMark();
  renderPanel();
}

function stateChips(row: FactoryHealthRow): HTMLElement {
  const chips = make("div", "panel-chips");
  row.states.forEach(function (s) {
    const t = stateTone(s.state);
    if (t === "ok") return;
    chips.appendChild(chip(s.count + " " + s.state, t));
  });
  if (row.unwired) chips.appendChild(chip(row.unwired + " " + WORDS.noWire, "bad", WORDS.powerProblems));
  if (row.no_generator) chips.appendChild(chip(row.no_generator + " " + WORDS.noGenerator, "bad", WORDS.powerProblems));
  if (row.review) chips.appendChild(chip("label " + row.review, "muted"));
  return chips;
}

function issueRow(group: IssueGroup): HTMLElement {
  const line = make("li", "panel-issue");
  const text = make("span", "panel-issue-text");
  text.appendChild(make("span", "panel-issue-state " + stateTone(group.state, true), group.state));
  text.appendChild(make("span", "panel-issue-what", group.what));
  const detail = [counted(group.issues.length, "machine"), group.cause].filter(Boolean).join(" · ");
  text.appendChild(make("span", "panel-issue-cause", detail));
  line.appendChild(text);
  const go = showOnMapButton(group.issues[0]!, group.what);
  if (go) line.appendChild(go);
  return line;
}

function factoryRow(row: FactoryHealthRow): HTMLElement {
  const selected = row.name === view.factory;
  const item = make("li", "panel-row" + (selected ? " on" : "") + (row.bbox_m ? " go" : ""));
  item.setAttribute("data-factory", row.name);
  const head = make("div", "panel-row-head");
  const nameEl = nameButton(row.name, selected, function () {
    selectFactory(row, true);
  });
  head.appendChild(nameEl);
  const up = make("span", "panel-badge", pct(row.uptime));
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
    const tools = make("div", "panel-row-tools");
    tools.appendChild(
      button(
        "rename",
        function () {
          const health = readings.health;
          if (!health) return;
          editName(nameEl, row.name, health.labels_version, function (reply) {
            if (reply) view.factory = reply.name;
            if (reply || renderDeferred) renderPanel();
          });
        },
        { title: "rename this factory" }
      )
    );
    const traceAttrs: Record<string, string> = {};
    traceAttrs[TRACE_ATTR] = "label:" + row.name;
    traceAttrs[TRACE_DIR_ATTR] = "up";
    tools.appendChild(delegatedButton("trace supply", traceAttrs, { title: "draw what feeds this factory on the map" }));
    const amendAttrs: Record<string, string> = {};
    amendAttrs[LASSO_ATTR] = row.name;
    tools.appendChild(delegatedButton("amend", amendAttrs, { title: "draw around machines on the map to add them or remove them" }));
    item.appendChild(tools);
  }
  const groups = selected ? issueGroups([row]) : [];
  if (groups.length) {
    const list = make("ul", "panel-issues");
    groups.forEach(function (group) {
      list.appendChild(issueRow(group));
    });
    const rest = row.actionable - issueCount(groups);
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
  const rows = readings.health.factories;
  if (!rows.length) {
    empty(body, "no " + WORDS.factories + " named yet", link("factories", "find and name them on the Factories tab"));
    return;
  }
  const todo = rows.filter(function (r) {
    return r.actionable > 0;
  }).length;
  const line = make("p", "panel-note", counted(rows.length, WORDS.factory, WORDS.factories) + " · ");
  line.appendChild(make("span", actionTone(statesOf(rows)), count(todo)));
  line.appendChild(document.createTextNode(" " + WORDS.needAction));
  body.appendChild(line);
  const list = make("ul", "panel-list");
  rows.forEach(function (row) {
    list.appendChild(factoryRow(row));
  });
  body.appendChild(list);
}

function circuitRow(row: CircuitRow): HTMLElement {
  const selected = row.index === view.circuit;
  const led = row.ledger;
  const r = ratedCircuit(row);
  const item = make("li", "panel-row" + (selected ? " on" : "") + (row.bbox_m ? " go" : ""));
  const head = make("div", "panel-row-head");
  head.appendChild(
    nameButton(circuitName(row), selected, function () {
      selectCircuit(row);
    })
  );
  const now = r.dark ? readGeneration(r) : readHeadroomNow(r);
  const badge = make("span", "panel-badge" + badgeTone(now, led.starved_generation_mw > 0), now.value);
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
    if (row.generators.length) item.appendChild(make("div", "panel-row-sub", buildingCounts(row.generators)));
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
  const data = readings.circuits;
  if (!data) {
    loading(body, "power circuits");
    return;
  }
  body.appendChild(make("h3", "panel-h", "whole world"));
  body.appendChild(ledgerBlock(ratedWorld(data), data.world.starved_generation_mw));
  if (data.generators.length) {
    const kinds = data.generators.map(function (g) {
      return g.count + "× " + g.name + " " + mw(g.mw);
    });
    panelNote(body, kinds.join(" · "));
  }
  if (data.paused) panelNote(body, counted(data.paused, "paused building") + ", left out of both sides");
  if (data.unmodellable.length) {
    panelNote(body, "not in game data, left out: " + data.unmodellable.join(", "));
  }
  if (data.unwired_generators.length) body.appendChild(refList("generators on no wire", data.unwired_generators, "generation no circuit can use"));
  if (data.starved.length || data.unwired.length || data.no_generator.length) body.appendChild(make("h3", "panel-h", WORDS.powerProblems));
  if (data.starved.length) body.appendChild(refList(WORDS.starvedGenerator + "s", data.starved, STARVED_HINT));
  if (data.unwired.length) body.appendChild(refList(WORDS.noWire, data.unwired, "machines on no power line"));
  if (data.no_generator.length) body.appendChild(refList(WORDS.noGenerator, data.no_generator, "wired to a circuit no generator stands on"));
  body.appendChild(make("h3", "panel-h", counted(data.circuits.length, "circuit")));
  const list = make("ul", "panel-list");
  data.circuits.forEach(function (row) {
    list.appendChild(circuitRow(row));
  });
  body.appendChild(list);
}

export function renderPanel(): void {
  const panel = el("panel");
  if (renamingIn(panel)) {
    renderDeferred = true;
    return;
  }
  renderDeferred = false;
  panel.className = view.open ? "" : "shut";
  const tabs = panel.querySelectorAll<HTMLButtonElement>("[data-tab]");
  Array.prototype.forEach.call(tabs, function (tab: HTMLButtonElement) {
    const on = view.open && tab.getAttribute("data-tab") === view.tab;
    tab.className = "panel-tab";
    tab.setAttribute("aria-selected", String(on));
    tab.tabIndex = on || (!view.open && tab.getAttribute("data-tab") === view.tab) ? 0 : -1;
  });
  const fold = el("panel-fold");
  fold.setAttribute("aria-expanded", String(view.open));
  fold.setAttribute("aria-label", view.open ? "fold the panel away" : "open the panel");
  fold.title = fold.getAttribute("aria-label")!;
  const body = el("panel-body");
  const scroll = body.scrollTop;
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
  const row = factoryNamed(name);
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
  const row = readings.circuits ? readings.circuits.circuits[index] : undefined;
  if (!row) return;
  view.tab = "power";
  view.circuit = -1;
  setOpen(true);
  selectCircuit(row);
}

function scrollToFactory(): void {
  const body = el("panel-body");
  const rows = body.querySelectorAll<HTMLElement>("[data-factory]");
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
  const tabs: HTMLElement[] = Array.prototype.slice.call(el("panel").querySelectorAll("[data-tab]"));
  tabs.forEach(function (tab, i) {
    tab.onclick = function () {
      pickTab(tab.getAttribute("data-tab") as Tab);
    };
    tab.onkeydown = function (event) {
      if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
      const next = tabs[(i + (event.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length]!;
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
    const row = factoryNamed((event as CustomEvent<string>).detail);
    if (!row) return;
    view.tab = "factories";
    selectFactory(row, false);
    if (view.open) scrollToFactory();
  });
}

recall();
wire();
renderPanel();
onSelect(followSelection);

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
    const s = selected();
    if (s?.kind === "factory" && !factoryNamed(s.key)) select(null);
    followSelection();
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
    followSelection();
    changed();
  },
  failed: function () {
    readings.circuits = null;
    readings.circuitsError = "power circuits could not be read for this save";
    changed();
  },
});
