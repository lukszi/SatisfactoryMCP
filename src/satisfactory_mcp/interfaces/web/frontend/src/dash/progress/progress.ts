/* The dashboard's Progress section: milestones, MAM, Space Elevator, hard drives, shards and
 * somersloops, facts only. See docs/frontend_vision.md, "Phase 4: Progress". */

import { link, subTabs } from "../../kit/dashkit";
import { make } from "../../kit/dom";
import { formatNumber } from "../../kit/format";
import { hashFor } from "../../map/map";
import { go } from "../../app/nav";
import { renderDrives } from "./drives";
import { renderElevator, shortParts, targetRow } from "./elevator";
import { drives, mam, phase, shards, sloops, visible } from "./feeds";
import { renderMam } from "./mam";
import { renderMilestones } from "./milestones";
import { renderShards } from "./shards";
import { renderSloops } from "./sloops";

import type { PointButton } from "./shards";

const PROGRESS_SECTIONS: [string, string][] = [
  ["", "Milestones"],
  ["mam", "MAM"],
  ["elevator", "Space Elevator"],
  ["drives", "Hard drives"],
  ["shards", "Power shards"],
  ["sloops", "Somersloops"],
];

// The space elevator's headline; null while the phase is unread.
function elevatorHeadline(): string | null {
  const phaseData = phase.data;
  if (!phaseData) return null;
  if (phaseData.deliverable === null) return "no target";
  if (phaseData.deliverable) return "deliverable";
  return shortParts(targetRow(phaseData)!) + " parts short";
}

/* One line linking each other section, with its headline, under the milestones. */
function nextUp(body: HTMLElement): void {
  const line = make("p", "dash-note");
  const parts: HTMLElement[] = [];
  const add = function (dash: string, label: string, text: string | null): void {
    if (text === null) return;
    const span = make("span", "", label + ": ");
    span.appendChild(link(dash, text));
    parts.push(span);
  };
  add("progress/elevator", "space elevator", elevatorHeadline());
  const mamData = mam.data;
  if (mamData) {
    const rows = visible(mamData.research);
    const ready = rows.filter(function (research) {
      return research.status === "READY";
    }).length;
    const running = rows.filter(function (research) {
      return research.status === "RUNNING";
    }).length;
    add("progress/mam", "MAM", ready + " affordable" + (running ? ", " + running + " running" : ""));
  }
  const driveData = drives.data;
  add("progress/drives", "hard drives", driveData ? driveData.drives.length + " pending" : null);
  const shardData = shards.data;
  add("progress/shards", "power shards", shardData ? formatNumber(shardData.free) + " free" : null);
  const sloopData = sloops.data;
  add("progress/sloops", "somersloops", sloopData ? formatNumber(sloopData.free) + " free" : null);
  if (!parts.length) return;
  parts.forEach(function (part, i) {
    if (i) line.appendChild(document.createTextNode(" · "));
    line.appendChild(part);
  });
  body.appendChild(line);
}

export function renderProgress(body: HTMLElement, subject: string, point: PointButton): void {
  const section = PROGRESS_SECTIONS.some(function (entry) {
    return entry[0] === subject;
  })
    ? subject
    : "";
  body.appendChild(
    subTabs(
      PROGRESS_SECTIONS.map(function (entry) {
        return { id: entry[0], label: entry[1], href: hashFor(entry[0] ? "progress/" + entry[0] : "progress") };
      }),
      section,
      function (id) {
        go(id ? "progress/" + id : "progress");
      },
      "Progress sections"
    )
  );
  if (section === "mam") renderMam(body);
  else if (section === "elevator") renderElevator(body);
  else if (section === "drives") renderDrives(body);
  else if (section === "shards") renderShards(body, point);
  else if (section === "sloops") renderSloops(body, point);
  else {
    nextUp(body);
    renderMilestones(body);
  }
}
