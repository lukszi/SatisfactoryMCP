/* The dashboard shell: the tabs, the routing on the fragment's `dash=` key and the pieces the
 * tab modules share. See docs/frontend_vision.md §8. */

import { onAdvice } from "../chat/advice";
import { button, choice as choiceBox, empty, fieldError, link, note, tabs2 } from "../kit/dashkit";
import { el, make } from "../kit/dom";
import { keepFocus } from "../kit/focus";
import { renderInventory } from "./inventory";
import { hashFor, writeHash } from "../map/map";
import { located, showFactory } from "../map/panel";
import { showMachine, showPoint } from "../map/map-highlight";
import { onVitals, vitals } from "../app/vitals";
import { renderPlanner, viewFocus } from "./planner/planner";
import { bench, onBench } from "./planner/planner-core";
import { planTitle } from "./planner/planner-list";
import { onProgress, renderProgress } from "./progress/progress";
import { renderRecipes } from "./recipes/recipes";
import { cancelRename, editName, renamingIn } from "./factories/rename";
import { amount, choice, onSetting, resetSettings, setSetting, setting, SETTINGS } from "../app/settings";
import type { Setting } from "../app/settings";
import { state } from "../app/state";
import { renderFactories, renderFactory, wireDetect } from "./factories/factories";
import { factoryAddress } from "./factories/factory-detail";
import { renderOverview } from "./overview";
import { renderCircuit, renderPower } from "./power-tab";
import { circuitName } from "./power-ledger";
import { mapPickerRow, renderMaps } from "./maps/settings-maps";
import { onMaps } from "../app/map-types";
import { dashParts, go } from "../app/nav";
import { drawRail } from "../app/rail";
import { select } from "../app/selection";
import { renderWorld, worldTitle } from "./world/world";
import { W } from "../kit/words";

import type { FactoryHealthRow } from "../api/shapes";

type Tab =
  | "overview"
  | "factories"
  | "power"
  | "progress"
  | "inventory"
  | "world"
  | "recipes"
  | "planner"
  | "settings";

export var TABS: [Tab, string][] = [
  ["overview", "Overview"],
  ["factories", "Factories"],
  ["power", "Power"],
  ["progress", "Progress"],
  ["inventory", "Inventory"],
  ["world", "World"],
  ["recipes", "Recipes"],
  ["planner", "Planner"],
  ["settings", "Settings"],
];

export var sort = { key: "actionable", desc: true };

var missed = false;

var SAVELESS: Tab[] = ["recipes", "settings"];

var lastDash = "overview";

var shownEpoch = 0;

function address(): { tab: Tab; subject: string } {
  var parts = dashParts();
  var tab: Tab = "overview";
  TABS.forEach(function (t) {
    if (t[0] === parts.tab) tab = t[0];
  });
  return { tab: tab, subject: parts.subject };
}

export { go } from "../app/nav";

export function toMap(action: () => void): void {
  var from = document.activeElement;
  var keyed = !!from && el("dash").contains(from);
  state.dash = "";
  history.pushState(null, "", hashFor(""));
  show();
  action();
  writeHash();
  var now = document.activeElement;
  if (keyed && (!now || now === document.body || el("dash").contains(now))) el("map").focus({ preventScroll: true });
}

export function mapButton(title: string, action: () => void, label?: string): HTMLButtonElement {
  return button(
    "map",
    function () {
      toMap(action);
    },
    { title: title, map: true, label: label }
  );
}

export function pointButton(
  row: {
    x_m: number | null;
    y_m: number | null;
    instance?: string;
    name?: string | null;
    what?: string;
  },
  label?: string
): HTMLElement {
  var instance = row.instance;
  var shown = { label: row.name || row.what, layers: instance ? ["machines"] : undefined };
  if (!located(row)) return make("span", "dash-muted", "–");
  var at = row;
  return mapButton(
    "fly the map to it",
    function () {
      if (instance) showMachine(instance, shown.label || "a machine", at.x_m, at.y_m, shown);
      else showPoint(at.x_m, at.y_m, shown);
    },
    label || (shown.label ? "show " + shown.label + " on the map" : undefined)
  );
}

export function factoryMapButton(row: FactoryHealthRow): HTMLElement {
  if (!row.bbox_m) return make("span", "dash-muted", "–");
  return mapButton(
    "fly the map to this factory",
    function () {
      showFactory(row.name);
    },
    "show " + row.name + " on the map"
  );
}

export function renameButton(name: string, host: HTMLElement, onRenamed: (to: string) => void): HTMLButtonElement {
  return button(
    "rename",
    function () {
      var h = vitals().health;
      if (!h) return;
      editName(host, name, h.labels_version, function (reply) {
        if (reply) onRenamed(reply.name);
        else if (missed) render();
      });
    },
    { title: "rename this factory", label: "rename " + name }
  );
}

