/* The dashboard shell: the tabs, the routing on the fragment's `dash=` key and the pieces the
 * tab modules share. See docs/frontend_vision.md §8. */

import { button, empty, heading, link, note } from "./dashkit";
import { el, make } from "./dom";
import { renderInventory } from "./inventory";
import { hashFor, writeHash } from "./map";
import { onVitals, showFactory, showPoint, vitals } from "./panel";
import { renderPlanner } from "./planner";
import { onProgress, renderProgress } from "./progress";
import { renderRecipes } from "./recipes";
import { cancelRename, editName, renamingIn } from "./rename";
import { amount, choice, setSetting, setting, SETTINGS } from "./settings";
import { state } from "./state";
import { renderFactories, renderFactory, wireDetect } from "./factories";
import { renderOverview } from "./overview";
import { renderCircuit, renderPower } from "./power-tab";
import { circuitName } from "./powerview";
import { dashParts } from "./nav";
import { W } from "./words";

import type { FactoryHealthRow } from "./api-shapes";

type Tab =
  | "overview"
  | "factories"
  | "power"
  | "progress"
  | "inventory"
  | "recipes"
  | "planner"
  | "settings";

var TABS: [Tab, string][] = [
  ["overview", "Overview"],
  ["factories", "Factories"],
  ["power", "Power"],
  ["progress", "Progress"],
  ["inventory", "Inventory"],
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

export { go } from "./nav";

export function toMap(action: () => void): void {
  state.dash = "";
  history.pushState(null, "", hashFor(""));
  show();
  action();
  writeHash();
}

export function mapButton(title: string, action: () => void): HTMLButtonElement {
  return button(
    "map",
    function () {
      toMap(action);
    },
    { title: title, map: true }
  );
}

interface Placed {
  x_m: number;
  y_m: number;
}

function located(row: { x_m: number | null; y_m: number | null }): row is Placed {
  return row.x_m !== null && row.y_m !== null;
}

export function pointButton(row: { x_m: number | null; y_m: number | null }): HTMLElement {
  if (!located(row)) return make("span", "dash-muted", "–");
  var at = row;
  return mapButton("fly the map to it", function () {
    showPoint(at.x_m, at.y_m);
  });
}

export function factoryMapButton(row: FactoryHealthRow): HTMLElement {
  if (!row.bbox_m) return make("span", "dash-muted", "–");
  return mapButton("fly the map to this factory", function () {
    showFactory(row.name);
  });
}

export function actionButton(text: string, title: string, action: () => void, disabled?: boolean): HTMLButtonElement {
  return button(text, action, { title: title, disabled: disabled });
}

export function renameButton(name: string, host: HTMLElement, onRenamed: (to: string) => void): HTMLButtonElement {
  return actionButton("rename", "rename this factory", function () {
    var h = vitals().health;
    if (!h) return;
    editName(host, name, h.labels_version, function (reply) {
      if (reply) onRenamed(reply.name);
      else if (missed) render();
    });
  });
}

function renderSettings(body: HTMLElement): void {
  var card = make("section", "dash-card");
  heading(card, "settings");
  note(card, "Kept in this browser only. Nothing is sent to the server.");
  SETTINGS.forEach(function (s) {
    var row = make("label", "dash-setting");
    if (s.kind === "switch") {
      var box = make("input");
      box.type = "checkbox";
      box.checked = setting(s.key);
      box.onchange = function () {
        setSetting(s.key, box.checked);
      };
      row.appendChild(box);
    } else if (s.kind === "choice") {
      var pick = make("select", "dash-select");
      s.options.forEach(function (o) {
        var option = make("option", "", o[1]);
        option.value = o[0];
        pick.appendChild(option);
      });
      pick.value = choice(s.key);
      pick.onchange = function () {
        setSetting(s.key, pick.value);
      };
      row.appendChild(pick);
    } else {
      var least = s.min;
      var num = make("input", "dash-number");
      num.type = "number";
      num.min = String(least);
      num.step = "1";
      num.value = String(amount(s.key));
      num.onchange = function () {
        var n = Math.floor(Number(num.value));
        if (n >= least) setSetting(s.key, n);
        else num.value = String(amount(s.key));
      };
      row.appendChild(num);
    }
    var words = make("span", "dash-setting-text");
    words.appendChild(make("span", "dash-setting-k", s.label));
    words.appendChild(make("span", "dash-setting-hint", s.hint));
    row.appendChild(words);
    card.appendChild(row);
  });
  body.appendChild(card);
}

function tabLabel(tab: Tab): string {
  var label = "";
  TABS.forEach(function (t) {
    if (t[0] === tab) label = t[1];
  });
  return label;
}

function subjectName(tab: Tab, subject: string): string {
  if (tab === "factories") return subject;
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

function forgetVitals(): void {
  if (state.epoch === shownEpoch) return;
  shownEpoch = state.epoch;
  var v = vitals();
  v.health = null;
  v.healthError = "";
  v.circuits = null;
  v.circuitsError = "";
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
  if (!state.dash) return;
  if (renamingIn(el("dash"))) {
    missed = true;
    return;
  }
  missed = false;
  var at = address();
  renderNav(at.tab);
  var body = el("dash-body");
  if (state.noSaves && SAVELESS.indexOf(at.tab) < 0) {
    body.textContent = "";
    empty(body, W.noSaves, "Save a game, or set SATISFACTORY_SAVES if the saves live elsewhere. Recipes and Settings still work.");
    return;
  }
  if (at.tab === "planner") {
    renderPlanner(body, at.subject);
    return;
  }
  var scroll = el("dash").scrollTop;
  var focused = document.activeElement;
  var typing = focused && body.contains(focused) ? focused.getAttribute("data-candidate") : null;
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
  else if (at.tab === "recipes") renderRecipes(body, at.subject, render);
  else renderSettings(body);
  el("dash").scrollTop = scroll;
  if (typing !== null) {
    var again = body.querySelector<HTMLInputElement>('[data-candidate="' + typing + '"]');
    if (again) again.focus();
  }
}

function relink(): void {
  var on = !!state.dash;
  if (on) lastDash = state.dash;
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
  wireDetect();
}

wire();
show();
