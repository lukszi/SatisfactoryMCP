/* The "generate a map" form: what to make and how, its cost estimate, and the button that
 * queues it. What was typed is kept across redraws. */

import { get } from "../../api/client";
import { appendNote, button, checkbox, fieldError, selectBox, slider } from "../../kit/dashkit";
import { make } from "../../kit/dom";
import { bytes, duration } from "../../kit/format";
import { friendlyError } from "../../kit/toast";
import { mapRegistry } from "../../app/map-types";
import { paintFirst, redraw, submit, submitRender } from "./maps-actions";

import type { MapEstimateResponse, MapsResponse } from "../../api/shapes";

const form = {
  preset: "render",
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
  open: false,
};

const SIZE_WORDS: Record<number, string> = {
  1024: "preview 1024",
  2048: "2048",
  4096: "4096",
  8192: "8192",
  16384: "16384",
  32768: "full 32768",
};

let estimateSerial = 0;

/* Palette-only needs a kept raster cache at this size, drawn with the current recipe. */
function formOptions(): Record<string, unknown> {
  if (form.preset === "render") {
    const body = mapRegistry.body;
    const restyle = form.restyle && !!body && body.cached_sizes.indexOf(form.size) >= 0 && form.recipe === "current";
    return {
      layers: (body ? body.styles : [])
        .map(function (style) {
          return style.layer;
        })
        .filter(function (layer) {
          return form.layers[layer];
        }),
      size: form.size,
      recipe: form.recipe,
      top: form.top,
      titan_trees: form.titanTrees,
      keep_cache: form.keepCache,
      restyle: restyle,
      light: form.light,
    };
  }
  if (form.preset === "artwork") return { enhance: form.enhance && !!mapRegistry.body && mapRegistry.body.can_generate.vulkan };
  return {};
}

function presetName(): string {
  return form.preset === "inputs" ? form.input : form.preset;
}

function estimateQuery(): string {
  const options = formOptions();
  const query = ["preset=" + presetName()];
  Object.keys(options).forEach(function (key) {
    const value = options[key];
    query.push(key + "=" + encodeURIComponent(Array.isArray(value) ? value.join(",") : String(value)));
  });
  return query.join("&");
}

/* Only the newest estimate may write the line; the button stays off while anything blocks. */
function updateEstimate(line: HTMLElement, go: HTMLButtonElement, body: MapsResponse): void {
  const serial = ++estimateSerial;
  let blocked = !body.can_generate.ok ? body.can_generate.reason || "generation cannot run here" : "";
  if (!blocked && form.preset === "render" && !body.can_generate.heightfield) blocked = "render maps need the heightfield first";
  if (!blocked && form.preset === "render" && !(formOptions().layers as string[]).length) blocked = "pick a layer";
  go.disabled = !!blocked;
  go.title = blocked;
  line.textContent = "estimating…";
  const paint = form.preset === "render" && paintFirst(body, formOptions().layers as string[]) ? " · the paint layers are built first, a few minutes more" : "";
  get<MapEstimateResponse>(("/api/maps/estimate?" + estimateQuery()) as "/api/maps/estimate")
    .then(function (cost) {
      if (serial !== estimateSerial) return;
      line.textContent =
        "≈ " + duration(cost.seconds) + (cost.measured ? " (from the last run)" : "") + " · keeps " + bytes(cost.keep_bytes) + " · needs " + bytes(cost.needs_bytes) + " free while running · " + bytes(cost.free_bytes) + " free" + paint;
      line.classList.toggle("maps-short", !cost.ok);
      if (!cost.ok && !blocked) {
        go.disabled = true;
        go.title = cost.reason || "not enough disk";
      }
    })
    .catch(function (reason) {
      if (serial === estimateSerial) line.textContent = "no estimate: " + friendlyError(reason);
    });
}

