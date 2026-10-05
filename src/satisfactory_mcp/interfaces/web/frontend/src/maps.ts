/* Settings → maps: the map types, the job card, the generate form, and the default picker.
 *
 * The registry and its jobs come from mapstore.ts. A `maps` event that only moves a job
 * redraws the job card in place, so the form keeps what was typed into it; a change to the
 * list redraws the tab. Every confirm is inline, never a browser dialog.
 * docs/maps_contract.md §6. */

import { get, send } from "./api";
import { button, checkbox, chip, choice, empty, error, fieldError, idChip, loading, note, slider } from "./dashkit";
import { make } from "./dom";
import { adoptMaps, fetchMaps, mapState, mapTitle, onMaps, staleWhy, staleWord } from "./mapstore";
import { askMode } from "./tiles";
import { fail, friendly } from "./toast";

import type { ApiPath, StatusError } from "./api";
import type {
  MapEstimateResponse,
  MapInputBody,
  MapJobBody,
  MapJobDetailResponse,
  MapJobResponse,
  MapsResponse,
  MapTypeBody,
} from "./api-shapes";

export interface MapsHost {
  toMap: (action: () => void) => void;
  render: () => void;
}

/* What the form says, kept across redraws: a redraw must not undo a half-filled form. */
var form = {
  preset: "render",
  input: "heightmap",
  layers: { terrain: true, satellite: true, painted: false } as Record<string, boolean>,
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

/* The one inline confirm open at a time: `delete:<id>`, `cancel:<job>`, `replace:<job>`. */
var confirming = "";
var renaming = "";
var jobHost: HTMLElement | null = null;
var host: MapsHost | null = null;
var logs: Record<string, string[]> = {};
var logOpen: Record<string, boolean> = {};

var SIZE_WORDS: Record<number, string> = {
  1024: "preview 1024",
  2048: "2048",
  4096: "4096",
  8192: "8192",
  16384: "16384",
  32768: "full 32768",
};

export function bytes(n: number | null | undefined): string {
  var value = n || 0;
  if (value >= 1e9) return (value / 1e9).toFixed(1) + " GB";
  if (value >= 1e6) return Math.round(value / 1e6) + " MB";
  if (value >= 1e3) return Math.round(value / 1e3) + " kB";
  return value + " B";
}

function duration(seconds: number | null | undefined): string {
  var s = Math.max(0, Math.round(seconds || 0));
  if (s < 90) return s + " s";
  if (s < 5400) return Math.round(s / 60) + " min";
  return (s / 3600).toFixed(1) + " h";
}

function when(ts: number | null | undefined): string {
  if (!ts) return "–";
  var d = new Date(ts * 1000);
  var pad = function (n: number) {
    return (n < 10 ? "0" : "") + n;
  };
  return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate());
}

function clockTime(ts: number | null | undefined): string {
  if (!ts) return "";
  var d = new Date(ts * 1000);
  return (d.getHours() < 10 ? "0" : "") + d.getHours() + ":" + (d.getMinutes() < 10 ? "0" : "") + d.getMinutes();
}

function refused(what: string): (reason: unknown) => void {
  return function (reason: unknown) {
    var status = (reason as StatusError).status;
    if (status === 409 && ((reason as StatusError).body as { stale?: boolean } | undefined)?.stale) fetchMaps();
    fail(what + ": " + friendly(reason));
  };
}

function write(method: "PUT" | "PATCH" | "DELETE" | "POST", path: ApiPath, body?: object, subject?: string, query?: string): Promise<MapsResponse> {
  return send<MapsResponse>(method, path, body, subject, query).then(function (reply) {
    adoptMaps(reply);
    return reply;
  });
}

/* ------------------------------------------------------------ the default picker */

