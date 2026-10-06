/* The dashboard shell: the tabs and the routing on the fragment's `dash=` key. What the tabs
 * ask back of it goes through dash/actions.ts. See docs/frontend_vision.md §8. */

import { onAdvice } from "../chat/advice";
import { empty, link } from "../kit/dashkit";
import { el, make } from "../kit/dom";
import { keepFocus } from "../kit/focus";
import { renderInventory } from "./inventory";
import { hashFor } from "../map/map";
import { onVitals, vitals } from "../app/vitals";
import { renderPlanner } from "./planner/planner";
import { viewFocus } from "./planner/planner-focus";
import { bench, onBench } from "./planner/planner-state";
import { planTitle } from "./planner/planner-plan-index";
import { onProgress } from "./progress/feeds";
import { renderProgress } from "./progress/progress";
import { renderRecipes } from "./recipes/recipes";
import { cancelRename, renamingIn } from "./factories/rename";
import { onSetting } from "../app/settings";
import { state } from "../app/state";
import { renderFactories } from "./factories/list";
import { renderFactory } from "./factories/page";
import { wireDetect } from "./factories/detect";
import { factoryAddress } from "./factories/address";
import { renderOverview } from "./overview";
import { renderCircuit, renderPower } from "./power-tab";
import { circuitName } from "./power-ledger";
import { renderSettingsTab } from "./settings-tab";
import { onMapRegistry } from "../app/map-types";
import { dashParts } from "../app/nav";
import { drawRail } from "../app/rail";
import { select } from "../app/selection";
import { renderWorld, worldTitle } from "./world/world";
import { WORDS } from "../kit/words";
import { leaveDashThen, pointButton, setDashHooks } from "./actions";

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

