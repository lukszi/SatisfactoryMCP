/* The dashboard shell: routing on the fragment's `dash=` key (app/shell/router.ts) into the
 * pages, and the top bar that follows it. What the pages ask back goes through
 * dash/actions.ts. See docs/frontend_kit.md. */

import { onAdvice } from "../chat/advice";
import { empty } from "../kit/dashkit";
import { el, make } from "../kit/dom";
import { keepFocus } from "../kit/focus";
import { html, mount } from "../kit/html";
import { seg } from "../kit/ui/controls";
import { renderInventory } from "./inventory";
import { hashFor } from "../map/map";
import { onVitals, vitals } from "../app/vitals";
import { renderPlanner } from "./planner/planner";
import { viewFocus } from "./planner/focus";
import { bench, onBench } from "./planner/state";
import { planTitle } from "./planner/plan-index";
import { onProgress } from "./progress/feeds";
import { renderProgress } from "./progress/progress";
import { renderRecipes } from "./recipes/recipes";
import { cancelRename, renamingIn } from "./factories/rename";
import { state } from "../app/state";
import { renderFactories } from "./factories/list";
import { renderFactory } from "./factories/page";
import { wireDetect } from "./factories/clusters";
import { factoryAddress } from "./factories/address";
import { renderOverview } from "./overview";
import { renderCircuit, renderPower } from "./power/page";
import { circuitName } from "./power/readings";
import { renderMapsPage } from "./maps/maps-page";
import { renderKitGallery } from "./kit-gallery/gallery";
import { onMapRegistry } from "../app/map-types";
import { dashParts } from "../app/nav";
import { select } from "../app/selection";
import { redirectFor, routeOf, TOP_TABS, type Page, type Route } from "../app/shell/router";
import { drawTopBar } from "../app/shell/topbar";
import { showSettings } from "../app/settings-dialog/dialog";
import { renderWorld, worldTitle } from "./world/world";
import { WORDS } from "../kit/words";
import { pointButton, setDashHooks } from "./actions";

const SAVELESS: Page[] = ["recipes", "maps", "kit"];

const PAGE_TITLE: Partial<Record<Page, string>> = { inventory: "Items", recipes: "Items", maps: "map types", kit: "kit" };

/* A redraw skipped while a factory name was being edited. */
let missed = false;

let shownEpoch = 0;

function address(): Route {
  return routeOf(state.dash);
}

function pageLabel(at: Route): string {
  return (
    PAGE_TITLE[at.page] ||
    TOP_TABS.filter(function (t) {
      return t.id === at.top;
    })[0]?.label ||
    ""
  );
}

function knownFactory(name: string): boolean {
  const health = vitals().health;
  return !!health && health.factories.some(function (row) {
    return row.name === name;
  });
}

function subjectName(at: Route): string {
  if (at.page === "factories") return factoryAddress(at.subject, knownFactory).name;
  if (at.page === "world") return worldTitle(at.subject);
  if (at.page === "planner") {
    const key = dashParts("planner/" + at.subject).rest[0] || "";
    if (!key) return "";
    return planTitle(key) || (bench.key === key && bench.plan ? bench.plan.name : "");
  }
  if (at.page !== "power" || !at.subject) return "";
  const circuits = vitals().circuits;
  const row = circuits ? circuits.circuits[+at.subject - 1] : undefined;
  return row ? circuitName(row) : "circuit " + at.subject;
}

function retitle(): void {
  const parts = ["Satisfactory"];
  if (!state.dash) parts.unshift("Map");
  else {
    const at = address();
    parts.unshift(pageLabel(at));
    const name = subjectName(at);
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

function follow(at: Route): void {
  if (!at.subject) return;
  const v = vitals();
  if (at.page === "factories" && v.health) {
    const name = factoryAddress(at.subject, knownFactory).name;
    if (knownFactory(name)) select({ kind: "factory", key: name, label: name });
  } else if (at.page === "power" && v.circuits) {
    const row = v.circuits.circuits[+at.subject - 1];
    if (row) select({ kind: "circuit", key: String(row.index), label: circuitName(row) });
  }
}

/* Items is the inventory and recipes pages until it has its own; this keeps both a click away. */
function itemsSwitch(body: HTMLElement, page: Page): void {
  const holder = make("div", "items-switch");
  const items = [
    { value: "inventory", label: "held", href: hashFor("inventory") },
    { value: "recipes", label: "recipes", href: hashFor("recipes") },
  ];
  mount(holder, html`${seg("items", items, page, "items view")}`);
  body.appendChild(holder);
}

function renderPage(body: HTMLElement, at: Route): void {
  const page = at.page;
  if (page === "inventory" || page === "recipes") itemsSwitch(body, page);
  if (page === "overview") renderOverview(body);
  else if (page === "factories") {
    if (at.subject) renderFactory(body, at.subject);
    else renderFactories(body);
  } else if (page === "power") {
    if (at.subject) renderCircuit(body, at.subject);
    else renderPower(body);
  } else if (page === "progress") renderProgress(body, at.subject, pointButton);
  else if (page === "inventory") renderInventory(body);
  else if (page === "world") renderWorld(body);
  else if (page === "recipes") renderRecipes(body, at.subject, render);
  else if (page === "maps") renderMapsPage(body);
  else if (page === "kit") renderKitGallery(body);
}

function render(): void {
  forgetVitals();
  const at = address();
  drawTopBar(at.top);
  retitle();
  viewFocus();
  if (!state.dash) return;
  if (renamingIn(el("dash"))) {
    missed = true;
    return;
  }
  missed = false;
  follow(at);
  const body = el("dash-body");
  if (state.noSaves && SAVELESS.indexOf(at.page) < 0) {
    body.textContent = "";
    empty(body, WORDS.noSaves, "save a game, or set SATISFACTORY_SAVES if the saves live elsewhere; Items and settings still work");
    return;
  }
  if (at.page === "planner") {
    renderPlanner(body, at.subject);
    return;
  }
  const scroll = el("dash").scrollTop;
  keepFocus(body, function () {
    body.textContent = "";
    renderPage(body, at);
    if (!body.querySelector("h1")) body.insertBefore(make("h1", "dk-hidden", pageLabel(at)), body.firstChild);
  });
  el("dash").scrollTop = scroll;
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

/* An old address lands where it lives now; `#dash=settings` opens the dialog over the page
 * that was showing. */
function settle(raw: string): string {
  const to = redirectFor(raw);
  if (!to) return raw;
  if (to.settings !== undefined) setTimeout(showSettings, 0, to.settings);
  return to.dash === null ? state.dash : to.dash;
}

export function applyDash(raw: string): void {
  const next = settle(raw);
  if (next === state.dash) return;
  cancelRename();
  const before = address().page + "/" + address().subject;
  state.dash = next;
  if (address().page + "/" + address().subject !== before) el("dash").scrollTop = 0;
  show();
}

function wire(): void {
  new MutationObserver(mirrorBusy).observe(el("map"), { attributes: true, attributeFilter: ["class"] });
  onVitals(render);
  onProgress(render);
  onAdvice(function () {
    const page = address().page;
    if (state.dash && (page === "overview" || page === "factories")) render();
  });
  onMapRegistry(function (listed) {
    if (listed && state.dash && address().page === "maps") render();
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
if (redirectFor(state.dash)) {
  const boot = state.dash;
  state.dash = "overview";
  state.dash = settle(boot);
}
show();