/** Settings → general: the type a fresh page opens on, shared by every browser here. */
export function mapPickerRow(): HTMLElement {
  var row = make("label", "dash-setting");
  var words = make("span", "dash-setting-text");
  words.appendChild(make("span", "dash-setting-k", "default base map"));
  words.appendChild(make("span", "dash-setting-hint", "what a fresh page opens on, in every browser here; the map's own switcher still changes the view"));
  row.appendChild(words);
  var body = mapState.body;
  if (!body) {
    row.appendChild(make("span", "dash-muted", mapState.failed ? "map list unreadable" : "loading…"));
    if (!mapState.failed) fetchMaps();
    return row;
  }
  var options: [string, string][] = body.types
    .filter(function (t) {
      return t.status === "ready";
    })
    .map(function (t): [string, string] {
      return [t.id, mapTitle(t) + (t.freshness.stale.length ? " (stale)" : "")];
    });
  options.push(["plain", "plain: no imagery"]);
  var value = body.default || "plain";
  var version = body.version;
  row.appendChild(
    choice(
      options,
      value,
      function (id) {
        write("PUT", "/api/maps/default", { id: id, version: version }).catch(refused("the default map was not changed"));
      },
      { label: "default base map" }
    )
  );
  return row;
}

/* --------------------------------------------------------------------- the jobs */

function presetWords(job: MapJobBody): string {
  var o = job.options as Record<string, unknown>;
  if (job.preset === "render") {
    var parts = ["render", (o.size as number) + " px"];
    if (o.recipe === "kernel-only") parts.push("kernel only");
    if (o.top === false) parts.push("no arches");
    if (o.titan_trees === false) parts.push("no Titan trees");
    if (o.keep_cache) parts.push("keeps the raster cache");
    if (o.restyle) parts.push("palette only");
    if (o.light) parts.push("live sun");
    return parts.join(" · ");
  }
  if (job.preset === "artwork") return o.enhance ? "artwork, upscaled" : "artwork";
  return job.preset + " input";
}

function jobTitle(job: MapJobBody): string {
  return job.produces.length ? job.produces.join(", ") : presetWords(job);
}

function bar(pct: number | null): HTMLElement {
  var track = make("div", "maps-bar" + (pct === null ? " maps-bar-busy" : ""));
  track.setAttribute("role", "progressbar");
  track.setAttribute("aria-valuemin", "0");
  track.setAttribute("aria-valuemax", "100");
  if (pct !== null) track.setAttribute("aria-valuenow", String(Math.round(pct * 100)));
  var fill = make("div", "maps-bar-fill");
  fill.style.width = pct === null ? "30%" : Math.round(pct * 100) + "%";
  track.appendChild(fill);
  return track;
}

function inlineConfirm(parent: HTMLElement, question: string, yes: string, no: string, act: () => void): void {
  var line = make("div", "maps-confirm");
  line.setAttribute("role", "group");
  line.appendChild(make("span", "", question));
  line.appendChild(
    button(yes, function () {
      confirming = "";
      act();
    })
  );
  line.appendChild(
    button(no, function () {
      confirming = "";
      redraw();
    })
  );
  parent.appendChild(line);
}

function cancelJob(job: MapJobBody): void {
  send<MapJobResponse>("DELETE", "/api/maps/jobs/{job}", undefined, job.id)
    .then(function () {
      fetchMaps();
    })
    .catch(refused("the job was not cancelled"));
}

function showLog(job: MapJobBody, box: HTMLElement): void {
  var pre = make("pre", "maps-log");
  pre.textContent = (logs[job.id] || ["…"]).slice(-20).join("\n");
  box.appendChild(pre);
  get<MapJobDetailResponse>("/api/maps/jobs/{job}", job.id)
    .then(function (reply) {
      logs[job.id] = reply.log_tail;
      pre.textContent = reply.log_tail.slice(-20).join("\n") || "(no output yet)";
    })
    .catch(function () {
      pre.textContent = "the log could not be read";
    });
}

function logFold(job: MapJobBody, parent: HTMLElement, label: string): void {
  var fold = make("details", "maps-fold");
  fold.open = !!logOpen[job.id];
  fold.appendChild(make("summary", "", label));
  fold.ontoggle = function () {
    logOpen[job.id] = fold.open;
    if (fold.open) showLog(job, fold);
  };
  if (fold.open) showLog(job, fold);
  parent.appendChild(fold);
}

