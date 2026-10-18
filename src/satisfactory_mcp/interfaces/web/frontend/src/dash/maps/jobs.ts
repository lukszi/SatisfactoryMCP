/* The map studio's jobs: the running one with its bar, the queue, and the last job that ended
 * in the past hour, on one grid (state | name and meta | time | action slot | ⋯). Progress
 * events redraw this list in place. */

import { get, send } from "../../api/client";
import { html, type Safe } from "../../kit/html";
import { btn, moreBtn } from "../../kit/ui/controls";
import { actSlots, entry, list } from "../../kit/ui/entry";
import { chip, jobBar } from "../../kit/ui/marks";
import { dashH } from "../../kit/ui/text";
import { bytes, timeOfDay } from "../../kit/format";
import { fetchMapRegistry, mapRegistry } from "../../app/map-types";
import { hashFor } from "../../map/map";
import { mapsDash, type MapsAddress } from "./address";
import { jobTime, shownJobs } from "./model";
import { inputWords, jobOptionsText, typeTitle } from "./words";
import { closeConfirm, confirmingOn, openConfirm, redraw, refused, setDefault, typeRow, write } from "./writes";

import type { MapJobBody, MapJobDetailResponse, MapJobResponse } from "../../api/shapes";

const logs: Record<string, string[]> = {};
const logOpen: Record<string, boolean> = {};

function stateChip(job: MapJobBody): Safe {
  if (job.status === "running") return chip(job.pct === null ? "running" : Math.floor(job.pct * 100) + "%");
  if (job.status === "queued") return chip("queued", "muted");
  if (job.status === "done") return chip("done", "ok");
  return chip(job.status, job.status === "failed" ? "bad" : "muted");
}

function nameOf(job: MapJobBody, a: MapsAddress): Safe {
  const rows = job.produces.map((id) => typeRow(id)).filter((row) => !!row);
  const text = rows.length ? rows.map((row) => typeTitle(row!)).join(", ") : job.label || (job.preset === "render" || job.preset === "artwork" ? job.produces.join(", ") || job.preset : inputWords(job.preset));
  if (!job.produces.length) return html`<span class="name" title="${text}">${text}</span>`;
  return html`<a class="name" href="${hashFor(mapsDash({ gen: a.gen, type: job.produces[0]! }))}" data-nav="replace" title="${text}">${text}</a>`;
}

function metaOf(job: MapJobBody): string {
  const parts = [jobOptionsText(job)];
  if (job.status === "running") {
    if (job.stage_words) parts.push(job.stage_words);
    if (job.peak_rss) parts.push("peak " + bytes(job.peak_rss));
    if (job.started) parts.push("started " + timeOfDay(job.started));
  }
  const old = job.status === "done" ? typeRow(typeRow(job.produces[0])?.replaces) : undefined;
  if (old) parts.push("replaces " + typeTitle(old));
  return parts.join(" · ");
}

function logBlock(job: MapJobBody): Safe {
  if (!logOpen[job.id]) return html``;
  return html`<pre class="logpre" id="${"log-" + job.id}">${(logs[job.id] || ["…"]).slice(-20).join("\n")}</pre>`;
}

function logBtn(job: MapJobBody, label: string): Safe {
  return html`<button type="button" class="btn" data-act="log" data-job="${job.id}" aria-expanded="${String(!!logOpen[job.id])}" title="the last lines of its log">${label}</button>`;
}

function confirmLine(key: string, question: string, yes: string, no: string): Safe {
  return html`<p class="note confirm" role="group">${question} ${btn(yes, { data: { act: "confirm", key: key } })}${btn(no, { data: { act: "keep" } })}</p>`;
}

/* The finished job's offer: make what it drew the default, or that and delete what it replaces. */
function doneSlots(job: MapJobBody): Safe {
  const made = typeRow(job.produces[0]);
  if (!made || made.default || made.status !== "ready") return actSlots("");
  const old = typeRow(made.replaces);
  const menu = old ? moreBtn(typeTitle(made), ["make default and delete " + typeTitle(old) + " (" + bytes(old.bytes) + ")"]) : "";
  return actSlots(btn("make default", { data: { act: "default", id: made.id } }), { more: menu });
}

