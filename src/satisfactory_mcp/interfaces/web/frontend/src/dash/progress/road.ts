/* The roadmap band: start and each phase as stops on one rail, the tiers each opens under it,
 * "you are here" between the last delivered phase and the target. */

import { html, type Safe } from "../../kit/html";
import { bar } from "../../kit/ui/marks";
import { hashFor } from "../../map/map";
import type { Road, Stop, TierSeg } from "./model";

function seg(t: TierSeg, delivered: number, opens: number): Safe {
  const lock = opens > delivered;
  const tip = "tier " + t.tier + ": " + t.done + " of " + t.total + " milestones" + (lock ? ", opens with phase " + opens : "");
  return html`<a class="${lock ? "tseg lock" : "tseg"}" href="${hashFor("progress/reference")}" title="${tip}"><span class="k">tier ${t.tier}</span><span class="v">${t.done} of ${t.total}</span>${bar(t.total ? (t.done / t.total) * 100 : 0)}</a>`;
}

function rail(s: Stop, road: Road, last: boolean): Safe {
  if (last) return html`<span class="rail"><i class="node"></i></span>`;
  if (s.phase < road.delivered) return html`<span class="rail"><i class="node"></i><i class="ln done"></i></span>`;
  if (s.phase === road.delivered && road.target !== null) return html`<span class="rail"><i class="node"></i><i class="ln done"></i><i class="here"><span>you are here</span></i><i class="ln"></i></span>`;
  return html`<span class="rail"><i class="node"></i><i class="ln"></i></span>`;
}

function stateText(s: Stop): string {
  if (s.phase === 0) return "open from the start";
  if (s.state === "done") return "delivered";
  if (s.state === "on") return "target" + (s.tiers.length ? " · opens tier " + s.tiers.map((t) => t.tier).join(", ") : "");
  return "after phase " + (s.phase - 1);
}

export function roadBand(road: Road, spoilersHref: string): Safe {
  const stops = road.stops.map(function (s, i) {
    const name = s.phase === 0 ? "start" : "phase " + s.phase;
    const head = s.state === "on" ? html`<a class="nm" href="${hashFor("progress/gate")}">${name}</a>` : html`<span class="nm">${name}</span>`;
    const segs = s.tiers.length ? html`<div class="segs">${s.tiers.map((t) => seg(t, road.delivered, s.phase))}</div>` : "";
    const last = i === road.stops.length - 1 && !road.later;
    return html`<li class="${"stop " + s.state}"${s.state === "on" ? html` aria-current="step"` : ""}>${head}${rail(s, road, last)}<span class="st">${stateText(s)}</span>${segs}</li>`;
  });
  const later = road.later
    ? html`<li class="stop later" title="hidden while spoilers are off"><span class="nm">+${road.later} later</span><span class="rail"><i class="node"></i></span><span class="st"><a href="${spoilersHref}">show</a></span></li>`
    : "";
  // The outer box runs right to left, so a narrow screen opens scrolled to the target end.
  return html`<nav class="road" aria-label="space elevator phases"><ol class="road-in">${stops}${later}</ol></nav>`;
}