function runningJob(job: MapJobBody, card: HTMLElement): void {
  var head = make("div", "maps-job-head");
  head.appendChild(make("strong", "", "generating "));
  head.appendChild(make("span", "maps-job-what", jobTitle(job)));
  var key = "cancel:" + job.id;
  if (confirming !== key)
    head.appendChild(
      button("cancel", function () {
        confirming = key;
        redraw();
      }, { title: "stop this job; what it wrote so far is deleted" })
    );
  card.appendChild(head);
  if (confirming === key) {
    inlineConfirm(card, "cancel? partial output is deleted", "yes, cancel", "keep running", function () {
      cancelJob(job);
    });
  }
  card.appendChild(make("div", "dash-sub", presetWords(job) + (job.started ? " · started " + clockTime(job.started) : "")));
  var line = make("div", "maps-progress");
  line.appendChild(bar(job.pct));
  var words = [job.pct === null ? "" : Math.round(job.pct * 100) + "%", job.stage_words];
  if (job.eta_s !== null) words.push("about " + duration(job.eta_s) + " left");
  else if (job.estimate_s && job.elapsed_s !== null) words.push(duration(job.elapsed_s) + " of about " + duration(job.estimate_s));
  line.appendChild(
    make(
      "span",
      "maps-progress-words",
      words
        .filter(function (w) {
          return !!w;
        })
        .join(" · ")
    )
  );
  card.appendChild(line);
  if (job.last_line) card.appendChild(make("div", "maps-last-line", job.last_line));
  logFold(job, card, "log (last lines)");
}

function queuedJob(job: MapJobBody, card: HTMLElement): void {
  var row = make("div", "maps-job-queued");
  row.appendChild(make("span", "", "queued: " + jobTitle(job) + " · " + presetWords(job)));
  row.appendChild(
    button("remove", function () {
      cancelJob(job);
    }, { title: "take this job off the queue" })
  );
  card.appendChild(row);
}

function replaceOffer(job: MapJobBody, card: HTMLElement): void {
  var body = mapState.body;
  var made = job.produces[0];
  var old = body && body.types.filter(function (t) {
    return t.id === made;
  })[0];
  var target = old && old.replaces;
  if (!body || !made || !target) return;
  var gone = body.types.filter(function (t) {
    return t.id === target;
  })[0];
  var key = "replace:" + job.id;
  var line = make("div", "maps-job-offer");
  if (confirming === key) {
    inlineConfirm(line, "make " + made + " the default and delete " + target + (gone ? " (" + bytes(gone.bytes) + ")" : "") + "?", "yes", "not now", function () {
      write("PUT", "/api/maps/default", { id: made })
        .then(function () {
          return write("DELETE", "/api/maps/{ident}", undefined, target || "");
        })
        .catch(refused("the switch did not complete"));
    });
  } else {
    line.appendChild(
      button("make default", function () {
        write("PUT", "/api/maps/default", { id: made }).catch(refused("the default map was not changed"));
      })
    );
    if (gone)
      line.appendChild(
        button("make default and delete " + target, function () {
          confirming = key;
          redraw();
        }, { title: "frees " + bytes(gone.bytes) })
      );
  }
  card.appendChild(line);
}

function finishedJob(job: MapJobBody, card: HTMLElement): void {
  var head = make("div", "maps-job-head");
  var took = job.started && job.ended ? duration(job.ended - job.started) : "";
  if (job.status === "done") head.appendChild(chip("done" + (took ? " in " + took : ""), "ok"));
  else if (job.status === "failed") head.appendChild(chip("failed", "bad"));
  else head.appendChild(chip(job.status, "muted"));
  head.appendChild(make("span", "maps-job-what", " " + jobTitle(job) + " · " + presetWords(job)));
  card.appendChild(head);
  if (job.status === "failed" && job.error_line) card.appendChild(make("div", "maps-last-line", job.error_line));
  if (job.status === "done") replaceOffer(job, card);
  if (job.status === "failed") logFold(job, card, "show log");
}

