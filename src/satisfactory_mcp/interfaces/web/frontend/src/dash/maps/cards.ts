/* The map types as a contact sheet: one card per type, led by its own z1 tiles, then its title,
 * chips, size and the action slots. A type without tiles shows a plain well, never stand-in art. */

import { html, type Safe } from "../../kit/html";
import { btn, busyBtn, glyphBtn, moreBtn } from "../../kit/ui/controls";
import { actSlots, entry, list, metric } from "../../kit/ui/entry";
import { chip, jobBar } from "../../kit/ui/marks";
import { dashH, emptyLine, toolrow } from "../../kit/ui/text";
import { bytes, dayMonth, hoursMinutes } from "../../kit/format";
import { mapRegistry, staleLabel, staleReasons } from "../../app/map-types";
import { requestBaseMode } from "../../map/tiles";
import { leaveDashThen } from "../actions";
import { mapsDash, type MapsAddress } from "./address";
import { formMarkup } from "./form";
import { typeTitle } from "./words";
import { closeConfirm, confirmingOn, openConfirm, redraw, refused, setDefault, submit, submitRender, typeRow, write } from "./writes";

import type { MapJobBody, MapsResponse, MapTypeBody } from "../../api/shapes";

/* The id whose label is being edited, and what was typed so far; "" when none is. */
let renaming = "";
let renameText = "";

function jobOf(row: MapTypeBody, body: MapsResponse): MapJobBody | undefined {
  return body.jobs.filter((job) => (job.status === "running" || job.status === "queued") && job.produces.indexOf(row.id) >= 0)[0];
}

function tileSrc(row: MapTypeBody, z: number, x: number, y: number): string {
  return "/api/maptiles/" + encodeURIComponent(row.id) + "/" + z + "/" + x + "/" + y;
}

/** The plain well of a type with no tiles: how far its render is, or why there is nothing. */
export function wellInner(row: MapTypeBody, job: MapJobBody | undefined): Safe {
  if (job && job.status === "running") {
    const pct = job.pct === null ? "…" : Math.floor(job.pct * 100) + "%";
    const left = job.eta_s === null ? "" : " · " + hoursMinutes(job.eta_s) + " left";
    return html`<span class="fig"><b>${pct}</b><span class="k">rendered</span></span>${jobBar(job.pct === null ? null : job.pct * 100, "rendering " + typeTitle(row))}<span class="jobmeta">${job.stage_words || "running"}${left}</span>`;
  }
  if (job) return html`<span class="jobmeta">queued</span>`;
  return html`<span class="jobmeta">${row.status === "failed" ? "the render failed" : "no tiles on disk"}</span>`;
}

function picture(row: MapTypeBody, body: MapsResponse): Safe {
  if (row.status !== "ready") return html`<div class="mpic none" data-well="${row.id}">${wellInner(row, jobOf(row, body))}</div>`;
  const tip = "open the map on " + typeTitle(row);
  const z1 = row.max_z === null || row.max_z >= 1;
  const tiles = z1 ? [[0, 0], [1, 0], [0, 1], [1, 1]].map((c) => html`<img src="${tileSrc(row, 1, c[0]!, c[1]!)}" alt="" loading="lazy">`) : html`<img class="whole" src="${tileSrc(row, 0, 0, 0)}" alt="" loading="lazy">`;
  return html`<button class="mpic" type="button" data-act="open" data-id="${row.id}" aria-label="${tip}" title="${tip}">${tiles}</button>`;
}

function chipsOf(row: MapTypeBody, body: MapsResponse): Safe {
  const stale = staleLabel(row);
  const building = row.status === "building" || !!jobOf(row, body);
  return html`${row.default ? chip("default", "ok") : ""}${building ? chip("building", "mid") : ""}${row.status === "failed" ? chip("failed", "bad") : ""}${stale ? chip(stale, "mid", staleReasons(row)) : ""}${row.freshness.restyle ? chip("newer palette", "muted", "the style's palette moved on; a re-render draws it") : ""}${row.freshness.rerender ? chip("re-render available", "muted", row.freshness.rerender.text) : ""}${row.in_switcher ? "" : chip("not in switcher", "muted")}`;
}