function renderRenderOptions(options: HTMLElement, body: MapsResponse, refresh: () => void): void {
  const layers = make("div", "maps-checks");
  body.styles.forEach(function (style) {
    const box = checkbox(style.label, !!form.layers[style.layer], function (on) {
      form.layers[style.layer] = on;
      refresh();
    });
    box.title = style.tone + " base";
    layers.appendChild(box);
  });
  options.appendChild(layers);
  const sizes = body.sizes;
  const at = Math.max(0, sizes.indexOf(form.size));
  const sizeWord = make("span", "dash-sub", form.size + " px");
  options.appendChild(
    slider(
      sizes.map(function (size) {
        return SIZE_WORDS[size] || String(size);
      }),
      at,
      function (i) {
        sizeWord.textContent = sizes[i] + " px";
      },
      function (i) {
        form.size = sizes[i] || form.size;
        refresh();
      },
      { label: "size in pixels" }
    )
  );
  options.appendChild(sizeWord);
  options.appendChild(
    checkbox("arches and boulders", form.top, function (on) {
      form.top = on;
      refresh();
    })
  );
  const lit = checkbox("live sun", form.light, function (on) {
    form.light = on;
    refresh();
  });
  lit.title = "draws colour unlit and bakes a lighting pyramid, so the map is relit in the browser by any sun";
  options.appendChild(lit);
  options.appendChild(
    checkbox("Titan trees (game-painted)", form.titanTrees, function (on) {
      form.titanTrees = on;
      refresh();
    })
  );
  const recipe = make("label", "maps-inline");
  recipe.appendChild(make("span", "", "recipe "));
  recipe.appendChild(
    selectBox(
      [
        ["current", "current (PCHIP)"],
        ["kernel-only", "kernel only (fast, no rocks)"],
      ],
      form.recipe,
      function (value) {
        form.recipe = value;
        refresh();
      },
      { label: "recipe" }
    )
  );
  options.appendChild(recipe);
  const keep = checkbox("keep the raster cache for palette experiments", form.keepCache, function (on) {
    form.keepCache = on;
    refresh();
  });
  keep.title = "the next render at this size skips the slow raster passes; costs disk until cleared";
  options.appendChild(keep);
  if (body.cached_sizes.indexOf(form.size) >= 0 && form.recipe === "current") {
    const fast = checkbox("palette only: draw from the kept raster cache", form.restyle, function (on) {
      form.restyle = on;
      refresh();
    });
    fast.title = "skips the geometry passes; only colour, compose and cut run";
    options.appendChild(fast);
  }
}

function renderArtworkOptions(options: HTMLElement, body: MapsResponse, refresh: () => void): void {
  if (body.can_generate.vulkan) {
    options.appendChild(
      checkbox("upscale on the GPU", form.enhance, function (on) {
        form.enhance = on;
        refresh();
      })
    );
    appendNote(options, "downloads a 45 MB upscaler once into the user cache folder");
  } else appendNote(options, "no Vulkan GPU was found when the server started, so the upscaler is not offered");
}

function renderInputOptions(options: HTMLElement, refresh: () => void): void {
  options.appendChild(
    selectBox(
      [
        ["heightmap", "heightfield (replaces data/local/heightmap)"],
        ["caves", "cave masks"],
        ["rocks", "rock collision"],
        ["paint", "paint layers (for game-painted)"],
      ],
      form.input,
      function (value) {
        form.input = value;
        refresh();
      },
      { label: "which input" }
    )
  );
  appendNote(options, "replaces the input in place once the new one is whole; maps drawn from the old one turn amber");
}

function presetPicker(): HTMLElement {
  const what = make("label", "maps-inline");
  what.appendChild(make("span", "", "what "));
  what.appendChild(
    selectBox(
      [
        ["render", "render: a drawn style"],
        ["artwork", "artwork from the game"],
        ["inputs", "heightfield inputs"],
      ],
      form.preset,
      function (value) {
        form.preset = value;
        redraw();
      },
      { label: "what to generate" }
    )
  );
  return what;
}

function labelField(): HTMLElement {
  const name = make("label", "maps-inline");
  name.appendChild(make("span", "", "name "));
  const input = make("input", "dash-input");
  input.type = "text";
  input.maxLength = 80;
  input.placeholder = "optional; the id is derived";
  input.value = form.label;
  input.oninput = function () {
    form.label = input.value;
    fieldError(input, input.value.length >= 80 ? "at most 80 characters" : "");
  };
  name.appendChild(input);
  return name;
}

export function renderForm(parent: HTMLElement, body: MapsResponse): void {
  const fold = make("details", "dash-card maps-form");
  fold.open = form.open || !body.types.length;
  fold.ontoggle = function () {
    form.open = fold.open;
  };
  fold.appendChild(make("summary", "dash-h maps-summary", "generate a map"));
  const running = body.jobs.some(function (job) {
    return job.status === "running" || job.status === "queued";
  });
  const estimate = make("p", "dash-note maps-estimate", "");
  const go = button(running ? "queue" : "generate", function () {
    go.disabled = true;
    const queued = form.preset === "render" ? submitRender(body, formOptions(), form.label, null) : submit(presetName(), formOptions(), form.label, null);
    queued.then(function (ok) {
      if (ok) form.label = "";
      else redraw();
    });
  });
  const refresh = function () {
    updateEstimate(estimate, go, body);
  };
  fold.appendChild(presetPicker());
  const options = make("div", "maps-options");
  if (form.preset === "render") renderRenderOptions(options, body, refresh);
  else if (form.preset === "artwork") renderArtworkOptions(options, body, refresh);
  else renderInputOptions(options, refresh);
  fold.appendChild(options);
  if (form.preset !== "inputs") fold.appendChild(labelField());
  fold.appendChild(estimate);
  fold.appendChild(go);
  parent.appendChild(fold);
  refresh();
}