function settingRow(s: Setting): HTMLElement {
  var row = make("label", "dash-setting");
  var words = make("span", "dash-setting-text");
  words.appendChild(make("span", "dash-setting-k", s.label));
  words.appendChild(make("span", "dash-setting-hint", s.hint));
  row.appendChild(words);
  if (s.kind === "switch") {
    var box = make("input");
    box.type = "checkbox";
    box.checked = setting(s.key);
    box.onchange = function () {
      setSetting(s.key, box.checked);
    };
    row.appendChild(box);
  } else if (s.kind === "choice") {
    row.appendChild(
      choiceBox(
        s.options,
        choice(s.key),
        function (value) {
          setSetting(s.key, value);
        },
        { label: s.label }
      )
    );
  } else {
    var least = s.min;
    var most = s.max;
    var num = make("input", "dash-number");
    num.type = "number";
    num.min = String(least);
    num.max = String(most);
    num.step = "1";
    num.value = String(amount(s.key));
    num.onchange = function () {
      var n = Number(num.value);
      if (Number.isInteger(n) && n >= least && n <= most) {
        fieldError(num, "");
        setSetting(s.key, n);
      } else fieldError(num, "a whole number from " + least + " to " + most);
    };
    var cell = make("span", "dash-setting-ctl");
    cell.appendChild(num);
    row.appendChild(cell);
  }
  return row;
}

var SETTINGS_SUBS: [string, string][] = [
  ["", "general"],
  ["maps", "maps"],
];

function renderSettings(body: HTMLElement, subject: string): void {
  var sub = subject === "maps" ? "maps" : "";
  body.appendChild(
    tabs2(
      SETTINGS_SUBS.map(function (s) {
        return { id: s[0], label: s[1], href: hashFor(s[0] ? "settings/" + s[0] : "settings") };
      }),
      sub,
      function (id) {
        go(id ? "settings/" + id : "settings");
      },
      "Settings sections"
    )
  );
  if (sub === "maps") {
    renderMaps(body, { toMap: toMap, render: render });
    return;
  }
  var card = make("section", "dash-card");
  var bar = make("div", "dash-title");
  bar.appendChild(make("h2", "dash-h", "settings"));
  bar.appendChild(button("reset to defaults", function () {
    resetSettings();
    render();
  }, { title: "put every setting back to its default" }));
  card.appendChild(bar);
  note(card, "kept in this browser, except those chat uses too: those every tab and chat share");
  var group = "";
  SETTINGS.forEach(function (s) {
    if (s.group !== group) {
      group = s.group;
      card.appendChild(make("h3", "dash-setting-group", group));
    }
    card.appendChild(settingRow(s));
  });
  // The last SETTINGS group is "map"; the default picker joins it.
  card.appendChild(mapPickerRow());
  body.appendChild(card);
}

function tabLabel(tab: Tab): string {
  var label = "";
  TABS.forEach(function (t) {
    if (t[0] === tab) label = t[1];
  });
  return label;
}

function knownFactory(name: string): boolean {
  var health = vitals().health;
  return !!health && health.factories.some(function (r) {
    return r.name === name;
  });
}

function subjectName(tab: Tab, subject: string): string {
  if (tab === "factories") return factoryAddress(subject, knownFactory).name;
  if (tab === "world") return worldTitle(subject);
  if (tab === "planner") {
    var key = dashParts("planner/" + subject).rest[0] || "";
    return key ? planTitle(key) || (bench.key === key && bench.plan ? bench.plan.name : "") : "";
  }
  if (tab !== "power" || !subject) return "";
  var circuits = vitals().circuits;
  var row = circuits ? circuits.circuits[+subject - 1] : undefined;
  return row ? circuitName(row) : "circuit " + subject;
}

function retitle(): void {
  var parts = ["Satisfactory"];
  if (!state.dash) parts.unshift("Map");
  else {
    var at = address();
    parts.unshift(tabLabel(at.tab));
    var name = subjectName(at.tab, at.subject);
    if (name) parts.unshift(name);
  }
  document.title = parts.join(" · ");
}

onBench(function () {
  if (state.dash.indexOf("planner/") === 0) retitle();
});

function forgetVitals(): void {
  if (state.epoch === shownEpoch) return;
  shownEpoch = state.epoch;
  var v = vitals();
  v.health = null;
  v.healthError = "";
  v.circuits = null;
  v.circuitsError = "";
}