function sizeLine(row: MapTypeBody): string {
  const size = row.size_px ? row.size_px.toLocaleString("en-US") + " px" : row.kind === "artwork" ? "artwork" : "";
  return [size, row.created ? dayMonth(row.created) : ""].filter(Boolean).join(" · ");
}

function nameCell(row: MapTypeBody, body: MapsResponse): Safe {
  const name =
    renaming === row.id
      ? html`<input type="text" class="rename" id="rename-input" maxlength="80" value="${renameText}" placeholder="empty: named by its style" aria-label="${"label for " + typeTitle(row)}">`
      : html`<span class="name" title="${typeTitle(row)}">${typeTitle(row)}</span>`;
  return html`<div class="id">${name}<div class="ctx" title="${row.name + " · " + row.id}">${chipsOf(row, body)} ${sizeLine(row)}</div></div>`;
}

function rerenderable(row: MapTypeBody, body: MapsResponse): boolean {
  const drawn = row.kind === "artwork" || body.styles.some((style) => style.layer === row.layer);
  return drawn && row.status === "ready" && !!(row.freshness.rerender || row.freshness.restyle || row.freshness.stale.length);
}

/* One visible action: draw it again when offered, else set as default; the rest in ⋯. Delete
 * and rename confirm in the same slots. */
function foot(row: MapTypeBody, body: MapsResponse): Safe {
  if (confirmingOn("delete:" + row.id)) {
    return html`<span class="ctx">delete? ${bytes(row.bytes)}</span>${actSlots(btn("delete", { data: { act: "confirm", key: "delete:" + row.id } }), { more: glyphBtn("×", "keep " + typeTitle(row), { data: { act: "keep" } }) })}`;
  }
  if (renaming === row.id) return actSlots(btn("save", { data: { act: "rename-save", id: row.id } }), { more: glyphBtn("×", "keep the name", { data: { act: "rename-x" } }) });
  if (row.status !== "ready") return actSlots("");
  const stale = row.freshness.stale.length > 0;
  const again = rerenderable(row, body)
    ? busyBtn(stale ? "regenerate" : "re-render", {
        disabled: !body.can_generate.ok,
        title: !body.can_generate.ok ? body.can_generate.reason || "" : row.freshness.rerender ? row.freshness.rerender.text : "draw it again from the current inputs; this one stays until deleted",
        data: { act: "rerender", id: row.id },
      })
    : "";
  const setDef = row.default ? "" : btn("set as default", { data: { act: "default", id: row.id } });
  const menu = (again && !row.default ? ["set as default"] : []).concat(["rename", row.in_switcher ? "hide from switcher" : "show in switcher"], row.default ? [] : ["delete"]);
  return actSlots(again || setDef, { more: moreBtn(typeTitle(row), menu) });
}

function card(row: MapTypeBody, body: MapsResponse, picked: boolean): Safe {
  const tone = confirmingOn("delete:" + row.id) ? "removing" : row.status === "failed" ? "bad" : row.freshness.stale.length ? "mid" : "";
  const size = row.bytes ? metric((row.bytes / 1e9).toFixed(1), "GB") : metric("–", "", "none");
  return entry({ tone: tone, key: row.id, picked: picked, extra: "mt" }, html`${picture(row, body)}${nameCell(row, body)}${size}<div class="mt-foot full">${foot(row, body)}</div>`);
}

export function sheetMarkup(body: MapsResponse, a: MapsAddress): Safe {
  const toggle = html`<button type="button" class="btn" data-act="gen" aria-pressed="${String(!!a.gen)}" title="${a.gen ? "close the generate form" : "draw a new map"}">generate</button>`;
  const head = toolrow(dashH("map types", body.types.length), toggle);
  const cards = body.types.length ? list("mt", "grid", body.types.map((row) => card(row, body, row.id === a.type))) : emptyLine("no map types yet", "generate one, or put a pyramid under data/local and press add");
  return html`<section id="maps-types">${head}${a.gen ? formMarkup(body, a.gen) : ""}${cards}</section>`;
}