function slotsOf(job: MapJobBody): Safe {
  if (job.status === "running") {
    if (confirmingOn("cancel:" + job.id)) return actSlots("");
    return actSlots({ split: [logBtn(job, "log"), btn("cancel", { title: "stop this job; what it wrote so far is deleted", data: { act: "ask", key: "cancel:" + job.id } })] });
  }
  if (job.status === "queued") return actSlots(btn("remove", { title: "take this job off the queue", data: { act: "remove", job: job.id } }));
  if (job.status === "done") return doneSlots(job);
  return actSlots(logBtn(job, "show log"));
}

function underRow(job: MapJobBody): Safe {
  const parts: Safe[] = [];
  if (job.status === "running") parts.push(jobBar(job.pct === null ? null : job.pct * 100, "rendering " + metaOf(job)));
  if (confirmingOn("cancel:" + job.id)) parts.push(confirmLine("cancel:" + job.id, "cancel? what it wrote so far is deleted", "yes, cancel", "keep running"));
  const made = typeRow(job.produces[0]);
  const old = typeRow(made?.replaces);
  if (old && confirmingOn("replace:" + job.id)) parts.push(confirmLine("replace:" + job.id, "make it the default and delete “" + typeTitle(old) + "”? " + bytes(old.bytes), "yes", "keep both"));
  if (job.status === "failed" && job.error_line) parts.push(html`<p class="note tone-bad">${job.error_line}</p>`);
  parts.push(logBlock(job));
  return html`<div class="full">${parts}</div>`;
}

function jobEntry(job: MapJobBody, a: MapsAddress): Safe {
  return entry(
    { tone: job.status === "failed" ? "bad" : "", key: "job:" + job.id },
    html`<div class="state">${stateChip(job)}</div><div class="id">${nameOf(job, a)}<div class="ctx">${metaOf(job)}</div></div><div class="when">${jobTime(job)}</div>${slotsOf(job)}${underRow(job)}`
  );
}

export function jobsMarkup(a: MapsAddress): Safe {
  const body = mapRegistry.body;
  if (!body) return html``;
  const rows = shownJobs(body.jobs, Date.now() / 1000).map((job) => jobEntry(job, a));
  if (!rows.length) return html``;
  return html`${dashH("jobs", rows.length)}${list("mjob", "stacked", rows)}`;
}

function fetchLog(id: string, host: HTMLElement): void {
  const pre = () => host.querySelector("#log-" + CSS.escape(id));
  get<MapJobDetailResponse>("/api/maps/jobs/{job}", id)
    .then(function (reply) {
      logs[id] = reply.log_tail;
      const at = pre();
      if (at) at.textContent = reply.log_tail.slice(-20).join("\n") || "(no output yet)";
    })
    .catch(function () {
      const at = pre();
      if (at) at.textContent = "the log could not be read";
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
  const job = mapRegistry.body?.jobs.filter((j) => j.id === jobId)[0];
  const made = typeRow(job?.produces[0]);
  if (!made || !made.replaces) return;
  write("PUT", "/api/maps/default", { id: made.id, version: mapRegistry.body?.version })
    .then(function () {
      return write("DELETE", "/api/maps/{ident}", undefined, made.replaces!, "version=" + mapRegistry.body?.version);
    })
    .catch(refused("the switch did not complete"));
}

/** A ⋯ pick on a job row. */
export function jobMenu(label: string, jobId: string): void {
  if (label.indexOf("make default and delete") === 0) openConfirm("replace:" + jobId);
}

/** The jobs list's buttons; false when the click was not one of them. */
export function jobAction(button: HTMLElement, host: HTMLElement, redrawJobs: () => void): boolean {
  const act = button.dataset.act;
  const key = button.dataset.key || "";
  if (act === "log") {
    const id = button.dataset.job!;
    logOpen[id] = !logOpen[id];
    redrawJobs();
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
  } else if (act === "remove") cancelJob(button.dataset.job!);
  else if (act === "default") setDefault(button.dataset.id || "");
  else return false;
  return true;
}
