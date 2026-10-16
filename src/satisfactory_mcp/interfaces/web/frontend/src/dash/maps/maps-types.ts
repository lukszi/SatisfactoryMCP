/* The map types: one card per registered map with its freshness and what can be done to it. */

import { html, type Safe } from "../../kit/html";
import { btn, busyBtn, moreBtn } from "../../kit/ui/controls";
import { acts, entry, list, metric } from "../../kit/ui/entry";
import { chip } from "../../kit/ui/marks";
import { dashH, emptyLine } from "../../kit/ui/text";
import { bytes, isoDate } from "../../kit/format";
import { mapRegistry, mapTypeAxes, staleLabel, staleReasons } from "../../app/map-types";
import { jobPercent } from "../../app/shell/jobstrip";
import { requestBaseMode } from "../../map/tiles";
import { leaveDashThen } from "../actions";
import { closeConfirm, confirmingOn, openConfirm, redraw, refused, submit, submitRender, write } from "./maps-actions";

import type { MapsResponse, MapTypeBody } from "../../api/shapes";

/* The id whose label is being edited; "" when none is. */
let renaming = "";

function stateChips(row: MapTypeBody): Safe {
  const stale = staleLabel(row);
  return html`${row.default ? chip("default", "ok") : ""}${row.status !== "ready" ? chip(row.status, row.status === "failed" ? "bad" : "mid") : ""}${stale ? chip(stale, "mid", staleReasons(row)) : ""}${row.freshness.rerender ? chip("re-render available", "muted", row.freshness.rerender.text) : ""}${row.freshness.restyle ? chip("newer palette") : ""}${chip(row.id, "id", row.dir)}`;
}

function thumb(row: MapTypeBody): Safe {
  if (row.status !== "ready") return html`<button class="thumb none" type="button" disabled aria-label="${"no picture of " + row.title + " yet"}"></button>`;
  return html`<button class="thumb" type="button" data-act="open" data-id="${row.id}" aria-label="${"open the map on " + row.title}" title="${"open the map on " + row.title}"><img src="${"/api/maptiles/" + encodeURIComponent(row.id) + "/0/0/0"}" alt="" loading="lazy"></button>`;
}

function nameCell(row: MapTypeBody): Safe {
  const name = renaming === row.id
    ? html`<span class="rename"><input type="text" id="rename-input" maxlength="80" value="${row.label || ""}" placeholder="empty: named by its style" aria-label="${"label for " + row.id}">${btn("save", { data: { act: "rename-save", id: row.id } })}</span>`
    : html`<span class="name" title="${row.title}">${row.title}</span>`;
  const ctx = mapTypeAxes(row) + " · built " + isoDate(row.created);
  return html`<div class="id">${name}<div class="ctx" title="${staleReasons(row) || ctx}">${stateChips(row)} ${ctx}</div></div>`;
}

function rerenderable(row: MapTypeBody, body: MapsResponse): boolean {
  const drawn = row.kind === "artwork" || body.styles.some((style) => style.layer === row.layer);
  return drawn && !!(row.freshness.rerender || row.freshness.restyle || row.freshness.stale.length);
}

function actions(row: MapTypeBody, body: MapsResponse): Safe {
  const key = "delete:" + row.id;
  if (confirmingOn(key)) return acts(html`<span class="ctx">delete ${row.id}? ${bytes(row.bytes)}</span>${btn("delete", { data: { act: "confirm", key: key } })}${btn("keep", { data: { act: "keep" } })}`, true);
  const setDefault = row.default || row.status !== "ready" ? "" : btn("set as default", { extra: "hide-phone", data: { act: "default", id: row.id } });
  const again = rerenderable(row, body)
    ? busyBtn(row.freshness.stale.length ? "regenerate" : "re-render", {
        extra: "hide-phone",
        disabled: !body.can_generate.ok,
        title: !body.can_generate.ok ? body.can_generate.reason || "" : row.freshness.rerender ? row.freshness.rerender.text : "queue a new map; this one stays until it is deleted",
      })
    : "";
  const menu = ["rename", row.in_switcher ? "hide from switcher" : "show in switcher"].concat(row.default ? [] : ["delete"]);
  return acts(html`${setDefault}${again ? html`<span data-act="rerender" data-id="${row.id}">${again}</span>` : ""}${moreBtn(row.id, row.default ? menu : ["set as default"].concat(menu))}`, true);
}

