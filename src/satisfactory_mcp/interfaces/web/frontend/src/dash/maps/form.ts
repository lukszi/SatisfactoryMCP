/* The generate form (a §2.12 card under the map types heading while `?gen=` is set): what to
 * make and how, its cost estimate, and the queue button. The preset is in the address; what
 * was typed lives here, so a redraw keeps it. */

import { get } from "../../api/client";
import { html, type Safe } from "../../kit/html";
import { busyBtn, check, selectBox } from "../../kit/ui/controls";
import { checks, form, frange, frow, ftext } from "../../kit/ui/forms";
import { bytes, duration } from "../../kit/format";
import { friendlyError } from "../../kit/toast";
import { mapRegistry } from "../../app/map-types";
import { GEN_PRESETS, type GenPreset } from "./address";
import { paintFirst, redraw, submit, submitRender } from "./writes";

import type { MapEstimateResponse, MapsResponse } from "../../api/shapes";

const state = {
  preset: "render" as GenPreset,
  input: "heightmap",
  layers: { terrain: true, painted: true } as Record<string, boolean>,
  size: 4096,
  top: true,
  light: true,
  titanTrees: true,
  recipe: "current",
  keepCache: false,
  restyle: false,
  enhance: false,
  label: "",
};

let estimateSerial = 0;

function canRestyle(body: MapsResponse | null): boolean {
  return !!body && body.cached_sizes.indexOf(state.size) >= 0 && state.recipe === "current";
}

function options(): Record<string, unknown> {
  const body = mapRegistry.body;
  if (state.preset === "render") {
    return {
      layers: (body ? body.styles : []).map((s) => s.layer).filter((layer) => state.layers[layer]),
      size: state.size,
      recipe: state.recipe,
      top: state.top,
      titan_trees: state.titanTrees,
      keep_cache: state.keepCache,
      restyle: state.restyle && canRestyle(body),
      light: state.light,
    };
  }
  if (state.preset === "artwork") return { enhance: state.enhance && !!body && body.can_generate.vulkan };
  return {};
}

function presetName(): string {
  return state.preset === "inputs" ? state.input : state.preset;
}

function renderRows(body: MapsResponse): Safe {
  const at = Math.max(0, body.sizes.indexOf(state.size));
  const layers = checks(body.styles.map((s) => check("gen-layer-" + s.layer, !!state.layers[s.layer], s.label, s.tone + " base")));
  const opts = checks(html`${check("gen-top", state.top, "arches and boulders")}${check("gen-light", state.light, "live sun", "draws colour unlit and bakes a lighting pyramid, so the map is relit in the browser by any sun")}${check("gen-titan", state.titanTrees, "Titan trees (game-painted)")}${check("gen-keep", state.keepCache, "keep the raster cache", "the next render at this size skips the slow raster passes; costs disk until cleared")}${canRestyle(body) ? check("gen-restyle", state.restyle, "palette only", "draw from the kept raster cache: only colour, compose and cut run") : ""}`);
  const recipes: [string, string][] = [
    ["current", "current (PCHIP)"],
    ["kernel-only", "kernel only (fast, no rocks)"],
  ];
  return html`${frow("layers", layers)}${frow("size", frange("gen-size", body.sizes.length - 1, at, state.size + " px"), { forId: "gen-size" })}${frow("options", opts)}${frow("recipe", selectBox("gen-recipe", recipes, state.recipe), { forId: "gen-recipe" })}`;
}

function artworkRows(body: MapsResponse): Safe {
  if (!body.can_generate.vulkan) return frow("options", html`<p class="note">no Vulkan GPU was found when the server started, so the upscaler is not offered</p>`);
  return frow("options", html`${check("gen-enhance", state.enhance, "upscale on the GPU")}<p class="note">downloads a 45 MB upscaler once into the user cache folder</p>`);
}

function inputRows(): Safe {
  const inputs: [string, string][] = [
    ["heightmap", "heightfield (replaces data/local/heightmap)"],
    ["caves", "cave masks"],
    ["rocks", "rock collision"],
    ["paint", "paint layers (for game-painted)"],
  ];
  return frow("input", html`${selectBox("gen-input", inputs, state.input)}<p class="note">replaces the input in place once the new one is whole; maps drawn from the old one turn “older data”</p>`, { forId: "gen-input" });
}

