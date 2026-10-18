/* The maps page, `dash=maps` (`#maps`): disk and setup, jobs, the generate form, the map
 * types and the inputs, against /api/maps. docs/maps_contract.md §6. */

import "./maps.css";
import { send } from "../../api/client";
import { busyButton } from "../../kit/busy";
import { html, mount, type Safe } from "../../kit/html";
import { wireMoreMenus } from "../../kit/overlay/menu";
import { btn, busyBtn } from "../../kit/ui/controls";
import { acts, entry, list } from "../../kit/ui/entry";
import { chip } from "../../kit/ui/marks";
import { dashH } from "../../kit/ui/text";
import { bytes } from "../../kit/format";
import { failedInto, loadingInto } from "../../kit/ui/states";
import { fetchMapRegistry, mapRegistry, onMapRegistry } from "../../app/map-types";
import { hashFor } from "../../map/map";
import { state } from "../../app/state";
import { redraw, refused, submit, write } from "./maps-actions";
import { formChange, formInput, formMarkup, queueFromForm, refreshEstimate, rememberFold } from "./maps-form";
import { jobAction, jobsMarkup } from "./maps-jobs";
import { renameKeys, typeAction, typeMenu, typesMarkup } from "./maps-types";

import type { MapInputBody, MapsResponse } from "../../api/shapes";

const INPUT_PRESET: Record<string, string> = { heightfield: "heightmap", caves: "caves", rocks: "rocks", paint: "paint" };

let jobsHost: HTMLElement | null = null;

function header(): Safe {
  const settings = hashFor(state.dash).replace(/&settings=[^&]*/, "") + "&settings=map";
  return html`<header class="phead"><nav class="crumbs" aria-label="breadcrumb"><a href="${hashFor("")}" data-nav="up">Map</a><span class="sep">/</span><span aria-current="page">map types</span></nav>
    <div class="title"><h1>map types</h1></div><p class="note">rendered maps, their jobs and inputs; how a map is drawn is in <a href="${settings}">settings</a></p></header>`;
}

function statusCard(body: MapsResponse): Safe {
  const cache = body.disk.cache_bytes ? html`<span class="muted">·</span><span class="muted">raster cache ${bytes(body.disk.cache_bytes)}</span>${btn("clear cache", { title: "delete the rasters kept for fast re-renders", data: { act: "clear-cache" } })}` : "";
  const lines: Safe[] = [];
  if (!body.can_generate.ok) lines.push(html`<p class="note">${chip("setup", "muted")} ${body.can_generate.reason || "generation cannot run here"}; picking and deleting still work</p>`);
  else if (!body.can_generate.heightfield) lines.push(html`<p class="note">${chip("setup", "muted")} render maps need the heightfield first ${btn("build it", { data: { act: "heightfield" } })}</p>`);
  if (body.unregistered.length) lines.push(html`<p class="note">found ${body.unregistered.length} unregistered pyramid${body.unregistered.length > 1 ? "s" : ""}: ${body.unregistered.join(", ")} ${btn("add", { data: { act: "adopt" } })}</p>`);
  return html`<div class="card"><div class="kv"><span>${body.types.length} map types</span><span class="muted">·</span><span>${bytes(body.disk.maps_bytes)}</span><span class="muted">·</span><span>${bytes(body.disk.free_bytes)} free</span>${cache}</div>${lines}</div>`;
}

function inputLine(row: MapInputBody): string {
  if (!row.present) return "not built";
  const parts = [];
  if (row.version !== null) parts.push("v" + row.version);
  if (row.cl !== null) parts.push("build " + row.cl);
  if (row.transcribed) parts.push("built " + row.transcribed);
  return parts.join(" · ");
}

function inputsMarkup(body: MapsResponse): Safe {
  const rows = body.inputs.map(function (row) {
    const preset = INPUT_PRESET[row.name];
    const action = preset
      ? html`<span data-act="rebuild" data-preset="${preset}">${busyBtn("rebuild", { disabled: !body.can_generate.ok, title: body.can_generate.ok ? "queue a rebuild of the " + row.name : body.can_generate.reason || "" })}</span>`
      : html`<span class="ctx">extracted by the colour workflow</span>`;
    return entry({ tone: row.present ? "" : "mid" }, html`<span class="name">${row.name}</span><div class="id ctx">${inputLine(row)}</div>${acts(action)}`);
  });
  return html`${dashH("inputs", rows.length, "h3")}${list("inp", "stacked", rows, "nolead")}`;
}

function pageMarkup(body: MapsResponse): Safe {
  return html`${header()}${statusCard(body)}<div id="maps-jobs" aria-live="polite">${jobsMarkup()}</div>${formMarkup(body)}${typesMarkup(body)}${inputsMarkup(body)}`;
}

function cardAction(act: string, target: HTMLElement): Promise<unknown> | boolean {
  if (act === "clear-cache") send("DELETE", "/api/maps/cache").then(fetchMapRegistry).catch(refused("the cache was not cleared"));
  else if (act === "heightfield") return submit("heightmap", {}, "", null);
  else if (act === "adopt") write("POST", "/api/maps/adopt").catch(refused("nothing was added"));
  else if (act === "rebuild") return submit(target.dataset.preset || "", {}, "", null);
  else return false;
  return true;
}

function onClick(event: MouseEvent, root: HTMLElement): void {
  const target = (event.target as Element).closest<HTMLElement>("[data-act], #gen-go");
  if (!target || !root.contains(target)) return;
  if (target.id === "gen-go") {
    busyButton(target as HTMLButtonElement, queueFromForm).then((ok) => (ok ? redraw() : refreshEstimate()));
    return;
  }
  const act = target.dataset.act || "";
  if (jobsHost && jobsHost.contains(target) && jobAction(target, jobsHost, redraw)) return;
  const done = cardAction(act, target) || typeAction(target);
  const button = target.tagName === "BUTTON" ? (target as HTMLButtonElement) : target.querySelector("button");
  if (done instanceof Promise && button) busyButton(button, () => done);
  if (act === "keep" || act === "confirm") redraw();
}

function wirePage(root: HTMLElement): void {
  root.addEventListener("click", (event) => onClick(event, root));
  root.addEventListener("change", (event) => formChange(event.target as HTMLInputElement));
  root.addEventListener("input", (event) => formInput(event.target as HTMLInputElement));
  root.addEventListener("keydown", renameKeys);
  root.addEventListener("toggle", (event) => (event.target as Element).id === "gen-fold" && rememberFold((event.target as HTMLDetailsElement).open), true);
  wireMoreMenus(root, function (label, button) {
    typeMenu(label, button.closest<HTMLElement>(".entry")?.dataset.key || "");
  });
}

export function renderMapsPage(parent: HTMLElement): void {
  const data = mapRegistry.body;
  if (!data) {
    if (mapRegistry.failed) failedInto(parent, "the map list", mapRegistry.failed, fetchMapRegistry);
    else {
      loadingInto(parent, "the map list");
      fetchMapRegistry();
    }
    return;
  }
  const root = document.createElement("div");
  root.className = "maps-page";
  mount(root, pageMarkup(data));
  parent.appendChild(root);
  jobsHost = root.querySelector("#maps-jobs");
  wirePage(root);
  refreshEstimate();
}

onMapRegistry(function (listed) {
  if (listed || !jobsHost || !jobsHost.isConnected) return;
  mount(jobsHost, jobsMarkup());
});
