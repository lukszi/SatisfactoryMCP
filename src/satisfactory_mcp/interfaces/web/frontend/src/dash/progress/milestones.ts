/* Progress > Milestones: the HUB tiers and every milestone with what it costs and lacks. */

import { tile } from "../../kit/dashkit";
import { make } from "../../kit/dom";
import { formatNumber, phaseText } from "../../kit/format";
import { hashFor } from "../../map/map";
import { amountList, doneFilteredCard, spoilerNote, STOCK, tierProgressBar } from "./cells";
import { changed, milestones, visible, waiting } from "./feeds";

import type { Column, SortState } from "../../kit/dashkit";
import type { MilestoneRow } from "../../api/shapes";

const milestoneSort: SortState = { key: "tier", desc: false };

let showDone = false;

function milestoneStatus(milestone: MilestoneRow): string {
  if (milestone.opens_at !== null && milestone.status !== "DONE") return "locked (phase " + milestone.opens_at + ")";
  if (milestone.status === "DONE") return "done";
  if (milestone.status === "READY") return "affordable";
  if (milestone.status === "BLOCKED") return "needs " + milestone.blocked_by.join(", ") + " first";
  return milestone.status.toLowerCase();
}

function affordable(rows: MilestoneRow[]): number {
  return rows.filter(function (milestone) {
    return milestoneStatus(milestone) === "affordable";
  }).length;
}

export function readyMilestones(): number | null {
  return milestones.data ? affordable(visible(milestones.data.milestones)) : null;
}

export function milestoneTile(): HTMLElement {
  if (!milestones.data) {
    return tile("milestones", "–", milestones.failed ? "could not be read" : "loading…", false, hashFor("progress"));
  }
  const ready = affordable(visible(milestones.data.milestones));
  const top = milestones.data.highest_complete_tier;
  return tile(
    "milestones",
    top === null ? "no tier complete" : "tier " + top + " complete",
    (phaseText(milestones.data.game_phase) || "no phase in this save") + " · " + ready + " affordable",
    false,
    hashFor("progress")
  );
}

const MILESTONE_COLUMNS: Column<MilestoneRow>[] = [
  {
    key: "name",
    label: "milestone",
    sort: function (milestone) {
      return milestone.name;
    },
    render: function (milestone) {
      return milestone.name;
    },
  },
  {
    key: "tier",
    label: "tier",
    align: "right",
    sort: function (milestone) {
      return milestone.tier * 1000 + (milestone.status === "DONE" ? 0 : 1);
    },
    render: function (milestone) {
      return milestone.tier;
    },
  },
  {
    key: "status",
    label: "status",
    sort: milestoneStatus,
    render: milestoneStatus,
    tone: function (milestone) {
      const status = milestoneStatus(milestone);
      return status === "affordable" ? "ok" : status.indexOf("locked") === 0 ? "dash-muted" : "";
    },
  },
  {
    key: "cost",
    label: "cost",
    title: "cost is " + STOCK,
    render: function (milestone) {
      return amountList(milestone.cost);
    },
  },
  {
    key: "short",
    label: "short by",
    render: function (milestone) {
      return milestone.status === "DONE" ? "" : amountList(milestone.short);
    },
    tone: function (milestone) {
      return milestone.short.length && milestone.status !== "DONE" && milestone.opens_at === null ? "bad" : "";
    },
  },
  {
    key: "recipes",
    label: "recipes",
    align: "right",
    title: "recipes the milestone newly grants",
    sort: function (milestone) {
      return milestone.unlocks;
    },
    render: function (milestone) {
      return milestone.unlocks || "";
    },
  },
];

export function renderMilestones(body: HTMLElement): void {
  const full = milestones.data;
  if (!full) {
    waiting(body, milestones);
    return;
  }
  const rows = visible(full.milestones);
  const tiers = visible(full.tiers);
  const counts: Record<string, number> = { affordable: 0, short: 0, done: 0, locked: 0 };
  rows.forEach(function (milestone) {
    const status = milestoneStatus(milestone);
    const key = status.indexOf("locked") === 0 ? "locked" : status.indexOf("blocked") === 0 ? "short" : status;
    counts[key] = (counts[key] || 0) + 1;
  });
  const tiles = make("div", "dash-tiles");
  tiles.appendChild(
    tile(
      "highest complete tier",
      full.highest_complete_tier === null ? "none" : String(full.highest_complete_tier),
      phaseText(full.game_phase) || "no phase in this save"
    )
  );
  tiles.appendChild(tile("affordable now", formatNumber(counts.affordable!), "bill covered by spendable stock"));
  tiles.appendChild(tile("short", formatNumber(counts.short!), counts.locked ? counts.locked + " more locked behind a phase" : "stock does not cover the bill"));
  tiles.appendChild(tile("done", formatNumber(counts.done!), "of " + rows.length + " milestones"));
  body.appendChild(tiles);

  const strip = make("div", "dash-tiers");
  tiers.forEach(function (tier) {
    strip.appendChild(tierProgressBar("tier " + tier.tier, tier.done, tier.total));
  });
  body.appendChild(strip);
  if (tiers.length < full.tiers.length) spoilerNote(body, "tiers the HUB has not opened yet are hidden");

  body.appendChild(
    doneFilteredCard(
      "milestones",
      showDone,
      function (on) {
        showDone = on;
        changed();
      },
      rows,
      function (milestone) {
        return milestone.status === "DONE";
      },
      MILESTONE_COLUMNS,
      milestoneSort,
      "every milestone shown here is done"
    )
  );
}
