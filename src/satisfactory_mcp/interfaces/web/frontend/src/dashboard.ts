/* The dashboard shell: the tabs, the routing on the fragment's `dash=` key and the pieces the
 * tab modules share. See docs/frontend_vision.md §8. */

import { heading, link, note } from "./dashkit";
import { el, make } from "./dom";
import { renderInventory } from "./inventory";
import { hashFor, writeHash } from "./map";
import { onVitals, showFactory, showPoint, vitals } from "./panel";
import { renderPlanner } from "./planner";
import { onProgress, renderProgress } from "./progress";
import { renderRecipes } from "./recipes";
import { editName } from "./rename";
import { amount, choice, setSetting, setting, SETTINGS } from "./settings";
import { state } from "./state";
import { renderFactories, renderFactory, wireDetect } from "./factories";
import { renderOverview } from "./overview";
import { renderCircuit, renderPower } from "./power-tab";

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

var renaming = "";

function address(): { tab: Tab; subject: string } {
  var raw = state.dash;
  var cut = raw.indexOf("/");
  var head = cut < 0 ? raw : raw.slice(0, cut);
  var tab: Tab = "overview";
  TABS.forEach(function (t) {
    if (t[0] === head) tab = t[0];
  });
  return { tab: tab, subject: cut < 0 ? "" : raw.slice(cut + 1) };
}

export function go(dash: string): void {
  location.hash = hashFor(dash);
}

export function toMap(action: () => void): void {
  state.dash = "";
  history.pushState(null, "", hashFor(""));
  show();
  action();
  writeHash();
}

export function mapButton(title: string, action: () => void): HTMLButtonElement {
  var button = make("button", "dash-map", "map");
  button.type = "button";
  button.title = title;
  button.onclick = function (event) {
    event.stopPropagation();
    toMap(action);
  };
  return button;
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

export function table(headers: [string, string][], sortable: boolean, onSort?: () => void): HTMLTableElement {
  var t = make("table", "dash-table");
  var head = make("thead");
  var tr = make("tr");
  headers.forEach(function (h) {
    var th = make("th", h[1] === "name" ? "" : "num", h[0]);
    if (sortable && h[1]) {
      th.className += " sort" + (sort.key === h[1] ? (sort.desc ? " desc" : " asc") : "");
      th.setAttribute("aria-sort", sort.key === h[1] ? (sort.desc ? "descending" : "ascending") : "none");
      th.tabIndex = 0;
      var pick = function () {
        if (sort.key === h[1]) sort.desc = !sort.desc;
        else {
          sort.key = h[1];
          sort.desc = h[1] !== "name";
        }
        if (onSort) onSort();
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

export function factoryMapButton(row: FactoryHealthRow): HTMLElement {
  if (!row.bbox_m) return make("span", "dash-muted", "–");
  return mapButton("fly the map to this factory", function () {
    showFactory(row.name);
  });
}

export function actionButton(text: string, title: string, action: () => void, disabled?: boolean): HTMLButtonElement {
  var button = make("button", "dash-map", text);
  button.type = "button";
  button.title = title;
  button.disabled = !!disabled;
  button.onclick = function (event) {
    event.stopPropagation();
    action();
  };
  return button;
}

export function renameButton(name: string, host: HTMLElement, onRenamed: (to: string) => void): HTMLButtonElement {
  return actionButton("rename", "rename this factory", function () {
    var h = vitals().health;
    if (!h) return;
    renaming = name;
    editName(host, name, h.labels_version, function (reply) {
      renaming = "";
      if (reply) onRenamed(reply.name);
      else render();
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
  if (!state.dash || renaming) return;
  var at = address();
  renderNav(at.tab);
  var body = el("dash-body");
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

function show(): void {
  var on = !!state.dash;
  document.body.classList.toggle("dash-on", on);
  el("dash").hidden = !on;
  var views = el("views").querySelectorAll<HTMLAnchorElement>("[data-view]");
  Array.prototype.forEach.call(views, function (a: HTMLAnchorElement) {
    var mine = (a.getAttribute("data-view") === "dash") === on;
    a.className = "view-link" + (mine ? " on" : "");
  });
  render();
}

export function applyDash(raw: string): void {
  if (raw === state.dash) return;
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
        a.setAttribute("href", hashFor(state.dash || "overview"));
        return;
      }
      event.preventDefault();
      if (state.dash) toMap(function () {});
    };
  });
  onVitals(render);
  onProgress(render);
  wireDetect();
}

wire();
show();
