/* What a factory's ⋯ menu and buttons do: rename in place, change icon, amend on map, make a
 * plan, pin, trace supply, show on the map. Shared by the list's cards and the page header. */

import { get, send } from "../../api/client";
import { createPin } from "../../chat/pins";
import { go, replaceDash } from "../../app/nav";
import { state } from "../../app/state";
import { vitals } from "../../app/vitals";
import { html, mount, type Safe } from "../../kit/html";
import { modal, type Modal } from "../../kit/overlay/dialog";
import { btn, moreBtn } from "../../kit/ui/controls";
import { fail, friendlyError, notify } from "../../kit/toast";
import { showFactory } from "../../map/panel";
import { startLasso } from "../../map/tools/lasso";
import { startTrace } from "../../map/tools/trace";
import { leaveDashThen, requestRender } from "../actions";
import { factoryAddress, factoryDash } from "./address";
import { factoryIcons, type IconStore } from "./icon-choice";
import { editName } from "./rename";

import type { ItemsResponse, PlanFromFactoryResponse } from "../../api/shapes";

export const MENU_CARD = ["rename", "change icon", "make a plan", "pin"];
export const MENU_PAGE = ["rename", "change icon", "amend on map", "make a plan", "pin"];

export function factoryMore(name: string, items: string[]): Safe {
  return html`<span data-factory="${name}">${moreBtn(name, items)}</span>`;
}

export function mapBtn(name: string, located: boolean): Safe {
  if (!located) return html`<span class="btn-gap" aria-hidden="true"></span>`;
  return html`<span data-factory="${name}">${btn("◎", { extra: "icon", title: "show " + name + " on the map", data: { fact: "map" } })}</span>`;
}

function rename(name: string, button: HTMLElement): void {
  const health = vitals().health;
  const host = button.closest(".entry, .phead")?.querySelector<HTMLElement>(".title h1, .entry > .id");
  if (!health || !host) return;
  editName(host, name, health.labels_version, function (reply) {
    if (reply) {
      factoryIcons.move(reply.was, reply.name);
      const at = factoryAddress(state.dash.slice("factories/".length));
      if (state.dash.indexOf("factories/") === 0 && at.name === name) replaceDash(factoryDash(reply.name, at.aspect, at.floor));
    }
    requestRender();
  });
}

function makePlan(name: string): void {
  send<PlanFromFactoryResponse>("POST", "/api/factories/plan", { factory: name })
    .then(function (reply) {
      const differs = reply.differences.length;
      notify("“" + reply.name + "” made" + (differs ? "; " + differs + " differences from the build are in its notes" : ""));
      go("planner/" + reply.key);
    })
    .catch(function (failure) {
      fail("making a plan from “" + name + "”: " + friendlyError(failure));
    });
}

export function runFactoryAction(label: string, name: string, button: HTMLElement): void {
  if (label === "rename") rename(name, button);
  else if (label === "change icon") openIconPicker(name, button);
  else if (label === "amend on map") leaveDashThen(() => startLasso(name));
  else if (label === "make a plan") makePlan(name);
  else if (label === "pin") createPin("factory", { factory: name });
  else if (label === "trace supply") leaveDashThen(() => startTrace("label:" + name, "up"));
  else if (label === "map") leaveDashThen(() => showFactory(name));
}

/** The ◎ and "trace supply" buttons inside `root` (the ⋯ items go through wireMoreMenus). */
export function wireFactoryButtons(root: HTMLElement): void {
  root.addEventListener("click", function (event) {
    const b = (event.target as Element).closest<HTMLElement>("[data-fact]");
    const name = b?.closest<HTMLElement>("[data-factory]")?.dataset.factory;
    if (!b || name === undefined || !root.contains(b)) return;
    runFactoryAction(b.dataset.fact || "", name, b);
  });
}

/* ------------------------------------------------------------------ icon picker */

let picker: { dialog: HTMLDialogElement; box: Modal; name: string; store: IconStore; timer: number } | null = null;

function pickerDialog(): NonNullable<typeof picker> {
  if (picker) return picker;
  const dialog = document.createElement("dialog");
  dialog.className = "icdlg";
  dialog.setAttribute("aria-label", "change icon");
  document.body.appendChild(dialog);
  const box = modal(dialog, function () {
    box.close();
  });
  picker = { dialog, box, name: "", store: factoryIcons, timer: 0 };
  const p = picker;
  dialog.addEventListener("input", function (event) {
    if ((event.target as HTMLElement).id !== "icq") return;
    window.clearTimeout(p.timer);
    p.timer = window.setTimeout(() => fillGrid((event.target as HTMLInputElement).value), 150);
  });
  dialog.addEventListener("click", function (event) {
    if ((event.target as Element).closest("[data-close]")) {
      box.close();
      return;
    }
    const hit = (event.target as Element).closest<HTMLElement>("[data-icon]");
    if (!hit) return;
    const icon = hit.dataset.icon!;
    p.store.choose(p.name, icon === "*default" ? null : icon === "*none" ? "" : icon);
    box.close();
    requestRender();
  });
  dialog.addEventListener("keydown", function (event) {
    const at = event.target as HTMLElement;
    if (!at.closest(".icgrid") || ["ArrowRight", "ArrowLeft", "ArrowDown", "ArrowUp"].indexOf(event.key) < 0) return;
    event.preventDefault();
    const cells = Array.prototype.slice.call(dialog.querySelectorAll(".icgrid button")) as HTMLElement[];
    const across = Math.max(1, Math.round(dialog.querySelector<HTMLElement>(".icgrid")!.clientWidth / (cells[0]?.offsetWidth || 32)));
    const by = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : event.key === "ArrowDown" ? across : -across;
    cells[Math.max(0, Math.min(cells.length - 1, cells.indexOf(at) + by))]?.focus();
  });
  return p;
}

function fillGrid(query: string): void {
  const p = pickerDialog();
  const grid = p.dialog.querySelector<HTMLElement>(".icgrid");
  if (!grid) return;
  get<ItemsResponse>(("/api/gamedata/items?limit=200&q=" + encodeURIComponent(query.trim())) as `/api/gamedata/items?${string}`)
    .then(function (body) {
      mount(
        grid,
        html`${body.items.map((item) => html`<button type="button" data-icon="${item.cls}" title="${item.name}" aria-label="${item.name}"><img class="ic" src="/api/icons/${encodeURIComponent(item.cls)}" alt="" loading="lazy"></button>`)}`
      );
    })
    .catch(function (failure) {
      mount(grid, html`<p class="empty">${friendlyError(failure)}</p>`);
    });
}

/** Picks an icon for `name` in `store` (factories by default); the page redraws after. */
export function openIconPicker(name: string, opener: HTMLElement, store?: IconStore): void {
  const p = pickerDialog();
  p.name = name;
  p.store = store || factoryIcons;
  const chosen = p.store.chosen(name);
  mount(
    p.dialog,
    html`<div class="ichead"><b>icon for “${name}”</b><input type="search" id="icq" placeholder="search items" aria-label="search items"><button type="button" class="btn icon" data-close="1" aria-label="close">×</button></div>
      <div class="icgrid" role="group" aria-label="items"></div>
      <div class="icfoot">${btn("use default", { data: { icon: "*default" }, disabled: chosen === undefined })}${btn("no icon", { data: { icon: "*none" } })}<span class="note">kept in this browser</span></div>`
  );
  fillGrid("");
  p.box.open(opener);
  p.dialog.querySelector<HTMLInputElement>("#icq")!.focus();
}