export function formMarkup(body: MapsResponse, preset: GenPreset): Safe {
  state.preset = preset;
  const running = body.jobs.some((job) => job.status === "running" || job.status === "queued");
  const rows = state.preset === "render" ? renderRows(body) : state.preset === "artwork" ? artworkRows(body) : inputRows();
  const name = state.preset === "inputs" ? "" : frow("name", ftext("gen-name", state.label, "optional; the id is derived", 80), { forId: "gen-name" });
  const go = frow("", html`<span class="btn-row">${busyBtn(running ? "queue" : "generate", { id: "gen-go" })}</span><p class="note" id="gen-estimate">estimating…</p>`);
  return html`<section class="card gen-card" aria-label="generate a map">${form(html`${frow("what", selectBox("gen-what", GEN_PRESETS, state.preset), { forId: "gen-what" })}${rows}${name}${go}`, "gen-form")}</section>`;
}

function blockedReason(body: MapsResponse): string {
  if (!body.can_generate.ok) return body.can_generate.reason || "generation cannot run here";
  if (state.preset === "render" && !body.can_generate.heightfield) return "render maps need the heightfield first";
  if (state.preset === "render" && !(options().layers as string[]).length) return "pick a layer";
  return "";
}

/** Asks the server what the form would cost; only the newest answer writes the line. */
export function refreshEstimate(): void {
  const body = mapRegistry.body;
  const line = document.getElementById("gen-estimate");
  const go = document.getElementById("gen-go") as HTMLButtonElement | null;
  if (!body || !line || !go) return;
  const serial = ++estimateSerial;
  const blocked = blockedReason(body);
  go.disabled = !!blocked;
  go.title = blocked;
  const opts = options();
  const query = ["preset=" + presetName()].concat(Object.keys(opts).map((k) => k + "=" + encodeURIComponent(Array.isArray(opts[k]) ? (opts[k] as string[]).join(",") : String(opts[k]))));
  const paint = state.preset === "render" && paintFirst(body, opts.layers as string[]) ? " · the paint layers are built first, a few minutes more" : "";
  get<MapEstimateResponse>(("/api/maps/estimate?" + query.join("&")) as "/api/maps/estimate")
    .then(function (cost) {
      if (serial !== estimateSerial) return;
      line.textContent = "≈ " + duration(cost.seconds) + (cost.measured ? " (from the last run)" : "") + " · keeps " + bytes(cost.keep_bytes) + " · needs " + bytes(cost.needs_bytes) + " free while running · " + bytes(cost.free_bytes) + " free" + paint;
      line.classList.toggle("tone-bad", !cost.ok);
      if (!cost.ok && !blocked) {
        go.disabled = true;
        go.title = cost.reason || "not enough disk";
      }
    })
    .catch(function (reason) {
      if (serial === estimateSerial) line.textContent = "no estimate: " + friendlyError(reason);
    });
}

const CHECKS: Record<string, (on: boolean) => void> = {
  "gen-top": (on) => (state.top = on),
  "gen-light": (on) => (state.light = on),
  "gen-titan": (on) => (state.titanTrees = on),
  "gen-keep": (on) => (state.keepCache = on),
  "gen-restyle": (on) => (state.restyle = on),
  "gen-enhance": (on) => (state.enhance = on),
};

/** A form control changed; true when it was one of the form's. */
export function formChange(target: HTMLInputElement | HTMLSelectElement): boolean {
  const id = target.id;
  const box = target as HTMLInputElement;
  if (id.indexOf("gen-layer-") === 0) state.layers[id.slice(10)] = box.checked;
  else if (CHECKS[id]) CHECKS[id]!(box.checked);
  else if (id === "gen-recipe") state.recipe = target.value;
  else if (id === "gen-input") state.input = target.value;
  else if (id === "gen-size") state.size = mapRegistry.body?.sizes[Number(target.value)] || state.size;
  else if (id === "gen-name") state.label = target.value;
  else return false;
  if (id === "gen-size" || id === "gen-recipe") redraw();
  else refreshEstimate();
  return true;
}

/** Live readout while the size slider moves. */
export function formInput(target: HTMLInputElement): void {
  if (target.id === "gen-name") state.label = target.value;
  if (target.id !== "gen-size") return;
  const size = mapRegistry.body?.sizes[Number(target.value)];
  const out = target.parentElement?.querySelector("output");
  if (size && out) out.textContent = size + " px";
}

/** Queues what the form says; false when the server refused it. */
export function queueFromForm(): Promise<boolean> {
  const body = mapRegistry.body;
  if (!body) return Promise.resolve(false);
  const queued = state.preset === "render" ? submitRender(body, options(), state.label, null) : submit(presetName(), options(), state.label, null);
  return queued.then(function (ok) {
    if (ok) state.label = "";
    return ok;
  });
}
