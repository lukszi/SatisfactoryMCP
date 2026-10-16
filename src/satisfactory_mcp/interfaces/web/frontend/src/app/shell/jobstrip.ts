/* The strip under the top bar on every page while a map job runs: what, how far, a link to
 * the maps page; × hides it for that job only. */

import { el } from "../../kit/dom";
import { html, mount } from "../../kit/html";
import { duration } from "../../kit/format";
import { jobBar } from "../../kit/ui/marks";
import { hashFor } from "../../map/map";
import { mapRegistry, onMapRegistry } from "../map-types";

import type { MapJobBody } from "../../api/shapes";

let dismissed = "";

export function runningJob(): MapJobBody | null {
  const jobs = mapRegistry.body ? mapRegistry.body.jobs : [];
  return jobs.filter((job) => job.status === "running")[0] || null;
}

export function jobName(job: MapJobBody): string {
  return job.produces.length ? job.produces.join(", ") : job.preset;
}

/** "46%" from the job's 0–1 fraction, or "" while its stage has no measure. */
export function jobPercent(job: MapJobBody): string {
  return job.pct === null ? "" : Math.floor(job.pct * 100) + "%";
}

export function jobTimeLeft(job: MapJobBody): string {
  if (job.eta_s !== null) return "~" + duration(job.eta_s) + " left";
  return job.elapsed_s !== null ? duration(job.elapsed_s) + " elapsed" : "";
}

export function drawJobStrip(): void {
  const strip = el("jobstrip");
  const job = runningJob();
  const shown = !!job && job.id !== dismissed;
  strip.hidden = !shown;
  document.body.classList.toggle("job-on", shown);
  if (!job || !shown) return mount(strip, html``);
  const pct = jobPercent(job);
  mount(
    strip,
    html`<a href="${hashFor("maps")}">rendering ${jobName(job)}${pct ? html` · <b>${pct}</b>` : ""}</a><span class="jobmeta">${jobTimeLeft(job)}</span><button type="button" class="btn icon" id="jobstrip-x" aria-label="hide the job strip" title="hide; the job keeps running">×</button>${jobBar(job.pct === null ? null : job.pct * 100, "rendering " + jobName(job))}`
  );
}

export function wireJobStrip(): void {
  el("jobstrip").addEventListener("click", function (event) {
    if (!(event.target as Element).closest("#jobstrip-x")) return;
    dismissed = runningJob()?.id || "";
    drawJobStrip();
  });
  onMapRegistry(drawJobStrip);
}
