/* Progress > MAM: research per tree, what each node costs, and what is under way. */

import { appendNote } from "../../kit/dashkit";
import { make } from "../../kit/dom";
import { amountList, doneFilteredCard, spoilerNote, STOCK, tierProgressBar } from "./cells";
import { changed, mam, visible, waiting } from "./feeds";

import type { Column, SortState } from "../../kit/dashkit";
import type { MamRow } from "../../api/shapes";

const mamSort: SortState = { key: "tree", desc: false };

let showDone = false;

function duration(seconds: number): string {
  const minutes = Math.round(seconds / 60);
  return minutes < 60 ? minutes + " min" : Math.floor(minutes / 60) + " h " + (minutes % 60) + " min";
}

function mamStatus(research: MamRow): string {
  if (research.status === "RUNNING") return "running, " + duration(research.running_s || 0) + " left at save";
  if (research.status === "BLOCKED") return "needs " + research.blocked_by.join(", ") + " first";
  if (research.status === "TREE SHUT") return "tree not open";
  if (research.status === "READY") return "affordable";
  if (research.status === "DONE") return "done";
  return research.status.toLowerCase();
}

function stillOwed(research: MamRow): boolean {
  return research.status !== "DONE" && research.status !== "RUNNING";
}

const MAM_COLUMNS: Column<MamRow>[] = [
  {
    key: "name",
    label: "research",
    sort: function (research) {
      return research.name;
    },
    render: function (research) {
      return research.name;
    },
  },
  {
    key: "tree",
    label: "tree",
    sort: function (research) {
      return (research.tree || "~") + "\u0000" + research.name;
    },
    render: function (research) {
      return research.tree || "–";
    },
  },
  {
    key: "status",
    label: "status",
    title: "running: paid for and under way; the time left is what the save recorded and does not run down while the game is closed",
    sort: mamStatus,
    render: mamStatus,
    tone: function (research) {
      if (research.status === "READY") return "ok";
      return research.status === "TREE SHUT" ? "dash-muted" : "";
    },
  },
  {
    key: "cost",
    label: "cost",
    title: "cost is " + STOCK,
    render: function (research) {
      return amountList(research.cost);
    },
  },
  {
    key: "short",
    label: "short by",
    render: function (research) {
      return stillOwed(research) ? amountList(research.short) : "";
    },
    tone: function (research) {
      return research.short.length && stillOwed(research) ? "bad" : "";
    },
  },
  {
    key: "recipes",
    label: "recipes",
    align: "right",
    title: "recipes the node newly grants",
    sort: function (research) {
      return research.unlocks;
    },
    render: function (research) {
      return research.unlocks || "";
    },
  },
];

function treeStrip(rows: MamRow[]): HTMLElement {
  const trees: Record<string, number[]> = {};
  rows.forEach(function (research) {
    const name = research.tree || "other";
    const tally = trees[name] || (trees[name] = [0, 0]);
    if (research.status === "DONE") tally[0]! += 1;
    tally[1]! += 1;
  });
  const strip = make("div", "dash-tiers");
  Object.keys(trees)
    .sort()
    .forEach(function (name) {
      strip.appendChild(tierProgressBar(name, trees[name]![0]!, trees[name]![1]!));
    });
  return strip;
}

export function renderMam(body: HTMLElement): void {
  const data = mam.data;
  if (!data) {
    waiting(body, mam);
    return;
  }
  const rows = visible(data.research);
  const hidden = data.research.length - rows.length;
  const capabilities = visible(data.capabilities);
  if (capabilities.length) {
    appendNote(
      body,
      capabilities
        .map(function (capability) {
          return (capability.schematic_name || capability.capability) + ": " + (capability.researched ? "researched" : "not researched");
        })
        .join(" · ")
    );
  }
  body.appendChild(treeStrip(rows));
  if (hidden) spoilerNote(body, hidden + " research nodes in trees not opened yet are hidden");
  if (!data.knows_trees) appendNote(body, "this save predates the list of opened trees, so a node in an unopened tree reads as available");

  body.appendChild(
    doneFilteredCard(
      "MAM research",
      showDone,
      function (on) {
        showDone = on;
        changed();
      },
      rows,
      function (research) {
        return research.status === "DONE";
      },
      MAM_COLUMNS,
      mamSort,
      "every research node shown here is done"
    )
  );
}
