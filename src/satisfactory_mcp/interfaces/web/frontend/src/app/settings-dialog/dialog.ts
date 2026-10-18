/* The settings dialog (docs/frontend_kit.md, dialog): a modal over the current page whose
 * section rides in the address as `settings=<id>`, so Back closes it and a link opens it. */

import { send } from "../../api/client";
import { el } from "../../kit/dom";
import { html, mount, type Safe } from "../../kit/html";
import { modal } from "../../kit/overlay/dialog";
import { dashH, emptyLine } from "../../kit/ui/text";
import { fail, friendlyError } from "../../kit/toast";
import { hashFor, writeHash } from "../../map/map";
import { adoptMapRegistry, fetchMapRegistry, mapRegistry, onMapRegistry } from "../map-types";
import { isChanged, onSetting, resetSettings, setSetting, SETTINGS } from "../settings";
import { settingsSection } from "../shell/router";
import { closesBack, pushHere } from "../nav";
import { parseHash, state } from "../state";
import { BASE_MAP_ROW, controlId, settingRow, type Row } from "./rows";
import { inSection, searchSettings, SECTION_IDS, SECTIONS } from "./sections";

import type { MapsResponse } from "../../api/shapes";

const ROWS: Row[] = (SETTINGS as Row[]).concat([BASE_MAP_ROW]);

let section = SECTIONS[0]!.id;
let query = "";

const dialog = el<HTMLDialogElement>("settings");
const box = modal(dialog, closeSettings);

function changedIn(id: string): number {
  return inSection(SETTINGS, id).filter(isChanged).length;
}

function navMarkup(searching: boolean): Safe {
  return html`${SECTIONS.map(function (s) {
    const n = changedIn(s.id);
    const current = !searching && s.id === section;
    return html`<button type="button" data-sdsec="${s.id}"${current ? html` aria-current="true"` : ""}>${s.label}${n ? html` <span class="n" title="${n + " changed"}">· ${n}</span>` : ""}</button>`;
  })}`;
}

function paneMarkup(): Safe {
  if (!query.trim()) {
    const rows = inSection(ROWS, section);
    const link = section === "map" ? html`<p class="sd-more"><a href="${hashFor("maps")}">map studio →</a></p>` : "";
    return html`<h3 class="sd-h">${SECTIONS.filter((s) => s.id === section)[0]!.label}</h3>${rows.map((r) => settingRow(r, ""))}${link}`;
  }
  const hits = searchSettings(ROWS, query);
  if (!hits.length) return emptyLine("no setting matches “" + query.trim() + "”");
  return html`${hits.map(function (found) {
    return html`${dashH(found.section.label, found.rows.length, "h3")}${found.rows.map((r) => settingRow(r, query))}`;
  })}`;
}

function draw(focusId?: string): void {
  const searching = !!query.trim();
  mount(el("sd-nav"), navMarkup(searching));
  mount(
    el("sd-sel"),
    html`${SECTIONS.map((s) => html`<option value="${s.id}"${s.id === section ? html` selected` : ""}>${s.label}</option>`)}`
  );
  mount(el("sd-pane"), paneMarkup());
  el<HTMLButtonElement>("sd-reset-all").disabled = !SETTINGS.some(isChanged);
  el("sd-status").textContent = searching ? searchSettings(ROWS, query).reduce((n, f) => n + f.rows.length, 0) + " settings match" : "";
  if (focusId) document.getElementById(focusId)?.focus();
}

function show(id: string): void {
  if (id) section = id;
  query = "";
  el<HTMLInputElement>("sd-q").value = "";
  if (!mapRegistry.body && !mapRegistry.failed) fetchMapRegistry();
  draw();
  box.open(el("gear"));
  el("gear").classList.add("on");
  el<HTMLInputElement>("sd-q").focus();
}

/** Opens from the gear: a history entry of its own, so Back closes it. */
export function openSettings(id: string): void {
  state.settings = id;
  pushHere();
  show(id);
}

