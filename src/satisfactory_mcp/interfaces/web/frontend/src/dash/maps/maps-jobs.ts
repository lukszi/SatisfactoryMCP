/* The maps job card: the running job with its progress and log, the queue, and the last job
 * that finished in the past hour. */

import { get, send } from "../../api/client";
import { button, chip } from "../../kit/dashkit";
import { make } from "../../kit/dom";
import { bytes, duration, timeOfDay } from "../../kit/format";
import { fetchMapRegistry, mapRegistry } from "../../app/map-types";
import { confirmingOn, inlineConfirm, openConfirm, refused, write } from "./maps-actions";

import type { MapJobBody, MapJobDetailResponse, MapJobResponse } from "../../api/shapes";

const logs: Record<string, string[]> = {};
const logOpen: Record<string, boolean> = {};

function jobOptionsText(job: MapJobBody): string {
  const options = job.options as Record<string, unknown>;
  if (job.preset === "render") {
    const parts = ["render", (options.size as number) + " px"];
    if (options.recipe === "kernel-only") parts.push("kernel only");
    if (options.top === false) parts.push("no arches");
    if (options.titan_trees === false) parts.push("no Titan trees");
    if (options.keep_cache) parts.push("keeps the raster cache");
    if (options.restyle) parts.push("palette only");
    if (options.light) parts.push("live sun");
    return parts.join(" · ");
  }
  if (job.preset === "artwork") return options.enhance ? "artwork, upscaled" : "artwork";
  return job.preset + " input";
}

function jobTitle(job: MapJobBody): string {
  return job.produces.length ? job.produces.join(", ") : jobOptionsText(job);
}

/* A null fraction is a stage with no measure: the bar sweeps instead. */
function progressBar(fraction: number | null): HTMLElement {
  const track = make("div", "maps-bar" + (fraction === null ? " maps-bar-busy" : ""));
  track.setAttribute("role", "progressbar");
  track.setAttribute("aria-valuemin", "0");
  track.setAttribute("aria-valuemax", "100");
  if (fraction !== null) track.setAttribute("aria-valuenow", String(Math.round(fraction * 100)));
  const fill = make("div", "maps-bar-fill");
  fill.style.width = fraction === null ? "30%" : Math.round(fraction * 100) + "%";
  track.appendChild(fill);
  return track;
}

function cancelJob(job: MapJobBody): void {
  send<MapJobResponse>("DELETE", "/api/maps/jobs/{job}", undefined, job.id)
    .then(function () {
      fetchMapRegistry();
    })
    .catch(refused("the job was not cancelled"));
}

function showLog(job: MapJobBody, box: HTMLElement): void {
  const pre = make("pre", "maps-log");
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
  const fold = make("details", "maps-fold");
  fold.open = !!logOpen[job.id];
  fold.appendChild(make("summary", "", label));
  fold.ontoggle = function () {
    logOpen[job.id] = fold.open;
    if (fold.open) showLog(job, fold);
  };
  if (fold.open) showLog(job, fold);
  parent.appendChild(fold);
}

function progressWords(job: MapJobBody): string {
  const words = [job.pct === null ? "" : Math.round(job.pct * 100) + "%", job.stage_words];
  if (job.eta_s !== null) words.push("about " + duration(job.eta_s) + " left");
  else if (job.estimate_s && job.elapsed_s !== null) words.push(duration(job.elapsed_s) + " of about " + duration(job.estimate_s));
  return words
    .filter(function (word) {
      return !!word;
    })
    .join(" · ");
}

function runningJob(job: MapJobBody, card: HTMLElement): void {
  const head = make("div", "maps-job-head");
  head.appendChild(make("strong", "", "generating "));
  head.appendChild(make("span", "maps-job-what", jobTitle(job)));
  const key = "cancel:" + job.id;
  if (!confirmingOn(key))
    head.appendChild(
      button("cancel", function () {
        openConfirm(key);
      }, { title: "stop this job; what it wrote so far is deleted" })
    );
  card.appendChild(head);
  if (confirmingOn(key)) {
    inlineConfirm(card, "cancel? partial output is deleted", "yes, cancel", "keep running", function () {
      cancelJob(job);
    });
  }
  card.appendChild(make("div", "dash-sub", jobOptionsText(job) + (job.started ? " · started " + timeOfDay(job.started) : "")));
  const line = make("div", "maps-progress");
  line.appendChild(progressBar(job.pct));
  line.appendChild(make("span", "maps-progress-words", progressWords(job)));
  card.appendChild(line);
  if (job.last_line) card.appendChild(make("div", "maps-last-line", job.last_line));
  logFold(job, card, "log (last lines)");
}

function queuedJob(job: MapJobBody, card: HTMLElement): void {
  const row = make("div", "maps-job-queued");
  row.appendChild(make("span", "", "queued: " + jobTitle(job) + " · " + jobOptionsText(job)));
  row.appendChild(
    button("remove", function () {
      cancelJob(job);
    }, { title: "take this job off the queue" })
  );
  card.appendChild(row);
}

/* A finished job that replaces another type offers to make it the default and drop the old. */
function replaceOffer(job: MapJobBody, card: HTMLElement): void {
  const body = mapRegistry.body;
  const made = job.produces[0];
  const old = body && body.types.filter(function (type) {
    return type.id === made;
  })[0];
  const target = old && old.replaces;
  if (!body || !made || !target) return;
  const gone = body.types.filter(function (type) {
    return type.id === target;
  })[0];
  const key = "replace:" + job.id;
  const line = make("div", "maps-job-offer");
  if (confirmingOn(key)) {
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
          openConfirm(key);
        }, { title: "frees " + bytes(gone.bytes) })
      );
  }
  card.appendChild(line);
}

function finishedJob(job: MapJobBody, card: HTMLElement): void {
  const head = make("div", "maps-job-head");
  const took = job.started && job.ended ? duration(job.ended - job.started) : "";
  if (job.status === "done") head.appendChild(chip("done" + (took ? " in " + took : ""), "ok"));
  else if (job.status === "failed") head.appendChild(chip("failed", "bad"));
  else head.appendChild(chip(job.status, "muted"));
  head.appendChild(make("span", "maps-job-what", " " + jobTitle(job) + " · " + jobOptionsText(job)));
  card.appendChild(head);
  if (job.status === "failed" && job.error_line) card.appendChild(make("div", "maps-last-line", job.error_line));
  if (job.status === "done") replaceOffer(job, card);
  if (job.status === "failed") logFold(job, card, "show log");
}

export function drawJobs(card: HTMLElement): void {
  card.textContent = "";
  const body = mapRegistry.body;
  if (!body) return;
  const active = body.jobs.filter(function (job) {
    return job.status === "running" || job.status === "queued";
  });
  const recent = body.jobs.filter(function (job) {
    return job.status !== "running" && job.status !== "queued" && job.ended && Date.now() / 1000 - job.ended < 3600;
  })[0];
  card.hidden = !active.length && !recent;
  active.forEach(function (job) {
    if (job.status === "running") runningJob(job, card);
    else queuedJob(job, card);
  });
  if (recent) finishedJob(recent, card);
}