/** The address with the generate form toggled. */
export function toggledGen(a: MapsAddress): string {
  return mapsDash({ gen: a.gen ? "" : "render", type: a.type });
}

/* A re-render that needs the heightfield, or the missing paint input, queues it first, and
 * nothing if that is refused; an otherwise current map restyles from a kept raster cache. */
function rerender(row: MapTypeBody): Promise<boolean> {
  const pending = row.freshness.rerender;
  const body = mapRegistry.body;
  let chain: Promise<boolean> = Promise.resolve(true);
  if (pending && pending.needs.indexOf("heightfield") >= 0) chain = submit("heightmap", {}, "", null);
  const size = row.size_px || 32768;
  const restyle = !pending && !row.freshness.stale.length && row.freshness.restyle && !!body && body.cached_sizes.indexOf(size) >= 0;
  const options = row.kind === "artwork" ? { enhance: !!body && body.can_generate.vulkan } : { layers: [row.layer], size: size, recipe: "current", top: true, light: !!row.axes.light, restyle: restyle };
  return chain.then(function (ok) {
    if (!ok) return false;
    if (row.kind === "artwork") return submit("artwork", options, row.label || "", row.id);
    return body ? submitRender(body, options, row.label || "", row.id) : submit("render", options, row.label || "", row.id);
  });
}

function focusRename(): void {
  setTimeout(function () {
    const input = document.getElementById("rename-input") as HTMLInputElement | null;
    input?.focus();
    input?.select();
  }, 0);
}

/** A ⋯ menu choice on a map type card. */
export function typeMenu(label: string, id: string): void {
  const row = typeRow(id);
  if (!row) return;
  if (label === "set as default") setDefault(id);
  else if (label === "rename") {
    renaming = id;
    renameText = row.label || "";
    redraw();
    focusRename();
  } else if (label === "delete") openConfirm("delete:" + id);
  else write("PATCH", "/api/maps/{ident}", { in_switcher: label === "show in switcher", version: mapRegistry.body?.version }, id).catch(refused("the switcher was not changed"));
}

function saveLabel(id: string): void {
  renaming = "";
  write("PATCH", "/api/maps/{ident}", { label: renameText, version: mapRegistry.body?.version }, id).catch(refused("the label was not saved"));
  redraw();
}

function stopRename(): void {
  renaming = "";
  redraw();
}

/** The cards' buttons; returns the work for a busy button, true for other handled clicks. */
export function typeAction(target: HTMLElement): Promise<boolean> | boolean {
  const act = target.dataset.act;
  const id = target.dataset.id || "";
  if (act === "open")
    leaveDashThen(function () {
      requestBaseMode(id, true);
    });
  else if (act === "default") setDefault(id);
  else if (act === "rerender") return typeRow(id) ? rerender(typeRow(id)!) : false;
  else if (act === "rename-save") saveLabel(id);
  else if (act === "rename-x") stopRename();
  else if (act === "keep") {
    closeConfirm();
    redraw();
  } else if (act === "confirm" && (target.dataset.key || "").indexOf("delete:") === 0) {
    closeConfirm();
    write("DELETE", "/api/maps/{ident}", undefined, (target.dataset.key || "").slice(7), "version=" + mapRegistry.body?.version).catch(refused("the map was not deleted"));
    redraw();
  } else return false;
  return true;
}

/** Typing, Enter and Esc in the rename field. */
export function renameKeys(event: KeyboardEvent): void {
  const input = event.target as HTMLInputElement;
  if (input.id !== "rename-input") return;
  if (event.key === "Enter") saveLabel(renaming);
  else if (event.key === "Escape") {
    event.stopPropagation();
    stopRename();
  }
}

export function renameInput(target: HTMLInputElement): void {
  if (target.id === "rename-input") renameText = target.value;
}

export function isRenaming(): boolean {
  return renaming !== "";
}
