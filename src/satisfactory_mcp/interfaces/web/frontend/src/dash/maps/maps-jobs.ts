/* The maps page's jobs list: the running job with its bar and log, the queue, and the last
 * job that finished in the past hour. Progress events redraw this list in place. */

import { get, send } from "../../api/client";
import { html, mount, type Safe } from "../../kit/html";
import { btn } from "../../kit/ui/controls";
import { acts, entry, list, nameLine } from "../../kit/ui/entry";
import { chip, jobBar } from "../../kit/ui/marks";
import { dashH } from "../../kit/ui/text";
import { bytes, duration, timeOfDay } from "../../kit/format";
import { fetchMapRegistry, mapRegistry } from "../../app/map-types";
import { jobName, jobPercent, jobTimeLeft } from "../../app/shell/jobstrip";
import { closeConfirm, confirmingOn, openConfirm, refused, write } from "./maps-actions";

import type { MapJobBody, MapJobDetailResponse, MapJobResponse } from "../../api/shapes";

const logs: Record<string, string[]> = {};
const logOpen: Record<string, boolean> = {};

export function jobOptionsText(job: MapJobBody): string {
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

function confirmLine(key: string, question: string, yes: string, no: string): Safe {
  return html`<span class="confirm" role="group">${question} ${btn(yes, { data: { act: "confirm", key: key } })} ${btn(no, { data: { act: "keep" } })}</span>`;
}

function logBlock(job: MapJobBody, label: string): Safe {
  const open = !!logOpen[job.id];
  const pre = open ? html`<pre class="logpre" id="${"log-" + job.id}">${(logs[job.id] || ["…"]).slice(-20).join("\n")}</pre>` : "";
  return html`${btn(label + (open ? " ▾" : " ▸"), { data: { act: "log", job: job.id } })}${pre}`;
}

function runningEntry(job: MapJobBody): Safe {
  const key = "cancel:" + job.id;
  const stop = confirmingOn(key)
    ? confirmLine(key, "cancel? partial output is deleted", "yes, cancel", "keep running")
    : btn("cancel", { title: "stop this job; what it wrote so far is deleted", data: { act: "ask", key: key } });
  const words = [jobPercent(job), job.stage_words, jobTimeLeft(job)].filter(Boolean).join(" · ");
  const started = job.started ? " · started " + timeOfDay(job.started) : "";
  return entry(
    {},
    html`<div class="id">${nameLine("generating " + jobName(job), jobOptionsText(job) + started)}</div>${acts(stop)}
    <div class="full">${jobBar(job.pct === null ? null : job.pct * 100, "generating " + jobName(job))}<p class="note jobmeta">${words}${job.last_line ? " · " + job.last_line : ""}</p>${logBlock(job, "log (last lines)")}</div>`
  );
}

function queuedEntry(job: MapJobBody): Safe {
  return entry({}, html`<div class="id">${nameLine(html`<span class="muted">queued</span> ${jobName(job)}`, jobOptionsText(job))}</div>${acts(btn("remove", { title: "take this job off the queue", data: { act: "cancel", job: job.id } }))}`);
}

/* A finished job that replaces another type offers to make it the default and drop the old. */
function replaceOffer(job: MapJobBody): Safe {
  const body = mapRegistry.body;
  const made = job.produces[0];
  const target = body?.types.filter((t) => t.id === made)[0]?.replaces;
  if (!body || !made) return html``;
  const gone = target ? body.types.filter((t) => t.id === target)[0] : undefined;
  const key = "replace:" + job.id;
  if (confirmingOn(key)) return confirmLine(key, "make " + made + " the default and delete " + target + (gone ? " (" + bytes(gone.bytes) + ")" : "") + "?", "yes", "not now");
  const swap = gone ? btn("make default and delete " + target, { title: "frees " + bytes(gone.bytes), data: { act: "ask", key: key } }) : "";
  return html`${btn("make default", { data: { act: "default", id: made } })}${swap}`;
}

function finishedEntry(job: MapJobBody): Safe {
  const took = job.started && job.ended ? duration(job.ended - job.started) : "";
  const state = job.status === "done" ? chip("done" + (took ? " in " + took : ""), "ok") : chip(job.status, job.status === "failed" ? "bad" : "muted");
  const failed = job.status === "failed";
  const extra = failed ? html`<div class="full">${job.error_line ? html`<p class="note tone-bad">${job.error_line}</p>` : ""}${logBlock(job, "show log")}</div>` : "";
  return entry({ tone: failed ? "bad" : "" }, html`<div class="id">${nameLine(html`${state} ${jobName(job)}`, jobOptionsText(job))}</div>${acts(job.status === "done" ? replaceOffer(job) : "")}${extra}`);
}

export function jobsMarkup(): Safe {
  const body = mapRegistry.body;
  if (!body) return html``;
  const active = body.jobs.filter((job) => job.status === "running" || job.status === "queued");
  const recent = body.jobs.filter((job) => job.status !== "running" && job.status !== "queued" && job.ended && Date.now() / 1000 - job.ended < 3600)[0];
  const rows = active.map((job) => (job.status === "running" ? runningEntry(job) : queuedEntry(job)));
  if (recent) rows.push(finishedEntry(recent));
  if (!rows.length) return html``;
  return html`${dashH("jobs", rows.length, "h3")}${list("job2", "stacked", rows, "nolead")}`;
}

function fetchLog(id: string, host: HTMLElement): void {
  get<MapJobDetailResponse>("/api/maps/jobs/{job}", id)
    .then(function (reply) {
      logs[id] = reply.log_tail;
      const pre = host.querySelector("#log-" + CSS.escape(id));
      if (pre) pre.textContent = reply.log_tail.slice(-20).join("\n") || "(no output yet)";
    })
    .catch(function () {
      const pre = host.querySelector("#log-" + CSS.escape(id));
      if (pre) pre.textContent = "the log could not be read";
    });
}

function cancelJob(id: string): void {
  send<MapJobResponse>("DELETE", "/api/maps/jobs/{job}", undefined, id)
    .then(function () {
      fetchMapRegistry();
    })
    .catch(refused("the job was not cancelled"));
}

/** Confirmed replace: make the new map the default, then delete the old one. */
function replaceNow(jobId: string): void {
  const body = mapRegistry.body;
  const job = body?.jobs.filter((j) => j.id === jobId)[0];
  const made = job?.produces[0];
  const target = body?.types.filter((t) => t.id === made)[0]?.replaces;
  if (!made || !target) return;
  write("PUT", "/api/maps/default", { id: made })
    .then(function () {
      return write("DELETE", "/api/maps/{ident}", undefined, target);
    })
    .catch(refused("the switch did not complete"));
}

/** The jobs list's buttons; false when the click was not one of them. */
export function jobAction(button: HTMLElement, host: HTMLElement, redraw: () => void): boolean {
  const act = button.dataset.act;
  const key = button.dataset.key || "";
  if (act === "log") {
    const id = button.dataset.job!;
    logOpen[id] = !logOpen[id];
    mount(host, jobsMarkup());
    if (logOpen[id]) fetchLog(id, host);
  } else if (act === "ask") openConfirm(key);
  else if (act === "keep") {
    closeConfirm();
    redraw();
  } else if (act === "confirm" && (key.indexOf("cancel:") === 0 || key.indexOf("replace:") === 0)) {
    closeConfirm();
    if (key.indexOf("cancel:") === 0) cancelJob(key.slice(7));
    else replaceNow(key.slice(8));
    redraw();
  } else if (act === "cancel") cancelJob(button.dataset.job!);
  else if (act === "default") write("PUT", "/api/maps/default", { id: button.dataset.id }).catch(refused("the default map was not changed"));
  else return false;
  return true;
}