function typeEntry(row: MapTypeBody, body: MapsResponse): Safe {
  const running = body.jobs.filter((job) => job.status === "running" && job.produces.indexOf(row.id) >= 0)[0];
  const figure = running ? metric(jobPercent(running) || "…", "rendered") : row.bytes ? metric((row.bytes / 1e9).toFixed(1), "GB") : metric("–", "", "none");
  const tone = confirmingOn("delete:" + row.id) ? "removing" : row.status === "failed" ? "bad" : row.freshness.stale.length ? "mid" : "";
  return entry({ tone: tone, key: row.id }, html`${thumb(row)}${nameCell(row)}${figure}${actions(row, body)}`);
}

export function typesMarkup(body: MapsResponse): Safe {
  if (!body.types.length) return html`${dashH("map types", 0, "h3")}${emptyLine("no map types yet", "generate one above, or put a pyramid under data/local and press add")}`;
  return html`${dashH("map types", body.types.length, "h3")}${list("mt", "grid", body.types.map((row) => typeEntry(row, body)))}`;
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

function rowOf(id: string | undefined): MapTypeBody | undefined {
  return mapRegistry.body?.types.filter((t) => t.id === id)[0];
}

function setDefault(id: string): void {
  write("PUT", "/api/maps/default", { id: id, version: mapRegistry.body?.version }).catch(refused("the default map was not changed"));
}

/** A ⋯ menu choice on a map type card. */
export function typeMenu(label: string, id: string): void {
  const row = rowOf(id);
  const version = mapRegistry.body?.version;
  if (!row) return;
  if (label === "set as default") setDefault(id);
  else if (label === "rename") {
    renaming = id;
    redraw();
    setTimeout(() => document.getElementById("rename-input")?.focus(), 0);
  } else if (label === "delete") openConfirm("delete:" + id);
  else write("PATCH", "/api/maps/{ident}", { in_switcher: label === "show in switcher", version: version }, id).catch(refused("the switcher was not changed"));
}

function saveLabel(id: string): void {
  const input = document.getElementById("rename-input") as HTMLInputElement | null;
  renaming = "";
  write("PATCH", "/api/maps/{ident}", { label: input ? input.value : "", version: mapRegistry.body?.version }, id).catch(refused("the label was not saved"));
}

/** The type cards' buttons; returns the work for a busy button, true for other handled clicks. */
export function typeAction(target: HTMLElement): Promise<boolean> | boolean {
  const act = target.dataset.act;
  const id = target.dataset.id || "";
  if (act === "open")
    leaveDashThen(function () {
      requestBaseMode(id, true);
    });
  else if (act === "default") setDefault(id);
  else if (act === "rerender") return rowOf(id) ? rerender(rowOf(id)!) : false;
  else if (act === "rename-save") saveLabel(id);
  else if (act === "confirm" && (target.dataset.key || "").indexOf("delete:") === 0) {
    closeConfirm();
    write("DELETE", "/api/maps/{ident}", undefined, (target.dataset.key || "").slice(7), "version=" + mapRegistry.body?.version).catch(refused("the map was not deleted"));
  } else return false;
  return true;
}

export function renameKeys(event: KeyboardEvent): void {
  const input = event.target as HTMLElement;
  if (input.id !== "rename-input") return;
  if (event.key === "Enter") saveLabel(renaming);
  else if (event.key === "Escape") {
    event.stopPropagation();
    renaming = "";
    redraw();
  }
}
