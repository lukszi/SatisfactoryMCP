/* Progress > Space Elevator: the phase delivered, the one deliveries go to, and what it owes. */

import { appendNote, heading, table, tile } from "../../kit/dashkit";
import { make } from "../../kit/dom";
import { formatNumber, phaseText } from "../../kit/format";
import { amountList, spoilerNote } from "./cells";
import { phase, visible, waiting } from "./feeds";

import type { Column } from "../../kit/dashkit";
import type { PhaseResponse, PhaseRow } from "../../api/shapes";

type Part = PhaseRow["outstanding"][number];

function phaseNumber(name: string | null): number | null {
  const match = /_(\d+)$/.exec(name || "");
  return match ? +match[1]! : null;
}

function phaseStatus(row: PhaseRow, data: PhaseResponse): string {
  const number = phaseNumber(row.phase);
  const current = phaseNumber(data.current_phase);
  const target = phaseNumber(data.target_phase);
  if (number === null || (current === null && target === null)) return "–";
  if (current !== null && number <= current) return "delivered";
  if (target !== null && number === target) return "next";
  return "not started";
}

export function targetRow(data: PhaseResponse): PhaseRow | null {
  return (
    data.phases.filter(function (row) {
      return !!row.phase && row.phase === data.target_phase;
    })[0] || null
  );
}

export function shortParts(row: PhaseRow): number {
  return row.outstanding.filter(function (part) {
    return part.short > 0;
  }).length;
}

const PART_COLUMNS: Column<Part>[] = [
  {
    key: "name",
    label: "part",
    render: function (part) {
      return part.name;
    },
  },
  {
    key: "amount",
    label: "needed",
    align: "right",
    render: function (part) {
      return formatNumber(part.amount);
    },
  },
  {
    key: "have",
    label: "have",
    align: "right",
    title: "spendable stock: carried, storage containers and the Dimensional Depot; parts in machines and on belts are not counted",
    render: function (part) {
      return formatNumber(part.have);
    },
  },
  {
    key: "short",
    label: "short by",
    align: "right",
    render: function (part) {
      return part.short > 0 ? formatNumber(part.short) : "";
    },
    tone: function (part) {
      return part.short > 0 ? "bad" : "";
    },
  },
];

function phaseColumns(data: PhaseResponse): Column<PhaseRow>[] {
  return [
    {
      key: "phase",
      label: "phase",
      render: function (row) {
        return phaseText(row.phase) || "–";
      },
    },
    {
      key: "status",
      label: "status",
      render: function (row) {
        return phaseStatus(row, data);
      },
      tone: function (row) {
        return phaseStatus(row, data) === "not started" ? "dash-muted" : "";
      },
    },
    {
      key: "owed",
      label: "parts still owed",
      render: function (row) {
        const status = phaseStatus(row, data);
        return status === "next" || (status === "not started" && row.trust === "usable") ? amountList(row.outstanding) : "–";
      },
    },
  ];
}

function deliverableTile(data: PhaseResponse, target: PhaseRow | null): HTMLElement {
  if (data.deliverable === null) return tile("deliverable now", "–", "no record for the target phase");
  if (data.deliverable) return tile("deliverable now", "yes", "every part is in stock");
  const short = target ? shortParts(target) : 0;
  return tile("deliverable now", "no", short + " of " + target!.outstanding.length + " parts short");
}

function elevatorTiles(data: PhaseResponse, target: PhaseRow | null): HTMLElement {
  const tiles = make("div", "dash-tiles");
  tiles.appendChild(tile("current phase", phaseText(data.current_phase) || "–", data.current_phase ? "the last phase delivered" : "no phase in this save"));
  tiles.appendChild(tile("target phase", phaseText(data.target_phase) || "–", data.target_phase ? "where deliveries go" : "no phase in this save"));
  tiles.appendChild(deliverableTile(data, target));
  return tiles;
}

function targetCard(body: HTMLElement, data: PhaseResponse, target: PhaseRow | null): void {
  const card = make("section", "dash-card");
  heading(card, "target: " + phaseText(data.target_phase));
  if (!target) appendNote(card, "the save has no per-phase record for the target phase");
  else if (!target.outstanding.length) appendNote(card, "nothing outstanding on the target phase");
  else card.appendChild(table(PART_COLUMNS, target.outstanding, { caption: "parts owed to the target phase" }));
  const paid = "delivered so far: " + (data.delivered.length ? amountList(data.delivered) : "nothing");
  appendNote(card, target?.trust === "derived" ? paid + "; the amounts owed are a lower bound" : paid);
  body.appendChild(card);
}

export function renderElevator(body: HTMLElement): void {
  const data = phase.data;
  if (!data) {
    waiting(body, phase);
    return;
  }
  const target = targetRow(data);
  body.appendChild(elevatorTiles(data, target));
  if (data.target_phase) targetCard(body, data, target);

  const named = data.phases.filter(function (row) {
    return !!row.phase;
  });
  const rows = visible(named);
  const all = make("section", "dash-card");
  heading(all, "phases");
  if (rows.length) all.appendChild(table(phaseColumns(data), rows, { caption: "Space Elevator phases" }));
  else if (rows.length === named.length) appendNote(all, "this save records no Space Elevator phase");
  const hidden = named.length - rows.length;
  if (hidden) spoilerNote(all, data.target_phase ? hidden + " phases past the target are hidden" : "the save names no target phase, so its " + hidden + " phase records are hidden");
  body.appendChild(all);
}