const TABS: [Tab, string][] = [
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

const SAVELESS: Tab[] = ["recipes", "settings"];

/* A redraw skipped while a factory name was being edited. */
let missed = false;

let lastDash = "overview";

let shownEpoch = 0;

function address(): { tab: Tab; subject: string } {
  const parts = dashParts();
  let tab: Tab = "overview";
  TABS.forEach(function (entry) {
    if (entry[0] === parts.tab) tab = entry[0];
  });
  return { tab: tab, subject: parts.subject };
}

function tabLabel(tab: Tab): string {
  let label = "";
  TABS.forEach(function (entry) {
    if (entry[0] === tab) label = entry[1];
  });
  return label;
}

function knownFactory(name: string): boolean {
  const health = vitals().health;
  return !!health && health.factories.some(function (row) {
    return row.name === name;
  });
}

function subjectName(tab: Tab, subject: string): string {
  if (tab === "factories") return factoryAddress(subject, knownFactory).name;
  if (tab === "world") return worldTitle(subject);
  if (tab === "planner") {
    const key = dashParts("planner/" + subject).rest[0] || "";
    return key ? planTitle(key) || (bench.key === key && bench.plan ? bench.plan.name : "") : "";
  }
  if (tab !== "power" || !subject) return "";
  const circuits = vitals().circuits;
  const row = circuits ? circuits.circuits[+subject - 1] : undefined;
  return row ? circuitName(row) : "circuit " + subject;
}

function retitle(): void {
  const parts = ["Satisfactory"];
  if (!state.dash) parts.unshift("Map");
  else {
    const at = address();
    parts.unshift(tabLabel(at.tab));
    const name = subjectName(at.tab, at.subject);
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
  const v = vitals();
  v.health = null;
  v.healthError = "";
  v.circuits = null;
  v.circuitsError = "";
}

function follow(tab: Tab, subject: string): void {
  if (!subject) return;
  const v = vitals();
  if (tab === "factories" && v.health) {
    const name = factoryAddress(subject, knownFactory).name;
    if (knownFactory(name)) select({ kind: "factory", key: name, label: name });
  } else if (tab === "power" && v.circuits) {
    const row = v.circuits.circuits[+subject - 1];
    if (row) select({ kind: "circuit", key: String(row.index), label: circuitName(row) });
  }
}

function renderNav(tab: Tab): void {
  const nav = el("dash-nav");
  nav.textContent = "";
  TABS.forEach(function (entry) {
    const anchor = link(entry[0], entry[1], "dash-tab" + (entry[0] === tab ? " on" : ""));
    if (entry[0] === tab) anchor.setAttribute("aria-current", "page");
    nav.appendChild(anchor);
  });
}

function renderTab(body: HTMLElement, tab: Tab, subject: string): void {
  if (tab === "overview") renderOverview(body);
  else if (tab === "factories") {
    if (subject) renderFactory(body, subject);
    else renderFactories(body);
  } else if (tab === "power") {
    if (subject) renderCircuit(body, subject);
    else renderPower(body);
  } else if (tab === "progress") renderProgress(body, subject, pointButton);
  else if (tab === "inventory") renderInventory(body);
  else if (tab === "world") renderWorld(body);
  else if (tab === "recipes") renderRecipes(body, subject, render);
  else renderSettingsTab(body, subject);
}

function render(): void {
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
  const at = address();
  follow(at.tab, at.subject);
  renderNav(at.tab);
  const body = el("dash-body");
  if (state.noSaves && SAVELESS.indexOf(at.tab) < 0) {
    body.textContent = "";
    empty(body, WORDS.noSaves, "save a game, or set SATISFACTORY_SAVES if the saves live elsewhere; Recipes and Settings still work");
    return;
  }
  if (at.tab === "planner") {
    renderPlanner(body, at.subject);
    return;
  }
  const scroll = el("dash").scrollTop;
  keepFocus(body, function () {
    body.textContent = "";
    renderTab(body, at.tab, at.subject);
    if (!body.querySelector("h1")) body.insertBefore(make("h1", "dk-hidden", tabLabel(at.tab)), body.firstChild);
  });
  el("dash").scrollTop = scroll;
}

function relink(): void {
  const on = !!state.dash;
  if (on) lastDash = state.dash;
  drawRail(TABS, on ? address().tab : "");
  const views = el("views").querySelectorAll<HTMLAnchorElement>("[data-view]");
  Array.prototype.forEach.call(views, function (anchor: HTMLAnchorElement) {
    const dash = anchor.getAttribute("data-view") === "dash";
    const mine = dash === on;
    anchor.className = "view-link" + (mine ? " on" : "");
    anchor.setAttribute("href", hashFor(dash ? lastDash : ""));
    if (mine) anchor.setAttribute("aria-current", "page");
    else anchor.removeAttribute("aria-current");
  });
}

function show(): void {
  const on = !!state.dash;
  document.body.classList.toggle("dash-on", on);
  el("dash").hidden = !on;
  render();
}

function mirrorBusy(): void {
  const on = el("map").classList.contains("busy");
  const dash = el("dash");
  if (dash.classList.contains("busy") === on) return;
  dash.classList.toggle("busy", on);
  if (on) dash.setAttribute("aria-busy", "true");
  else dash.removeAttribute("aria-busy");
  render();
}

export function applyDash(raw: string): void {
  if (raw === state.dash) return;
  cancelRename();
  const before = address().tab + "/" + address().subject;
  state.dash = raw;
  if (address().tab + "/" + address().subject !== before) el("dash").scrollTop = 0;
  show();
}

function wire(): void {
  const views = el("views").querySelectorAll<HTMLAnchorElement>("[data-view]");
  Array.prototype.forEach.call(views, function (anchor: HTMLAnchorElement) {
    const dash = anchor.getAttribute("data-view") === "dash";
    anchor.onclick = function (event) {
      if (dash) {
        anchor.setAttribute("href", hashFor(state.dash || lastDash));
        return;
      }
      event.preventDefault();
      if (state.dash) leaveDashThen(function () {});
    };
  });
  new MutationObserver(mirrorBusy).observe(el("map"), { attributes: true, attributeFilter: ["class"] });
  onVitals(render);
  onProgress(render);
  onAdvice(function () {
    const tab = address().tab;
    if (state.dash && (tab === "overview" || tab === "factories")) render();
  });
  onSetting(function () {
    if (state.dash && address().tab === "settings") render();
  });
  onMapRegistry(function (listed) {
    if (listed && state.dash && address().tab === "settings") render();
  });
  wireDetect();
}

setDashHooks({
  render: render,
  show: show,
  renderIfDeferred: function () {
    if (missed) render();
  },
});
wire();
show();
