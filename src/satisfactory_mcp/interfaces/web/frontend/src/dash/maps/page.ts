/* The map studio, `dash=maps` (`#maps?gen=<preset>&type=<id>`): a slim header, the jobs while
 * any exist, the map types as a contact sheet with the generate form, then the inputs; all from
 * /api/maps. docs/frontend_kit.md, Map studio; docs/maps_contract.md §6. */

import "./maps.css";
import { send } from "../../api/client";
import { busyButton } from "../../kit/busy";
import { keepFocus } from "../../kit/focus";
import { html, mount, type Safe } from "../../kit/html";
import { wireMoreMenus } from "../../kit/overlay/menu";
import { notify } from "../../kit/toast";
import { btn, busyBtn } from "../../kit/ui/controls";
import { pageHead } from "../../kit/ui/text";
import { bytes } from "../../kit/format";
import { failedInto, loadingInto } from "../../kit/ui/states";
import { fetchMapRegistry, mapRegistry, onMapRegistry } from "../../app/map-types";
import { go } from "../../app/nav";
import { hashFor } from "../../map/map";
import { mapsAddress, mapsDash, type GenPreset, type MapsAddress } from "./address";
import { renameInput, renameKeys, sheetMarkup, toggledGen, typeAction, typeMenu, wellInner } from "./cards";
import { formChange, formInput, queueFromForm, refreshEstimate } from "./form";
import { inputsMarkup } from "./inputs";
import { jobAction, jobMenu, jobsMarkup } from "./jobs";
import { redraw, refused, submit, typeRow, write } from "./writes";

import type { MapCacheResponse, MapsResponse } from "../../api/shapes";

let jobsHost: HTMLElement | null = null;
let shown: MapsAddress = { gen: "", type: "" };
/* The type last scrolled into view, so a redraw does not scroll again. */
let scrolledTo = "";

function setupLines(body: MapsResponse): Safe[] {
  const lines: Safe[] = [];
  if (!body.can_generate.ok) lines.push(html`<p class="note">${body.can_generate.reason || "generation cannot run here"}; picking and deleting still work</p>`);
  else if (!body.can_generate.heightfield) lines.push(html`<p class="note">render maps need the heightfield first ${btn("build it", { data: { act: "heightfield" } })}</p>`);
  if (body.unregistered.length) lines.push(html`<p class="note">${body.unregistered.length} unregistered pyramid${body.unregistered.length > 1 ? "s" : ""}: ${body.unregistered.join(", ")} ${btn("add", { data: { act: "adopt" } })}</p>`);
  return lines;
}

function header(body: MapsResponse): Safe {
  const disk = body.disk;
  const clear = busyBtn("clear cache", {
    disabled: !disk.cache_bytes,
    title: disk.cache_bytes ? "delete the rasters kept for fast re-renders (" + bytes(disk.cache_bytes) + ")" : "no raster cache kept",
    data: { act: "clear-cache" },
  });
  const meta = bytes(disk.maps_bytes) + " on disk · " + bytes(disk.free_bytes) + " free · raster cache " + bytes(disk.cache_bytes);
  return pageHead({ crumbs: [["Map", hashFor("")]], name: "map studio", acts: clear, note: meta, more: setupLines(body) });
}

function clearCache(): Promise<unknown> {
  return send<MapCacheResponse>("DELETE", "/api/maps/cache")
    .then(function (reply) {
      notify("freed " + bytes(reply.freed_bytes));
      return fetchMapRegistry();
    })
    .catch(refused("the cache was not cleared"));
}

function pageAction(act: string, target: HTMLElement): Promise<unknown> | boolean {
  if (act === "gen") go(toggledGen(shown), "replace");
  else if (act === "clear-cache") return clearCache();
  else if (act === "heightfield") return submit("heightmap", {}, "", null);
  else if (act === "adopt") write("POST", "/api/maps/adopt").catch(refused("nothing was added"));
  else if (act === "rebuild") return submit(target.dataset.preset || "", {}, "", null);
  else return false;
  return true;
}

function redrawJobs(): void {
  if (!jobsHost || !jobsHost.isConnected) return;
  const host = jobsHost;
  keepFocus(host, () => mount(host, jobsMarkup(shown)));
}

function onClick(event: MouseEvent, root: HTMLElement): void {
  const target = (event.target as Element).closest<HTMLElement>("[data-act], #gen-go");
  if (!target || !root.contains(target) || (target as HTMLButtonElement).disabled) return;
  if (target.id === "gen-go") {
    busyButton(target as HTMLButtonElement, queueFromForm).then((ok) => (ok ? redraw() : refreshEstimate()));
    return;
  }
  const act = target.dataset.act || "";
  if (jobsHost && jobsHost.contains(target) && jobAction(target, jobsHost, redrawJobs)) return;
  const done = pageAction(act, target) || typeAction(target);
  if (done instanceof Promise && target.tagName === "BUTTON") busyButton(target as HTMLButtonElement, () => done);
}

function onChange(target: HTMLInputElement): void {
  if (target.id === "gen-what") go(mapsDash({ gen: target.value as GenPreset, type: shown.type }), "replace");
  else formChange(target);
}

function wirePage(root: HTMLElement): void {
  root.addEventListener("click", (event) => onClick(event, root));
  root.addEventListener("change", (event) => onChange(event.target as HTMLInputElement));
  root.addEventListener("input", function (event) {
    formInput(event.target as HTMLInputElement);
    renameInput(event.target as HTMLInputElement);
  });
  root.addEventListener("keydown", renameKeys);
  wireMoreMenus(root, function (label, button) {
    const key = button.closest<HTMLElement>(".entry")?.dataset.key || "";
    if (key.indexOf("job:") === 0) jobMenu(label, key.slice(4));
    else typeMenu(label, key);
  });
}

/* A picked type is scrolled into view once, when the address picks it. */
function revealPick(root: HTMLElement, a: MapsAddress): void {
  if (!a.type || a.type === scrolledTo) {
    scrolledTo = a.type;
    return;
  }
  scrolledTo = a.type;
  requestAnimationFrame(() => root.querySelector(".l-mt .entry.picked")?.scrollIntoView({ block: "nearest" }));
}

export function renderMapsPage(parent: HTMLElement, subject: string): void {
  const data = mapRegistry.body;
  if (!data) {
    if (mapRegistry.failed) failedInto(parent, "the map list", mapRegistry.failed, fetchMapRegistry);
    else {
      loadingInto(parent, "the map list");
      fetchMapRegistry();
    }
    return;
  }
  shown = mapsAddress(subject);
  const root = document.createElement("div");
  root.className = "maps-page";
  mount(root, html`${header(data)}<div id="maps-jobs" aria-live="polite">${jobsMarkup(shown)}</div>${sheetMarkup(data, shown)}${inputsMarkup(data)}`);
  parent.appendChild(root);
  jobsHost = root.querySelector("#maps-jobs");
  wirePage(root);
  if (shown.gen) refreshEstimate();
  revealPick(root, shown);
}

/* Job progress redraws the jobs list and the rendering cards' wells in place; a change to the
 * list itself redraws the page through the shell. */
onMapRegistry(function (listed) {
  if (listed || !jobsHost || !jobsHost.isConnected) return;
  redrawJobs();
  const body = mapRegistry.body;
  document.querySelectorAll<HTMLElement>(".maps-page [data-well]").forEach(function (well) {
    const row = typeRow(well.dataset.well);
    const job = body?.jobs.filter((j) => (j.status === "running" || j.status === "queued") && j.produces.indexOf(well.dataset.well || "") >= 0)[0];
    if (row) mount(well, wellInner(row, job));
  });
});