/** Opens over the page in place, for an old `#settings` address. */
export function showSettings(id: string): void {
  state.settings = id;
  writeHash();
  show(id);
}

export function closeSettings(): void {
  if (state.settings === null && !box.isOpen()) return;
  state.settings = null;
  el("gear").classList.remove("on");
  box.close();
  if (closesBack()) history.back();
  else writeHash();
}

function pickSection(id: string, focusSelect: boolean): void {
  section = id;
  query = "";
  el<HTMLInputElement>("sd-q").value = "";
  state.settings = id;
  writeHash();
  draw(focusSelect ? "sd-sel" : undefined);
  if (!focusSelect) document.querySelector<HTMLElement>('[data-sdsec="' + id + '"]')?.focus();
}

function setDefaultMap(id: string): void {
  const body = mapRegistry.body;
  send<MapsResponse>("PUT", "/api/maps/default", { id: id, version: body ? body.version : null })
    .then(adoptMapRegistry)
    .catch(function (reason) {
      fetchMapRegistry();
      fail("the default map was not changed: " + friendlyError(reason));
    });
}

function commit(target: HTMLInputElement | HTMLSelectElement): void {
  const key = target.id.slice(4);
  if (key === BASE_MAP_ROW.key) return setDefaultMap(target.value);
  const setting = SETTINGS.filter((s) => s.key === key)[0];
  if (!setting) return;
  if (setting.kind === "switch") setSetting(key, (target as HTMLInputElement).checked);
  else if (setting.kind === "choice") setSetting(key, target.value);
  else {
    const value = Number(target.value);
    if (Number.isInteger(value) && value >= setting.min && value <= setting.max) setSetting(key, value);
    else target.setAttribute("aria-invalid", "true");
  }
}

function onClick(event: MouseEvent): void {
  const target = event.target as Element;
  if (target.closest("#sd-x")) return closeSettings();
  const sec = target.closest<HTMLElement>("[data-sdsec]");
  if (sec) return pickSection(sec.dataset.sdsec!, false);
  const reset = target.closest<HTMLElement>("[data-reset]");
  if (reset) {
    const setting = SETTINGS.filter((s) => s.key === reset.dataset.reset)[0];
    if (setting) setSetting(setting.key, setting.fallback);
    return draw(controlId(reset.dataset.reset!));
  }
  if (target.closest("#sd-reset-all")) {
    resetSettings();
    draw("sd-q");
    el("sd-status").textContent = "every setting is back to its default";
  }
}

function wire(): void {
  dialog.addEventListener("click", onClick);
  dialog.addEventListener("change", function (event) {
    const target = event.target as HTMLInputElement | HTMLSelectElement;
    if (target.id === "sd-sel") pickSection(target.value, true);
    else if (target.id.indexOf("set-") === 0) commit(target);
  });
  el("sd-q").addEventListener("input", function () {
    query = el<HTMLInputElement>("sd-q").value;
    draw();
  });
  dialog.addEventListener("keydown", function (event) {
    const typing = (event.target as Element).matches("input, select, textarea");
    if (event.key === "/" && !typing) {
      event.preventDefault();
      el("sd-q").focus();
    }
  });
  window.addEventListener("hashchange", syncSettingsFromAddress);
  onSetting(function () {
    if (box.isOpen()) draw((document.activeElement as HTMLElement | null)?.id);
  });
  onMapRegistry(function (listed) {
    if (listed && box.isOpen()) draw((document.activeElement as HTMLElement | null)?.id);
  });
}

/** Opens, switches or closes the dialog to match the address bar. */
export function syncSettingsFromAddress(): void {
  const asked = settingsSection(parseHash(location.hash).settings, SECTION_IDS);
  if (asked === null) {
    if (!box.isOpen()) return;
    state.settings = null;
    el("gear").classList.remove("on");
    box.close();
    return;
  }
  state.settings = asked;
  if (!box.isOpen()) show(asked);
  else if (asked && asked !== section) pickSection(asked, false);
}

wire();
