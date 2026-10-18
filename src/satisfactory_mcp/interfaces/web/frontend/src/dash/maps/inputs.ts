/* The inputs maps are drawn from, in fixed columns: name | version line | maps drawn from it |
 * rebuild. */

import { html, type Safe } from "../../kit/html";
import { busyBtn } from "../../kit/ui/controls";
import { actSlots, entry, list, metric } from "../../kit/ui/entry";
import { chip } from "../../kit/ui/marks";
import { dashH } from "../../kit/ui/text";
import { dayMonth } from "../../kit/format";
import { drawnFrom } from "./model";
import { INPUT_PRESET } from "./words";

import type { MapInputBody, MapsResponse } from "../../api/shapes";

function versionLine(row: MapInputBody): string {
  const parts: string[] = [];
  if (row.version !== null) parts.push("v" + row.version);
  if (row.cl !== null) parts.push("build " + row.cl);
  if (row.transcribed) {
    const at = Date.parse(row.transcribed);
    parts.push(isNaN(at) ? row.transcribed : dayMonth(at / 1000));
  }
  return parts.join(" · ");
}

function inputEntry(row: MapInputBody, body: MapsResponse): Safe {
  const preset = INPUT_PRESET[row.name];
  const queued = !!preset && body.jobs.some((job) => job.status === "queued" && job.preset === preset);
  const running = !!preset && body.jobs.some((job) => job.status === "running" && job.preset === preset);
  const lead = queued ? chip("queued", "mid") : running ? chip("building", "mid") : "";
  const line = row.present ? versionLine(row) : "not built";
  const why = !body.can_generate.ok ? body.can_generate.reason || "generation cannot run here" : queued || running ? "a rebuild is already queued" : "rebuild the " + row.name + "; maps drawn from the old one turn “older data”";
  const action = preset ? busyBtn("rebuild", { disabled: !body.can_generate.ok || queued || running, title: why, data: { act: "rebuild", preset: preset } }) : "";
  return entry(
    { tone: row.present ? "" : "mid" },
    html`<span class="name">${row.name}</span><div class="ctx">${lead} ${line}</div>${metric(drawnFrom(row.name, body.types), "maps")}${actSlots(action, { more: false })}`
  );
}

export function inputsMarkup(body: MapsResponse): Safe {
  const rows = body.inputs.map((row) => inputEntry(row, body));
  return html`<section id="maps-inputs">${dashH("inputs", rows.length)}${list("minp", "stacked", rows)}</section>`;
}