function drawJobs(card: HTMLElement): void {
  card.textContent = "";
  var body = mapState.body;
  if (!body) return;
  var active = body.jobs.filter(function (j) {
    return j.status === "running" || j.status === "queued";
  });
  var recent = body.jobs.filter(function (j) {
    return j.status !== "running" && j.status !== "queued" && j.ended && Date.now() / 1000 - j.ended < 3600;
  })[0];
  card.hidden = !active.length && !recent;
  active.forEach(function (job) {
    if (job.status === "running") runningJob(job, card);
    else queuedJob(job, card);
  });
  if (recent) finishedJob(recent, card);
}

/* ------------------------------------------------------------------- the form */

var estimateSerial = 0;

function formOptions(): Record<string, unknown> {
  if (form.preset === "render") {
    var body = mapState.body;
    var restyle = form.restyle && !!body && body.cached_sizes.indexOf(form.size) >= 0 && form.recipe === "current";
    return {
      layers: (body ? body.styles : [])
        .map(function (s) {
          return s.layer;
        })
        .filter(function (l) {
          return form.layers[l];
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
  if (form.preset === "artwork") return { enhance: form.enhance && !!mapState.body && mapState.body.can_generate.vulkan };
  return {};
}

function presetName(): string {
  return form.preset === "inputs" ? form.input : form.preset;
}

function estimateQuery(): string {
  var o = formOptions();
  var q = ["preset=" + presetName()];
  Object.keys(o).forEach(function (k) {
    var v = o[k];
    q.push(k + "=" + encodeURIComponent(Array.isArray(v) ? v.join(",") : String(v)));
  });
  return q.join("&");
}

function updateEstimate(line: HTMLElement, go: HTMLButtonElement, body: MapsResponse): void {
  var serial = ++estimateSerial;
  var blocked = !body.can_generate.ok ? body.can_generate.reason || "generation cannot run here" : "";
  if (!blocked && form.preset === "render" && !body.can_generate.heightfield) blocked = "render maps need the heightfield first";
  if (!blocked && form.preset === "render" && !(formOptions().layers as string[]).length) blocked = "pick a layer";
  go.disabled = !!blocked;
  go.title = blocked;
  line.textContent = "estimating…";
  get<MapEstimateResponse>(("/api/maps/estimate?" + estimateQuery()) as "/api/maps/estimate")
    .then(function (cost) {
      if (serial !== estimateSerial) return;
      line.textContent =
        "≈ " + duration(cost.seconds) + (cost.measured ? " (from the last run)" : "") + " · keeps " + bytes(cost.keep_bytes) + " · needs " + bytes(cost.needs_bytes) + " free while running · " + bytes(cost.free_bytes) + " free";
      line.classList.toggle("maps-short", !cost.ok);
      if (!cost.ok && !blocked) {
        go.disabled = true;
        go.title = cost.reason || "not enough disk";
      }
    })
    .catch(function (reason) {
      if (serial === estimateSerial) line.textContent = "no estimate: " + friendly(reason);
    });
}

function submit(preset: string, options: Record<string, unknown>, label: string, replaces: string | null): Promise<void> {
  return send<MapJobResponse>("POST", "/api/maps/jobs", { preset: preset, options: options, label: label || null, replaces: replaces })
    .then(function () {
      fetchMaps();
    })
    .catch(function (reason) {
      fail("the job was not queued: " + friendly(reason));
    });
}

function renderForm(parent: HTMLElement, body: MapsResponse): void {
  var fold = make("details", "dash-card maps-form");
  fold.open = form.open || !body.types.length;
  fold.ontoggle = function () {
    form.open = fold.open;
  };
  fold.appendChild(make("summary", "dash-h maps-summary", "generate a map"));
  var running = body.jobs.some(function (j) {
    return j.status === "running" || j.status === "queued";
  });
  var est = make("p", "dash-note maps-estimate", "");
  var go = button(running ? "queue" : "generate", function () {
    go.disabled = true;
    submit(presetName(), formOptions(), form.label, null).then(function () {
      form.label = "";
    });
  });
  var refresh = function () {
    updateEstimate(est, go, body);
  };
  var what = make("label", "maps-inline");
  what.appendChild(make("span", "", "what "));
  what.appendChild(
    choice(
      [
        ["render", "render: a drawn style"],
        ["artwork", "artwork from the game"],
        ["inputs", "heightfield inputs"],
      ],
      form.preset,
      function (v) {
        form.preset = v;
        redrawTab();
      },
      { label: "what to generate" }
    )
  );
  fold.appendChild(what);
  var opts = make("div", "maps-options");
  if (form.preset === "render") {
    var layers = make("div", "maps-checks");
    body.styles.forEach(function (style) {
      var box = checkbox(style.label, !!form.layers[style.layer], function (on) {
        form.layers[style.layer] = on;
        refresh();
      });
      box.title = style.tone + " base";
      layers.appendChild(box);
    });
    opts.appendChild(layers);
    var sizes = body.sizes;
    var at = Math.max(0, sizes.indexOf(form.size));
    var sizeWord = make("span", "dash-sub", form.size + " px");
    opts.appendChild(
      slider(
        sizes.map(function (s) {
          return SIZE_WORDS[s] || String(s);
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
    opts.appendChild(sizeWord);
    opts.appendChild(
      checkbox("arches and boulders", form.top, function (on) {
        form.top = on;
        refresh();
      })
    );
    var lit = checkbox("live sun", form.light, function (on) {
      form.light = on;
      refresh();
    });
    lit.title = "draws colour unlit and bakes a lighting pyramid, so the map is relit in the browser by any sun";
    opts.appendChild(lit);
    opts.appendChild(
      checkbox("Titan trees (game-painted)", form.titanTrees, function (on) {
        form.titanTrees = on;
        refresh();
      })
    );
    var recipe = make("label", "maps-inline");
    recipe.appendChild(make("span", "", "recipe "));
    recipe.appendChild(
      choice(
        [
          ["current", "current (PCHIP)"],
          ["kernel-only", "kernel only (fast, no rocks)"],
        ],
        form.recipe,
        function (v) {
          form.recipe = v;
          refresh();
        },
        { label: "recipe" }
      )
    );
    opts.appendChild(recipe);
    var keep = checkbox("keep the raster cache for palette experiments", form.keepCache, function (on) {
      form.keepCache = on;
      refresh();
    });
    keep.title = "the next render at this size skips the slow raster passes; costs disk until cleared";
    opts.appendChild(keep);
    if (body.cached_sizes.indexOf(form.size) >= 0 && form.recipe === "current") {
      var fast = checkbox("palette only: draw from the kept raster cache", form.restyle, function (on) {
        form.restyle = on;
        refresh();
      });
      fast.title = "skips the geometry passes; only colour, compose and cut run";
      opts.appendChild(fast);
    }
  } else if (form.preset === "artwork") {
    if (body.can_generate.vulkan) {
      opts.appendChild(
        checkbox("upscale on the GPU", form.enhance, function (on) {
          form.enhance = on;
          refresh();
        })
      );
      note(opts, "downloads a 45 MB upscaler once into the user cache folder");
    } else note(opts, "no Vulkan GPU was found when the server started, so the upscaler is not offered");
  } else {
    opts.appendChild(
      choice(
        [
          ["heightmap", "heightfield (replaces data/local/heightmap)"],
          ["caves", "cave masks"],
          ["rocks", "rock collision"],
          ["paint", "paint layers (for game-painted)"],
        ],
        form.input,
        function (v) {
          form.input = v;
          refresh();
        },
        { label: "which input" }
      )
    );
    note(opts, "replaces the input in place once the new one is whole; maps drawn from the old one turn amber");
  }
  fold.appendChild(opts);
  if (form.preset !== "inputs") {
    var name = make("label", "maps-inline");
    name.appendChild(make("span", "", "name "));
    var input = make("input", "dash-input");
    input.type = "text";
    input.maxLength = 80;
    input.placeholder = "optional; the id is derived";
    input.value = form.label;
    input.oninput = function () {
      form.label = input.value;
      fieldError(input, input.value.length >= 80 ? "at most 80 characters" : "");
    };
    name.appendChild(input);
    fold.appendChild(name);
  }
  fold.appendChild(est);
  fold.appendChild(go);
  parent.appendChild(fold);
  refresh();
}

/* ------------------------------------------------------------------- the types */

function rerender(row: MapTypeBody): void {
  var r = row.freshness.rerender;
  var chain: Promise<void> = Promise.resolve();
  if (r && r.needs.indexOf("heightfield") >= 0) chain = submit("heightmap", {}, "", null);
  var body = mapState.body;
  var size = row.size_px || 32768;
  var options =
    row.kind === "artwork"
      ? { enhance: !!body && body.can_generate.vulkan }
      : { layers: [row.layer], size: size, recipe: "current", top: true, light: !!row.axes.light, restyle: !r && !row.freshness.stale.length && row.freshness.restyle && !!body && body.cached_sizes.indexOf(size) >= 0 };
  chain.then(function () {
    return submit(row.kind === "artwork" ? "artwork" : "render", options, row.label || "", row.id);
  });
}

function nameCell(row: MapTypeBody, body: MapsResponse): HTMLElement {
  var cell = make("div", "maps-name");
  var title = make("div", "maps-title");
  if (renaming === row.id) {
    var input = make("input", "dash-input");
    input.type = "text";
    input.maxLength = 80;
    input.value = row.label || "";
    input.placeholder = row.name;
    input.setAttribute("aria-label", "label for " + row.id);
    var save = function () {
      renaming = "";
      write("PATCH", "/api/maps/{ident}", { label: input.value, version: body.version }, row.id).catch(refused("the label was not saved"));
    };
    input.onkeydown = function (event) {
      if (event.key === "Enter") save();
      if (event.key === "Escape") {
        renaming = "";
        redraw();
      }
    };
    title.appendChild(input);
    title.appendChild(button("save", save));
    setTimeout(function () {
      input.focus();
    }, 0);
  } else {
    title.appendChild(make("strong", "", mapTitle(row)));
  }
  title.appendChild(idChip(row.id, row.dir));
  if (row.default) title.appendChild(chip("★ default", "ok"));
  if (row.status !== "ready") title.appendChild(chip(row.status, row.status === "failed" ? "bad" : "muted"));
  cell.appendChild(title);
  var sub = [row.label ? row.name : "", row.size_px ? row.size_px + " px" : "", "data/local/" + row.dir.replace(/^\.$/, "")];
  cell.appendChild(
    make(
      "div",
      "dash-sub",
      sub
        .filter(function (part) {
          return !!part;
        })
        .join(" · ")
    )
  );
  return cell;
}

function freshnessCell(row: MapTypeBody): HTMLElement {
  var cell = make("div", "maps-fresh");
  var stale = staleWord(row);
  if (stale) {
    var amber = chip(stale, "muted", staleWhy(row));
    amber.classList.add("maps-stale");
    cell.appendChild(amber);
  }
  if (row.freshness.rerender) cell.appendChild(chip("re-render available", "muted", row.freshness.rerender.text));
  if (row.freshness.restyle) cell.appendChild(chip("newer palette", "muted"));
  if (stale) cell.appendChild(make("div", "dash-sub", staleWhy(row)));
  if (row.freshness.incomplete) cell.title = "provenance incomplete: this map was drawn before its sidecar recorded every input";
  return cell;
}

function actionsCell(row: MapTypeBody, body: MapsResponse): HTMLElement {
  var cell = make("div", "maps-actions");
  var key = "delete:" + row.id;
  if (confirming === key) {
    inlineConfirm(cell, "delete " + row.id + "? " + bytes(row.bytes), "delete", "keep", function () {
      write("DELETE", "/api/maps/{ident}", undefined, row.id, "version=" + body.version).catch(refused(row.id + " was not deleted"));
    });
    return cell;
  }
  cell.appendChild(
    button("set as default", function () {
      write("PUT", "/api/maps/default", { id: row.id, version: body.version }).catch(refused("the default map was not changed"));
    }, { disabled: row.default || row.status !== "ready", title: row.default ? "this is the default" : "open fresh pages on this map" })
  );
  if (row.freshness.rerender || row.freshness.restyle || row.freshness.stale.length) {
    var rr = row.freshness.rerender;
    cell.appendChild(
      button(row.freshness.stale.length ? "regenerate" : "re-render", function () {
        rerender(row);
      }, { disabled: !body.can_generate.ok, title: body.can_generate.ok ? (rr ? rr.text : "queue a new map; this one stays until you delete it") : body.can_generate.reason || "" })
    );
  }
  cell.appendChild(
    button("rename", function () {
      renaming = row.id;
      redraw();
    }, { label: "rename " + row.id })
  );
  cell.appendChild(
    checkbox("in switcher", row.in_switcher, function (on) {
      write("PATCH", "/api/maps/{ident}", { in_switcher: on, version: body.version }, row.id).catch(refused("the switcher was not changed"));
    })
  );
  cell.appendChild(
    button("delete", function () {
      confirming = key;
      redraw();
    }, { disabled: row.default, title: row.default ? "pick another default first" : "moves its files to the trash, then deletes them", label: "delete " + row.id })
  );
  return cell;
}

function thumb(row: MapTypeBody): HTMLElement {
  var link = make("button", "maps-thumb");
  link.type = "button";
  link.title = "open the map on " + mapTitle(row);
  link.setAttribute("aria-label", "open the map on " + mapTitle(row));
  if (row.status === "ready") {
    var img = make("img");
    img.loading = "lazy";
    img.alt = "";
    img.src = "/api/maptiles/" + encodeURIComponent(row.id) + "/0/0/0";
    link.appendChild(img);
    link.onclick = function () {
      if (host) host.toMap(function () {
        askMode(row.id, true);
      });
    };
  } else link.disabled = true;
  return link;
}

function renderTypes(parent: HTMLElement, body: MapsResponse): void {
  var card = make("section", "dash-card");
  card.appendChild(make("h2", "dash-h", "map types"));
  if (!body.types.length) {
    empty(card, "no map types yet", "generate one above, or put a pyramid under data/local and press add");
    parent.appendChild(card);
    return;
  }
  var list = make("div", "maps-list");
  list.setAttribute("role", "list");
  body.types.forEach(function (row) {
    var item = make("div", "maps-row" + (row.freshness.stale.length ? " stale" : ""));
    item.setAttribute("role", "listitem");
    item.appendChild(thumb(row));
    item.appendChild(nameCell(row, body));
    var meta = make("div", "maps-meta");
    meta.appendChild(make("span", "", "built " + when(row.created)));
    meta.appendChild(make("span", "", bytes(row.bytes)));
    item.appendChild(meta);
    item.appendChild(freshnessCell(row));
    item.appendChild(actionsCell(row, body));
    list.appendChild(item);
  });
  card.appendChild(list);
  parent.appendChild(card);
}

function inputLine(row: MapInputBody): string {
  if (!row.present) return "not built";
  var parts = [];
  if (row.version !== null) parts.push("v" + row.version);
  if (row.cl !== null) parts.push("build " + row.cl);
  if (row.transcribed) parts.push("built " + row.transcribed);
  return parts.join(" · ");
}

var INPUT_PRESET: Record<string, string> = {
  heightfield: "heightmap",
  caves: "caves",
  rocks: "rocks",
  paint: "paint",
};

function renderInputs(parent: HTMLElement, body: MapsResponse): void {
  var fold = make("details", "dash-card maps-inputs");
  fold.appendChild(make("summary", "dash-h maps-summary", "inputs"));
  body.inputs.forEach(function (row) {
    var line = make("div", "maps-input");
    line.appendChild(make("strong", "", row.name));
    line.appendChild(make("span", "dash-sub", inputLine(row)));
    var preset = INPUT_PRESET[row.name];
    if (preset)
      line.appendChild(
        button("rebuild", function () {
          submit(preset!, {}, "", null);
        }, { disabled: !body.can_generate.ok, title: body.can_generate.ok ? "queue a rebuild of the " + row.name : body.can_generate.reason || "" })
      );
    else line.appendChild(make("span", "dash-sub", "extracted by the colour workflow"));
    fold.appendChild(line);
  });
  parent.appendChild(fold);
}

function renderStatus(parent: HTMLElement, body: MapsResponse): void {
  var card = make("section", "dash-card maps-status");
  var line = make("div", "maps-status-line");
  line.appendChild(make("span", "", body.types.length + " map types · " + bytes(body.disk.maps_bytes) + " · " + bytes(body.disk.free_bytes) + " free"));
  if (body.disk.cache_bytes) {
    line.appendChild(make("span", "dash-sub", " · raster cache " + bytes(body.disk.cache_bytes)));
    line.appendChild(
      button("clear cache", function () {
        send("DELETE", "/api/maps/cache")
          .then(function () {
            fetchMaps();
          })
          .catch(refused("the cache was not cleared"));
      }, { title: "delete the rasters kept for fast re-renders" })
    );
  }
  card.appendChild(line);
  if (!body.can_generate.ok) {
    var setup = make("div", "maps-setup");
    setup.appendChild(chip("setup", "muted"));
    setup.appendChild(make("span", "", " " + (body.can_generate.reason || "generation cannot run here") + "; picking and deleting still work"));
    card.appendChild(setup);
  } else if (!body.can_generate.heightfield) {
    var hf = make("div", "maps-setup");
    hf.appendChild(chip("setup", "muted"));
    hf.appendChild(make("span", "", " render maps need the heightfield first "));
    hf.appendChild(
      button("build it", function () {
        submit("heightmap", {}, "", null);
      })
    );
    card.appendChild(hf);
  }
  if (body.unregistered.length) {
    var found = make("div", "maps-setup");
    found.appendChild(make("span", "", "found " + body.unregistered.length + " unregistered pyramid" + (body.unregistered.length > 1 ? "s" : "") + ": " + body.unregistered.join(", ") + " "));
    found.appendChild(
      button("add", function () {
        write("POST", "/api/maps/adopt").catch(refused("nothing was added"));
      })
    );
    card.appendChild(found);
  }
  parent.appendChild(card);
}

/* ---------------------------------------------------------------------- the tab */

var tabBody: HTMLElement | null = null;

function redrawTab(): void {
  if (host) host.render();
}

function redraw(): void {
  redrawTab();
}

export function renderMaps(body: HTMLElement, into: MapsHost): void {
  host = into;
  tabBody = body;
  var data = mapState.body;
  if (!data) {
    if (mapState.failed) error(body, "the map list", mapState.failed, function () {
      fetchMaps();
    });
    else {
      loading(body, "the map list");
      fetchMaps();
    }
    return;
  }
  renderStatus(body, data);
  var jobs = make("section", "dash-card maps-jobs");
  jobs.setAttribute("aria-live", "polite");
  drawJobs(jobs);
  jobHost = jobs;
  body.appendChild(jobs);
  renderForm(body, data);
  renderTypes(body, data);
  renderInputs(body, data);
}

onMaps(function (listed) {
  if (listed || !jobHost || !jobHost.isConnected || !tabBody || !tabBody.isConnected) return;
  drawJobs(jobHost);
});
