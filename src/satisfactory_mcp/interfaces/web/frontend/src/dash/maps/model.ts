/* What the map studio derives from /api/maps before drawing it. Pure, so it is tested
 * without a page. */

import { hoursMinutes } from "../../kit/format";

import type { MapJobBody, MapTypeBody } from "../../api/shapes";

/** The jobs worth a row: running, queued, and the last one that ended in the past hour. */
export function shownJobs(jobs: MapJobBody[], now: number): MapJobBody[] {
  const active = jobs.filter((job) => job.status === "running" || job.status === "queued");
  const ended = jobs.filter((job) => job.status !== "running" && job.status !== "queued" && job.ended && now - job.ended < 3600);
  ended.sort((a, b) => (b.ended || 0) - (a.ended || 0));
  return ended.length ? active.concat([ended[0]!]) : active;
}

/** The time column, one format: "32 min left" running, the estimate queued, the run's length
 *  once it ended. */
export function jobTime(job: MapJobBody): string {
  if (job.status === "running") return job.eta_s === null ? "" : hoursMinutes(job.eta_s) + " left";
  if (job.status === "queued") return job.estimate_s === null ? "" : hoursMinutes(job.estimate_s);
  return job.started !== null && job.ended !== null ? hoursMinutes(job.ended - job.started) : "";
}

/** How many types record this input among what they were drawn from. */
export function drawnFrom(name: string, types: MapTypeBody[]): number {
  return types.filter(function (row) {
    const inputs = (row.axes as { inputs?: Record<string, unknown> }).inputs;
    return !!inputs && typeof inputs === "object" && name in inputs;
  }).length;
}