function follow(tab: Tab, subject: string): void {
  if (!subject) return;
  var v = vitals();
  if (tab === "factories" && v.health) {
    var name = factoryAddress(subject, knownFactory).name;
    if (knownFactory(name)) select({ kind: "factory", key: name, label: name });
  } else if (tab === "power" && v.circuits) {
    var row = v.circuits.circuits[+subject - 1];
    if (row) select({ kind: "circuit", key: String(row.index), label: circuitName(row) });
  }
}

function renderNav(tab: Tab): void {
  var nav = el("dash-nav");
  nav.textContent = "";
  TABS.forEach(function (t) {
    var a = link(t[0], t[1], "dash-tab" + (t[0] === tab ? " on" : ""));
    if (t[0] === tab) a.setAttribute("aria-current", "page");
    nav.appendChild(a);
  });
}

export function render(): void {
  forgetVitals();
  relink();
  retitle();
  viewFocus();
  if (!state.dash) return;
  if (renamingIn(el("dash"))) {
    missed = true;
    return;
  }
  missed = false;
  var at = address();
  follow(at.tab, at.subject);
  renderNav(at.tab);
  var body = el("dash-body");
  if (state.noSaves && SAVELESS.indexOf(at.tab) < 0) {
    body.textContent = "";
    empty(body, W.noSaves, "save a game, or set SATISFACTORY_SAVES if the saves live elsewhere; Recipes and Settings still work");
    return;
  }
  if (at.tab === "planner") {
    renderPlanner(body, at.subject);
    return;
  }
  var scroll = el("dash").scrollTop;
  keepFocus(body, function () {
    body.textContent = "";
    if (at.tab === "overview") renderOverview(body);
    else if (at.tab === "factories") {
      if (at.subject) renderFactory(body, at.subject);
      else renderFactories(body);
    } else if (at.tab === "power") {
      if (at.subject) renderCircuit(body, at.subject);
      else renderPower(body);
    } else if (at.tab === "progress") renderProgress(body, at.subject, pointButton);
    else if (at.tab === "inventory") renderInventory(body, { toMap: toMap, render: render });
    else if (at.tab === "world") renderWorld(body);
    else if (at.tab === "recipes") renderRecipes(body, at.subject, render);
    else renderSettings(body, at.subject);
    if (!body.querySelector("h1")) body.insertBefore(make("h1", "dk-hidden", tabLabel(at.tab)), body.firstChild);
  });
  el("dash").scrollTop = scroll;
}

function relink(): void {
  var on = !!state.dash;
  if (on) lastDash = state.dash;
  drawRail(TABS, on ? address().tab : "");
  var views = el("views").querySelectorAll<HTMLAnchorElement>("[data-view]");
  Array.prototype.forEach.call(views, function (a: HTMLAnchorElement) {
    var dash = a.getAttribute("data-view") === "dash";
    var mine = dash === on;
    a.className = "view-link" + (mine ? " on" : "");
    a.setAttribute("href", hashFor(dash ? lastDash : ""));
    if (mine) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
}

function show(): void {
  var on = !!state.dash;
  document.body.classList.toggle("dash-on", on);
  el("dash").hidden = !on;
  render();
}

function mirrorBusy(): void {
  var on = el("map").classList.contains("busy");
  var dash = el("dash");
  if (dash.classList.contains("busy") === on) return;
  dash.classList.toggle("busy", on);
  if (on) dash.setAttribute("aria-busy", "true");
  else dash.removeAttribute("aria-busy");
  render();
}

export function applyDash(raw: string): void {
  if (raw === state.dash) return;
  cancelRename();
  var before = address().tab + "/" + address().subject;
  state.dash = raw;
  if (address().tab + "/" + address().subject !== before) el("dash").scrollTop = 0;
  show();
}

function wire(): void {
  var views = el("views").querySelectorAll<HTMLAnchorElement>("[data-view]");
  Array.prototype.forEach.call(views, function (a: HTMLAnchorElement) {
    var dash = a.getAttribute("data-view") === "dash";
    a.onclick = function (event) {
      if (dash) {
        a.setAttribute("href", hashFor(state.dash || lastDash));
        return;
      }
      event.preventDefault();
      if (state.dash) toMap(function () {});
    };
  });
  new MutationObserver(mirrorBusy).observe(el("map"), { attributes: true, attributeFilter: ["class"] });
  onVitals(render);
  onProgress(render);
  onAdvice(function () {
    var tab = address().tab;
    if (state.dash && (tab === "overview" || tab === "factories")) render();
  });
  onSetting(function () {
    if (state.dash && address().tab === "settings") render();
  });
  onMaps(function (listed) {
    if (listed && state.dash && address().tab === "settings") render();
  });
  wireDetect();
}

wire();
show();
